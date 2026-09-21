"""
Tests for reading a matchup's score so far off Yahoo's Matchup page.

No network: the page is synthetic, built in the markup the real one uses
(checked against a completed public 2025-26 league, served and after Yahoo's
scripts have run). A real league's page is not committed.

Pinned hardest are the readings a user would be misled by: a dash is nothing
recorded yet rather than a zero, a starred column is shown but not scored,
Yahoo's SV% is the engine's SVpct, and signed out the page is team 1's unless
the request names the team - so the URL has to carry mid1.

Author - Jason Druckenmiller
Created - 9/21/2026
Updated - 9/21/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "yahoo-matchup-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yahoo_matchup as ym                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


HEADINGS = [("G", "Goals"), ("A", "Assists"), ("HIT", "Hits"), ("W", "Wins"),
            ("GA*", "Goals Against"), ("GAA", "Goals Against Average"),
            ("SV*", "Saves"), ("SV%", "Save Percentage"), ("SHO", "Shutouts")]


def team_row(number, name, values, won, league="11111"):
    cells = "".join(f'<td class="Ta-c"><div>{v}</div></td>' for v in values)
    return (f'<tr><td class="Alt Ta-start"><div><div class="Pstart-lg Grid-h-mid">'
            f'<a class="Grid-u" href="/hockey/{league}/{number}"><img alt="logo" src="x.png"/></a>'
            f'<span class="Grid-u Nowrap"><a href="https://hockey.fantasysports.yahoo.com/hockey/{league}/{number}">'
            f'{name}</a></span></div></div></td>{cells}'
            f'<td class="Ta-c Bg-shade"><div><div class="Fw-b">{won}</div></div></td></tr>')


def page(rows, week=3, mid1=6, title="Matchup | Fantasy Hockey | Yahoo! Sports"):
    heads = "".join(f'<th class="Ta-c" title="{t}"><div>{c}</div></th>' for c, t in HEADINGS)
    weeks = "".join(
        f'<option value="?week={n}&amp;module=matchup&amp;mid1={mid1}"{" selected" if n == week else ""}>Week {n}</option>'
        for n in range(1, 6))
    return (f"<html><head><title>{title}</title></head><body>"
            f'<select>{weeks}</select>'
            f'<table class="Table-plain Table Datatable Ta-center"><thead><tr>'
            f'<th class="Alt Pstart-xl Ta-start"><div>Team</div></th>{heads}<th class="Ta-c"><div></div></th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></body></html>')


MATCHUP = page([
    team_row(6, "Mark It Zero", ["11", "17", "1,084", "1", "12", "3.33", "87", ".879", "0"], 6),
    team_row(7, "Emporiums", ["8", "18", "29", "-", "13", "4.42", "85", ".867", "-"], 2),
])


# --------------------------------------------------------------------------
print("\n=== 1. addresses ===")

check("the week and team go on the URL, with module=matchup",
      ym.matchup_url("5848", week=3, team=6)
      == "https://hockey.fantasysports.yahoo.com/hockey/5848/matchup?week=3&mid1=6&module=matchup",
      ym.matchup_url("5848", week=3, team=6))
check("with neither, the bare page (Yahoo's current week)",
      ym.matchup_url("5848") == "https://hockey.fantasysports.yahoo.com/hockey/5848/matchup")
check("a pasted league URL is read for its ID",
      ym.matchup_url("https://hockey.fantasysports.yahoo.com/hockey/5848/", team=2).startswith(
          "https://hockey.fantasysports.yahoo.com/hockey/5848/matchup?"))
check("test mode reads the completed test league whatever is asked",
      ym.matchup_url("5848", week=9, team=1, test=True) == ym.TEST_MATCHUP_URL)
try:
    ym.matchup_url("abc")
    check("a League ID that is not a number is refused", False)
except ym.RosterPageError as exc:
    check("a League ID that is not a number is refused", exc.code == "invalid_id")


# --------------------------------------------------------------------------
print("\n=== 2. parsing ===")

parsed = ym.parse(MATCHUP)
mine, theirs = parsed["teams"]
check("two teams, named, with their Yahoo team numbers",
      [(t["name"], t["yahooTeamId"]) for t in parsed["teams"]] == [("Mark It Zero", "6"), ("Emporiums", "7")],
      parsed["teams"])
check("the week comes from the week picker", parsed["week"] == 3, parsed["week"])
check("counting stats are numbers, thousands separators and all",
      mine["stats"]["G"] == 11 and mine["stats"]["HIT"] == 1084, mine["stats"])
check("a dash is nothing recorded yet, not zero",
      theirs["stats"]["W"] is None and theirs["stats"]["SHO"] is None, theirs["stats"])
check("SV% is the engine's SVpct, and a leading-dot rate reads",
      mine["stats"]["SVpct"] == 0.879 and "SV%" not in mine["stats"], mine["stats"])
check("categories won are kept apart from the stats",
      mine["won"] == 6 and theirs["won"] == 2 and "" not in mine["stats"])
scored = {c["code"]: c["scored"] for c in parsed["categories"]}
check("a starred column is shown but not scored, and loses its star",
      scored["GA"] is False and scored["SV"] is False and scored["GAA"] is True and "GA*" not in scored,
      scored)

for label, html, code in [
    ("a sign-in page says private", "<html><head><title>Login - Sign in to Yahoo</title></head></html>", "private"),
    ("Yahoo's error page says not found", "<html><head><title>There was a problem</title></head></html>", "not_found"),
    ("some other Yahoo page says unrecognised", "<html><head><title>Standings</title></head><body></body></html>",
     "unrecognised"),
    ("a matchup without two teams says unrecognised",
     page([team_row(1, "Alone", ["1"] * 9, 0)]), "unrecognised"),
]:
    try:
        ym.parse(html)
        check(label, False, "no error raised")
    except ym.RosterPageError as exc:
        check(label, exc.code == code, exc.code)


# --------------------------------------------------------------------------
print("\n=== 3. routes ===")

try:
    import app as app_module
    import routes.standalone_routes as routes

    flask_app = app_module.app
    client = flask_app.test_client()
    fetched = []
    real_fetch = ym.fetch

    def fake_fetch(url, session=None):
        fetched.append(url)
        if "/22222/" in url:
            raise ym.RosterPageError("private", "private")
        return MATCHUP

    ym.fetch = fake_fetch
    try:
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
        response = client.post("/standalone/api/matchup/scrape", json={"league_id": "22222", "week": 4})
        check("test mode reads the test matchup whatever is asked",
              response.status_code == 200 and fetched[-1] == ym.TEST_MATCHUP_URL
              and response.get_json()["test"] is True, (response.status_code, fetched[-1:]))

        flask_app.config["ROSTER_SCRAPE_TEST"] = False
        response = client.post("/standalone/api/matchup/scrape",
                               json={"league_id": "11111", "week": 3, "team": "6"})
        data = response.get_json()
        check("otherwise the league, week and team are fetched",
              response.status_code == 200 and fetched[-1].endswith("/hockey/11111/matchup?week=3&mid1=6&module=matchup"),
              fetched[-1:])
        check("each team's stats come back keyed by projection column too",
              data["teams"][0]["columns"].get("proj_goals") == 11
              and data["teams"][0]["columns"].get("proj_savePct") == 0.879
              and data["teams"][1]["columns"].get("proj_wins", "absent") is None,
              data["teams"][0]["columns"])
        check("and the rate columns are named, so the page does not bank them",
              "proj_savePct" in data["rateColumns"] and "proj_goalsAgainstAverage" in data["rateColumns"])

        response = client.post("/standalone/api/matchup/scrape", json={"league_id": "22222"})
        data = response.get_json()
        check("a private league is a 403 with the Yahoo page to open",
              response.status_code == 403 and data["code"] == "private"
              and data["yahooUrl"].endswith("/hockey/22222/matchup"), data)
        before = len(fetched)
        for body, label in [({"league_id": "11111", "week": "x"}, "week"),
                            ({"league_id": "11111", "team": "1;2"}, "team")]:
            response = client.post("/standalone/api/matchup/scrape", json=body)
            check(f"a {label} that is not a number is a 400", response.status_code == 400, response.status_code)
        check("and nothing is fetched for either", len(fetched) == before)

        yahoo_url = "https://hockey.fantasysports.yahoo.com/hockey/22222/matchup"
        response = client.post("/standalone/api/matchup/parse", json={"html": MATCHUP, "url": yahoo_url})
        check("a page the bookmarklet sends is parsed the same way",
              response.status_code == 200 and response.get_json()["source"] == "browser"
              and response.get_json()["teams"][0]["columns"]["proj_goals"] == 11)
        response = client.post("/standalone/api/matchup/parse",
                               json={"html": MATCHUP, "url": "https://yahoo.com.evil.example/matchup"})
        check("a page claiming to come from anywhere but Yahoo is refused",
              response.status_code == 422, response.status_code)
        response = client.post("/standalone/api/matchup/parse",
                               data=b"x" * (routes.MAX_ROSTER_HTML_BYTES + 1),
                               content_type="application/json")
        check("an oversized post is refused before it is parsed", response.status_code == 422,
              response.status_code)
    finally:
        ym.fetch = real_fetch
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
except Exception as exc:                                    # noqa: BLE001
    check("route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
