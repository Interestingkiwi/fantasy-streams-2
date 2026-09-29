"""
Tests for reading a league's transactions from Yahoo (yahoo_transactions.py).

No network. The API responses are small stand-ins in the JSON shape the real
public read-only API returns, and the pages are synthetic, built in the markup
the real Transactions page uses. Both were checked against the real thing
before these were written: on a completed 12-team season the page parser and
the API agreed on all 1,132 transactions at minute precision - types, players,
where each came from and went to, trades and their picks, and years inferred
across New Year. A real league's data is not committed.

What a user would be misled by, and so what is pinned: a waiver claim told
from a free-agent pickup, a drop to free agents told from one to waivers, a
trade's two rows read as one trade with each side's players going the right
way, the year turning at New Year while walking back through the list, page
times read in the right zone, and commissioner settings changes kept out.

Author - Jason Druckenmiller
Created - 9/29/2026
Updated - 9/29/2026
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

os.environ.setdefault("FLASK_SECRET_KEY", "yahoo-transactions-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yahoo_transactions as yt                             # noqa: E402
from yahoo_rosters import RosterPageError                   # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def raises(code, fn):
    try:
        fn()
    except RosterPageError as exc:
        return exc.code == code
    return False


EASTERN = ZoneInfo("America/New_York")


def epoch(*args, tz=EASTERN):
    return int(datetime(*args, tzinfo=tz).timestamp())


# ---------------------------------------------------------------- API stand-ins

def api_player(yahoo_id, name, team, positions, kind, source, destination, src_team=None, dst_team=None,
               as_list=False):
    data = {"type": kind, "source_type": source, "destination_type": destination}
    if src_team:
        data.update(source_team_key=f"465.l.1.t.{src_team[0]}", source_team_name=src_team[1])
    if dst_team:
        data.update(destination_team_key=f"465.l.1.t.{dst_team[0]}", destination_team_name=dst_team[1])
    return {"player": {"player_key": f"465.p.{yahoo_id}", "player_id": str(yahoo_id),
                       "name": {"full": name}, "editorial_team_abbr": team,
                       "display_position": positions,
                       "transaction_data": [data] if as_list else data}}


def api_tx(tx_id, kind, when, players=(), status="successful", **extra):
    item = {"transaction_key": f"465.l.1.tr.{tx_id}", "transaction_id": str(tx_id), "type": kind,
            "status": status, "timestamp": str(when), **extra}
    if players:
        item["players"] = list(players)
    return {"transaction": item}


ALPHA, BRAVO = ("3", "Alpha"), ("7", "Bravo")
RAW = [
    api_tx(9, "trade", epoch(2026, 1, 2, 13, 36), [
        api_player(101, "Vincent Trocheck", "UTA", "C", "trade", "team", "team", BRAVO, ALPHA),
        api_player(102, "Zach Hyman", "EDM", "LW,RW", "trade", "team", "team", ALPHA, BRAVO),
    ], trader_team_key="465.l.1.t.7", trader_team_name="Bravo",
       tradee_team_key="465.l.1.t.3", tradee_team_name="Alpha",
       picks=[{"pick": {"source_team_key": "465.l.1.t.7", "destination_team_key": "465.l.1.t.3", "round": "3"}}]),
    api_tx(8, "commish", epoch(2025, 12, 31, 9, 0)),
    api_tx(7, "add/drop", epoch(2025, 12, 30, 23, 45), [
        api_player(103, "Neal Pionk", "WPG", "D", "add", "freeagents", "team", dst_team=ALPHA),
        api_player(104, "Steven Stamkos", "NSH", "C,LW,RW", "drop", "team", "freeagents", src_team=ALPHA),
    ]),
    api_tx(6, "add", epoch(2025, 12, 29, 4, 4), [
        api_player(105, "Kevin Fiala", "LA", "LW,RW", "add", "waivers", "team", dst_team=BRAVO, as_list=True),
    ]),
    api_tx(5, "add", epoch(2025, 12, 28, 10, 0), status="failed"),
    api_tx(4, "drop", epoch(2025, 10, 7, 19, 34), [
        api_player(106, "Luca Cagnoni", "SJ", "D", "drop", "team", "waivers", src_team=BRAVO),
    ]),
]

# --------------------------------------------------------------------------
print("\n=== 1. from the API ===")

parsed = yt.from_api({"name": "Test League", "season": "2025"}, RAW)
tx = parsed["transactions"]
check("every player move comes through, newest first, and nothing else",
      [t["id"] for t in tx] == ["9", "7", "6", "4"], [t["id"] for t in tx])
check("a commissioner settings change is not a transaction here",
      all(t["type"] != "commish" for t in tx))
check("nor is one that did not go through", "5" not in [t["id"] for t in tx])
check("a trade sends each player from one team to the other",
      tx[0]["type"] == "trade" and tx[0]["moves"] == [["101", "7", "3"], ["102", "3", "7"]], tx[0])
check("and carries its draft picks", tx[0].get("picks") == [[3, "7", "3"]], tx[0].get("picks"))
check("an add/drop is two moves: in from free agents, out to free agents",
      tx[1]["type"] == "add/drop" and tx[1]["moves"] == [["103", "freeagents", "3"], ["104", "3", "freeagents"]],
      tx[1]["moves"])
check("a waiver claim is told from a free-agent pickup, even when Yahoo wraps its data in a list",
      tx[2]["moves"] == [["105", "waivers", "7"]], tx[2]["moves"])
check("a drop to waivers is told from one to free agents", tx[3]["moves"] == [["106", "7", "waivers"]])
check("times are Yahoo's exact epoch seconds", tx[1]["time"] == epoch(2025, 12, 30, 23, 45))
check("teams are keyed by Yahoo team number, as the roster scrape keys them",
      parsed["teams"] == {"3": "Alpha", "7": "Bravo"}, parsed["teams"])
check("each player is kept once, with his NHL team and positions",
      parsed["players"]["104"] == ["Steven Stamkos", "NSH", "C,LW,RW"] and len(parsed["players"]) == 6)
check("the league's name and season ride along", parsed["league"] == {"name": "Test League", "season": "2025"})


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """Answers each GET with the next response, and records the URLs."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def league_page(count, start_id=0):
    return FakeResponse(200, {"fantasy_content": {"league": {
        "name": "Paged", "season": "2026",
        "transactions": [api_tx(start_id + n, "drop", 1790000000 - n, [
            api_player(start_id + n, "P", "BOS", "C", "drop", "team", "waivers", src_team=ALPHA)])
            for n in range(count)]}}})


