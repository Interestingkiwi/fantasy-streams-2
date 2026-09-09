"""
Tests for the ageing curve.

The claim the whole thing rests on is that restating a season at the age being
projected is a *re-basing*, not a haircut: it has to move an old player down, a
young one up, and a peak-age one hardly at all, using the same curve for all
three. Most of what follows pins that, plus the two places this is easy to get
quietly wrong - signed stats, which must not be scaled at all, and the birthday
arithmetic, which decides whether a player is 40 or 41.

Author - Jason Druckenmiller
Created - 9/9/2026
Updated - 9/9/2026
"""

import ast
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "preseason_db_build"))

import aging                                                # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


SCORING = aging.AGE_INDEX[aging.SCORING]
PERIPHERAL = aging.AGE_INDEX[aging.PERIPHERAL]


# --------------------------------------------------------------------------
print("\n=== 1. the shape of the curve ===")

check("scoring peaks in the mid-twenties",
      24 <= max(SCORING, key=SCORING.get) <= 26,
      max(SCORING, key=SCORING.get))
check("peripherals peak later than scoring - usage outlasts burst",
      max(PERIPHERAL, key=PERIPHERAL.get) > max(SCORING, key=SCORING.get))

check("scoring rises every year up to the peak",
      all(SCORING[age] < SCORING[age + 1] for age in range(aging.MIN_AGE, 24)))
check("scoring falls every year from 26 on",
      all(SCORING[age] > SCORING[age + 1] for age in range(26, aging.MAX_AGE)))

decline = {age: 1 - SCORING[age + 1] / SCORING[age] for age in range(26, aging.MAX_AGE)}
check("the decline gets steeper every year - a 38-year-old loses more than a 30-year-old",
      all(decline[age] < decline[age + 1] for age in range(26, aging.MAX_AGE - 1)))

check("peripherals decline more slowly than scoring at every age past the peak",
      all((1 - PERIPHERAL[age + 1] / PERIPHERAL[age]) < (1 - SCORING[age + 1] / SCORING[age])
          for age in range(27, aging.MAX_AGE)))

# The measurements these were fitted to, so a stray edit to the constants has
# to be a deliberate one. See derive_aging_curve.py for where they come from.
check("a 30-year-old loses about 5% of his scoring",
      0.04 < decline[30] < 0.07, f"{decline[30]:.3f}")
check("a 40-year-old loses about 14%",
      0.12 < decline[40] < 0.17, f"{decline[40]:.3f}")
check("a 30-year-old loses about 1.5% of his peripherals",
      0.005 < (1 - PERIPHERAL[31] / PERIPHERAL[30]) < 0.03,
      f"{1 - PERIPHERAL[31] / PERIPHERAL[30]:.3f}")


# --------------------------------------------------------------------------
print("\n=== 2. re-basing a season, not docking one ===")

check("a season played at the projected age is left exactly alone",
      aging.age_factor(28, 28) == 1.0)
check("an older player's past season is worth less now",
      aging.age_factor(39, 40) < 1.0)
check("a younger player's past season is worth more now",
      aging.age_factor(20, 21) > 1.0)
check("...and a peak-age player's is barely touched",
      abs(aging.age_factor(25, 26) - 1.0) < 0.02, aging.age_factor(25, 26))

check("the further back the season, the harder it is discounted for an old player",
      aging.age_factor(38, 41) < aging.age_factor(39, 41) < aging.age_factor(40, 41) < 1.0)
check("...and the harder it is credited for a young one",
      aging.age_factor(18, 21) > aging.age_factor(19, 21) > aging.age_factor(20, 21) > 1.0)

check("a year of scoring decline costs more than a year of peripheral decline",
      aging.age_factor(37, 38) < aging.age_factor(37, 38, aging.PERIPHERAL) < 1.0)


# --------------------------------------------------------------------------
print("\n=== 2b. growth is damped, decline is not ===")

raw = lambda a, b, g=aging.SCORING: aging.AGE_INDEX[g][b] / aging.AGE_INDEX[g][a]

check("the growth side is credited at less than the curve says",
      aging.age_factor(20, 21) < raw(20, 21),
      f"{aging.age_factor(20, 21):.4f} vs raw {raw(20, 21):.4f}")
