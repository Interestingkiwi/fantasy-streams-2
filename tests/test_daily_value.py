"""
Tests for the per-game value engine.

The interesting assertions are the ones that pin the three deliberate
departures from the draft board - no centering, no capping, no replacement
level - and the goalie-per-start rule, because those are the decisions a
future reader is most likely to mistake for bugs and "fix".

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_value as dv                                    # noqa: E402
from lineup_utils import lineup_value, optimal_lineup       # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def close(a, b, tol=1e-9):
    return abs(a - b) < tol


SKATER = {
    "fullName": "Test Skater", "positionCode": "C", "projectedGames": 80,
    "proj_goals": 40.0, "proj_assists": 60.0, "proj_points": 100.0,
    "proj_shots": 240.0, "proj_hits": 80.0, "proj_penaltyMinutes": 40.0,
    "proj_ppPoints": 30.0, "proj_ppGoals": 12.0,
}
GOALIE = {
    "fullName": "Test Goalie", "positionCode": "G", "projectedGames": 60,
    "proj_gamesStarted": 50.0, "proj_wins": 30.0, "proj_saves": 1250.0,
    "proj_goalsAgainst": 125.0, "proj_shutouts": 5.0,
    "proj_savePct": 0.909, "proj_goalsAgainstAverage": 2.50,
}

# --------------------------------------------------------------------------
print("\n=== 1. per-game conversion ===")

values = dv.per_game(SKATER, ["G", "A", "SOG"])
check("counting stats divide by projected games",
      close(values["G"], 0.5) and close(values["A"], 0.75) and close(values["SOG"], 3.0),
      values)

goalie = dv.per_game(GOALIE, ["W", "SV", "GA", "SVpct", "GAA"])
check("goalie counting stats divide by STARTS, not appearances",
      close(goalie["W"], 0.6) and close(goalie["SV"], 25.0),
      goalie)
check("rates are carried through untouched",
      close(goalie["SVpct"], 0.909) and close(goalie["GAA"], 2.50), goalie)

check("a skater has no saves at all, rather than zero saves",
      "SV" not in dv.per_game(SKATER, ["G", "SV"]),
      dv.per_game(SKATER, ["G", "SV"]))
check("a derived category is the difference of two columns",
      close(dv.per_game(SKATER, ["PPA"])["PPA"], 18.0 / 80), dv.per_game(SKATER, ["PPA"]))
check("zero projected games is not a division error",
      dv.per_game({**SKATER, "projectedGames": 0}, ["G"])["G"] == 0.0)

check("categories split into scored, rates and missing",
      dv.supported(["G", "SVpct", "GWG"]) == (["G"], ["SVpct"], ["GWG"]),
      dv.supported(["G", "SVpct", "GWG"]))


# --------------------------------------------------------------------------
print("\n=== 2. scales and polarity ===")

pool = [
    {**SKATER, "projectedGames": 80, "proj_goals": 40.0},
    {**SKATER, "projectedGames": 80, "proj_goals": 20.0},
    # Below the sample floor: gets a value, but no vote on the spread.
    {**SKATER, "projectedGames": 5, "proj_goals": 30.0},
]
scales = dv.category_scales(pool, ["G"])
check("the sample floor keeps small samples out of sigma",
      close(scales["G"], dv._stdev([0.5, 0.25])), scales)
check("one qualifying player means no spread, not a crash",
      dv.category_scales([pool[0]], ["G"])["G"] == 0.0)

polarity = dv.category_polarity(["G", "GA", "L", "PIM"])
check("goals against and losses count against you",
      polarity["GA"] == -1.0 and polarity["L"] == -1.0, polarity)
check("PIM defaults negative", polarity["PIM"] == -1.0)
check("...and flips when the league rewards it",
      dv.category_polarity(["PIM"], pim_positive=True)["PIM"] == 1.0)

check("a category with no spread gets no weight, not an infinity",
      dv.default_weights({"G": 0.0}, {"G": 1.0})["G"] == 0.0)


# --------------------------------------------------------------------------
print("\n=== 3. the three departures from the draft board ===")

# No centering: production of zero is worth zero, not "below average".
empty = {"fullName": "Nobody", "positionCode": "C", "projectedGames": 80,
         "proj_goals": 0.0, "proj_assists": 0.0}
valued = dv.value_players([SKATER, empty], ["G", "A"])
check("a player who produces nothing is worth exactly nothing",
      close(valued[1]["value"], 0.0), valued[1]["value"])
check("...and a producer is worth more than nothing", valued[0]["value"] > 0)

# No capping: value stays linear in production, all the way up.
one = dict(SKATER)
two = {**SKATER, "proj_goals": SKATER["proj_goals"] * 2}
ten = {**SKATER, "proj_goals": SKATER["proj_goals"] * 10}
scaled = dv.value_players([one, two, ten], ["G"],
                          weights={"G": 1.0})
check("value is linear in production and never clipped",
      close(scaled[1]["value"], 2 * scaled[0]["value"])
      and close(scaled[2]["value"], 10 * scaled[0]["value"]), [p["value"] for p in scaled])

# No replacement level: the value carries no positional adjustment at all.
forward = dv.value_players([SKATER], ["G", "A"], weights={"G": 1.0, "A": 1.0})[0]
defender = dv.value_players([{**SKATER, "positionCode": "D"}], ["G", "A"],
                            weights={"G": 1.0, "A": 1.0})[0]
check("identical production is identical value regardless of position",
      close(forward["value"], defender["value"]))


# --------------------------------------------------------------------------
print("\n=== 4. weights are the only difference between league modes ===")

points = dv.points_weights({"G": 6.0, "A": 4.0, "SOG": 0.9})
fpg = dv.value_players([SKATER], ["G", "A", "SOG"], weights=points)[0]
expected = 0.5 * 6.0 + 0.75 * 4.0 + 3.0 * 0.9
check("a points league scores fantasy points per game",
      close(fpg["value"], expected), (fpg["value"], expected))

check("points_weights ignores categories the league does not score",
      dv.points_weights({"G": 6.0, "A": 4.0}, categories=["G"]) == {"G": 6.0})

check("rates never reach the scalar",
      close(dv.score({"SVpct": 0.909, "W": 0.6}, {"SVpct": 100.0, "W": 1.0}), 0.6),
      dv.score({"SVpct": 0.909, "W": 0.6}, {"SVpct": 100.0, "W": 1.0}))

check("perGame is kept alongside the scalar for later re-weighting",
      "PPA" in dv.value_players([SKATER], ["PPA"])[0]["perGame"])


# --------------------------------------------------------------------------
print("\n=== 5. real projections ===")

try:
    from db import fetch_all

    rows = [dict(r) for r in fetch_all("SELECT * FROM final_projections")]
    check("projections are loaded", len(rows) > 500, len(rows))

    cats = ["G", "A", "SOG", "HIT", "BLK", "PPP", "W", "SV", "GA", "SHO"]
    valued = dv.value_players(rows, cats)
    skaters = sorted([p for p in valued if p["positionCode"] != "G"],
                     key=lambda p: -p["value"])
    top = {p["fullName"] for p in skaters[:10]}

    check("the best skaters come out on top",
          {"Connor McDavid", "Nathan MacKinnon", "Nikita Kucherov"} <= top,
          sorted(top))
    check("every value is finite",
          all(p["value"] == p["value"] and abs(p["value"]) < 1e6 for p in valued))

    # Per game, not per season: durability must not enter the value.
    by_games = sorted([p for p in valued if p["positionCode"] != "G"],
                      key=lambda p: p["projectedGames"])
    check("a low-games player can still out-rank a full-season one",
          max(p["value"] for p in by_games[:100]) > min(p["value"] for p in by_games[-100:]))

    # A league scoring goals against with no credit for saves or wins: every
    # goalie start is a net loss, and seat_all=False should say so.
    punitive = dv.value_players(rows, ["G", "A", "SOG", "GA"])
    goalies = [p for p in punitive if p["positionCode"] == "G"]
    check("goalies go negative when a league scores GA and nothing else for them",
          all(p["value"] < 0 for p in goalies),
          max(p["value"] for p in goalies))

    for player in punitive:
        player["eligiblePositions"] = player.get("eligiblePositions") or player["positionCode"]
    roster = [p for p in punitive if p["positionCode"] == "G"][:3]
    seated = optimal_lineup(roster, {"G": 2})
    benched_all = optimal_lineup(roster, {"G": 2}, seat_all=False)
    check("...seat_all still starts them", len(seated["G"]) == 2)
    check("...seat_all=False leaves the slots empty instead",
          len(benched_all["G"]) == 0, benched_all)

    # End to end: value the pool, then seat a day's worth of it.
    cats_normal = ["G", "A", "SOG", "HIT", "BLK", "W", "SV", "GA", "SHO"]
    day = dv.value_players(rows, cats_normal)
    for player in day:
        player["eligiblePositions"] = player.get("eligiblePositions") or player["positionCode"]
    # A roster deep enough to fill the shape, so a short lineup means a bug
    # rather than a thin bench.
    shape = {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2}
    day.sort(key=lambda p: -p["value"])

    def take(position, count):
        picked = [p for p in day
                  if position in str(p["eligiblePositions"]).split(",")][:count]
        return picked

    roster = take("C", 3) + take("LW", 3) + take("RW", 3) + take("D", 5) + take("G", 2)
    roster = list({id(p): p for p in roster}.values())

    lineup = optimal_lineup(roster, shape)
    starters = sum(len(s) for s in lineup.values())
    check("the value engine feeds the matcher end to end",
          starters == sum(shape.values()) and lineup_value(lineup) > 0,
          (starters, sum(shape.values()), lineup_value(lineup)))
    check("...and the best goalies are the ones seated",
          {p["fullName"] for p in lineup["G"]}
          == {p["fullName"] for p in sorted(take("G", 2), key=lambda x: -x["value"])[:2]},
          [p["fullName"] for p in lineup["G"]])

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
