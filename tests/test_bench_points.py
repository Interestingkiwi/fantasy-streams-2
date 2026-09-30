"""
Tests for bench points (bench_points.py) and the public-API reader behind them
(yahoo_league_api.py).

No network and no database for the maths: a small league built in the shapes
`yahoo_league_api` produces. The route runs against a stubbed reader.

What is pinned is what a reader could be misled by: only the bench counts (IR
never does); a bench player can only replace a starter whose slot he could
fill, a goalie only a goalie; a starter with no game that day is the cheapest
swap and is offered; a swap that wins one category and loses another says so;
and GAA and save percentage are rebuilt from their parts, with a goalie's
minutes recovered from his own GAA rather than assumed to be an hour.

Author - Jason Druckenmiller
Created - 9/30/2026
Updated - 9/30/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "bench-points-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bench_points as bp                                   # noqa: E402
import yahoo_league_api as api                              # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


G, A, P, HIT, W, GA, GAA, SA, SV, SVP = "1", "2", "3", "31", "19", "22", "23", "24", "25", "26"


def cat(stat_id, name, higher=True, display=False, goalie=False):
    return {"id": stat_id, "name": name, "full": name, "higherBetter": higher,
            "displayOnly": display, "goalie": goalie}


INFO = {
    "key": "1.l.1", "name": "Test", "season": "2026", "scoring": "head",
    "categories": [cat(G, "G"), cat(A, "A"), cat(P, "P"), cat(HIT, "HIT"),
                   cat(W, "W", goalie=True), cat(GA, "GA", False, True, True),
                   cat(GAA, "GAA", False, goalie=True), cat(SA, "SA", display=True, goalie=True),
                   cat(SV, "SV", display=True, goalie=True), cat(SVP, "SV%", goalie=True)],
}


def skater(g=None, a=None, hits=None):
    """A skater's day: None everywhere is no game."""
    if g is None and a is None and hits is None:
        return {G: None, A: None, P: None, HIT: None}
    return {G: g or 0, A: a or 0, P: (g or 0) + (a or 0), HIT: hits or 0}


def goalie(w=0, ga=0, gaa=0.0, sa=0, sv=0, played=True):
    if not played:
        return {W: None, GA: None, GAA: None, SA: None, SV: None, SVP: None}
    return {W: w, GA: ga, GAA: gaa, SA: sa, SV: sv, SVP: (sv / sa) if sa else None}


DAYS = [{
    "date": "2026-10-06",
    "names": {"1": "Mine", "2": "Theirs"},
    "players": {str(n): [f"Player {n}", "BOS", "C"] for n in range(1, 20)},
    "teams": {
        "1": [
            ["1", "C", ["C"], skater(0, 0, 1)],          # a starting centre who did little
            ["2", "LW", ["LW"], skater()],               # a starting winger with no game
            ["3", "D", ["D"], skater(0, 1, 4)],
            ["4", "BN", ["C", "LW"], skater(1, 1, 0)],   # benched: a goal and an assist
            ["5", "BN", ["D"], skater()],                # benched, no game: not an appearance
            ["6", "IR", ["C"], skater(2, 0, 0)],         # on IR: never counts
            ["7", "G", ["G"], goalie(0, 4, 4.0, 30, 26)],
            ["8", "BN", ["G"], goalie(1, 1, 1.0, 30, 29)],
        ],
        "2": [
            ["11", "C", ["C"], skater(1, 0, 2)],
            ["12", "BN", ["D"], skater(0, 0, 3)],
            ["13", "G", ["G"], goalie(1, 2, 2.0, 25, 23)],
        ],
    },
}]

# The week so far: Mine trails G and A, ties HIT, and has the worse goaltending
WEEKS = [{
    "week": 1, "start": "2026-10-05", "end": "2026-10-11", "status": "postevent", "playoffs": False,
    "matchups": [{"teams": ["1", "2"], "names": {"1": "Mine", "2": "Theirs"},
                  "totals": {
                      "1": {G: 3, A: 4, P: 7, HIT: 10, W: 0, GA: 4, GAA: 4.0, SA: 30, SV: 26, SVP: 26 / 30},
                      "2": {G: 4, A: 5, P: 9, HIT: 10, W: 1, GA: 2, GAA: 2.0, SA: 25, SV: 23, SVP: 23 / 25},
                  }}],
}]

