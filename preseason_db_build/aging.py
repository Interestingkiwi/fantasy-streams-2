"""
How a player's rates move with age, and by how much.

The projection engine blends the last three seasons and treats them as
interchangeable, which quietly assumes a player is the same player at 38 as he
was at 36. He is not, and neither is the 21-year-old whose age-19 season is
still being averaged in at face value. The blend therefore leans a fading
veteran up and a rising kid down, and it leans hardest exactly where a season
is furthest from the one being projected.

The fix is to restate each historical season as what it would be worth *at next
season's age* before it is averaged, so the weighted mean is a mean of
comparable numbers. Everything here is that restatement: an index of production
by age, and the ratio between two of its entries.

The curve is measured from this repo's own `historic_skaters_baseline` by
`derive_aging_curve.py` - the delta method, pairing each player's consecutive
seasons and comparing his per-game rates against himself, which is what removes
the talent differences a raw age-vs-production plot is swamped by. See that
script for the derivation and the numbers it prints.

What it found, over 2,388 season pairs:

  * Scoring falls by a share that grows steadily with age - about 5.5% a year at
    30, 10% at 35, 14% at 40. In logs that is a straight line in age, and it
    holds from 24 all the way to 38, which is as far as the sample supports.
  * Peripherals (hits, blocks, PIM, faceoffs) fade far more slowly - 2% a year
    at 30, 7% at 40. They are usage, not burst, and usage survives.
  * Growth before 24 is steep and does not fit the same line: a 20-year-old
    gains ~11% on the way to 21. Only half of that is actually credited - see
    GROWTH_CONFIDENCE, the one judgement call in this file.

Two checks that the number is real rather than an artefact:

  * **League drift.** Scoring league-wide moves a few percent a season, which
    the delta method would otherwise book as ageing. Dividing each pair by its
    own season-to-season league ratio moved the slope from -0.00983 to -0.00982,
    i.e. not at all, so drift is not what is being measured.
  * **Survivorship.** Only players who play again appear in a pair, and the ones
    who do are the ones who held up. Requiring 20 games in both seasons, 10 in
    the first and 1 in the second, or 40 in the first and 1 in the second, gives
    slopes of -0.00982, -0.00981 and -0.00987. The threshold does not matter.

Survivorship *is* visible past 35, where the year-over-year survival rate drops
from ~85% to 56-68% and the measured decline flattens out (n=10 at 38, n=5 at
39). That flattening is the bias, not a real reprieve, so the fitted line is
extrapolated through the tail rather than the thin measurements being taken at
face value.

Games played are deliberately not aged. Among skaters who were regulars, mean
games the following season does not fall with age - it sits between 63 and 70
from 21 right through 38, because the old players still in the league are the
durable ones. There is no age penalty in the data to apply, and the projection
already reads durability off each player's own history, which is the better
signal anyway.

Goalies are deliberately not aged either. The same measurement on
`historic_goalies_baseline` returns a save percentage falling ~4 points a year
at *every* age from 24 to 36 - that is the league-wide save percentage decline
over these seasons, not ageing, and with 230 pairs there is nothing left to
separate the two. Where age really reaches a goalie is his workload, and
workload already comes from the editorial override table rather than a curve.

Author - Jason Druckenmiller
Created - 9/9/2026
Updated - 9/9/2026
"""

import math
from datetime import date

# Age is taken on 1 February of the season's end year - the midpoint of a
# season, so a player is described by the age he spent most of it at rather
# than the one he opened or closed it with.
AGE_REFERENCE_MONTH = 2
AGE_REFERENCE_DAY = 1

# The index is built over this span. Nobody projected falls outside it, and
# clamping stops a junk birthdate extrapolating the fitted line somewhere absurd.
MIN_AGE = 18
MAX_AGE = 44

# Fitted on ages 24-38: log of the year-over-year rate ratio, linear in age,
# weighted by pair count, league-detrended. Re-derive with derive_aging_curve.py.
SCORING_SLOPE, SCORING_INTERCEPT = -0.00982, 0.23843
PERIPHERAL_SLOPE, PERIPHERAL_INTERCEPT = -0.00568, 0.15100

# Below 24 the fitted line is wrong - it reads the growth years as a mild
# decline - so these are the measured ratios instead. Ages 20 through 23 are
# taken as measured (48 to 191 pairs each). The 19->20 bucket holds 14 pairs and
# the 18->19 bucket 5, so their 1.25 and 1.17 are smoothed toward the better
# sampled years rather than carried across at face value.
SCORING_GROWTH_RATIOS = {18: 1.18, 19: 1.15, 20: 1.107, 21: 1.107, 22: 1.063, 23: 1.075}

# Peripherals need no such special case: measured growth before 24 sits within
# noise of the fitted line, which already peaks around 26 rather than 24.

