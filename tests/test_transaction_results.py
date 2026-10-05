"""
Tests for Transaction Results (transaction_results.py) and its route.

No network and no database: a two-team league over one week, in the shapes
`yahoo_league_api` produces, with every number worked out by hand below. The
route runs against a stubbed reader, pool and game data.

What is pinned is what a reader could be misled by: an add and its drop are one
move, and so are a lone drop and a lone add a few hours apart, but not a day
and a half apart or across teams; the added player's line counts only on
nights he started and played (a bench night is a game, not production); the
dropped player counts only on nights a spot he could fill was open - the added
player's own slot, an empty slot, or an idle starter's - and a goalie never
fills a skater's; a week's swing names the category a move won and the one it
cost; a start the dropped player could not have made going to a teammate who
played on the bench, never left empty; ratios rebuilt from summed parts, so
two shutouts are two hours; and points leagues get no swings.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("FLASK_SECRET_KEY", "transaction-results-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bench_points as bp                                   # noqa: E402
import transaction_results as tr                            # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


G, A, HIT, W, GA, GAA, SA, SV, SVP = "1", "2", "31", "19", "22", "23", "24", "25", "26"
ET = ZoneInfo("America/New_York")


def at(day, hour):
    """Epoch seconds for a US Eastern date and hour."""
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=ET).timestamp())


def cat(stat_id, name, higher=True, display=False, goalie=False):
    return {"id": stat_id, "name": name, "full": name, "higherBetter": higher,
            "displayOnly": display, "goalie": goalie}


INFO = {"key": "1.l.1", "name": "Test", "season": "2026", "scoring": "head",
        "categories": [cat(G, "G"), cat(A, "A"), cat(HIT, "HIT"), cat(W, "W", goalie=True),
                       cat(GA, "GA", False, True, True), cat(GAA, "GAA", False, goalie=True),
                       cat(SA, "SA", display=True, goalie=True), cat(SV, "SV", display=True, goalie=True),
                       cat(SVP, "SV%", goalie=True)],
        "slots": {"C": 1, "LW": 1, "D": 1, "Util": 1, "G": 1, "BN": 3}}
SLOTS = {k: v for k, v in INFO["slots"].items() if k != "BN"}
SCORED = bp.scored_categories(INFO)


def sk(g=None, a=None, hit=None):
    """A skater's night; all None is no game."""
    if g is None and a is None and hit is None:
        return {G: None, A: None, HIT: None}
    return {G: g or 0, A: a or 0, HIT: hit or 0}


def gl(w=0, ga=0, gaa=0.0, sa=0, sv=0, played=True):
    if not played:
        return {W: None, GA: None, GAA: None, SA: None, SV: None, SVP: None}
    return {W: w, GA: ga, GAA: gaa, SA: sa, SV: sv, SVP: (sv / sa) if sa else None}