session = FakeSession(league_page(yt.API_PAGE), league_page(3, yt.API_PAGE))
league, raw = yt.fetch_api("5848", session=session)
check("a full page asks for the next; a short one is the last",
      len(raw) == yt.API_PAGE + 3 and len(session.urls) == 2, (len(raw), session.urls))
check("pages are asked for by start and count, for the current season's league",
      "league/nhl.l.5848/transactions;start=0;count=500" in session.urls[0]
      and ";start=500;" in session.urls[1], session.urls)
check("test mode reads the completed test league instead",
      yt.league_key("anything", test=True) == yt.TEST_LEAGUE_KEY)

error = {"error": {"description": "You must be logged in to view this league."}}
check("a private league is 'private', which sends the page to the bookmarklet",
      raises("private", lambda: yt.fetch_api("5848", session=FakeSession(FakeResponse(401, error)))))
check("a league Yahoo does not have is 'not_found'",
      raises("not_found", lambda: yt.fetch_api("5848", session=FakeSession(FakeResponse(400, {})))))
check("Yahoo falling over is 'unreachable'",
      raises("unreachable", lambda: yt.fetch_api("5848", session=FakeSession(FakeResponse(503, {})))))
check("so is no answer at all",
      raises("unreachable", lambda: yt.fetch_api(
          "5848", session=FakeSession(requests.ConnectionError("down")))))
check("a JSON answer that is not a league is 'unrecognised'",
      raises("unrecognised", lambda: yt.fetch_api(
          "5848", session=FakeSession(FakeResponse(200, {"nothing": 1})))))
untouched = FakeSession()
check("a League ID that is not a number is refused before Yahoo is asked",
      raises("invalid_id", lambda: yt.fetch_api("fifty", session=untouched)) and not untouched.urls)


# ---------------------------------------------------------------- page markup

def entry(yahoo_id, name, team_pos, note):
    return (f'<div class="Pbot-xs"><a href="https://sports.yahoo.com/nhl/players/{yahoo_id}" target="sports">{name}</a>'
            f'<span class="F-position Fz-xxs">{team_pos}</span>'
            f'<a class="yfa-icon playernote" href="https://sports.yahoo.com/nhl/players/{yahoo_id}/news"></a>'
            f'<h6 class="F-shade Fz-xxs">{note}</h6></div>')


def team_cell(number, name, stamp, css='class="Tst-team-name" ', league="1"):
    return (f'<td class="Ta-end"><div class="Grid-h-top Nowrap Fz-xxs"><span class="Grid-u">'
            f'<a {css}href="https://hockey.fantasysports.yahoo.com/hockey/{league}/{number}">{name}</a>'
            f'<span class="Block F-timestamp Fz-xxs Nowrap">{stamp}</span></span>'
            f'<a class="Grid-u" href="/hockey/{league}/{number}"><img alt="logo"></a></div></td>')