check("...by exactly GROWTH_CONFIDENCE of the gain above 1",
      abs(aging.age_factor(20, 21)
          - (1 + aging.GROWTH_CONFIDENCE * (raw(20, 21) - 1))) < 1e-12)
check("the decline side passes through untouched - it is the measured half",
      abs(aging.age_factor(39, 40) - raw(39, 40)) < 1e-12)
check("...at every declining age, on both curves",
      all(abs(aging.age_factor(a, a + 1, g) - raw(a, a + 1, g)) < 1e-12
          for g in (aging.SCORING, aging.PERIPHERAL)
          for a in range(28, aging.MAX_AGE)))

check("damping is a shrink toward no change, so it never flips a credit into a debit",
      aging.age_factor(18, 22) > 1.0)
check("it keeps the ordering - a season further back is still credited more",
      aging.age_factor(18, 22) > aging.age_factor(19, 22) > aging.age_factor(21, 22))

# Damping deliberately breaks the symmetry the curve alone would have: crediting
# a young player up is a weaker claim than marking an old one down, and the
# whole point is to treat them differently.
check("ageing up and back down is no longer a round trip - on purpose",
      aging.age_factor(30, 34) * aging.age_factor(34, 30) < 1.0)

# Guard the seam itself rather than only its current setting.
_confidence = aging.GROWTH_CONFIDENCE
try:
    aging.GROWTH_CONFIDENCE = 1.0
    check("at full confidence the curve is applied exactly as measured",
          abs(aging.age_factor(20, 21) - raw(20, 21)) < 1e-12)
    aging.GROWTH_CONFIDENCE = 0.0
    check("at zero confidence growth is switched off entirely, decline untouched",
          aging.age_factor(20, 21) == 1.0
          and abs(aging.age_factor(39, 40) - raw(39, 40)) < 1e-12)
finally:
    aging.GROWTH_CONFIDENCE = _confidence

check("the damping dial is between off and fully trusted",
      0.0 <= aging.GROWTH_CONFIDENCE <= 1.0)


# --------------------------------------------------------------------------
print("\n=== 3. what the curve refuses to touch ===")

check("plusMinus has no curve - it is signed, and scaling it toward zero "
      "would read as a bad player improving",
      aging.group_for("plusMinus") is None)

# Read the projected stats straight out of the pipeline rather than restating
# them here, so adding a stat there and forgetting it here is a failure, not a
# silently unadjusted column.
engine_source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "preseason_db_build", "calculate_skater_projections.py")
projected_stats = None
for node in ast.walk(ast.parse(open(engine_source, encoding="utf-8").read())):
    if (isinstance(node, ast.Assign) and node.targets
            and getattr(node.targets[0], "id", None) == "stats_to_project"):
        projected_stats = ast.literal_eval(node.value)

check("found the projection engine's stat list", projected_stats is not None)
if projected_stats:
    uncurved = [stat for stat in projected_stats if aging.group_for(stat) is None]
    check("every projected stat is on a curve except plusMinus",
          uncurved == ["plusMinus"], f"uncurved: {uncurved}")
    stray = [stat for stat in aging.STAT_GROUPS if stat not in projected_stats]
    check("and the curve claims no stat the engine does not project",
          not stray, f"stray: {stray}")


# --------------------------------------------------------------------------
print("\n=== 4. unknown ages opt out instead of crashing ===")

check("a missing birthdate means no adjustment", aging.age_factor(None, 30) == 1.0)
check("an unknown target age means no adjustment", aging.age_factor(30, None) == 1.0)
check("season_age of nothing is nothing", aging.season_age(None, 20262027) is None)
check("season_age survives a NaT-shaped value",
      aging.season_age(float("nan"), 20262027) is None)
check("season_age survives a junk season id",
      aging.season_age(datetime.date(1990, 1, 1), "not-a-season") is None)


# --------------------------------------------------------------------------
print("\n=== 5. how old the player actually is ===")

# Age is taken on 1 February of the season's end year - the middle of the season
# rather than either end of it.
check("a July birthday has already happened by February",
      aging.season_age(datetime.date(1995, 7, 4), 20262027) == 31)