C, LW, D, CLW, GOALIE = ["C"], ["LW"], ["D"], ["C", "LW"], ["G"]
DAYS = [
    # Team 1 added 10 (C) for 3 (C, LW) that morning; 10 sits in Util and plays.
    # Team 2 added 20 (D) for 21 (LW), and has left its LW slot empty.
    {"date": "2026-10-06", "teams": {
        "1": [["1", "C", C, sk(1, 0, 0)], ["2", "LW", LW, sk()], ["5", "D", D, sk(0, 0, 3)],
              ["10", "Util", C, sk(0, 1, 2)], ["6", "G", GOALIE, gl(1, 2, 2.0, 30, 28)]],
        "2": [["22", "C", C, sk(1, 1, 1)], ["20", "D", D, sk(0, 0, 3)],
              ["23", "Util", C, sk(0, 0, 1)], ["24", "G", GOALIE, gl(played=False)],
              # Benched and playing: 26 would have had 20's D slot without the move
              ["26", "BN", D, sk(0, 0, 4)], ["27", "BN", D, sk(0, 0, 1)],
              ["28", "BN", GOALIE, gl(1, 1, 1.0, 20, 19)]],
    }},
    # 10 plays from the bench, and every team-1 spot 3 could fill is taken by
    # someone who played. Team 2's LW is back, held by 25, who has no game.
    {"date": "2026-10-07", "teams": {
        "1": [["1", "C", C, sk(0, 1, 1)], ["2", "LW", LW, sk(0, 0, 1)], ["5", "D", D, sk(0, 0, 1)],
              ["7", "Util", D, sk(0, 0, 2)], ["10", "BN", C, sk(0, 1, 0)],
              ["6", "G", GOALIE, gl(played=False)]],
        "2": [["22", "C", C, sk(0, 0, 1)], ["25", "LW", LW, sk()], ["20", "D", D, sk(0, 0, 2)],
              ["23", "Util", C, sk(0, 1, 0)], ["24", "G", GOALIE, gl(1, 3, 3.0, 25, 22)]],
    }},
    # 10 starts in Util with no game: a spot that was his, so 3 would have had it
    {"date": "2026-10-08", "teams": {
        "1": [["1", "C", C, sk(0, 0, 0)], ["2", "LW", LW, sk(0, 0, 0)], ["5", "D", D, sk()],
              ["10", "Util", C, sk()], ["6", "G", GOALIE, gl(played=False)]],
        "2": [["22", "C", C, sk()], ["25", "LW", LW, sk(0, 0, 0)], ["20", "D", D, sk()],
              ["23", "Util", C, sk()], ["24", "G", GOALIE, gl(played=False)]],
    }},
    # 10 is gone (dropped): his tenure ended the night before
    {"date": "2026-10-09", "teams": {
        "1": [["1", "C", C, sk(0, 0, 0)], ["2", "LW", LW, sk()], ["5", "D", D, sk()],
              ["6", "G", GOALIE, gl(played=False)]],
        "2": [["22", "C", C, sk()], ["25", "LW", LW, sk()], ["20", "D", D, sk()],
              ["23", "Util", C, sk()], ["24", "G", GOALIE, gl(played=False)]],
    }},
]
for d in DAYS:
    d["names"] = {"1": "Mine", "2": "Theirs"}

# The week's real totals, Yahoo's. Mine wins A, GAA and SV%, ties G and W, loses HIT.
WEEKS = [{"week": 1, "start": "2026-10-05", "end": "2026-10-11", "status": "postevent", "playoffs": False,
          "matchups": [{"teams": ["1", "2"], "names": {"1": "Mine", "2": "Theirs"}, "totals": {
              "1": {G: 3, A: 4, HIT: 10, W: 1, GA: 2, GAA: 2.0, SA: 30, SV: 28, SVP: 28 / 30},
              "2": {G: 3, A: 3, HIT: 11, W: 1, GA: 3, GAA: 3.0, SA: 25, SV: 22, SVP: 22 / 25}}}]}]

TRANSACTIONS = [
    {"id": "1", "type": "add/drop", "time": at("2026-10-06", 10),
     "moves": [["10", "freeagents", "1"], ["3", "1", "waivers"]]},
    {"id": "2", "type": "add/drop", "time": at("2026-10-06", 8),
     "moves": [["20", "waivers", "2"], ["21", "2", "freeagents"]]},
    {"id": "3", "type": "drop", "time": at("2026-10-09", 9), "moves": [["10", "1", "freeagents"]]},
]
PLAYERS = {"3": ["Dropped Centre", "BOS", "C,LW"], "10": ["Added Centre", "BOS", "C"],
           "20": ["Added D", "TOR", "D"], "21": ["Dropped Winger", "TOR", "LW"],
           "1": ["One", "BOS", "C"], "2": ["Two", "BOS", "LW"], "26": ["Bench D", "TOR", "D"]}
# The dropped players' NHL games: 3 played the 6th, 7th, 8th and 9th; 21 the 6th and 7th
OUTSIDE = {"3": {"2026-10-06": sk(1, 0, 1), "2026-10-07": sk(0, 1, 0), "2026-10-08": sk(1, 0, 0),
                 "2026-10-09": sk(5, 5, 5)},
           "21": {"2026-10-06": sk(0, 1, 0), "2026-10-07": sk(1, 0, 0)}}

# --------------------------------------------------------------------------
print("\n=== 1. which moves are moves ===")

