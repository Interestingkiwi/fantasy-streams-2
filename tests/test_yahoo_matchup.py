"""
Tests for reading a matchup's score so far off Yahoo's Matchup page.

No network: the page is synthetic, built in the markup the real one uses
(checked against a completed public 2025-26 league, served and after Yahoo's
scripts have run). A real league's page is not committed.

Pinned hardest are the readings a user would be misled by: a dash is nothing
recorded yet rather than a zero, a starred column is shown but not scored,
Yahoo's SV% is the engine's SVpct, and signed out the page is team 1's unless
the request names the team - so the URL has to carry mid1. Then the goalie
minimum, which lives on each team's own page: both pages read for the
matchup's week, a page showing another week not used, and a page that cannot
be read costing the appearances but never the score.

Author - Jason Druckenmiller
Created - 9/21/2026
Updated - 10/7/2026
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


def appearances_box(week, appearances, minimum, reached=True):
    """A team page's Goaltender Appearances box, in the markup Yahoo serves."""
    note = "Minimum Reached" if reached else f"{minimum - appearances} more needed"
    return (f'<section class="Bdr P-lg"><section class="Mod Thm-inherit No-mbot" id="position-caps-head">'
            f'<header class="Hd"><h2 class="Fz-lg">Goaltender Appearances</h2></header><div class="Bd"><dl>'
            f'<dt class="Inlineblock Mend-lg">Total for Week {week}:</dt><dd class="Inlineblock">'
            f'<span class="{"F-positive" if reached else "F-negative"}">{appearances} <strong>({note})</strong>'
            f'</span></dd></dl><p class="My-lg">Note: Each week, your goaltenders must reach the minimum of '
            f'<strong>{minimum} appearances</strong>. If you fail to reach this mark, you will lose all of '
            f'your goaltending games for that week.</p></div></section></section>')


def team_page(week, appearances, minimum, reached=True):
    return (f"<html><head><title>Team | Fantasy Hockey</title></head><body><h1>Roster</h1>"
            f"{appearances_box(week, appearances, minimum, reached)}</body></html>")


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

check("a team's own page is the matchup's league and week",
      ym.team_url("https://hockey.fantasysports.yahoo.com/hockey/5848/matchup?week=3&mid1=6&module=matchup", 7)
      == "https://hockey.fantasysports.yahoo.com/hockey/5848/7?week=3")
check("a past season's league keeps its year",
      ym.team_url(ym.TEST_MATCHUP_URL, "7") == "https://hockey.fantasysports.yahoo.com/2025/hockey/22705/7?week=2")
check("with no week on the matchup, the team page's own (Yahoo's current week)",
      ym.team_url("https://hockey.fantasysports.yahoo.com/hockey/5848/matchup", 4)
      == "https://hockey.fantasysports.yahoo.com/hockey/5848/4")
check("and no team number, no page", ym.team_url(ym.TEST_MATCHUP_URL, None) is None
      and ym.team_url(ym.TEST_MATCHUP_URL, "7;x") is None)


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

check("a team page's goalie appearances, the week and the league's minimum",
      ym.parse_appearances(team_page(3, 4, 3)) == {"week": 3, "appearances": 4, "minimum": 3})
check("short of it reads the same way - the count, not the shortfall",
      ym.parse_appearances(team_page(3, 1, 3, reached=False))["appearances"] == 1)
check("the box alone, as the bookmarklet sends it, reads the same",
      ym.parse_appearances(appearances_box(5, 2, 4)) == {"week": 5, "appearances": 2, "minimum": 4})
check("a page with no such box says so rather than zero", ym.parse_appearances(MATCHUP) is None
      and ym.parse_appearances("") is None)


# --------------------------------------------------------------------------
print("\n=== 3. routes ===")

try:
    import app as app_module
    import routes.standalone_routes as routes

    flask_app = app_module.app
    client = flask_app.test_client()
    fetched = []
    real_fetch = ym.fetch

    # Team 6 is short of the minimum, team 7 through it; team pages of any
    # other league have no box, and league 33333's team pages are down
    TEAM_PAGES = {"6": team_page(3, 1, 3, reached=False), "7": team_page(3, 4, 3)}

    def fake_fetch(url, session=None):
        fetched.append(url)
        if "/22222/" in url:
            raise ym.RosterPageError("private", "private")
        if "/33333/" in url and "/matchup" not in url:
            raise ym.RosterPageError("unreachable", "down")
        team = url.split("/hockey/11111/")[-1].split("?")[0] if "/hockey/11111/" in url else None
        if team in TEAM_PAGES:
            return TEAM_PAGES[team]
        return MATCHUP

    ym.fetch = fake_fetch
    try:
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
        before = len(fetched)
        response = client.post("/standalone/api/matchup/scrape", json={"league_id": "22222", "week": 4})
        check("test mode reads the test matchup whatever is asked",
              response.status_code == 200 and fetched[before] == ym.TEST_MATCHUP_URL
              and response.get_json()["test"] is True, (response.status_code, fetched[before:]))

        flask_app.config["ROSTER_SCRAPE_TEST"] = False
        before = len(fetched)
        response = client.post("/standalone/api/matchup/scrape",
                               json={"league_id": "11111", "week": 3, "team": "6"})
        data = response.get_json()
        check("otherwise the league, week and team are fetched",
              response.status_code == 200 and fetched[before].endswith("/hockey/11111/matchup?week=3&mid1=6&module=matchup"),
              fetched[before:])
        check("then both teams' own pages, for the same week",
              sorted(fetched[before + 1:]) == ["https://hockey.fantasysports.yahoo.com/hockey/11111/6?week=3",
                                               "https://hockey.fantasysports.yahoo.com/hockey/11111/7?week=3"],
              fetched[before:])
        check("each team's goalie appearances so far, and the league's minimum, come back",
              [t.get("goalieAppearances") for t in data["teams"]] == [1, 4] and data["goalieMinimum"] == 3,
              (data["teams"], data.get("goalieMinimum")))

        TEAM_PAGES["7"] = team_page(4, 2, 3)
        stale = client.post("/standalone/api/matchup/scrape",
                            json={"league_id": "11111", "week": 3, "team": "6"}).get_json()
        check("a team page showing another week is not used for this one",
              "goalieAppearances" not in stale["teams"][1] and stale["teams"][0]["goalieAppearances"] == 1,
              stale["teams"])
        TEAM_PAGES["7"] = team_page(3, 4, 3)

        down = client.post("/standalone/api/matchup/scrape", json={"league_id": "33333", "week": 3})
        check("team pages that cannot be read leave the score standing, without appearances",
              down.status_code == 200 and down.get_json()["goalieMinimum"] is None
              and down.get_json()["teams"][0]["columns"]["proj_goals"] == 11
              and all("goalieAppearances" not in t for t in down.get_json()["teams"]),
              (down.status_code, down.get_json()))
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
        boxes = client.post("/standalone/api/matchup/parse", json={
            "html": MATCHUP, "url": yahoo_url,
            "goalies": {"6": appearances_box(3, 2, 3, reached=False), "7": appearances_box(3, 3, 3), "8": 5},
        }).get_json()
        check("and the appearances boxes it read off the two team pages",
              [t.get("goalieAppearances") for t in boxes["teams"]] == [2, 3] and boxes["goalieMinimum"] == 3,
              boxes.get("teams"))
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
