"""
Weighting categories by how much they are still in doubt.

Step 5 of docs/OPTIMIZER.md. A head-to-head category league is not won by
maximising production, it is won by maximising **expected categories won**. A
unit of production in a category you are winning by 15 or losing by 15 is
worth nothing; a unit in a category still in reach is worth a great deal.

So a player's value on a given night is

    Σ_cat  projection[cat] × marginal_worth(cat)

where `marginal_worth` is how much one more unit moves the probability of
taking that category. Model the end-of-week margin as roughly normal and that
derivative is the normal density at the current margin, `φ(margin/σ) / σ`.
Ahead by a lot or behind by a lot, the density is tiny. Near a tie it peaks.
That is what makes the engine bench a playmaker for a shooter when points are
safe and shots are not.

**Weight the projected final margin, never the realised one.** This is the
rule that resolves what would otherwise be a contradiction - wanting to write
off a hopeless category on day 1, while not wanting to punt a category
prematurely:

- A category the *projections* say you lose by 3σ is genuinely worth ~0 on
  Monday morning. Downweighting it immediately is correct, not premature.
- A category where you are *currently* down ten hits but the rest-of-week
  projection says it closes is still live, and downweighting it would be the
  premature punt.

Both fall out of one rule as long as the margin fed to φ is
`banked + projected remaining`, not `banked` alone. A floor on the weights
keeps a written-off category from reaching exactly zero, because a 3σ
projection can be wrong and retaining a little weight costs almost nothing.

**The weights depend on the lineups, which depend on the weights.** Marginal
worth is a function of the projected final margin, which is a function of
lineups not yet set. So iterate: flat weights, project, re-weight, re-optimise.
Damped, it settles in two or three passes. This is why the exact matcher had
to come first - a greedy heuristic has no fixed point worth finding.

**The opponent stays on flat weights.** They will play their best players, not
counter-optimise against you, and assuming otherwise makes you exploitable.
Their lineups are therefore set once, outside the loop.

**σ, now measured.** The margin is modelled as normal with variance
`dispersion x (mine + theirs)`, Poisson being `dispersion = 1`. That started as
an assumption for want of data; it has since been checked against a full
2025-26 season of per-game results, by drawing random 12-skater rosters over
real weeks and comparing what they produced against the sum of their own season
rates.

Poisson turns out to be very nearly exact for the scoring categories - goals,
assists, points and power-play points all land between 0.95 and 0.98 - mild for
shots, blocks and hits, and **badly wrong for penalty minutes at 3.64**, which
is lumpy in a way the others are not: most games are zero and a fight is five
at once. `CATEGORY_DISPERSION` carries the measured values.

The distinction that makes those numbers meaningful: an NHL team's weekly total
is overdispersed by a factor of two to nine, but almost entirely because teams
play two to four games a week. This module already knows how many starts it is
projecting - it sums per-game values over the actual lineup - so games-played
variance is not its to model. Conditioned on that, Poisson holds.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import math

from daily_value import RATE_COLUMNS, category_polarity, score
from lineup_utils import optimal_lineup

# Variance of a weekly roster total over its projection, as a multiple of
# Poisson. Measured on the 2025-26 season (see the module docstring); anything
# not listed falls back to Poisson, which is what the scoring categories
# measured at anyway.
CATEGORY_DISPERSION = {
    'G': 1.0, 'A': 1.0, 'P': 1.0, 'PPP': 1.0, 'PPG': 1.0, 'PPA': 1.0,
    'SOG': 1.1,
    'BLK': 1.1,
    'HIT': 1.3,
    # Most games are zero and a fight is five minutes at once. Treating this as
    # Poisson overstates its marginal worth by about 90%, since sigma is nearly
    # double what Poisson predicts.
    'PIM': 3.6,
}

DEFAULT_DISPERSION = 1.0


def dispersion_for(category, dispersion=None):
    """The measured dispersion for a category, or an explicit override."""
    if dispersion is not None:
        return dispersion
    return CATEGORY_DISPERSION.get(category, DEFAULT_DISPERSION)

# A margin's σ can collapse toward zero in a low-volume category, and a
# vanishing σ sends the weight to infinity. Floor it.
MIN_SIGMA = 1e-3

# The smallest weight any category keeps, as a share of the largest. A written
# off category should be cheap, not free: a 3σ projection can be wrong, and
# holding a little weight back costs almost nothing.
WEIGHT_FLOOR = 0.05

# Blend of new weights into old between passes. Undamped iteration can
# oscillate between two lineups that each justify the other's weights.
DAMPING = 0.5

ITERATIONS = 3

_ROOT_TWO_PI = math.sqrt(2.0 * math.pi)


def normal_pdf(x):
    """φ(x), the standard normal density."""
    return math.exp(-0.5 * x * x) / _ROOT_TWO_PI


def margin_sigma(mine, theirs, dispersion=DEFAULT_DISPERSION):
    """
    The standard deviation of `mine - theirs` for one category.

    Poisson, so the variance of each side is its own mean and the two add.
    Absolute values, because a category like +/- can be negative while its
    volatility still comes from the volume of events underneath it.
    """
    variance = dispersion * (abs(mine) + abs(theirs))
    return max(math.sqrt(variance), MIN_SIGMA)


def marginal_worth(margin, sigma):
    """
    How much one more unit is worth: the normal density at the margin.

    Peaks at a tie and decays fast, which is the whole behaviour being bought.
    """
    return normal_pdf(margin / sigma) / sigma


def category_weights(mine, theirs, categories, polarity=None,
                     banked_margin=None, dispersion=None,
                     floor=WEIGHT_FLOOR):
    """
    {category: weight} from what both sides still have to come.

    `mine` and `theirs` are projected *remaining* totals. `banked_margin` is
    the matchup's current score as a raw `mine - theirs` per category, which
    is all the maths needs of it - the projected final margin is what gets
    weighted, never the realised one.

    **Banked production moves the margin but adds no variance**, because it
    has already happened. So σ comes from the remaining totals alone: a
    category five points apart with one night left is far more settled than
    the same five points on Monday, and that is exactly the difference σ
    carries.

    Weights are normalised to a mean absolute value of one. That changes no
    lineup - the matcher only sees relative value - but keeps the numbers
    legible and the arithmetic well conditioned. Rate categories get no
    weight; they are not linear in a player's projection.
    """
    polarity = polarity or category_polarity(categories)
    banked_margin = banked_margin or {}

    raw = {}
    for category in categories:
        if category in RATE_COLUMNS:
            raw[category] = 0.0
            continue

        remaining_mine = mine.get(category, 0.0)
        remaining_theirs = theirs.get(category, 0.0)
        sign = polarity.get(category, 1.0)

        # Signed so a positive margin always means "winning this one",
        # whether the category rewards more of the stat or less.
        margin = sign * (remaining_mine - remaining_theirs
                         + banked_margin.get(category, 0.0))
        sigma = margin_sigma(remaining_mine, remaining_theirs,
                             dispersion_for(category, dispersion))
        raw[category] = sign * marginal_worth(margin, sigma)

    return _normalise(raw, floor)


def project_totals(lineups, categories):
    """
    {category: total} summed over every seated player in every day's lineup.

    `lineups` is an iterable of the {slot: [player]} dicts the matcher
    returns; each player is expected to carry `perGame`, which for a goalie
    should already be scaled by his start probability.
    """
    totals = {category: 0.0 for category in categories}
    for lineup in lineups:
        for seats in lineup.values():
            for player in seats:
                per_game = player.get('perGame') or {}
                for category in categories:
                    if category in RATE_COLUMNS:
                        continue
                    totals[category] += float(per_game.get(category) or 0.0)
    return totals


def optimise_week(days, roster_slots, categories, flat_weights,
                  polarity=None, banked_margin=None, dispersion=None,
                  iterations=ITERATIONS, damping=DAMPING, floor=WEIGHT_FLOOR,
                  seat_all=True):
    """
    Set a week's lineups against the categories that are actually in doubt.

    `days` is [{'date': ..., 'mine': [player], 'theirs': [player]}], each
    player carrying `perGame` - the output of `daily_value.value_players`,
    with goalies already run through `goalie_starts.expected_value`.
    `flat_weights` is the matchup-blind baseline, normally
    `daily_value.default_weights`, used for the first pass and for the
    opponent throughout. `banked_margin` is the matchup's current score as
    `mine - theirs` per category.

    Returns the chosen lineups by date, the weights they were chosen under,
    both sides' projected totals, the projected final margins, and the
    per-pass history - which is what makes a surprising lineup explainable
    rather than merely trusted.
    """
    polarity = polarity or category_polarity(categories)
    banked_margin = banked_margin or {}

    # The opponent is assumed to play his best team, not to counter-optimise,
    # so his lineups are set once and never revisited.
    opponent_lineups = [
        optimal_lineup(_valued(day.get('theirs', []), flat_weights), roster_slots,
                       seat_all=seat_all)
        for day in days
    ]
    theirs = project_totals(opponent_lineups, categories)

    weights = dict(flat_weights)
    history = []
    lineups, mine = {}, {}

    for _ in range(max(1, iterations)):
        by_date = {}
        for day in days:
            by_date[day.get('date')] = optimal_lineup(
                _valued(day.get('mine', []), weights), roster_slots, seat_all=seat_all)

        lineups = by_date
        mine = project_totals(by_date.values(), categories)

        target = category_weights(mine, theirs, categories, polarity=polarity,
                                  banked_margin=banked_margin,
                                  dispersion=dispersion, floor=floor)
        history.append({'weights': dict(weights), 'projected': dict(mine)})
        weights = _blend(weights, target, damping)

    margins = {
        category: polarity.get(category, 1.0) * (
            mine.get(category, 0.0) - theirs.get(category, 0.0)
            + banked_margin.get(category, 0.0))
        for category in categories if category not in RATE_COLUMNS
    }

    return {
        'lineups': lineups,
        'weights': weights,
        'projected': mine,
        'opponent': theirs,
        'margins': margins,
        'history': history,
    }


def _valued(players, weights):
    """Re-score a day's players under the current weights."""
    valued = []
    for player in players:
        row = dict(player)
        row['value'] = score(player.get('perGame') or {}, weights)
        valued.append(row)
    return valued


def _normalise(raw, floor):
    """
    Scale to a mean of one and lift anything below the floor.

    The floor is applied before the scaling it affects, so normalise again
    afterwards rather than leaving the mean drifting with the floor.
    """
    magnitudes = [abs(value) for value in raw.values() if value]
    if not magnitudes:
        return {category: 0.0 for category in raw}

    ceiling = max(magnitudes)
    floored = {}
    for category, value in raw.items():
        if value == 0.0:
            floored[category] = 0.0
            continue
        sign = 1.0 if value > 0 else -1.0
        floored[category] = sign * max(abs(value), ceiling * floor)

    scale = sum(abs(v) for v in floored.values()) / max(
        1, sum(1 for v in floored.values() if v))
    if scale <= 0:
        return floored
    return {category: value / scale for category, value in floored.items()}


def _blend(current, target, damping):
    categories = set(current) | set(target)
    return {
        category: (1 - damping) * current.get(category, 0.0)
                  + damping * target.get(category, 0.0)
        for category in categories
    }