T0 = at("2026-10-12", 9)
pairs = tr.pair([
    {"type": "add/drop", "time": T0, "moves": [["10", "freeagents", "1"], ["3", "1", "waivers"]]},
    {"type": "drop", "time": T0 + 3600, "moves": [["4", "1", "freeagents"]]},
    {"type": "add", "time": T0 + 7 * 3600, "moves": [["11", "waivers", "1"]]},
    {"type": "add", "time": T0 + 2 * 86400, "moves": [["12", "freeagents", "1"]]},
    {"type": "drop", "time": T0 + 2 * 86400 + 30 * 3600, "moves": [["13", "1", "freeagents"]]},
    {"type": "drop", "time": T0 + 3600, "moves": [["14", "2", "freeagents"]]},
    {"type": "trade", "time": T0 + 600, "moves": [["1", "1", "2"], ["22", "2", "1"]]},
    {"type": "add", "time": None, "moves": [["99", "freeagents", "1"]]},
])
by_added = {m["added"]: m for m in pairs if m["added"]}
check("an add/drop is one move", by_added["10"]["dropped"] == "3" and by_added["10"]["paired"] == "transaction")
check("a lone drop and a lone add six hours apart, same team, are one move",
      by_added["11"]["dropped"] == "4" and by_added["11"]["paired"] == "nearby", by_added["11"])
check("and the claim off waivers is marked", by_added["11"]["claim"] is True and by_added["10"]["claim"] is False)
check("thirty hours apart they are not", by_added["12"]["dropped"] is None
      and any(m["dropped"] == "13" and not m["added"] for m in pairs))
check("another team's drop is never paired with this team's add",
      any(m["dropped"] == "14" and m["team"] == "2" and not m["added"] for m in pairs))
check("trades are not moves, and nor is a transaction with no time",
      not any(m.get("added") in ("22", "99") or m.get("dropped") == "1" for m in pairs), pairs)
check("moves come oldest first, each with its day in Eastern",
      [m["time"] for m in pairs] == sorted(m["time"] for m in pairs) and by_added["10"]["date"] == "2026-10-12")

# --------------------------------------------------------------------------
print("\n=== 2. who sat where ===")

season = tr.Season(DAYS)
check("the added player's tenure runs from his first night until he is gone",
      season.tenure("10", "1", "2026-10-06") == ["2026-10-06", "2026-10-07", "2026-10-08"])
check("a move effective tomorrow starts tomorrow",
      season.tenure("10", "1", "2026-10-05") == ["2026-10-06", "2026-10-07", "2026-10-08"])
check("someone never seen within two days has no tenure", season.tenure("10", "2", "2026-10-06") == [])

roster = season.roster("2026-10-06", "1")
check("the added player's own slot is the dropped player's first claim",
      tr.open_spot(roster, CLW, "Util", SLOTS, "10") == ("own", "Util"))
check("a slot he cannot play is not his to take - a winger takes the idle winger's instead",
      tr.open_spot(roster, LW, "C", SLOTS, "10") == ("idle", "LW"))
check("an empty starting slot is open", tr.open_spot(season.roster("2026-10-06", "2"), LW, "D", SLOTS, "20")
      == ("empty", "LW"))
check("a starter with no game leaves his slot open", tr.open_spot(season.roster("2026-10-07", "2"), LW, "D", SLOTS, "20")
      == ("idle", "LW"))
check("every spot he fits filled by someone who played: none",
      tr.open_spot(season.roster("2026-10-07", "1"), CLW, None, SLOTS, "10") is None)
check("a goalie never fills a skater's slot, idle or not",
      tr.open_spot(season.roster("2026-10-06", "1"), GOALIE, "Util", SLOTS, "10") is None)
check("and a skater never the goalie's, though that goalie had no game",
      tr.open_spot(season.roster("2026-10-07", "1"), CLW, None, {"G": 1}, "10") is None)

bench = season.roster("2026-10-06", "2")
check("a start the dropped player could not make goes to the busiest teammate who played on the bench",
      tr.stand_in(bench, "D", SCORED) == ("26", sk(0, 0, 4)), tr.stand_in(bench, "D", SCORED))
check("a benched goalie never covers a skater's slot, and only he covers the goalie's",
      tr.stand_in({"28": bench["28"]}, "D", SCORED) is None and tr.stand_in(bench, "G", SCORED)[0] == "28")
check("nobody on the bench who played: nobody covers",
      tr.stand_in(season.roster("2026-10-07", "2"), "D", SCORED) is None)