# --------------------------------------------------------------------------
print("\n=== 1. who counts ===")

out = bp.summarise(INFO, DAYS, WEEKS)
mine = out["weeks"][0]["teams"]["1"]
players = [a["player"] for a in mine["appearances"]]
check("a benched player who played is an appearance", "4" in players, players)
check("a benched goalie who played is one too", "8" in players, players)
check("a benched player with no game is not", "5" not in players)
check("IR never counts, whatever he scored", "6" not in players)
check("the season adds up what the bench scored",
      out["season"]["1"]["appearances"] == 2 and out["season"]["1"]["totals"].get(G) == 1
      and out["season"]["1"]["totals"].get(A) == 1, out["season"]["1"])
check("ratio categories are never added up across a season",
      GAA not in out["season"]["1"]["totals"] and SVP not in out["season"]["1"]["totals"])
check("display-only categories are not scored",
      {c["id"] for c in out["categories"]} == {G, A, P, HIT, W, GAA, SVP})
check("an appearance carries only what he did - no zeroes",
      next(a for a in mine["appearances"] if a["player"] == "4")["stats"] == {G: 1, A: 1, P: 2},
      mine["appearances"])

# --------------------------------------------------------------------------
print("\n=== 2. swaps ===")

skater_swaps = [s for s in mine["swaps"] if s["bench"] == "4"]
starters = {st["id"] for s in skater_swaps for st in s["starters"]}
check("a bench player is only swapped for a starter whose slot he can fill",
      starters <= {"1", "2"} and "3" not in starters, starters)
no_game = [s for s in skater_swaps if any(not st["played"] for st in s["starters"])]
check("a starter with no game that day is offered, and marked so", bool(no_game), skater_swaps)
best = skater_swaps[0]
gains = dict(best["gains"])
check("the goal and assist over the winger with no game tie G, A and P",
      gains == {G: "tie", A: "tie", P: "tie"} and best["losses"] == []
      and [st["id"] for st in best["starters"]] == ["2"], best)
check("and the record says so: 0-6-1 becomes 0-3-4",
      best["before"] == [0, 6, 1] and best["after"] == [0, 3, 4], (best["before"], best["after"]))
losing = [s for s in skater_swaps if s["losses"]]
check("a swap that costs a category says which - benching the centre loses his hit",
      any(dict(s["losses"]).get(HIT) == "loss" for s in losing), skater_swaps)

goalie_swaps = [s for s in mine["swaps"] if s["bench"] == "8"]
check("a benched goalie is only ever swapped for the goalie", goalie_swaps
      and all(st["slot"] == "G" for s in goalie_swaps for st in s["starters"]), goalie_swaps)
g_gains = dict(goalie_swaps[0]["gains"]) if goalie_swaps else {}
check("his win ties W", g_gains.get(W) == "tie", g_gains)
check("and GAA and save percentage are rebuilt from their parts, and won",
      g_gains.get(GAA) == "win" and g_gains.get(SVP) == "win", g_gains)
check("nothing is offered to a team with no bench appearance", out["weeks"][0]["teams"]["2"]["swaps"] == []
      or all(s["bench"] == "12" for s in out["weeks"][0]["teams"]["2"]["swaps"]))

# --------------------------------------------------------------------------
print("\n=== 3. the ratio arithmetic ===")

check("minutes come back out of GAA exactly", abs(bp.minutes(3, 3.0) - 60) < 1e-9
      and abs(bp.minutes(2, 4.0) - 30) < 1e-9)
check("a shutout counts an hour, a goalie with no game none",
      bp.minutes(0, 0.0) == 60 and bp.minutes(None, None) == 0)
after = bp.swapped_totals(WEEKS[0]["matchups"][0]["totals"]["1"],
                          DAYS[0]["teams"]["1"][6][3], DAYS[0]["teams"]["1"][7][3], out["categories"])
