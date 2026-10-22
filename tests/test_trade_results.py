"""
Tests for Trade Results (trade_results.py) and its route.

No network and no database: a four-team league over two weeks, in the shapes
`yahoo_league_api` produces, with every number worked out by hand below. The
route runs against a stubbed reader, pool and game data.

What is pinned is what a reader could be misled by: a trade is two sides, its
picks listed both ways and a drop inside it no part of either; the players
received count only on nights they started and played; the players sent
count their line wherever they played - Yahoo's on another fantasy roster,
NHL game data on none - and only on nights a spot they fit was open; a side is
judged until the last player it received is gone, and one that took only
picks, or a player gone before his first night, to the end of the season;
every week since the trade is reviewed, changed or not; in a week the two
teams met, both sides are undone, so one side's gain is the other's loss; a
team's trades are scored together, so a category two trades each won alone
counts once, and a player who arrived in one trade and left in another is not
brought back; and points leagues get no swings.

Author - Jason Druckenmiller
Created - 10/22/2026
Updated - 10/22/2026
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("FLASK_SECRET_KEY", "trade-results-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import trade_results as trd                                 # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


G, A, HIT = "1", "2", "31"
ET = ZoneInfo("America/New_York")


def at(day, hour):
    """Epoch seconds for a US Eastern date and hour."""
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=ET).timestamp())


def cat(stat_id, name):
    return {"id": stat_id, "name": name, "full": name, "higherBetter": True, "displayOnly": False}


INFO = {"key": "1.l.1", "name": "Test", "season": "2026", "scoring": "head",
        "categories": [cat(G, "G"), cat(A, "A"), cat(HIT, "HIT")],
        "slots": {"C": 1, "LW": 1, "D": 1, "Util": 1, "G": 1, "BN": 3}}
SLOTS = {k: v for k, v in INFO["slots"].items() if k != "BN"}


def sk(g=None, a=None, hit=None):
    """A skater's night; all None is no game."""
    if g is None and a is None and hit is None:
        return {G: None, A: None, HIT: None}
    return {G: g or 0, A: a or 0, HIT: hit or 0}


def day(when, teams):
    return {"date": when, "names": {"1": "Mine", "2": "Theirs", "3": "Third", "4": "Fourth"}, "teams": teams}


C, LW, D, CLW = ["C"], ["LW"], ["D"], ["C", "LW"]
# Team 1 trades 3 (C, LW) to team 2 for 20 (C) on the morning of the 6th; team
# 2 drops 3 the next morning. Team 3 trades 30 (D) to team 4 for a pick on the
# 12th.
DAYS = [
    # 20 starts in Util and plays; 3 plays on team 2's bench (Yahoo's line, and
    # 20's Util is his to take). On team 2, 23 starts in Util with no game: a
    # spot 20 could have filled.
    day("2026-10-06", {
        "1": [["1", "C", C, sk(1, 0, 2)], ["2", "LW", LW, sk()], ["5", "D", D, sk(0, 0, 1)],
              ["20", "Util", C, sk(1, 0, 2)]],
        "2": [["3", "BN", CLW, sk(0, 1, 0)], ["22", "C", C, sk(0, 1, 1)], ["23", "Util", C, sk()]],
    }),
    # 3 is on no roster: his game is NHL data. 20 plays from the bench, and
    # every spot 3 fits is held by someone who played.
    day("2026-10-07", {
        "1": [["1", "C", C, sk(0, 1, 1)], ["2", "LW", LW, sk(0, 0, 1)], ["5", "D", D, sk(0, 0, 1)],
              ["7", "Util", D, sk(0, 0, 2)], ["20", "BN", C, sk(0, 0, 1)]],
        "2": [["22", "C", C, sk()], ["23", "Util", C, sk()]],
    }),
    # 20 starts in Util with no game: 3 would have had it
    day("2026-10-08", {
        "1": [["1", "C", C, sk(0, 0, 0)], ["2", "LW", LW, sk(0, 0, 0)], ["5", "D", D, sk()],
              ["20", "Util", C, sk()]],
        "2": [["22", "C", C, sk()], ["23", "Util", C, sk()]],
    }),
    # Week 2. 20 starts and plays, 3 into his Util. Team 3 has sent 30 to team
    # 4 and has no defenceman: an empty D slot he would have filled.
    day("2026-10-12", {
        "1": [["1", "C", C, sk(0, 0, 0)], ["2", "LW", LW, sk(0, 1, 1)], ["5", "D", D, sk(1, 0, 2)],
              ["20", "Util", C, sk(0, 1, 0)]],
        "2": [["22", "C", C, sk()]],
        "3": [["31", "C", C, sk(1, 1, 1)]],
        "4": [["30", "D", D, sk(0, 0, 1)]],
    }),
]