def move_row(icons, entries, number, name, stamp):
    spans = "".join(f'<span class="F-icon Block Fz-lg Cur-h" title="{i}"></span>' for i in icons)
    return (f'<tr><td class="Grid-u-1-12 Ta-c">{spans}</td>'
            f'<td class="Fill-x No-pstart" colspan="2">{"".join(entries)}</td>'
            f'{team_cell(number, name, stamp)}</tr>')


def trade_rows(first, second, stamp):
    """first/second: (team number, name, [(yahoo id, name, team_pos)], [rounds])."""
    def side(parts, lead):
        number, name, players, rounds = parts
        paras = "".join(f'<p> <a href="https://sports.yahoo.com/nhl/players/{p}" target="sports">{n}</a>'
                        f'<span class="F-position Fz-xxs">{tp}</span></p>' for p, n, tp in players)
        paras += "".join(f"<p>Round {r}</p>" for r in rounds)
        return (f"<tr>{lead}<td class=\"No-pstart\">{paras}</td><td class=\"Fz-xxs\">Traded to</td>"
                f"{team_cell(number, name, stamp, css='')}</tr>")
    icon = '<td class="Grid-u-1-12 Pstart-xl Px-lg Ta-c" rowspan="2"><span class="F-icon Fz-xl F-trade"></span></td>'
    return side(first, icon) + side(second, "")


def table(*rows):
    return f'<table class="Table Table-mid No-bdr Tst-transaction-table"><tbody>{"".join(rows)}</tbody></table>'


# The same league as RAW above, as the page shows it - split over two pages,
# as the bookmarklet sends them
PAGES = (table(
    trade_rows(("3", "Alpha", [("101", "Vincent Trocheck", "UTA - C")], [3]),
               ("7", "Bravo", [("102", "Zach Hyman", "EDM - LW,RW")], []), "Jan 2, 1:36 pm"),
    move_row(["Added Player", "Dropped Player"],
             [entry(103, "Neal Pionk", "WPG - D", "Free Agent"),
              entry(104, "Steven Stamkos", "NSH - C,LW,RW", "Dropped ( by Commissioner ) To Free Agent")],
             "3", "Alpha", "Dec 30, 11:45 pm"),
) + "\n" + table(
    move_row(["Added Player"], [entry(105, "Kevin Fiala", "LA - LW,RW", "Waiver")], "7", "Bravo", "Dec 29, 4:04 am"),
    move_row(["Dropped Player"], [entry(106, "Luca Cagnoni", "SJ - D", "To Waivers")], "7", "Bravo", "Oct 7, 7:34 pm"),
))

# --------------------------------------------------------------------------
print("\n=== 2. from the page ===")

page = yt.parse_pages(PAGES, "America/New_York", url="https://hockey.fantasysports.yahoo.com/2025/hockey/1/transactions")
ptx = page["transactions"]


def same(a, b):
    """Two transactions alike but for the id the page does not show."""
    return (a["type"], a["time"], sorted(map(tuple, a["moves"])), sorted(map(tuple, a.get("picks", [])))) \
        == (b["type"], b["time"], sorted(map(tuple, b["moves"])), sorted(map(tuple, b.get("picks", []))))


check("the page and the API describe the same league identically",
      len(ptx) == len(tx) and all(same(a, b) for a, b in zip(tx, ptx)),
      [(a, b) for a, b in zip(tx, ptx) if not same(a, b)][:2])
check("a trade's two rows are one trade, each side's players going the right way",
      ptx[0]["type"] == "trade" and sorted(ptx[0]["moves"]) == [["101", "7", "3"], ["102", "3", "7"]],
      ptx[0])
check("a pick goes to the side whose row lists it", ptx[0].get("picks") == [[3, "7", "3"]])
check("a commissioner's drop to free agents is still a drop to free agents",
      ["104", "3", "freeagents"] in ptx[1]["moves"])
check("a waiver claim is told from a free-agent pickup on the page too",
      ptx[2]["moves"] == [["105", "waivers", "7"]])
check("teams and players come off the page as the API gives them",
      page["teams"] == parsed["teams"] and page["players"] == parsed["players"],
      (page["teams"], page["players"]))
check("the page has no transaction ids to give", all(t["id"] is None for t in ptx))
check("walking back through the list, the year turns at New Year",
      yt.utc(ptx[0]["time"]).year == 2026 and yt.utc(ptx[1]["time"]).year == 2025)
check("a past season's page is dated in that season",
      page["league"]["season"] == "2025" and yt.utc(ptx[-1]["time"]).date() == date(2025, 10, 7))

pacific = yt.parse_pages(PAGES, "America/Los_Angeles", url=yt.TEST_PAGE_URL)["transactions"]
check("a page read in another zone is read in that zone - three hours later in epoch terms",
      pacific[1]["time"] - ptx[1]["time"] == 3 * 3600)
check("a zone that does not exist falls back to Eastern, what a signed-out page shows",
      yt.parse_pages(PAGES, "Not/AZone", url=yt.TEST_PAGE_URL)["transactions"][1]["time"] == ptx[1]["time"])