check("swapping a 4-goal start for a 1-goal one: GAA from 4.00 to 1.00",
      abs(after[GAA] - 1.0) < 1e-9, after[GAA])
check("and save percentage from .867 to .967", abs(after[SVP] - 29 / 30) < 1e-9, after[SVP])
check("a lower-better category is won by the lower number",
      bp.compare(2.0, 3.0, False) == 1 and bp.compare(3.0, 2.0, False) == -1)
check("a counting category nobody has recorded is zero, a ratio with nothing is no result",
      bp.record({}, {W: 1}, [cat(W, "W")])[1][W] == -1
      and bp.record({}, {GAA: 2.0}, [cat(GAA, "GAA", False)])[1][GAA] is None)
check("slots follow Yahoo's generics",
      bp.can_fill(["C"], "F") and bp.can_fill(["LW"], "W") and not bp.can_fill(["C"], "W")
      and bp.can_fill(["D"], "Util") and not bp.can_fill(["G"], "Util"))

points = bp.summarise({**INFO, "scoring": "headpoint"}, DAYS, WEEKS)
check("a points league gets its bench totals but no category swaps",
      points["season"]["1"]["appearances"] == 2 and not points["swapsScored"]
      and points["weeks"][0]["teams"]["1"]["swaps"] == [])

# --------------------------------------------------------------------------
print("\n=== 4. reading Yahoo ===")

check("Yahoo's dash is no value, and numbers come through as numbers",
      api.number("-") is None and api.number(None) is None and api.number(".912") == 0.912
      and api.number("3") == 3.0)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response, self.urls = response, []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.response


day_body = {"fantasy_content": {"league": {"teams": [{"team": {
    "team_key": "477.l.5848.t.3", "team_id": "3", "name": "Three",
    "roster": {"players": [{"player": {
        "player_id": "5980", "name": {"full": "Nathan MacKinnon"}, "editorial_team_abbr": "COL",
        "display_position": "C", "eligible_positions": [{"position": "C"}],
        "selected_position": {"position": "BN"},
        "player_stats": {"stats": [{"stat": {"stat_id": "1", "value": "2"}},
                                   {"stat": {"stat_id": "2", "value": "-"}}]}}}]}}}]}}}
session = FakeSession(FakeResponse(200, day_body))
read = api.day("477.l.5848", "2099-01-01", session=session)
check("a day's rosters come back compact: slot, eligibility and that day's stats",
      read["teams"]["3"] == [["5980", "BN", ["C"], {"1": 2.0, "2": None}]]
      and read["names"]["3"] == "Three" and read["players"]["5980"][0] == "Nathan MacKinnon", read)
check("asked for with the date on both the roster and the stats",
      "roster;date=2099-01-01/players/stats;type=date;date=2099-01-01" in session.urls[0], session.urls)
try:
    api.get_json("/league/x", FakeSession(FakeResponse(401, {})))
    check("a private league is refused as private", False)
except api.RosterPageError as exc:
    check("a private league is refused as private", exc.code == "private", exc.code)

# --------------------------------------------------------------------------
print("\n=== 5. the route ===")

try:
    import app as app_module

    client = app_module.app.test_client()
    real = api.season

    def fake_season(league_id, test=False):
        if league_id == "4":
            raise api.RosterPageError("private", "private")
        return INFO, DAYS, WEEKS

    api.season = fake_season
    try:
        answer = client.post("/standalone/api/bench", json={"league_id": "5848"})
        data = answer.get_json()
        check("bench points come back for a public league", answer.status_code == 200
              and data["season"]["1"]["appearances"] == 2 and data["asOf"] == "2026-10-06", data.get("message"))
        private = client.post("/standalone/api/bench", json={"league_id": "4"})
        check("a private league is a 403 that points at the bookmarklet", private.status_code == 403
              and "bookmarklet" in private.get_json()["message"], private.get_json())
    finally:
        api.season = real

    home = client.get("/standalone/").data.decode("utf-8")
    check("Season History has the bench section, and Update from Yahoo loads it",
          'id="bench-section"' in home and "name: 'Bench points'" in home)
    check("the bookmarklet reads a day's lineups from Starting Rosters, and a week's pairings",
          "/startingrosters?date=" in home and "module=matchups" in home and "fs_benchLineups" in home)