# How much of the modelled *growth* to actually credit. 1.0 applies the curve as
# measured; lower keeps the shape and shortens the reach.
#
# This one is a judgement call, and the only number here that is not a
# measurement - so it gets said plainly rather than dressed up. Two things were
# checked before setting it, and only one of them supports damping:
#
#   * Growth really is mildly right-skewed. The median young player gains less
#     than the pooled ratio says: median/pooled is 0.951 across ages 19-23
#     against 0.970 at 26-32 and 0.973 at 34+. A projection wants the typical
#     outcome, not one pulled up by breakouts - but that is a ~2% effect, and it
#     is nowhere near the whole of this factor.
#   * Growth is *not* more variable than decline, which is what would have
#     justified shrinking it harder. The spread of year-over-year point ratios
#     is flat with age: sd 0.417 at 19-23, 0.411 at 26-32, 0.386 at 34-39. The
#     obvious argument for damping does not survive being measured.
#
# So the rest is deliberate conservatism about the thinnest part of the model.
# The growth ratios rest on 5 to 191 pairs against ~200 a year through the
# decline, they compound hardest over the seasons furthest back, and 24 of the
# players they lift have only one NHL season to lift. Undershooting a breakout
# costs a pick; overshooting one costs a roster spot in round three.
#
# The decline side is untouched at 1.0 - it is measured, corroborated, and was
# the point of the exercise.
GROWTH_CONFIDENCE = 0.5

# A guardrail, not a tuning knob. Three seasons of decline off a 40-year-old
# lands near 0.64 and is meant to; anything outside this range is a bad
# birthdate, not a player.
MIN_FACTOR, MAX_FACTOR = 0.50, 1.75

SCORING = "scoring"
PERIPHERAL = "peripheral"

# Which curve each projected stat follows. plusMinus is deliberately absent:
# it is signed, and scaling a negative plus-minus by 0.85 would *improve* it.
STAT_GROUPS = {
    "goals": SCORING,
    "assists": SCORING,
    "points": SCORING,
    "ppGoals": SCORING,
    "ppPoints": SCORING,
    "shGoals": SCORING,
    "shPoints": SCORING,
    "shots": SCORING,
    "hits": PERIPHERAL,
    "blockedShots": PERIPHERAL,
    "penaltyMinutes": PERIPHERAL,
    "totalFaceoffs": PERIPHERAL,
    "totalFaceoffWins": PERIPHERAL,
    "totalFaceoffLosses": PERIPHERAL,
}


def _step(age, slope, intercept, growth=None):
    """Rate ratio from `age` to `age` + 1."""
    if growth and age in growth:
        return growth[age]
    return math.exp(slope * age + intercept)


def _build_index(slope, intercept, growth=None):
    """Production index by age, anchored at 1.0 on age 24.

    Only ratios between entries are ever used, so the anchor is arbitrary; 24
    is chosen because it is where the scoring curve turns over.
    """
    index = {24: 1.0}
    for age in range(24, MAX_AGE):
        index[age + 1] = index[age] * _step(age, slope, intercept, growth)
    for age in range(24, MIN_AGE, -1):
        index[age - 1] = index[age] / _step(age - 1, slope, intercept, growth)
    return index


AGE_INDEX = {
    SCORING: _build_index(SCORING_SLOPE, SCORING_INTERCEPT, SCORING_GROWTH_RATIOS),
    PERIPHERAL: _build_index(PERIPHERAL_SLOPE, PERIPHERAL_INTERCEPT),
}


def season_age(birth_date, season_id):
    """Age on 1 February of the year the season `season_id` ends in.

    `season_id` is the NHL's 8-digit form, e.g. 20252026. Returns None when the
    birthdate is missing, which is how a player with no bios row opts out of the
    adjustment rather than stopping the run.
    """
    # `!=` against itself catches NaN and pandas' NaT without importing pandas,
    # which keeps this module pure enough to test on its own.
    if birth_date is None or birth_date != birth_date:
        return None

    try:
        end_year = int(str(int(season_id))[4:])
    except (TypeError, ValueError):
        return None

    born = birth_date.date() if hasattr(birth_date, "date") else birth_date
    if not isinstance(born, date):
        return None

    reference = date(end_year, AGE_REFERENCE_MONTH, AGE_REFERENCE_DAY)
    age = reference.year - born.year
    if (reference.month, reference.day) < (born.month, born.day):
        age -= 1
    return age


def age_factor(from_age, to_age, group=SCORING):
    """What a rate produced at `from_age` is worth at `to_age`.

    Above 1.0 for a player still growing into the target age, below it for one
    ageing out of it. Returns 1.0 - no adjustment - when either age is unknown.
    """
    if from_age is None or to_age is None:
        return 1.0

    index = AGE_INDEX.get(group)
    if index is None:
        return 1.0

    start = index[_clamp_age(from_age)]
    if start <= 0:
        return 1.0

    factor = index[_clamp_age(to_age)] / start

    # Only the upward half is damped. A factor above 1 means this season is
    # being credited *up* toward a later, better age - which is the growth
    # curve, and the half of the model that is thin. Decline passes through.
    if factor > 1.0:
        factor = 1.0 + GROWTH_CONFIDENCE * (factor - 1.0)

    return min(MAX_FACTOR, max(MIN_FACTOR, factor))


def group_for(stat):
    """The curve a projected stat follows, or None if it is not aged at all."""
    return STAT_GROUPS.get(stat)


def _clamp_age(age):
    return int(min(MAX_AGE, max(MIN_AGE, round(age))))