# Week 1 the two trade partners meet; week 2 team 1 plays team 3, who are not
# its partners. Teams 2 and 4 have no matchup in week 2.
WEEKS = [
    {"week": 1, "start": "2026-10-05", "end": "2026-10-11", "status": "postevent", "matchups": [
        {"teams": ["1", "2"], "names": {"1": "Mine", "2": "Theirs"},
         "totals": {"1": {G: 3, A: 2, HIT: 8}, "2": {G: 2, A: 3, HIT: 8}}}]},
    {"week": 2, "start": "2026-10-12", "end": "2026-10-18", "status": "midevent", "matchups": [
        {"teams": ["1", "3"], "names": {"1": "Mine", "3": "Third"},
         "totals": {"1": {G: 1, A: 2, HIT: 5}, "3": {G: 1, A: 1, HIT: 6}}}]},
]

TRANSACTIONS = [
    {"id": "1", "type": "trade", "time": at("2026-10-06", 9),
     # The drop that made room for 3 on team 2 is no part of the trade
     "moves": [["3", "1", "2"], ["20", "2", "1"], ["24", "2", "waivers"]]},
    {"id": "2", "type": "drop", "time": at("2026-10-07", 8), "moves": [["3", "2", "freeagents"]]},
    {"id": "3", "type": "trade", "time": at("2026-10-12", 8), "moves": [["30", "3", "4"]], "picks": [[5, "4", "3"]]},
    {"id": "4", "type": "add/drop", "time": at("2026-10-07", 9), "moves": [["7", "freeagents", "1"], ["8", "1", "waivers"]]},
]
PLAYERS = {"3": ["Sent Centre", "BOS", "C,LW"], "20": ["Got Centre", "TOR", "C"],
           "30": ["Sent D", "MTL", "D"], "24": ["Dropped", "TOR", "D"]}
# 3's NHL games while on no fantasy roster
OUTSIDE = {"3": {"2026-10-07": sk(1, 0, 0), "2026-10-08": sk(0, 0, 3), "2026-10-12": sk(0, 0, 2)}}

# --------------------------------------------------------------------------
print("\n=== 1. which trades, and their sides ===")

found = trd.trades(TRANSACTIONS)
check("only trades, oldest first", [t["date"] for t in found] == ["2026-10-06", "2026-10-12"], found)
first = {s["team"]: s for s in found[0]["sides"]}
check("each side what it received and what it sent",
      first["1"]["received"] == ["20"] and first["1"]["sent"] == ["3"]
      and first["2"]["received"] == ["3"] and first["2"]["sent"] == ["20"], first)
check("a drop inside the trade's transaction is no part of either side",
      "24" not in first["2"]["sent"] + first["2"]["received"])
second = {s["team"]: s for s in found[1]["sides"]}
check("a pick is listed on both sides, by round",
      second["3"]["picksIn"] == [5] and second["4"]["picksOut"] == [5] and second["3"]["received"] == [], second)

# --------------------------------------------------------------------------
print("\n=== 2. what each side got and gave ===")

out = trd.results(INFO, DAYS, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, SLOTS)
check("trades come newest first", [t["date"] for t in out["trades"]] == ["2026-10-12", "2026-10-06"])
sides = {s["team"]: s for s in out["trades"][1]["sides"]}
mine, theirs = sides["1"], sides["2"]
got = mine["received"][0]
check("the player received: three games, two started, his line those two nights only",
      got["games"] == 3 and got["starts"] == 2 and got["totals"] == {G: 1, A: 1, HIT: 2}, got)
check("on the roster from the trade to now", got["first"] == "2026-10-06" and got["last"] == "2026-10-12"
      and got["current"] is True, got)
gave = mine["sent"][0]
check("the player sent: four games elsewhere, three with a spot open - Yahoo's line on team 2's bench, "
      "NHL data on no roster, and not the night every spot he fits was taken",
      gave["games"] == 4 and gave["open"] == 3 and gave["totals"] == {A: 1, HIT: 5}, gave)
check("and where he is now: on no team", "nowOn" not in gave and "unmatched" not in gave, gave)
check("the side's totals, received and sent", mine["totals"] == {"received": {G: 1, A: 1, HIT: 2},
                                                                 "sent": {A: 1, HIT: 5}}, mine["totals"])