except Exception as exc:                                    # noqa: BLE001
    check("route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n=== 6. private leagues: lineups from the pages, stats from our games ===")

import bench_lineups as bl                                  # noqa: E402

skater_row = {"goals": 1, "assists": 2, "points": 3, "plusMinus": -1, "penaltyMinutes": 2,
              "ppGoals": 1, "ppPoints": 2, "shGoals": 0, "shPoints": 0, "gameWinningGoals": 1,
              "shots": 4, "hits": 3, "blockedShots": 1, "timeOnIce": 1200}
line = bl.game_line(skater_row)
check("a skater's game becomes Yahoo's stats, PPA worked out from PPP and PPG",
      line["1"] == 1 and line["2"] == 2 and line["8"] == 2 and line["7"] == 1 and line["14"] == 4
      and line["31"] == 3 and "toi" not in line, line)
goalie_row = {"gamesStarted": 1, "wins": 1, "losses": 0, "goalsAgainst": 2, "shotsAgainst": 30,
              "saves": 28, "shutouts": 0, "timeOnIce": 3690}
g_line = bl.game_line(goalie_row)
check("a goalie's GAA comes from his own seconds, not an assumed hour",
      abs(g_line["23"] - 2 * 3600 / 3690) < 1e-9 and abs(g_line["26"] - 28 / 30) < 1e-9
      and g_line["toi"] == 3690, g_line)

check("time on ice counts in minutes, and shooting percentage is goals over shots",
      line["33"] == 20 and abs(line["15"] - 0.25) < 1e-9, line)
check("faceoffs come through when the row has them",
      bl.game_line({**skater_row, "faceoffWins": 12, "faceoffLosses": 9})["16"] == 12
      and bl.game_line({**skater_row, "faceoffWins": 12, "faceoffLosses": 9})["17"] == 9)
check("and a row scraped before faceoffs were collected has none, not zero",
      "16" not in line and "17" not in line, line)

info, unsupported = bl.league_info(["G", "A", "HIT", "GAA", "SVpct", "FW", "FL", "TOI", "OTL"])
check("League Home's categories become Yahoo's, lower-better where they are",
      [c["id"] for c in info["categories"]] == ["1", "2", "31", "23", "26", "16", "17", "33"]
      and next(c for c in info["categories"] if c["id"] == "23")["higherBetter"] is False
      and next(c for c in info["categories"] if c["id"] == "17")["higherBetter"] is False, info)
check("faceoffs and time on ice are covered; only what Yahoo has no stat for is not",
      unsupported == ["OTL"], unsupported)
shooting = bp.swapped_totals({G: 3, "14": 30, "15": 0.1}, {G: 0, "14": 4}, {G: 2, "14": 3},
                             [cat(G, "G"), cat("14", "SOG"), cat("15", "SH%")])
check("shooting percentage is rebuilt from goals over shots, not added",
      abs(shooting["15"] - 5 / 29) < 1e-9, shooting)
check("and Yahoo's MM:SS time on ice is read as minutes",
      abs(api.number("18:30") - 18.5) < 1e-9 and api.number("1:xx") is None)
check("the page's 'TEAM - POS' splits into team and positions",
      bl.split_info("COL - C,LW") == ("COL", ["C", "LW"]) and bl.split_info("") == ("", []))

POOL = [
    {"playerId": 100, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "C"},
    {"playerId": 200, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "D"},
    {"playerId": 300, "fullName": "Mitch Marner", "teamAbbrevs": "VGK", "positionCode": "R"},
    {"playerId": 400, "fullName": "Anze Kopitar", "teamAbbrevs": "LAK", "positionCode": "C"},
]
ids = bl.match_players({"7909": ["Elias Pettersson", "VAN - C"], "8645": ["Elias Pettersson", "VAN - D"],
                        "5387": ["Mitch Marner", "VGK - RW"], "3637": ["Anze Kopitar", "LA - C"]}, POOL)
