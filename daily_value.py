"""
What a player is worth in one game, in a particular league.

Turns `final_projections` season rows into the per-game, per-category values
the lineup matcher scores against. Step 2 of the build order in
docs/OPTIMIZER.md; `lineup_utils.optimal_lineup` is the consumer.

**Three things the draft board does that a lineup engine must not.** All three
are in `ranking_utils`, all three are right for a draft, and all three are
wrong here:

- *Centering.* A z-score compares a player to the average player. A lineup
  compares him to an empty slot, which produces nothing, so the origin has to
  be real zero. Centered values would make half the league "negative" and, once
  `seat_all=False` is in play, get them benched for being ordinary.
- *Capping.* Clipping outliers stops one elite category running away with a
  ranking. But §5 will multiply these values by a category's marginal worth and
  compare the result against a real projected margin, and a clipped value is no
  longer in the units that comparison needs.
- *Replacement level.* It prices the opportunity cost of a roster spot. In a
  lineup the alternative to starting someone is another player already on the
  roster, and the matcher enforces positional scarcity exactly, as a hard
  constraint - subtracting it here would count scarcity twice.

So a category-league value is `Σ polarity × per_game / σ`: scaled so the
categories are commensurable, but not centered and not capped. A points-league
value is `Σ points_per × per_game`, which is fantasy points per game.

One honest caveat on that zero. Under these default weights a value goes
negative only when a player's harmful categories outweigh his helpful ones -
a goalie in a league that scores GA but not saves, say - which is the right
answer but a coarse one, because σ here is the spread *across players* rather
than the volatility of a weekly team total. The zero becomes properly
meaningful when §5 supplies real marginal worth; until then treat
`seat_all=False` as a blunt instrument.

**The two league modes differ only in their weights.** `score()` takes a weight
per category either way, and §5 replaces those weights with matchup-aware ones
without touching anything else.

**Per game, not per season.** Durability is deliberately absent: for tonight's
lineup, a player who plays 40 games a year is worth exactly what he produces on
the nights he plays. Goalie rates are per *start*, so §3 can multiply them by a
start probability.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import logging
import math

log = logging.getLogger(__name__)

# Yahoo's category codes against the projection columns that carry them.
# Counting stats are divided by games; the rates are not.
COUNTING_COLUMNS = {
    'G': 'proj_goals',
    'A': 'proj_assists',
    'P': 'proj_points',
    '+/-': 'proj_plusMinus',
    'PIM': 'proj_penaltyMinutes',
    'PPG': 'proj_ppGoals',
    'PPP': 'proj_ppPoints',
    'SHG': 'proj_shGoals',
    'SHP': 'proj_shPoints',
    'SOG': 'proj_shots',
    'HIT': 'proj_hits',
    'BLK': 'proj_blockedShots',
    'FW': 'proj_totalFaceoffWins',
    'FL': 'proj_totalFaceoffLosses',
    'FOT': 'proj_totalFaceoffs',
    'W': 'proj_wins',
    'L': 'proj_losses',
    'OTL': 'proj_otLosses',
    'GA': 'proj_goalsAgainst',
    'SA': 'proj_shotsAgainst',
    'SV': 'proj_saves',
    'SHO': 'proj_shutouts',
    'GS': 'proj_gamesStarted',
    'TOI': 'proj_timeOnIce',
}

# Categories the table carries only as a difference of two columns.
DERIVED_COLUMNS = {
    'PPA': ('proj_ppPoints', 'proj_ppGoals'),
    'SHA': ('proj_shPoints', 'proj_shGoals'),
}

# Ratios. Carried per player so §5 can give them the first-order treatment it
# needs, but kept out of the scalar: adding a start moves numerator and
# denominator together, so their contribution is not linear in the projection
# and summing them would be wrong rather than merely imprecise.
RATE_COLUMNS = {
    'GAA': 'proj_goalsAgainstAverage',
    'SVpct': 'proj_savePct',
}

# Categories the projection pipeline does not model at all. Named rather than
# silently skipped, because 8 of the 25 imported leagues score GWG.
UNMODELLED = frozenset({'GWG'})

# Lower is better.
NEGATIVE_CATEGORIES = frozenset({'GA', 'L', 'OTL', 'FL', 'GAA'})

# Categories only goalies record, which is also the pool their σ comes from.
GOALIE_CATEGORIES = frozenset({
    'W', 'L', 'OTL', 'GA', 'SA', 'SV', 'SHO', 'GS', 'TOI', 'GAA', 'SVpct',
})

# Sample floors for the σ baseline. A player below the floor still gets a
# value; he just does not get a vote on how wide the category is. Goalies get
# a lower one because a 40-start season is already a clear starter, and
# calibrating σ on starters alone would understate the spread.
SKATER_BASELINE_GAMES = 40
GOALIE_BASELINE_STARTS = 20


def supported(categories):
    """
    Split a league's categories into (scored, rates, missing).

    `scored` reach the scalar, `rates` are carried but not summed, `missing`
    are not in the projection table at all. Callers should surface the last
    two rather than quietly ranking against a partial category set.
    """
    scored, rates, missing = [], [], []
    for category in categories:
        if category in RATE_COLUMNS:
            rates.append(category)
        elif category in COUNTING_COLUMNS or category in DERIVED_COLUMNS:
            scored.append(category)
        else:
            missing.append(category)
    return scored, rates, missing


def per_game(player, categories):
    """
    {category: per-game value} for one player.

    Goalie counting stats divide by projected starts rather than projected
    appearances, so §3 can multiply the result by a start probability. A
    category the player does not record - a skater's saves - is absent from
    the result rather than zero, which is what keeps him out of that
    category's σ pool.
    """
    games = _games_for(player)
    values = {}

    for category in categories:
        raw = _raw(player, category)
        if raw is None:
            continue
        if category in RATE_COLUMNS:
            values[category] = raw
        elif games > 0:
            values[category] = raw / games
        else:
            values[category] = 0.0

    return values


def category_scales(players, categories):
    """
    {category: σ of its per-game values}, over players with enough games.

    σ is what makes goals and blocked shots commensurable. It is taken over
    the players who actually record the category, so a skater's absent save
    total cannot flatten the goalie spread.
    """
    samples = {category: [] for category in categories}

    for player in players:
        games = _games_for(player)
        values = per_game(player, categories)
        for category, value in values.items():
            floor = (GOALIE_BASELINE_STARTS if category in GOALIE_CATEGORIES
                     else SKATER_BASELINE_GAMES)
            if games >= floor:
                samples[category].append(value)

    scales = {}
    for category, values in samples.items():
        scales[category] = _stdev(values)
    return scales


def category_polarity(categories, pim_positive=False):
    """
    {category: +1 or -1}. PIM is the one a league really does flip, so it is a
    parameter rather than a constant - the draft page already carries the
    setting as `fs_pimPolarity`.
    """
    polarity = {}
    for category in categories:
        if category == 'PIM':
            polarity[category] = 1.0 if pim_positive else -1.0
        else:
            polarity[category] = -1.0 if category in NEGATIVE_CATEGORIES else 1.0
    return polarity


def default_weights(scales, polarity):
    """
    The weights that make `score()` a z-sum: `polarity / σ` per category.

    §5 replaces these with `polarity × φ(margin/σ) / σ` - the marginal worth of
    a unit in a category whose outcome is still in doubt - which is the same
    shape with a matchup term in front. A category with no spread gets zero
    weight rather than an infinity.
    """
    weights = {}
    for category, scale in scales.items():
        weights[category] = (polarity.get(category, 1.0) / scale) if scale > 0 else 0.0
    return weights


def points_weights(points_per_category, categories=None):
    """Weights for a points league: the league's own points-per-stat."""
    wanted = set(categories) if categories is not None else set(points_per_category)
    return {category: float(value or 0.0)
            for category, value in points_per_category.items()
            if category in wanted}