check("a side is judged until the last player it received is gone - team 2 dropped 3 the next morning",
      theirs["start"] == "2026-10-06" and theirs["end"] == "2026-10-06" and "current" not in theirs
      and [w["week"] for w in theirs["weeks"]] == [1], theirs)
check("its player received sat on the bench: a game, not a start",
      theirs["received"][0]["games"] == 1 and theirs["received"][0]["starts"] == 0
      and "totals" not in theirs["received"][0], theirs["received"])
check("its player sent, 20, would have taken an idle starter's Util; he is on team 1 now",
      theirs["sent"][0]["open"] == 1 and theirs["sent"][0]["totals"] == {G: 1, HIT: 2}
      and theirs["sent"][0]["nowOn"] == "1", theirs["sent"])

# --------------------------------------------------------------------------
print("\n=== 3. week by week ===")

week1, week2 = mine["weeks"]
check("every week since the trade is reviewed, with each player's line in it",
      week1["received"] == {"20": [1, 2, {G: 1, HIT: 2}]} and week1["sent"] == {"3": [2, 3, {A: 1, HIT: 3}]}
      and week2["received"] == {"20": [1, 1, {A: 1}]} and week2["sent"] == {"3": [1, 1, {HIT: 2}]},
      (week1, week2))
# Week 1, against the partner: team 1 without the trade is G 2, A 3, HIT 9;
# team 2 without it is G 3, A 3, HIT 10 (20's line in its idle Util). With:
# 1-1-1. Without: 0-2-1. Scored against team 2's real totals instead it would
# have been -0.5: the trade looks worse than it was.
check("in a week the two teams met, both sides are undone",
      week1.get("met") is True and week1["opponent"] == "2"
      and week1["gains"] == [[G, "win", "loss"], [HIT, "tie", "loss"]]
      and week1["costs"] == [[A, "loss", "tie"]] and week1["net"] == 1.0
      and week1["with"] == [1, 1, 1] and week1["without"] == [0, 2, 1], week1)
twin = theirs["weeks"][0]
check("so one side's gain is the other's loss",
      twin.get("met") is True and twin["net"] == -1.0 and twin["gains"] == [[A, "win", "tie"]]
      and twin["costs"] == [[G, "loss", "win"], [HIT, "tie", "win"]], twin)
# Week 2, against team 3: 20's assist off and 3's two hits on. Assists won,
# hits lost - against a tie and a win.
check("a week against someone else is scored against his real totals",
      "met" not in week2 and week2["opponent"] == "3" and week2["gains"] == [[A, "win", "tie"]]
      and week2["costs"] == [[HIT, "loss", "win"]] and week2["net"] == -0.5, week2)
check("the season since adds the weeks up, and counts the matchups it turned",
      (mine["gained"], mine["lost"], mine["net"], mine["better"], mine["worse"]) == (3, 2, 0.5, 1, 1)
      and (theirs["gained"], theirs["lost"], theirs["net"], theirs["worse"]) == (1, 2, -1.0, 1), mine)

picks = {s["team"]: s for s in out["trades"][0]["sides"]}
check("a side that took only a pick is judged to the end, on what it sent away",
      picks["3"]["end"] == "2026-10-12" and picks["3"]["sent"][0]["open"] == 1
      and picks["3"]["picksIn"] == [5], picks["3"])
check("a week it changed nothing in is still reviewed, with the matchup either way",
      picks["3"]["weeks"][0]["with"] == picks["3"]["weeks"][0]["without"] == [1, 1, 1]
      and "gains" not in picks["3"]["weeks"][0], picks["3"]["weeks"])
check("a week with no matchup is reviewed but not scored",
      "with" not in picks["4"]["weeks"][0] and picks["4"]["received"][0]["starts"] == 1, picks["4"])

# Team 2 drops 3 six hours after the trade, before his first night with them:
# what they gave up is judged to the end, as if they had received nothing
gone_days = [{**d, "teams": {**d["teams"], "2": [r for r in d["teams"].get("2", []) if r[0] != "3"]}} for d in DAYS]
early = trd.results(INFO, gone_days, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, SLOTS)
gone = next(s for s in early["trades"][1]["sides"] if s["team"] == "2")
check("a player gone before his first night leaves the side judged to the end",
      gone["end"] == "2026-10-12" and gone["sent"][0]["games"] == 3, gone)

