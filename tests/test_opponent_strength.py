"""
Tests for the opponent-strength adjustment.

Three things carry the suite. That the adjustment is mean-neutral, because an
asymmetric drift would silently bias every matchup projection. That the
per-category directions are right, since goals against and saves respond to
opposite things and getting one backwards is invisible in aggregate. And that
the adjustment is small enough to break ties without reordering tiers - the
"never a fourth-liner over McDavid" requirement, tested directly.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/8/2026
"""

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import opponent_strength as ops                             # noqa: E402
from lineup_utils import optimal_lineup                     # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def team(code, ga=3.0, sa=28.0, pk=0.79, gf=3.0, sf=28.0, played=82):
    return {"teamCode": code, "gamesPlayed": played,
            "goalsAgainstPerGame": ga, "shotsAgainstPerGame": sa,
            "penaltyKillPct": pk, "goalsForPerGame": gf,
            "shotsForPerGame": sf, "powerPlayPct": 0.21}


# A spread wide enough to standardise: one leaky team, one stingy, one average.
POOL = [team("LEAK", ga=3.8, sa=32.0, pk=0.72, gf=3.6, sf=33.0),
        team("MID", ga=3.0, sa=28.0, pk=0.79, gf=3.0, sf=28.0),
        team("WALL", ga=2.4, sa=24.0, pk=0.85, gf=2.5, sf=24.0)]
Z = ops.team_z_scores(POOL)

# --------------------------------------------------------------------------
print("\n=== 1. standardising ===")

check("every team gets a score", set(Z) == {"LEAK", "MID", "WALL"}, sorted(Z))
check("the league average is the zero point",
      abs(statistics.mean(z["goalsAgainstPerGame"] for z in Z.values())) < 1e-9)
check("a leaky team scores above the mean, a stingy one below",
      Z["LEAK"]["goalsAgainstPerGame"] > 0 > Z["WALL"]["goalsAgainstPerGame"],
      (Z["LEAK"]["goalsAgainstPerGame"], Z["WALL"]["goalsAgainstPerGame"]))

check("a stat every team shares has no spread and so no score",
      "goalsAgainstPerGame" not in ops.team_z_scores(
          [team("A"), team("B"), team("C")]).get("A", {}))
check("one team alone cannot be standardised",
      ops.team_z_scores([team("A")]) == {"A": {}})
check("no teams at all is not a crash", ops.team_z_scores([]) == {})

# Regression: the same rates, but only a handful of games behind them.
early = ops.team_z_scores([team("LEAK", ga=3.8, played=4),
                           team("MID", ga=3.0, played=4),
                           team("WALL", ga=2.4, played=4)])
check("few games shrinks the score toward zero",
      abs(early["LEAK"]["goalsAgainstPerGame"]) < abs(Z["LEAK"]["goalsAgainstPerGame"]) / 3,
      (early["LEAK"]["goalsAgainstPerGame"], Z["LEAK"]["goalsAgainstPerGame"]))
check("...so the adjustment fades in rather than switching on",
      abs(ops.multiplier("G", "LEAK", early) - 1.0) < 0.005,
      ops.multiplier("G", "LEAK", early))


# --------------------------------------------------------------------------
print("\n=== 2. direction, per category ===")

check("a leaky defence helps a scorer",
      ops.multiplier("G", "LEAK", Z) > 1.0 > ops.multiplier("G", "WALL", Z),
      (ops.multiplier("G", "LEAK", Z), ops.multiplier("G", "WALL", Z)))
check("a shot-generous defence helps a shooter",
      ops.multiplier("SOG", "LEAK", Z) > 1.0 > ops.multiplier("SOG", "WALL", Z))
check("a WEAK penalty kill helps power-play points, so the sign is flipped",
      ops.multiplier("PPP", "LEAK", Z) > 1.0 > ops.multiplier("PPP", "WALL", Z),
      (ops.multiplier("PPP", "LEAK", Z), ops.multiplier("PPP", "WALL", Z)))

# The case a single blanket multiplier gets backwards.
check("a high-volume opponent means MORE saves",
      ops.multiplier("SV", "LEAK", Z) > 1.0)
check("...and also more goals against, which is the opposite in fantasy terms",
      ops.multiplier("GA", "LEAK", Z) > 1.0)
check("a weak-offence opponent helps a goalie's wins",
      ops.multiplier("W", "WALL", Z) > 1.0 > ops.multiplier("W", "LEAK", Z))