two_shutouts = tr.add_line(tr.add_line({}, gl(1, 0, 0.0, 20, 20)), gl(1, 0, 0.0, 25, 25))
check("two shutouts are two hours, not one", two_shutouts["min"] == 120.0, two_shutouts)
check("game data's seconds are minutes as played",
      tr.add_line({}, {GA: 2, GAA: 2.4, "toi": 3000})["min"] == 50.0)
check("a summed line's own minutes are what a swap takes off",
      bp.line_minutes({GA: 1, GAA: 3.0, "min": 120.0}) == 120.0 and bp.line_minutes({GA: 1, GAA: 3.0}) == 20.0)
shown = tr.shown(tr.add_line(tr.add_line({}, gl(1, 3, 3.0, 30, 27)), gl(0, 0, 0.0, 20, 20)), SCORED)
check("GAA and SV% are rebuilt from the summed parts, not averaged",
      shown[GAA] == 1.5 and abs(shown[SVP] - 47 / 50) < 1e-9 and shown[W] == 1, shown)

# --------------------------------------------------------------------------
print("\n=== 3. what each move brought ===")

out = tr.results(INFO, DAYS, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, SLOTS, detail_team="1")
mine = next(m for m in out["moves"] if m["team"] == "1" and m.get("added", {}).get("player") == "10")
added, dropped = mine["added"], mine["dropped"]
check("the added player: two games, one of them started", added["games"] == 2 and added["starts"] == 1, added)
check("his line is the started night only - the bench night's assist is not in it",
      added["totals"] == {A: 1, HIT: 2}, added["totals"])
check("an idle start is neither a game nor a start", added["starts"] == 1)
check("his tenure and whether he is still there",
      added["first"] == "2026-10-06" and added["last"] == "2026-10-08" and "current" not in added, added)
check("the dropped player: three games in that time, two with a spot open",
      dropped["games"] == 3 and dropped["open"] == 2, dropped)
check("his line is those two nights, and not the night after the tenure",
      dropped["totals"] == {G: 2, HIT: 1}, dropped["totals"])
kinds = {n[0]: (n[3], n[4]) for n in mine["nights"]}
check("each night says which spot was open, if any",
      kinds == {"2026-10-06": ("own", "Util"), "2026-10-07": (None, None), "2026-10-08": ("own", "Util")}, kinds)
check("a night carries both lines as bench points shows one",
      mine["nights"][0][2] == {A: 1, HIT: 2} and mine["nights"][0][5] == {G: 1, HIT: 1}, mine["nights"][0])

theirs = next(m for m in out["moves"] if m["team"] == "2")
check("another team's move is listed when it changed a week, without lines or nights",
      "totals" not in theirs["added"] and "nights" not in theirs and theirs["weeks"], theirs)
check("its dropped player had an empty spot one night and an idle starter's the next",
      theirs["dropped"]["open"] == 2 and theirs["dropped"]["games"] == 2, theirs["dropped"])

# --------------------------------------------------------------------------
print("\n=== 4. the weeks a move decided ===")

week = mine["weeks"][0]
check("without the move: the added player's assist and hits off, the dropped player's goals on",
      week["with"] == [3, 1, 2] and week["without"] == [3, 1, 2], week)
check("it won assists, which would have been a tie", week["gains"] == [[A, "win", "tie"]], week["gains"])
check("and cost goals, which the dropped player would have won", week["costs"] == [[G, "tie", "win"]], week["costs"])
week = theirs["weeks"][0]
check("their move won hits - only a win over a tie, since 26 would have played 20's first night - "
      "and cost assists and goals",
      week["gains"] == [[HIT, "win", "tie"]]
      and sorted(week["costs"]) == sorted([[A, "loss", "tie"], [G, "tie", "win"]]) and week["net"] == -0.5, week)
check("and says one of its starts a teammate on the bench would have covered", theirs["added"]["covered"] == 1)
check("a win made of a tie, and a tie made of a win, net to nothing", mine["weeks"][0]["net"] == 0.0)
check("the season tallies every move: moves, starts, open games, categories won and lost",
      out["season"]["1"] == {"moves": 2, "starts": 1, "dropGames": 2, "gained": 1, "lost": 1, "net": 0.0}
      and out["season"]["2"]["gained"] == 1 and out["season"]["2"]["lost"] == 2
      and out["season"]["2"]["net"] == -0.5, out["season"])