# --------------------------------------------------------------------------
print("\n=== 4. a team's trades together ===")

check("the team's season: trades made, starts, open games, categories and matchups",
      out["season"]["1"] == {"trades": 1, "starts": 2, "open": 3, "gained": 3, "lost": 2, "net": 0.5,
                             "better": 1, "worse": 1}, out["season"]["1"])
check("each week's tally the trades in effect that week",
      out["weekly"]["2"]["1"]["trades"] == 1 and out["weekly"]["2"]["3"]["open"] == 1
      and out["weekly"]["1"]["2"]["net"] == -1.0, out["weekly"])
check("together says against whom, and that they met",
      out["together"]["1"]["1"]["opponent"] == "2" and out["together"]["1"]["1"]["met"] is True)


def joint_days(rows_6, rows_7):
    return [day("2026-10-06", {"1": rows_6, "9": [["90", "C", C, sk(0, 0, 0)]]}),
            day("2026-10-07", {"1": rows_7, "9": [["90", "C", C, sk(0, 0, 0)]]})]


def joint_week(mine_totals, theirs_totals):
    return [{"week": 1, "start": "2026-10-05", "end": "2026-10-11", "status": "postevent",
             "matchups": [{"teams": ["1", "9"], "totals": {"1": mine_totals, "9": theirs_totals}}]}]


# Two trades the same morning, each bringing a centre who had two assists.
# Mine won assists 4-3; either trade undone alone loses it, so each says it
# won assists - but the team won one category, not two.
PAIR_DAYS = joint_days([["1", "C", C, sk(0, 0, 0)], ["50", "Util", C, sk(0, 2, 0)], ["51", "BN", C, sk()]],
                       [["1", "C", C, sk(0, 0, 0)], ["51", "Util", C, sk(0, 2, 0)], ["50", "BN", C, sk()]])
PAIR_TX = [{"id": "a", "type": "trade", "time": at("2026-10-06", 8), "moves": [["50", "5", "1"], ["60", "1", "5"]]},
           {"id": "b", "type": "trade", "time": at("2026-10-06", 9), "moves": [["51", "6", "1"], ["61", "1", "6"]]}]
both = trd.results(INFO, PAIR_DAYS, joint_week({G: 1, A: 4, HIT: 5}, {G: 1, A: 3, HIT: 5}), PAIR_TX, {}, {}, SLOTS)
alone = [s for t in both["trades"] for s in t["sides"] if s["team"] == "1"]
check("each trade alone says it won assists",
      len(alone) == 2 and all(s["gained"] == 1 and s["net"] == 1.0 for s in alone), alone)
check("but the team's week counts assists once",
      both["season"]["1"]["gained"] == 1 and both["season"]["1"]["net"] == 1.0
      and both["together"]["1"]["1"]["gains"] == [[A, "win", "loss"]], both["season"]["1"])
only = trd.results(INFO, PAIR_DAYS, joint_week({G: 1, A: 4, HIT: 5}, {G: 1, A: 1, HIT: 5}), PAIR_TX, {}, {}, SLOTS)
check("and a category only the two together decided is counted, though neither alone changed it",
      all(not s.get("gained") for t in only["trades"] for s in t["sides"])
      and only["season"]["1"]["gained"] == 1, only["season"]["1"])

# A chain: on the 6th team 1 gets 70 and 71 for 72; on the 8th it sends 70 on
# for 73, and 70 has two assists for his new team that night. Undone alone,
# the second trade keeps 70 and his assists - a cost. Undone together with the
# first (still in effect, since 71 stayed), 70 was never there.
CHAIN_DAYS = [
    day("2026-10-06", {"1": [["1", "C", C, sk(0, 1, 0)], ["70", "Util", C, sk(0, 0, 0)], ["71", "LW", LW, sk(0, 0, 0)]],
                       "5": [["72", "BN", C, sk()]], "9": [["90", "C", C, sk(0, 0, 0)]]}),
    day("2026-10-08", {"1": [["1", "C", C, sk(0, 0, 0)], ["73", "Util", C, sk(0, 0, 0)], ["71", "LW", LW, sk(0, 0, 0)]],
                       "6": [["70", "C", C, sk(0, 2, 0)]], "9": [["90", "C", C, sk(0, 0, 0)]]}),
]
CHAIN_TX = [{"id": "a", "type": "trade", "time": at("2026-10-06", 8),
             "moves": [["70", "5", "1"], ["71", "5", "1"], ["72", "1", "5"]]},
            {"id": "b", "type": "trade", "time": at("2026-10-08", 8), "moves": [["70", "1", "6"], ["73", "6", "1"]]}]