def score(values, weights):
    """Collapse {category: per-game value} to one number. Rates score 0."""
    return sum(value * weights.get(category, 0.0)
               for category, value in values.items()
               if category not in RATE_COLUMNS)


def value_players(players, categories, weights=None, pim_positive=False,
                  value_key='value'):
    """
    Attach a per-game category breakdown and a scalar value to every player.

    Returns new dicts - the inputs are not modified - each carrying `perGame`
    alongside the original columns, plus `value_key`. Pass `weights` for a
    points league (`points_weights`) or to override the default z-sum weights;
    omit it and the weights come from the pool's own σ.
    """
    scored, rates, missing = supported(categories)
    if missing:
        log.warning("No projection for %s - scored as zero.", ", ".join(sorted(missing)))
    active = scored + rates

    if weights is None:
        polarity = category_polarity(active, pim_positive=pim_positive)
        weights = default_weights(category_scales(players, active), polarity)

    valued = []
    for player in players:
        values = per_game(player, active)
        row = dict(player)
        row['perGame'] = values
        row[value_key] = score(values, weights)
        valued.append(row)
    return valued


def _games_for(player):
    """
    Projected games, or projected starts for a goalie.

    Goalie counting stats are wanted per start, and the two differ - a backup
    projected for 54 appearances may be projected for 51 starts.
    """
    starts = player.get('proj_gamesStarted')
    if starts is not None and _number(starts) > 0 and _is_goalie(player):
        return _number(starts)
    return _number(player.get('projectedGames'))


def _is_goalie(player):
    return 'G' in str(player.get('positionCode') or '').split(',')


def _raw(player, category):
    """The season total for a category, or None if the player does not record it."""
    if category in DERIVED_COLUMNS:
        whole, part = DERIVED_COLUMNS[category]
        if player.get(whole) is None and player.get(part) is None:
            return None
        return _number(player.get(whole)) - _number(player.get(part))

    column = COUNTING_COLUMNS.get(category) or RATE_COLUMNS.get(category)
    if column is None:
        return None

    value = player.get(column)
    return None if value is None else _number(value)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


def _stdev(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)