flipped = tr.swing({G: 2, A: 1}, {G: 1, A: 1}, {G: 2}, {}, [cat(G, "G"), cat(A, "A")])
check("a loss turned into a win is worth a whole category, and the records say so",
      flipped["net"] == 1.0 and flipped["with"] == [1, 0, 1] and flipped["without"] == [0, 1, 1], flipped)
half = tr.swing({G: 2, A: 1}, {G: 1, A: 1}, {G: 1}, {}, [cat(G, "G"), cat(A, "A")])
check("a tie turned into a win is worth half", half["net"] == 0.5 and half["gains"] == [[G, "win", "tie"]], half)
lone = next(m for m in out["moves"] if m["team"] == "1" and not m.get("added"))
check("a lone drop is judged until the team's next pickup - here none, so to the end",
      lone["dropped"]["player"] == "10" and lone["dropped"]["games"] == 0, lone)
check("only the players the moves name are sent", set(out["players"]) == {"3", "10", "20", "21"}, out["players"])

points = tr.results({**INFO, "scoring": "headpoint"}, DAYS, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, SLOTS, "1")
check("a points league gets contributions but no swings",
      not points["swingsScored"] and all(not m.get("weeks") for m in points["moves"])
      and not any(m["team"] == "2" for m in points["moves"]), points["moves"])

no_slots = tr.results(INFO, DAYS, WEEKS, TRANSACTIONS, PLAYERS, OUTSIDE, {}, "2")
theirs = next(m for m in no_slots["moves"] if m["team"] == "2")
check("with no slot counts an empty slot cannot be seen - only the idle starter's",
      theirs["dropped"]["open"] == 1, theirs["dropped"])
check("the night says who would have covered, with his line, and he is among the players sent",
      theirs["nights"][0][6] == ["26", {HIT: 4}] and theirs["nights"][1][6] is None
      and "26" in no_slots["players"], theirs["nights"])

# --------------------------------------------------------------------------
print("\n=== 5. the route ===")

try:
    import app as app_module
    from routes import standalone_routes as routes
    import yahoo_rosters

    client = app_module.app.test_client()
    real = (routes.yahoo_league_api.season, routes.player_pool.pool, routes.player_pool.aliases,
            routes._game_lines)
    NHL = {"3": 8003, "21": 8021}
    routes.yahoo_league_api.season = lambda league_id, test=False, fetched=None: (INFO, DAYS, WEEKS)
    routes.player_pool.pool = lambda: [
        {"playerId": 8003, "fullName": "Dropped Centre", "teamAbbrevs": "BOS", "positionCode": "C"},
        {"playerId": 8021, "fullName": "Dropped Winger", "teamAbbrevs": "TOR", "positionCode": "L"},
        {"playerId": 8010, "fullName": "Added Centre", "teamAbbrevs": "BOS", "positionCode": "C"}]
    routes.player_pool.aliases = lambda: {}
    routes._game_lines = lambda ids, first, last: {
        (NHL[k], when): line for k in NHL for when, line in OUTSIDE[k].items()
        if NHL[k] in set(ids) and first <= when <= last}
    try:
        body = {"league_id": "1", "team": "1", "slots": {"C": 1},
                "transactions": {"transactions": TRANSACTIONS, "players": PLAYERS}}
        answer = client.post("/standalone/api/transaction-results", json=body)
        data = answer.get_json()
        got = next((m for m in data.get("moves", []) if m.get("added", {}).get("player") == "10"), {})
        check("a public league: the dropped players matched to NHL games by name and team",
              answer.status_code == 200 and got.get("dropped", {}).get("totals") == {G: 2, HIT: 1}
              and data["unmatched"] == [], (answer.status_code, data.get("message"), got))
        check("Yahoo's own slot counts are used over the page's",
              next(m for m in data["moves"] if m["team"] == "2")["dropped"]["open"] == 2)

        def private(*_args, **_kwargs):
            raise yahoo_rosters.RosterPageError("private", "Private.")
        routes.yahoo_league_api.season = private
        answer = client.post("/standalone/api/transaction-results", json=body)
        check("a private league answers 403 private, for the page to send its lineups",
              answer.status_code == 403 and answer.get_json()["code"] == "private"
              and "bookmarklet" in answer.get_json()["message"], answer.get_json())
        check("transactions that are not a scrape are refused",
              client.post("/standalone/api/transaction-results",
                          json={**body, "transactions": "nope"}).status_code == 400)
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
