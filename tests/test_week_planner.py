"""
Tests for standalone mode's week planner and its routes.

The planner half runs on a stub pool, so it asserts exact behaviour: a
player marked out is counted but never seated, a night with no game leaves a
player off it, and a goalie's start odds come from his whole team - owning
only the backup must not make him the starter. The route half runs against
the real `final_projections` and `nhl_schedule` and asserts invariants, since
tests must not rewrite either table.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/16/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "standalone-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import week_planner as wp                                   # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def skater(pid, name, pos, team, goals, games=80):
    return {"playerId": pid, "fullName": name, "positionCode": pos,
            "eligiblePositions": pos, "teamAbbrevs": team,
            "projectedGames": games, "proj_goals": goals, "proj_shots": goals * 8}


def goalie(pid, name, team, starts, wins):
    return {"playerId": pid, "fullName": name, "positionCode": "G",
            "eligiblePositions": "G", "teamAbbrevs": team, "projectedGames": starts,
            "proj_gamesStarted": starts, "proj_wins": wins, "proj_saves": starts * 25}


# Enough skaters and goalies for σ to have a spread.
POOL = [skater(i, f"Filler {i}", "C", "SEA", 5 + i, 60) for i in range(10, 40)]
POOL += [
    skater(1, "Star", "C", "TOR", 50),
    skater(2, "Depth", "C", "TOR", 10),
    skater(3, "Winger", "LW", "MTL", 30),
    skater(4, "Idle", "D", "EDM", 20),
    goalie(5, "Starter", "TOR", 60, 35),
    goalie(6, "Backup", "TOR", 24, 11),
    goalie(7, "Other", "MTL", 55, 30),
]
POOL += [goalie(100 + i, f"Goalie {i}", "SEA", 40 + i, 20 + i) for i in range(6)]

# A full season for the goalie balancing; the week is its first three nights.
SEASON = []
for day in range(1, 29):
    iso = f"2027-02-{day:02d}"
    SEASON.append((iso, "TOR", "MTL") if day % 2 else (iso, "SEA", "TOR"))
WEEK = ["2027-02-01", "2027-02-02", "2027-02-03"]
SLOTS = {"C": 1, "LW": 1, "D": 1, "G": 1, "BN": 4}


# --------------------------------------------------------------------------
print("\n=== 1. categories from draft prep's column names ===")

cats, unmapped = wp.categories_from_columns(
    ["proj_goals", "proj_shots", "SV", "proj_goals", "proj_gamesPlayedish"])
check("columns map to Yahoo codes, in order", cats == ["G", "SOG", "SV"], cats)
check("a Yahoo code passes straight through", "SV" in cats)
check("an unknown column is reported, not dropped silently",
      unmapped == ["proj_gamesPlayedish"], unmapped)
check("rate columns map too",
      wp.categories_from_columns(["proj_savePct"])[0] == ["SVpct"])


# --------------------------------------------------------------------------
print("\n=== 2. seating a week ===")

plan = wp.plan_week(POOL, [1, 2, 3, 4, 5], ["G", "SOG", "W", "SV"], SLOTS, WEEK, SEASON)
by_id = {p["playerId"]: p for p in plan["players"]}
first = plan["days"][0]

check("a day per date, in order", [d["date"] for d in plan["days"]] == WEEK)
check("every seat is listed, filled or not",
      [s["slot"] for s in first["slots"]] == ["C", "LW", "D", "G"], first["slots"])
check("the better centre takes the only C slot",
      first["slots"][0]["player"]["fullName"] == "Star", first["slots"][0])
check("the worse one is benched, and the bench says so",
      [p["fullName"] for p in first["bench"]] == ["Depth"] and by_id[2]["benchedGames"] == 3,
      first["bench"])
check("a player whose team is idle is never on the night",
      by_id[4]["games"] == 0 and first["slots"][2]["player"] is None, by_id[4])
check("the opponent and venue ride along",
      first["slots"][0]["player"]["opponent"] == "MTL" and first["slots"][0]["player"]["home"] is True,
      first["slots"][0]["player"])
check("no team_stats means unadjusted, and says so", plan["adjusted"] is False)
check("totals cover counting categories only",
      set(plan["totals"]) == {"G", "SOG", "W", "SV"}, plan["totals"])

unknown = wp.plan_week(POOL, [1, 999999], ["G"], SLOTS, WEEK, SEASON)
check("a pruned player is reported, not a crash",
      unknown["unknownPlayers"] == [999999], unknown["unknownPlayers"])

duplicated = wp.plan_week(POOL, [1, 1, "1"], ["G"], SLOTS, WEEK, SEASON)
check("the same player twice is one player", len(duplicated["players"]) == 1)


# --------------------------------------------------------------------------
print("\n=== 3. out ===")

out = wp.plan_week(POOL, [1, 2], ["G"], SLOTS, WEEK, SEASON, out=[1])
out_by_id = {p["playerId"]: p for p in out["players"]}
check("an out player still has his games counted",
      out_by_id[1]["games"] == 3 and out_by_id[1]["out"] is True, out_by_id[1])
check("but is never seated", out_by_id[1]["starts"] == 0)
check("and his slot goes to the next man",
      out["days"][0]["slots"][0]["player"]["fullName"] == "Depth")


# --------------------------------------------------------------------------
print("\n=== 4. goalies ===")

backup_only = wp.plan_week(POOL, [6], ["W", "SV"], SLOTS, WEEK, SEASON)
seat = backup_only["days"][0]["slots"][-1]["player"]
check("owning only the backup does not make him the starter",
      seat and 0 < seat["startProbability"] < 0.5, seat)

tandem = wp.plan_week(POOL, [5, 6], ["W", "SV"], {"G": 2}, WEEK, SEASON)
for day in tandem["days"]:
    odds = sum(s["player"]["startProbability"] for s in day["slots"] if s["player"])
    check(f"{day['date']}: both of a team's goalies seated sum to one start",
          abs(odds - 1.0) < 1e-6, odds)
expected = sum(p["starts"] for p in tandem["players"])
check("expected starts across the week equal the team's games",
      abs(expected - 3.0) < 0.01, expected)


# --------------------------------------------------------------------------
print("\n=== 5. points leagues ===")

points = wp.plan_week(POOL, [1, 2], ["G", "SOG"], SLOTS, WEEK, SEASON,
                      points={"G": 3.0, "SOG": 0.0})
check("a zero-point stat drops out of the categories", points["categories"] == ["G"])
star = points["days"][0]["slots"][0]["player"]
check("value is fantasy points per game", abs(star["value"] - 3.0 * 50 / 80) < 1e-3, star)


# --------------------------------------------------------------------------
print("\n=== 6. against an opponent ===")

check("no opponent, no matchup", plan["matchup"] is None and plan["opponent"] is None)

versus = wp.plan_week(POOL, [1], ["G", "SOG"], SLOTS, WEEK, SEASON, opponent=[2])
rows = {r["category"]: r for r in versus["matchup"]["categories"]}
check("the stronger side is favoured", rows["G"]["winProbability"] > 0.5, rows["G"])
check("expected wins is the sum of the category odds",
      abs(versus["matchup"]["expectedWins"]
          - sum(r["winProbability"] for r in rows.values())) < 0.01, versus["matchup"])
check("the opponent's lineups hold the opponent's players",
      versus["opponent"]["days"][0]["slots"][0]["player"]["fullName"] == "Depth")
check("contested is 1 at a dead heat and falls away from it",
      0 < rows["G"]["contested"] < 1, rows["G"])

swamped = wp.plan_week(POOL, [1], ["G"], SLOTS, WEEK, SEASON, opponent=[2],
                       banked={"G": {"mine": 0, "theirs": 40}})
check("a banked deficit is counted in the final margin",
      swamped["matchup"]["categories"][0]["winProbability"] < 0.01
      and swamped["matchup"]["categories"][0]["theirs"] > 40, swamped["matchup"])

inverse = wp.plan_week(POOL, [5], ["GA"], SLOTS, WEEK, SEASON, opponent=[7],
                       banked={"GA": {"mine": 0, "theirs": 30}})
check("an inverse category is won by having less of it",
      inverse["matchup"]["categories"][0]["winProbability"] > 0.99, inverse["matchup"])

benched_opp = wp.plan_week(POOL, [1], ["G"], SLOTS, WEEK, SEASON, opponent=[2], opponent_out=[2])
check("an opponent marked out projects nothing",
      benched_opp["opponent"]["totals"]["G"] == 0, benched_opp["opponent"]["totals"])

rate = wp.plan_week(POOL, [5], ["W", "SVpct"], SLOTS, WEEK, SEASON, opponent=[7])
check("a rate category is listed but not given odds",
      {"category": "SVpct", "rate": True} in rate["matchup"]["categories"],
      rate["matchup"]["categories"])

points_vs = wp.plan_week(POOL, [1], ["G"], SLOTS, WEEK, SEASON, opponent=[2],
                         points={"G": 3.0})
check("a points league reports points, and the better side is favoured",
      points_vs["matchup"]["mode"] == "points"
      and points_vs["matchup"]["minePoints"] > points_vs["matchup"]["theirsPoints"]
      and points_vs["matchup"]["winProbability"] > 0.5, points_vs["matchup"])

# The point of the opponent: with goals already lost and hits in reach, the
# one C slot should go to the grinder rather than the sniper, even though the
# sniper is the better player on flat weights.
sniper = {**skater(50, "Sniper", "C", "TOR", 40), "proj_hits": 20}
grinder = {**skater(51, "Grinder", "C", "TOR", 12), "proj_hits": 80}
rival = {**skater(52, "Rival", "C", "MTL", 12), "proj_hits": 75}
hitters = [{**p, "proj_hits": p.get("proj_goals", 0) * 3} for p in POOL if p["positionCode"] != "G"] + [sniper, grinder, rival]
flat = wp.plan_week(hitters, [50, 51], ["G", "HIT"], {"C": 1}, WEEK, SEASON)
chasing = wp.plan_week(hitters, [50, 51], ["G", "HIT"], {"C": 1}, WEEK, SEASON,
                       opponent=[52], banked={"G": {"mine": 0, "theirs": 30}})
check("on flat weights the better player starts",
      flat["days"][0]["slots"][0]["player"]["fullName"] == "Sniper",
      flat["days"][0]["slots"][0])
check("against a lost category, the lineup chases the live one",
      chasing["days"][0]["slots"][0]["player"]["fullName"] == "Grinder",
      (chasing["days"][0]["slots"][0], chasing["weights"]))


# --------------------------------------------------------------------------
print("\n=== 7. routes on real data ===")

try:
    import app as app_module
    from db import fetch_all

    client = app_module.app.test_client()

    page = client.get("/standalone/")
    check("the page renders signed out", page.status_code == 200
          and b"Lineups" in page.data, page.status_code)
    check("the old stub URL still lands there",
          client.get("/standalone").status_code in (301, 308))

    setup = client.get("/standalone/api/setup").get_json()
    check("setup lists players and scorable categories",
          setup["status"] == "success" and setup["players"]
          and any(c["code"] == "SOG" for c in setup["categories"]),
          setup.get("message"))

    week = fetch_all('SELECT min("gameDate") AS lo FROM nhl_schedule')[0]["lo"]
    if not week:
        check("nhl_schedule has games to plan against", False, "empty table")
    else:
        ids = [p["playerId"] for p in setup["players"][:25]]
        body = {"roster": ids, "categories": ["proj_goals", "proj_shots", "proj_wins"],
                "slots": {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2, "BN": 4},
                "start": week, "end": week}
        response = client.post("/standalone/api/week", json=body)
        data = response.get_json()
        check("a real roster plans", response.status_code == 200
              and data["status"] == "success", data.get("message"))
        if response.status_code == 200:
            day = data["days"][0]
            seated = [s["player"] for s in day["slots"] if s["player"]]
            check("nobody is seated twice in a night",
                  len({p["playerId"] for p in seated}) == len(seated))
            check("every seated player has a game that night",
                  all(p.get("opponent") for p in seated), seated[:3])

        bad = [
            ({**body, "roster": []}, "an empty roster"),
            ({**body, "categories": ["nope"]}, "no usable categories"),
            ({**body, "slots": {"BN": 5}}, "no starting slots"),
            ({**body, "end": "2000-01-01"}, "an end before the start"),
            ({**body, "league_mode": "points", "points": {}}, "points with no values"),
        ]
        for payload, label in bad:
            check(f"{label} is a 400, not a 500",
                  client.post("/standalone/api/week", json=payload).status_code == 400)

except Exception as exc:                                    # noqa: BLE001
    check("database-backed checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
