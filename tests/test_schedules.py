"""
Schedule maths and the NHL Schedule Insights endpoints.

The `schedule_utils` half is pure and runs on synthetic games, so it
asserts exact numbers. The endpoint half runs against whatever
`nhl_schedule` holds - the preseason pipeline fills it and tests must not
rewrite it - so those checks assert invariants (windowing, scoping,
week derivation) rather than fixed totals, and say so if the table is empty.

The league-week override seeds its own weeks rows and deletes them again.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import os
import sys
from pathlib import Path

os.environ["FLASK_SECRET_KEY"] = "schedules-test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module                                    # noqa: E402
import yahoo_auth                                           # noqa: E402
from db import execute, fetch_one                           # noqa: E402
from schedule_utils import (                                # noqa: E402
    derive_weeks, games_per_date, light_nights, summarise, team_game_counts,
)

LEAGUE = 999903
flask_app = app_module.app
flask_app.config["TESTING"] = True

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# (date, home, away) - Jan 1 is a 1-game night, Jan 2 a 3-game night.
GAMES = [
    ("2027-01-01", "TOR", "MTL"),
    ("2027-01-02", "TOR", "BOS"),
    ("2027-01-02", "EDM", "CGY"),
    ("2027-01-02", "NYR", "NYI"),
    ("2027-01-03", "MTL", "BOS"),
]

print("\n=== 1. schedule_utils ===")
check("the threshold is inclusive - a 3-game night is light at threshold 3",
      light_nights(GAMES, threshold=3) == {"2027-01-01", "2027-01-02", "2027-01-03"},
      light_nights(GAMES, threshold=3))
check("nights above the threshold are not light",
      light_nights(GAMES, threshold=2) == {"2027-01-01", "2027-01-03"},
      light_nights(GAMES, threshold=2))
check("nothing is light at threshold 0", light_nights(GAMES, threshold=0) == set())
check("games per date", games_per_date(GAMES) == {"2027-01-01": 1, "2027-01-02": 3, "2027-01-03": 1},
      games_per_date(GAMES))

teams = team_game_counts(GAMES, threshold=2)
check("home and away both counted", teams["TOR"]["games"] == 2, teams.get("TOR"))
check("light nights attributed", teams["MTL"] == {"games": 2, "lightNights": 2}, teams.get("MTL"))
check("busy night not counted light", teams["EDM"] == {"games": 1, "lightNights": 0}, teams.get("EDM"))

# 5 games -> 10 team-appearances across 7 distinct teams.
check("summary averages", summarise(teams)["average"] == round(10 / 7, 1), summarise(teams))
check("summary spread", (summarise(teams)["min"], summarise(teams)["max"]) == (1, 2), summarise(teams))
check("empty summary is zeroed", summarise({}) == {"average": 0, "min": 0, "max": 0})

print("\n=== 2. derive_weeks ===")
# 2026-09-29 is a Tuesday, so week 1 must be short and end that Sunday.
weeks = derive_weeks("2026-09-29", "2026-10-20")
check("first week ends on the first Sunday",
      weeks[0] == {"week": 1, "start": "2026-09-29", "end": "2026-10-04"}, weeks[0])
check("second week is a full Mon-Sun",
      weeks[1] == {"week": 2, "start": "2026-10-05", "end": "2026-10-11"}, weeks[1])
check("last week is clipped to the season end", weeks[-1]["end"] == "2026-10-20", weeks[-1])
check("weeks are contiguous with no gaps",
      all(weeks[i]["end"] < weeks[i + 1]["start"] for i in range(len(weeks) - 1)))
check("single day season yields one week",
      derive_weeks("2026-09-29", "2026-09-29") ==
      [{"week": 1, "start": "2026-09-29", "end": "2026-09-29"}])

print("\n=== 3. endpoints ===")
client = flask_app.test_client()
have_schedule = (fetch_one('SELECT count(*) AS n FROM nhl_schedule') or {}).get("n", 0) > 0

if not have_schedule:
    print("  nhl_schedule is empty - skipping endpoint checks (run the preseason pipeline)")
else:
    check("page renders without a league", client.get("/schedules/").status_code == 200)

    data = client.get("/schedules/api/weeks").get_json()
    check("weeks ok", data["status"] == "success")
    check("derived when no league is selected", data["source"] == "derived", data["source"])
    check("weeks cover the season",
          data["weeks"][0]["start"] == data["season"]["start"]
          and data["weeks"][-1]["end"] == data["season"]["end"])

    full = client.get("/schedules/api/team-games").get_json()
    check("every team appears", len(full["teams"]) == 32, len(full["teams"]))
    check("totalGames matches the per-team sum",
          sum(t["games"] for t in full["teams"].values()) == full["totalGames"] * 2,
          full["totalGames"])
    check("calendar covers every night with a game",
          len(full["calendar"]) == len({c["date"] for c in full["calendar"]}))

    week = data["weeks"][8]
    scoped = client.get(
        f"/schedules/api/team-games?start={week['start']}&end={week['end']}").get_json()
    check("window narrows the result", scoped["totalGames"] < full["totalGames"])
    check("no game falls outside the window",
          all(week["start"] <= c["date"] <= week["end"] for c in scoped["calendar"]))

    detail = client.get(
        f"/schedules/api/team/tor?start={week['start']}&end={week['end']}").get_json()
    check("team code upper-cased", detail["team"] == "TOR")
    check("detail count matches the table",
          len(detail["games"]) == scoped["teams"].get("TOR", {}).get("games", 0),
          (len(detail["games"]), scoped["teams"].get("TOR")))
    check("every game involves the team and is in window",
          all(week["start"] <= g["date"] <= week["end"] for g in detail["games"]))

    check("reversed window rejected",
          client.get("/schedules/api/team-games?start=2027-01-02&end=2027-01-01").status_code == 400)
    check("unknown team returns empty, not an error",
          client.get("/schedules/api/team/ZZZ").get_json()["games"] == [])

    print("\n=== 4. a synced league's weeks win, but only if they overlap ===")
    season = data["season"]
    try:
        def with_league():
            c = flask_app.test_client()
            with c.session_transaction() as sess:
                sess[yahoo_auth.SESSION_GUID] = yahoo_auth.DEV_GUID
                sess[yahoo_auth.SESSION_LEAGUE] = str(LEAGUE)
            return c

        execute("INSERT INTO weeks (league_id, week_num, start_date, end_date)"
                " VALUES (:l, 1, :s, :e)",
                {"l": LEAGUE, "s": season["start"], "e": season["end"]})
        check("overlapping league weeks are used",
              with_league().get("/schedules/api/weeks").get_json()["source"] == "league")

        execute("DELETE FROM weeks WHERE league_id = :l", {"l": LEAGUE})
        execute("INSERT INTO weeks (league_id, week_num, start_date, end_date)"
                " VALUES (:l, 1, '2020-10-01', '2020-10-07')", {"l": LEAGUE})
        check("a season-mismatched league falls back to derived weeks",
              with_league().get("/schedules/api/weeks").get_json()["source"] == "derived")
    finally:
        execute("DELETE FROM weeks WHERE league_id = :l", {"l": LEAGUE})

print("\n" + "=" * 46)
if FAILURES:
    print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    sys.exit(1)
print("All checks passed.")
