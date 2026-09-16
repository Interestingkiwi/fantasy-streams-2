"""
Tests for reading every team's roster off Yahoo's Starting Rosters page.

No network: the pages are synthetic, built in the markup the real page uses
(checked against a real public league, and against the same page after
Yahoo's scripts have run, since that is what the bookmarklet sends). A real
league's page is not committed - it is other people's rosters.

What gets pinned hardest is what a user would otherwise be misled by: a
private league, a wrong League ID and an unrelated page each say what they are
rather than importing nothing; IR, IR+ and NA players stay on their team but
out; and two projected players with one name are told apart by Yahoo's own
record of the player, not guessed.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/16/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "yahoo-rosters-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yahoo_rosters as yr                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def row(slot, name, yahoo_id):
    return (f'<tr><td class="pos first">{slot}</td><td class="player last">'
            f'<div class="ysf-player-name Nowrap"><a class="Nowrap name F-link" '
            f'href="https://sports.yahoo.com/nhl/players/{yahoo_id}" title="{name}">{name}</a>'
            f'</div></td></tr>')


def team(number, title, rows, league="11111"):
    return (f'<div><p class="W-100"><a href="/hockey/{league}/{number}">{title}</a></p>'
            f'<table id="Tst-team-{number}" class="Table"><thead><tr><th>Pos</th><th>Player</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def page(*teams, title="Starting Rosters | Fantasy Hockey | Yahoo! Sports"):
    return f"<html><head><title>{title}</title></head><body>{''.join(teams)}</body></html>"


LEAGUE = page(
    team(1, "Home Team", [
        row("C", "Connor McDavid", 6743),
        row("BN", "Elias Pettersson", 7909),        # the centre
        row("IR+", "Cale Makar", 7515),
        row("", "", 0).replace('<a class', '<span class'),   # an empty slot
    ]),
    team(2, "Away Team", [
        row("D", "Elias Pettersson", 8645),         # the defenceman
        row("IR", "Leon Draisaitl", 5823),
        row("NA", "Some Prospect", 9999),
        row("G", "Tim Stützle", 8330),              # accent in the page, none in the pool
    ]),
)

POOL = [
    {"playerId": 1, "fullName": "Connor McDavid", "teamAbbrevs": "EDM", "positionCode": "C"},
    {"playerId": 2, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "C"},
    {"playerId": 3, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "D"},
    {"playerId": 4, "fullName": "Cale Makar", "teamAbbrevs": "COL", "positionCode": "D"},
    {"playerId": 5, "fullName": "Leon Draisaitl", "teamAbbrevs": "EDM", "positionCode": "C"},
    {"playerId": 6, "fullName": "Tim Stutzle", "teamAbbrevs": "OTT", "positionCode": "C"},
]


# --------------------------------------------------------------------------
print("\n=== 1. League IDs ===")

check("a bare number is the ID", yr.league_id_from(" 11111 ") == "11111")
check("a pasted current-season URL gives its ID",
      yr.league_id_from("https://hockey.fantasysports.yahoo.com/hockey/22222/startingrosters") == "22222")
check("a pasted past-season URL gives its ID",
      yr.league_id_from("https://hockey.fantasysports.yahoo.com/2025/hockey/22705/startingrosters") == "22705")
for bad in ["", "abc", "58-48", "https://example.com/nothing"]:
    try:
        yr.league_id_from(bad)
        check(f"{bad!r} is refused", False)
    except yr.RosterPageError as exc:
        check(f"{bad!r} is refused as invalid_id", exc.code == "invalid_id", exc.code)

check("test mode ignores the ID entirely",
      yr.rosters_url("not even a number", test=True) == yr.TEST_ROSTERS_URL)
check("otherwise the ID fills the current-season address",
      yr.rosters_url("11111") == "https://hockey.fantasysports.yahoo.com/hockey/11111/startingrosters")


# --------------------------------------------------------------------------
print("\n=== 2. parsing ===")

parsed = yr.parse(LEAGUE)
teams = parsed["teams"]
check("every team, in page order, with its name and number",
      [(t["name"], t["yahooTeamId"]) for t in teams] == [("Home Team", "1"), ("Away Team", "2")],
      [(t["name"], t["yahooTeamId"]) for t in teams])
check("an empty slot is skipped, not read as a player",
      len(teams[0]["players"]) == 3, teams[0]["players"])
check("Yahoo's player id comes off the player link",
      teams[0]["players"][0]["yahooId"] == "6743", teams[0]["players"][0])
out = {p["name"]: p["out"] for t in teams for p in t["players"]}
check("IR+ stays on the team, marked out", out["Cale Makar"] is True)
check("IR stays on the team, marked out", out["Leon Draisaitl"] is True)
check("NA stays on the team, marked out", out["Some Prospect"] is True)
check("the bench is not out", out["Elias Pettersson"] is False and out["Connor McDavid"] is False)
check("the slot is kept", teams[1]["players"][1]["slot"] == "IR")

for label, html, code in [
    ("a sign-in page is private", page(title="Login - Sign in to Yahoo"), "private"),
    ("a league that does not exist is not_found",
     page(title="There was a problem | Fantasy Hockey | Yahoo! Sports"), "not_found"),
    ("a bad address is not_found",
     page(title="The document you requested was not found | Fantasy Hockey | Yahoo! Sports"), "not_found"),
    ("some other Yahoo page is unrecognised", page(title="Matchups | Fantasy Hockey"), "unrecognised"),
    ("nothing at all is unrecognised", "", "unrecognised"),
]:
    try:
        yr.parse(html)
        check(label, False, "parsed without complaint")
    except yr.RosterPageError as exc:
        check(label, exc.code == code, exc.code)


# --------------------------------------------------------------------------
print("\n=== 3. matching ===")

asked = []


def lookup(ids):
    asked.extend(ids)
    return {"7909": {"team": "VAN", "positions": {"C"}},
            "8645": {"team": "VAN", "positions": {"D"}}}


matched = yr.match(yr.parse(LEAGUE), POOL, aliases={}, lookup=lookup)
ids = {(t["name"], p["name"]): p["playerId"] for t in matched["teams"] for p in t["players"]}
check("a unique name matches straight away",
      ids[("Home Team", "Connor McDavid")] == 1)
check("the centre Elias Pettersson is told apart by Yahoo's position",
      ids[("Home Team", "Elias Pettersson")] == 2, ids)
check("...and the defenceman",
      ids[("Away Team", "Elias Pettersson")] == 3, ids)
check("accents on the page do not stop a match",
      ids[("Away Team", "Tim Stützle")] == 6, ids)
check("only players a name cannot settle are looked up",
      sorted(asked) == ["7909", "8645", "9999"], asked)
check("a player with no projection is reported, not dropped silently",
      matched["unmatched"] == ["Some Prospect"], matched["unmatched"])

aliased = yr.match(yr.parse(page(team(1, "T", [row("C", "Mitch Marner", 1)]))),
                   [{"playerId": 16, "fullName": "Mitchell Marner", "teamAbbrevs": "VGK", "positionCode": "R"}],
                   aliases={"Mitch Marner": 16})
check("an alias matches a name the pool spells differently",
      aliased["teams"][0]["players"][0]["playerId"] == 16, aliased)

no_lookup = yr.match(yr.parse(LEAGUE), POOL)
check("without a lookup, a shared name stays unmatched rather than guessed",
      "Elias Pettersson" in no_lookup["unmatched"], no_lookup["unmatched"])


# --------------------------------------------------------------------------
print("\n=== 4. fetching, against a stub session ===")


class Response:
    def __init__(self, url, status=200, text="", payload=None):
        self.url, self.status_code, self.text, self._payload = url, status, text, payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise yr.requests.HTTPError(self.status_code)


class Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


url = yr.rosters_url("22222")
private = Session(Response("https://login.yahoo.com/?.src=spt&.done=" + url, text="<html>login</html>"))
try:
    yr.fetch(url, private)
    check("a redirect to login.yahoo.com is private", False)
except yr.RosterPageError as exc:
    check("a redirect to login.yahoo.com is private", exc.code == "private", exc.code)
check("no cookies are sent - only headers, no auth",
      "cookies" not in private.calls[0][1] and "auth" not in private.calls[0][1], private.calls[0][1])

public = Session(Response(url, text=LEAGUE))
check("a public page comes back as HTML", yr.fetch(url, public) == LEAGUE)

for label, session in [("a server error is unreachable", Session(Response(url, status=503))),
                       ("a network failure is unreachable",
                        Session(error=yr.requests.ConnectionError("down")))]:
    try:
        yr.fetch(url, session)
        check(label, False)
    except yr.RosterPageError as exc:
        check(label, exc.code == "unreachable", exc.code)

api = Session(Response("", payload={"fantasy_content": {"players": [
    {"player": {"player_key": "477.p.7909", "editorial_team_abbr": "Van", "display_position": "C,RW"}},
    {"player": {"player_key": "477.p.4000", "editorial_team_abbr": "TB", "display_position": "LW"}},
]}}))
details = yr.yahoo_details(["7909", "4000", "7909"], api)
check("player details are read by Yahoo id", set(details) == {"7909", "4000"}, details)
check("positions come back as a set", details["7909"]["positions"] == {"C", "RW"}, details)
check("Yahoo's tricodes are turned into the NHL's", details["4000"]["team"] == "TBL", details)
check("ids are asked for once each", api.calls[0][0].count("nhl.p.7909") == 1, api.calls[0][0])
check("a failing player API costs the match, not the import",
      yr.yahoo_details(["1"], Session(error=yr.requests.ConnectionError("down"))) == {})


# --------------------------------------------------------------------------
print("\n=== 5. the copies of the pipeline's matching rules ===")

try:
    sys.path.insert(0, str(ROOT / "preseason_db_build"))
    import player_utils                                     # noqa: E402
    import scrape_yahoo_adp                                 # noqa: E402

    for name in ["Tim Stützle", "J.T. Miller", "Ryan O'Reilly", "Jean-Gabriel  Pageau"]:
        check(f"normalise agrees with the pipeline on {name!r}",
              yr.normalise(name) == player_utils.normalise(name),
              (yr.normalise(name), player_utils.normalise(name)))
    check("team fixes match the ADP scrape's", yr.TEAM_FIXES == scrape_yahoo_adp.TEAM_FIXES)
    check("position widening matches the ADP scrape's",
          yr.POSITION_WIDENING == scrape_yahoo_adp.POSITION_WIDENING)
except Exception as exc:                                    # noqa: BLE001
    check("the pipeline modules could be compared", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n=== 6. routes ===")

try:
    import app as app_module
    import routes.standalone_routes as routes

    flask_app = app_module.app
    client = flask_app.test_client()
    fetched = []
    real_fetch, real_details = yr.fetch, yr.yahoo_details

    def fake_fetch(url, session=None):
        fetched.append(url)
        if "/22222/" in url:
            raise yr.RosterPageError("private", "private")
        if "/99999999/" in url:
            raise yr.RosterPageError("not_found", "not found")
        return LEAGUE

    yr.fetch = fake_fetch
    yr.yahoo_details = lambda ids, session=None: {}
    try:
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
        response = client.post("/standalone/api/rosters/scrape", json={"league_id": "22222"})
        data = response.get_json()
        check("test mode reads the test league whatever ID is typed",
              response.status_code == 200 and fetched[-1] == yr.TEST_ROSTERS_URL and data["test"] is True,
              (response.status_code, fetched[-1:]))
        check("the page shows the test-mode notice",
              b"Local test mode" in client.get("/standalone/").data)

        flask_app.config["ROSTER_SCRAPE_TEST"] = False
        response = client.post("/standalone/api/rosters/scrape", json={"league_id": "11111"})
        check("otherwise the typed ID is fetched",
              response.status_code == 200 and fetched[-1].endswith("/hockey/11111/startingrosters"),
              fetched[-1:])
        check("the result carries teams and their players",
              len(response.get_json()["teams"]) == 2)
        check("and no test-mode notice on the page",
              b"Local test mode" not in client.get("/standalone/").data)

        response = client.post("/standalone/api/rosters/scrape", json={"league_id": "22222"})
        data = response.get_json()
        check("a private league is a 403 with the Yahoo page to open",
              response.status_code == 403 and data["code"] == "private"
              and data["yahooUrl"].endswith("/hockey/22222/startingrosters"), data)
        response = client.post("/standalone/api/rosters/scrape", json={"league_id": "99999999"})
        check("a league that does not exist is a 404",
              response.status_code == 404 and response.get_json()["code"] == "not_found")
        response = client.post("/standalone/api/rosters/scrape", json={"league_id": "abc"})
        check("a League ID that is not a number is a 400, and nothing is fetched",
              response.status_code == 400 and response.get_json()["code"] == "invalid_id"
              and not fetched[-1].endswith("abc/startingrosters"))

        yahoo_url = "https://hockey.fantasysports.yahoo.com/hockey/22222/startingrosters"
        response = client.post("/standalone/api/rosters/parse", json={"html": LEAGUE, "url": yahoo_url})
        check("a page the bookmarklet sends is parsed the same way",
              response.status_code == 200 and response.get_json()["source"] == "browser"
              and len(response.get_json()["teams"]) == 2)
        response = client.post("/standalone/api/rosters/parse",
                               json={"html": LEAGUE, "url": "https://yahoo.com.evil.example/x"})
        check("a page claiming to come from anywhere but Yahoo is refused",
              response.status_code == 422, response.status_code)
        response = client.post("/standalone/api/rosters/parse",
                               json={"html": page(title="Login - Sign in to Yahoo"), "url": yahoo_url})
        check("a sign-in page sent by mistake says private",
              response.status_code == 403 and response.get_json()["code"] == "private")
        response = client.post("/standalone/api/rosters/parse",
                               data=b"x" * (routes.MAX_ROSTER_HTML_BYTES + 1),
                               content_type="application/json")
        check("an oversized post is refused before it is parsed", response.status_code == 422,
              response.status_code)
    finally:
        yr.fetch, yr.yahoo_details = real_fetch, real_details
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
except Exception as exc:                                    # noqa: BLE001
    check("database-backed route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