# The current season: the walk starts from today
current = yt.parse_pages(table(move_row(["Added Player"], [entry(1, "A", "BOS - C", "Free Agent")],
                                        "1", "Only", "Sep 29, 8:31 am")),
                         None, url="https://hockey.fantasysports.yahoo.com/hockey/5848/transactions",
                         today=date(2026, 9, 29))
check("a current season's page is dated from today",
      current["transactions"][0]["time"] == epoch(2026, 9, 29, 8, 31) and current["league"]["season"] == "2026")
jan = yt.parse_pages(table(move_row(["Added Player"], [entry(1, "A", "BOS - C", "Free Agent")],
                                    "1", "Only", "Dec 30, 8:31 am")),
                     None, url="https://hockey.fantasysports.yahoo.com/hockey/5848/transactions",
                     today=date(2027, 1, 3))
check("seen in January, December's moves are last year's",
      yt.utc(jan["transactions"][0]["time"]).year == 2026 and jan["league"]["season"] == "2026")
check("a league with no moves yet is an empty list, not an error",
      yt.parse_pages(table(), None, url=yt.TEST_PAGE_URL)["transactions"] == [])
check("a page with no transactions table is not a Transactions page",
      raises("unrecognised", lambda: yt.parse_pages("<html><body><table></table></body></html>")))


# --------------------------------------------------------------------------
print("\n=== 3. the routes ===")

try:
    import app as app_module
    from routes import account_routes

    flask_app = app_module.app
    client = flask_app.test_client()
    real_fetch = yt.fetch_api
    asked = []

    def fake_fetch(league_id, test=False, session=None):
        asked.append((league_id, test))
        if str(league_id) == "4":
            raise RosterPageError("private", "This league is private.")
        return {"name": "Stub", "season": "2026"}, RAW

    yt.fetch_api = fake_fetch
    try:
        flask_app.config["ROSTER_SCRAPE_TEST"] = False
        ok = client.post("/standalone/api/transactions/scrape", json={"league_id": "5848"})
        body = ok.get_json()
        check("a public league scrapes through the API, in the stored shape",
              ok.status_code == 200 and body["source"] == "api" and len(body["transactions"]) == 4
              and body["teams"] == {"3": "Alpha", "7": "Bravo"}, body)
        private = client.post("/standalone/api/transactions/scrape", json={"league_id": "4"})
        check("a private one is a 403 pointing at its Transactions page, for the bookmarklet",
              private.status_code == 403 and private.get_json()["code"] == "private"
              and private.get_json()["yahooUrl"] == "https://hockey.fantasysports.yahoo.com/hockey/4/transactions",
              private.get_json())
        check("a League ID that is not a number is a 400",
              client.post("/standalone/api/transactions/scrape", json={"league_id": "x"}).status_code == 400)
        flask_app.config["ROSTER_SCRAPE_TEST"] = True
        asked.clear()
        client.post("/standalone/api/transactions/scrape", json={"league_id": "whatever"})
        check("test mode passes the flag, whatever the ID", asked == [("whatever", True)], asked)
    finally:
        yt.fetch_api = real_fetch
        flask_app.config["ROSTER_SCRAPE_TEST"] = True

    parsed_route = client.post("/standalone/api/transactions/parse", json={
        "html": PAGES, "url": "https://hockey.fantasysports.yahoo.com/2025/hockey/1/transactions",
        "timeZone": "America/New_York"})
    check("the bookmarklet's pages parse to the same shape",
          parsed_route.status_code == 200 and parsed_route.get_json()["source"] == "browser"
          and all(same(a, b) for a, b in zip(tx, parsed_route.get_json()["transactions"])),
          parsed_route.get_json())
    check("pages claiming to be from anywhere but Yahoo are refused",
          client.post("/standalone/api/transactions/parse",
                      json={"html": PAGES, "url": "https://example.com/transactions"}).status_code == 422)
    check("so is a Yahoo page with no transactions on it",
          client.post("/standalone/api/transactions/parse",
                      json={"html": "<html></html>", "url": yt.TEST_PAGE_URL}).status_code == 422)

    home = client.get("/standalone/").data.decode("utf-8")
    check("League Home has a Season History tab",
          'data-tab="history"' in home and 'id="tab-history"' in home)
    check("and the bookmarklet reads every Transactions page, 25 at a time",
          "Tst-transaction-table" in home and "transactionsfilter=all&count=" in home)
    check("scraped transactions are kept with the league, so an account carries them",
          "fs_leagueTransactions" in account_routes.LEAGUE_KEYS)
    check("a busy season's transactions fit in what an account will store",
          len(json.dumps(parsed)) * 1136 / 4 < account_routes.MAX_STATE_BYTES)
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