check("hits are left alone, because nothing collected predicts them",
      all(ops.multiplier(c, "LEAK", Z) == 1.0 for c in ops.UNDRIVEN_CATEGORIES),
      [c for c in ops.UNDRIVEN_CATEGORIES if ops.multiplier(c, "LEAK", Z) != 1.0])
check("an unknown opponent changes nothing", ops.multiplier("G", "NOPE", Z) == 1.0)
check("an unknown category changes nothing", ops.multiplier("XYZ", "LEAK", Z) == 1.0)


# --------------------------------------------------------------------------
print("\n=== 3. size and neutrality ===")

extreme = ops.team_z_scores([team("A", ga=8.0), team("B", ga=3.0), team("C", ga=0.5)])
check("no adjustment ever exceeds the cap",
      all(abs(ops.multiplier("G", t, extreme) - 1.0) <= ops.MAX_ADJUSTMENT + 1e-12
          for t in extreme),
      [ops.multiplier("G", t, extreme) for t in extreme])

check("rates are passed through untouched",
      ops.adjust({"SVpct": 0.91, "SV": 25.0}, "LEAK", Z)["SVpct"] == 0.91)
check("counting categories are scaled",
      ops.adjust({"SV": 25.0}, "LEAK", Z)["SV"] != 25.0)
check("an empty line is not a crash", ops.adjust(None, "LEAK", Z) == {})

facing = ops.opponents_on([("2027-01-04", "TOR", "MTL")])
check("a fixture is readable from either side",
      facing["2027-01-04"] == {"TOR": "MTL", "MTL": "TOR"}, facing)


# --------------------------------------------------------------------------
print("\n=== 4. it breaks ties without reordering tiers ===")

# The requirement in one test: the adjustment may decide between near-equals,
# and must never overturn a real gap.
def seats(star_value, scrub_value, star_opponent, scrub_opponent):
    star = {"fullName": "Star", "eligiblePositions": "C",
            "value": star_value * ops.multiplier("G", star_opponent, Z)}
    scrub = {"fullName": "Scrub", "eligiblePositions": "C",
             "value": scrub_value * ops.multiplier("G", scrub_opponent, Z)}
    lineup = optimal_lineup([star, scrub], {"C": 1})
    return lineup["C"][0]["fullName"]

check("a tier apart, the best possible matchup cannot flip the pick",
      seats(10.0, 6.0, "WALL", "LEAK") == "Star")
check("...even at the very extremes of the league",
      seats(10.0, 9.5, "WALL", "LEAK") == "Star")
check("but a near-tie can be decided by the matchup",
      seats(10.0, 9.95, "WALL", "LEAK") == "Scrub",
      "a 0.5% gap should yield to a ~6% swing in matchups")


# --------------------------------------------------------------------------
print("\n=== 5. the real 2025-26 season ===")

try:
    from db import engine, text

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text(
            'SELECT * FROM team_stats WHERE "statWindow" = :w'), {"w": "season"})]

    check("a scraped season is in the database", len(rows) == 32, len(rows))

    scores = ops.team_z_scores(rows)
    check("every team standardised", len(scores) == 32, len(scores))

    # The one that would silently bias every matchup if it broke.
    for category in ("G", "SOG", "PPP", "W", "SV", "GA"):
        mean = statistics.mean(ops.multiplier(category, t, scores) for t in scores)
        check(f"{category} is mean-neutral across the league",
              abs(mean - 1.0) < 1e-6, mean)

    scoring = {t: ops.multiplier("G", t, scores) for t in scores}
    spread = max(scoring.values()) - min(scoring.values())
    check("the real spread lands inside the cap rather than sitting on it",
          spread < 2 * ops.MAX_ADJUSTMENT, spread)
    check("...but is big enough to be worth computing",
          spread > 0.02, spread)

    # Sanity against the actual season: the worst defences should be the
    # easiest to score on.
    easiest = max(scoring, key=scoring.get)
    hardest = min(scoring, key=scoring.get)
    by_ga = {row["teamCode"]: row["goalsAgainstPerGame"] for row in rows}
    check("the easiest opponent to score on is the one allowing most goals",
          by_ga[easiest] == max(by_ga.values()), (easiest, by_ga[easiest]))
    check("...and the hardest is the one allowing fewest",
          by_ga[hardest] == min(by_ga.values()), (hardest, by_ga[hardest]))

    check("hits stay untouched against every real team",
          all(ops.multiplier("HIT", t, scores) == 1.0 for t in scores))

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