check("a September birthday has too - the season is already past new year",
      aging.season_age(datetime.date(1985, 9, 17), 20262027) == 41)
check("a March birthday has not",
      aging.season_age(datetime.date(1995, 3, 7), 20262027) == 31)
check("the reference day itself counts as the birthday having passed",
      aging.season_age(datetime.date(1995, 2, 1), 20262027) == 32)
check("the day after it does not",
      aging.season_age(datetime.date(1995, 2, 2), 20262027) == 31)

check("consecutive seasons are exactly one year apart",
      aging.season_age(datetime.date(1985, 9, 17), 20252026) + 1
      == aging.season_age(datetime.date(1985, 9, 17), 20262027))
check("a datetime works as well as a date",
      aging.season_age(datetime.datetime(1985, 9, 17, 12, 0), 20262027) == 41)


# --------------------------------------------------------------------------
print("\n=== 6. the guardrail ===")

check("ages below the table are clamped, not extrapolated",
      aging.age_factor(10, 24) == aging.age_factor(aging.MIN_AGE, 24))
check("ages above it too",
      aging.age_factor(60, 24) == aging.age_factor(aging.MAX_AGE, 24))
check("no factor escapes the clamp, at any pair of ages, on either curve",
      all(aging.MIN_FACTOR <= aging.age_factor(a, b, group) <= aging.MAX_FACTOR
          for group in (aging.SCORING, aging.PERIPHERAL)
          for a in range(aging.MIN_AGE, aging.MAX_AGE + 1)
          for b in range(aging.MIN_AGE, aging.MAX_AGE + 1)))

# The real three-season reach: the oldest season the engine weights is two years
# back, and that has to stay inside the clamp or it would be silently truncated.
check("the three-season reach for a 41-year-old is inside the clamp, not against it",
      aging.MIN_FACTOR < aging.age_factor(38, 41) < 1.0, aging.age_factor(38, 41))
check("...and for a 21-year-old",
      1.0 < aging.age_factor(18, 21) < aging.MAX_FACTOR, aging.age_factor(18, 21))


# --------------------------------------------------------------------------
print("\n=== 7. against the blend it feeds ===")


def blended(per_game_by_season, target_age, group=aging.SCORING):
    """The engine's 60/30/10 weighting, with and without the age curve."""
    weights = (6, 3, 1)
    raw = sum(value * weight for value, weight in zip(per_game_by_season, weights)) / 10
    aged = sum(value * weight * aging.age_factor(target_age - offset - 1, target_age, group)
               for offset, (value, weight)
               in enumerate(zip(per_game_by_season, weights))) / 10
    return raw, aged


# Ovechkin's real per-game goals for 2025-26, 2024-25 and 2023-24, projected at
# 41. The middle season is the record chase - his best rate of the three, and
# the one the flat blend leans on hardest after last year.
raw, aged = blended([32 / 82, 44 / 65, 31 / 79], 41)
check("a 41-year-old's blended goal rate comes down",
      aged < raw, f"{raw:.3f} -> {aged:.3f}")
check("...by roughly a fifth, not a rounding error and not off a cliff",
      0.15 < 1 - aged / raw < 0.30, f"{1 - aged / raw:.3f}")

hits_raw, hits_aged = blended([134 / 82, 110 / 65, 162 / 79], 41, aging.PERIPHERAL)
check("his hits come down about half as far as his goals - the point of two curves",
      (1 - hits_aged / hits_raw) < 0.6 * (1 - aged / raw),
      f"hits {1 - hits_aged / hits_raw:.3f} vs goals {1 - aged / raw:.3f}")

# Same three rates, same blend, a 21-year-old instead.
young_raw, young_aged = blended([0.35, 0.30, 0.25], 21)
check("a 21-year-old's blended rate goes up", young_aged > young_raw,
      f"{young_raw:.3f} -> {young_aged:.3f}")

flat_raw, flat_aged = blended([0.60, 0.55, 0.50], 26)
check("a 26-year-old's barely moves",
      abs(1 - flat_aged / flat_raw) < 0.02, f"{1 - flat_aged / flat_raw:.4f}")


# --------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {', '.join(FAILURES)}")
    sys.exit(1)
print("All aging checks passed.")