chain = trd.results(INFO, CHAIN_DAYS, joint_week({G: 0, A: 1, HIT: 0}, {G: 0, A: 2, HIT: 0}), CHAIN_TX, {}, {}, SLOTS)
later = next(s for s in chain["trades"][0]["sides"] if s["team"] == "1")
earlier = next(s for s in chain["trades"][1]["sides"] if s["team"] == "1")
check("the first trade is still in effect after 70 left: 71 stayed",
      earlier["end"] == "2026-10-08", earlier)
check("undone alone, the second trade keeps 70, and his assists cost it",
      later["weeks"][0]["costs"] == [[A, "loss", "win"]], later["weeks"])
check("undone together, he was never there: no category changes",
      "1" not in chain["together"] and chain["season"]["1"]["lost"] == 0, (chain["together"], chain["season"]))

# --------------------------------------------------------------------------
print("\n=== 5. points leagues ===")

points = trd.results({**INFO, "scoring": "headpoint"}, DAYS, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, SLOTS)
psides = [s for t in points["trades"] for s in t["sides"]]
check("a points league gets the lines but no swings",
      not points["swingsScored"] and all("gains" not in w and "with" not in w for s in psides for w in s["weeks"])
      and points["season"]["1"]["net"] == 0
      and next(s for s in psides if s["team"] == "1")["totals"]["received"] == {G: 1, A: 1, HIT: 2}, psides)
check("a player sent who was always on a fantasy roster needs no NHL data, so is never unmatched",
      not any(p.get("unmatched") for s in psides for p in s.get("sent", [])), psides)
check("only the players the trades name are sent", set(out["players"]) == {"3", "20", "30"}, out["players"])

# --------------------------------------------------------------------------
print("\n=== 6. the route ===")

try:
    import app as app_module
    from routes import standalone_routes as routes
    import yahoo_rosters

    client = app_module.app.test_client()
    real = (routes.yahoo_league_api.season, routes.player_pool.pool, routes.player_pool.aliases,
            routes._game_lines)
    NHL = {"3": 8003}
    routes.yahoo_league_api.season = lambda league_id, test=False, fetched=None: (INFO, DAYS, WEEKS)
    routes.player_pool.pool = lambda: [
        {"playerId": 8003, "fullName": "Sent Centre", "teamAbbrevs": "BOS", "positionCode": "C"},
        {"playerId": 8020, "fullName": "Got Centre", "teamAbbrevs": "TOR", "positionCode": "C"},
        {"playerId": 8030, "fullName": "Sent D", "teamAbbrevs": "MTL", "positionCode": "D"}]
    routes.player_pool.aliases = lambda: {}
    routes._game_lines = lambda ids, first, last: {
        (NHL[k], when): line for k in NHL for when, line in OUTSIDE[k].items()
        if NHL[k] in set(ids) and first <= when <= last}
    try:
        body = {"league_id": "1", "slots": {"C": 1},
                "transactions": {"transactions": TRANSACTIONS, "players": PLAYERS}}
        answer = client.post("/standalone/api/trade-results", json=body)
        data = answer.get_json()
        trade = {s["team"]: s for s in (data.get("trades") or [{}, {"sides": []}])[1]["sides"]}
        check("a public league: the players sent matched to NHL games where they were on no roster",
              answer.status_code == 200 and trade.get("1", {}).get("sent", [{}])[0].get("totals") == {A: 1, HIT: 5}
              and data["unmatched"] == [], (answer.status_code, data.get("message"), trade.get("1")))
        check("the picks the page stored reach the trade",
              {s["team"]: s for s in data["trades"][0]["sides"]}["3"].get("picksIn") == [5], data["trades"][0])

        def private(*_args, **_kwargs):
            raise yahoo_rosters.RosterPageError("private", "Private.")
        routes.yahoo_league_api.season = private
        answer = client.post("/standalone/api/trade-results", json=body)
        check("a private league answers 403 private, for the page to send its lineups",
              answer.status_code == 403 and answer.get_json()["code"] == "private", answer.get_json())
        check("transactions that are not a scrape are refused",
              client.post("/standalone/api/trade-results", json={**body, "transactions": "nope"}).status_code == 400)
    finally:
        (routes.yahoo_league_api.season, routes.player_pool.pool, routes.player_pool.aliases,
         routes._game_lines) = real
except Exception as exc:                                    # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