check("two Elias Petterssons are told apart by the page's positions, with no call to Yahoo",
      ids.get("7909") == 100 and ids.get("8645") == 200, ids)
check("everyone else matches by name", ids.get("5387") == 300 and ids.get("3637") == 400, ids)

LINEUPS = {
    "dates": {
        "2026-10-06": {"1": [["5387", "RW"], ["3637", "BN"], ["7909", "C"]],
                       "2": [["8645", "D"]]},
        "2026-10-07": {"1": [["5387", "RW"], ["3637", "C"], ["7909", "BN"]],
                       "2": [["8645", "D"]]},
    },
    "players": {"5387": ["Mitch Marner", "VGK - RW"], "3637": ["Anze Kopitar", "LA - C"],
                "7909": ["Elias Pettersson", "VAN - C"], "8645": ["Elias Pettersson", "VAN - D"]},
    "pairs": {"1": [["1", "2"]]},
}
LINES = {
    (300, "2026-10-06"): bl.game_line({**skater_row, "goals": 1, "assists": 0, "points": 1}),
    (400, "2026-10-06"): bl.game_line({**skater_row, "goals": 2, "assists": 0, "points": 2}),   # benched
    (100, "2026-10-06"): bl.game_line({**skater_row, "goals": 0, "assists": 0, "points": 0}),
    (200, "2026-10-06"): bl.game_line({**skater_row, "goals": 3, "assists": 0, "points": 3}),
    (400, "2026-10-07"): bl.game_line({**skater_row, "goals": 1, "assists": 0, "points": 1}),
}
DAYS_P = bl.build_days(LINEUPS, ids, LINES)
day_one = {r[0]: r for r in DAYS_P[0]["teams"]["1"]}
check("a day's rows carry the slot, the page's positions and that night's stats",
      day_one["3637"][1] == "BN" and day_one["3637"][2] == ["C"] and day_one["3637"][3]["1"] == 2, day_one)
check("a player with no game that night has no stats",
      {r[0]: r for r in DAYS_P[1]["teams"]["1"]}["7909"][3] == {})
WEEKS_P = bl.build_weeks([{"week": 1, "start": "2026-10-05", "end": "2026-10-11"}], LINEUPS, ids, LINES,
                         "2026-10-20", bp.NOT_STARTING)
totals = WEEKS_P[0]["matchups"][0]["totals"]
check("a week's totals count starters only - Kopitar's two benched goals are not in",
      totals["1"]["1"] == 1 + 0 + 1 and totals["2"]["1"] == 3, totals)
check("a week that has ended is final", WEEKS_P[0]["status"] == "postevent")
PRIVATE = bp.summarise({"scoring": "head", "categories": [cat(G, "G")]}, DAYS_P, WEEKS_P)
kopitar = PRIVATE["weeks"][0]["teams"]["1"]
check("and bench_points reads it as it reads the public shape: Kopitar's benched night is an appearance",
      [a["player"] for a in kopitar["appearances"]] == ["3637"], kopitar["appearances"])
check("with the swap over the centre who did nothing, which turns a 2-3 loss in G into a 4-3 win",
      kopitar["swaps"] and [st["id"] for st in kopitar["swaps"][0]["starters"]] == ["7909"]
      and dict(kopitar["swaps"][0]["gains"]).get(G) == "win"
      and kopitar["swaps"][0]["before"] == [0, 1, 0] and kopitar["swaps"][0]["after"] == [1, 0, 0],
      kopitar["swaps"])

try:
    empty = client.post("/standalone/api/bench/lineups", json={"lineups": {}})
    check("the lineups route asks for lineups before it has any", empty.status_code == 400)
    bad = client.post("/standalone/api/bench/lineups",
                      json={"lineups": {"dates": {"not-a-date": {}}}, "weeks": []})
    check("and refuses a date that is not one", bad.status_code == 400)
except Exception as exc:                                    # noqa: BLE001
    check("lineups route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
