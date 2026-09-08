"""
Tests for the opponent-strength adjustment.

Four things carry the suite. That the adjustment is mean-neutral, because an
asymmetric drift would silently bias every matchup projection. That the
per-category directions are right, since goals against and saves respond to
opposite things and getting one backwards is invisible in aggregate. That the
adjustment is small enough to break ties without reordering tiers - the "never
a fourth-liner over McDavid" requirement, tested directly. And that venue and
opponent strength do not double-count the league-wide home advantage, which is
the one way combining them could quietly go wrong.

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
print("\n=== 5. home ice ===")

SPLIT_ROWS = (
    [dict(r, statWindow="season") for r in POOL]
    + [dict(r, statWindow="season-home", goalsForPerGame=r["goalsForPerGame"] * 1.02,
            goalsAgainstPerGame=r["goalsAgainstPerGame"] * 0.98,
            shotsForPerGame=r["shotsForPerGame"] * 1.02,
            shotsAgainstPerGame=r["shotsAgainstPerGame"] * 0.98,
            wins=24, gamesPlayed=41) for r in POOL]
    + [dict(r, statWindow="season-road", goalsForPerGame=r["goalsForPerGame"] * 0.98,
            goalsAgainstPerGame=r["goalsAgainstPerGame"] * 1.02,
            shotsForPerGame=r["shotsForPerGame"] * 0.98,
            shotsAgainstPerGame=r["shotsAgainstPerGame"] * 1.02,
            wins=17, gamesPlayed=41) for r in POOL]
)
for row in SPLIT_ROWS:
    row.setdefault("wins", 41)
VENUE = ops.venue_multipliers(SPLIT_ROWS)

check("venue effects are derived from the scraped splits",
      abs(VENUE["goalsForPerGame"]["home"] - 1.02) < 1e-9, VENUE.get("goalsForPerGame"))
check("scoring is better at home than away",
      ops.venue_multiplier("G", True, VENUE) > 1.0
      > ops.venue_multiplier("G", False, VENUE))
check("a goalie allows fewer goals at home",
      ops.venue_multiplier("GA", True, VENUE) < 1.0)
check("...and makes fewer saves, because he faces fewer shots",
      ops.venue_multiplier("SV", True, VENUE) < 1.0)
check("...but wins more often",
      ops.venue_multiplier("W", True, VENUE) > 1.0,
      ops.venue_multiplier("W", True, VENUE))
check("an inverted driver flips the split, so losses fall at home",
      ops.venue_multiplier("L", True, VENUE) < 1.0
      and ops.venue_multiplier("SHO", True, VENUE) > 1.0,
      (ops.venue_multiplier("L", True, VENUE), ops.venue_multiplier("SHO", True, VENUE)))
check("categories with no venue driver are untouched",
      all(ops.venue_multiplier(c, True, VENUE) == 1.0 for c in ops.UNDRIVEN_CATEGORIES))
check("with no splits scraped it degrades to venue-blind, not to nothing",
      ops.venue_multiplier("G", True, {}) == 1.0
      and ops.venue_multipliers([dict(r, statWindow="season") for r in POOL]) == {})

check("a visiting opponent is judged on its road record",
      ops.OPPONENT_WINDOW[True] == "season-road"
      and ops.OPPONENT_WINDOW[False] == "season-home")
SPLITS = ops.split_z_scores(SPLIT_ROWS)
check("each window is standardised separately",
      set(SPLITS) == {"season", "season-home", "season-road"}, sorted(SPLITS))
check("the split z table is chosen by where the game is",
      ops.opponent_z_for(True, SPLITS) is SPLITS["season-road"])
check("...falling back to the plain season window when splits are missing",
      ops.opponent_z_for(True, {"season": Z}) is Z)

check("omitting venue leaves the old opponent-only behaviour intact",
      ops.adjust({"G": 1.0}, "LEAK", Z)["G"]
      == ops.adjust({"G": 1.0}, "LEAK", Z, is_home=None, venue=VENUE)["G"])
check("including it moves the line further",
      ops.adjust({"G": 1.0}, "LEAK", Z, is_home=True, venue=VENUE)["G"]
      > ops.adjust({"G": 1.0}, "LEAK", Z)["G"])

# The property that makes combining the two safe. Because an opponent's z is
# standardised inside its own split, averaging over every opponent leaves
# exactly the venue multiplier - the league-wide home effect is counted once.
for category in ("G", "SOG", "GA", "SV"):
    for at_home in (True, False):
        table = ops.opponent_z_for(at_home, SPLITS)
        combined = [ops.adjust({category: 1.0}, t, table,
                               is_home=at_home, venue=VENUE)[category] for t in table]
        expected = ops.venue_multiplier(category, at_home, VENUE)
        check(f"{category} {'at home' if at_home else 'away'}: venue counted once, not twice",
              abs(statistics.mean(combined) - expected) < 1e-9,
              (statistics.mean(combined), expected))

check("a balanced season of home and road is venue-neutral overall",
      all(abs((ops.venue_multiplier(c, True, VENUE)
               + ops.venue_multiplier(c, False, VENUE)) / 2 - 1.0) < 0.002
          for c in ("G", "SOG", "GA", "SV")))


# --------------------------------------------------------------------------
print("\n=== 6. the real 2025-26 season ===")

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

    with engine.connect() as conn:
        every = [dict(r._mapping) for r in conn.execute(text("SELECT * FROM team_stats"))]

    real_venue = ops.venue_multipliers(every)
    check("the home/road windows were scraped too",
          set(real_venue) >= {"goalsForPerGame", "winRate"}, sorted(real_venue))
    check("real home advantage in scoring is a couple of per cent",
          1.01 < real_venue["goalsForPerGame"]["home"] < 1.05,
          real_venue["goalsForPerGame"])
    check("real home advantage in winning is the larger effect",
          real_venue["winRate"]["home"] > real_venue["goalsForPerGame"]["home"],
          (real_venue["winRate"], real_venue["goalsForPerGame"]))
    check("home and road straddle one, so a full season is unbiased",
          all(abs((e["home"] + e["road"]) / 2 - 1.0) < 0.005
              for e in real_venue.values()),
          real_venue)

    # And the same no-double-count property, on the real league.
    real_splits = ops.split_z_scores(every)
    for at_home in (True, False):
        table = ops.opponent_z_for(at_home, real_splits)
        combined = [ops.adjust({"G": 1.0}, t, table, is_home=at_home,
                               venue=real_venue)["G"] for t in table]
        check(f"real league, {'home' if at_home else 'road'}: venue counted once",
              abs(statistics.mean(combined)
                  - ops.venue_multiplier("G", at_home, real_venue)) < 1e-9,
              statistics.mean(combined))

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
