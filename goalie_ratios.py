"""
GAA and save percentage in the matchup: where each side's will finish, and the
odds of winning each.

Both are ratios, so neither is a sum of what each start contributes - which is
why the Matchup tab listed them unprojected until 10/7/2026. What is summed is
their parts: goals against over minutes (x 60), and saves over shots against.
A side's final ratio is its parts so far plus what its goalie seats still
project:

- **So far**: the Matchup scrape's GA, SA and SV (or what was typed on Goalie
  Planning), with the minutes worked back out of Yahoo's GAA
  (`goalie_planning.minutes_played`) - or out of the shots faced, for a week
  of shutouts.
- **Still to come**: each seated goalie's per-start line - adjusted for the
  opponent and venue, as the lineups use it - times the odds he starts, and
  58.6 minutes a start (`goalie_planning.AVERAGE_START_MINUTES`).

Goalie Planning works its projected ratios out of the same parts in the same
way (`so_far` is shared), so the two tabs agree.

**The odds need a spread, and a ratio's comes from its parts.** To first order
a final GAA moves by `60/M x (G - r x M)`, where M is the side's minutes and r
its goals per minute, so

    Var(GAA) = (60/M)^2 x Σ [ p x d_GA x ga  +  p(1 - p) x (ga - r x m)^2 ]

over the seats still to come: what happens in a start he makes (goals around
the minutes he plays), and whether he makes it at all. The second term is the
one worth reading - a start that may not happen matters only as far as his GAA
differs from the side's, since a start at exactly the running GAA leaves it
where it was. Save percentage is the same with saves over shots:

    Var(SV%) = Σ [ p x d_SV x sa x q(1 - q)  +  p(1 - p) x (sv - s x sa)^2 ] / SA^2

What has already happened adds no variance, as with the counting categories,
but it dilutes what is to come: one more start moves a GAA built on 300 banked
minutes far less than one built on 60. The two sides are independent, the
margin is taken as normal, and a tie is a coin flip. Being first-order, the
spread is within 10% of a simulation of the same model, a little wide where a
single coin-flip start is most of the week (`test_goalie_ratios`).

**d is measured** on 2025-26's starts by `derive_ratio_dispersion.py`, against
each goalie's own season rate - the convention `matchup_weights` measured the
counting categories by. Goals against around the minutes played run at 0.87 of
Poisson: a pulled goalie stops conceding, and empty-net goals are not his.
Saves around the shots faced run at 0.99 of binomial.

**Then checked where it matters, on whole weeks**: 20,000 random two-goalie
sides drawn from real 2025-26 weeks, each predicted from its goalies' season
rates over the minutes and shots they actually had. The errors, in units of
the predicted spread, came out at 0.98 (GAA) and 1.00 (save percentage) where
1.0 is right, and the odds held bin by bin - on GAA, sides given 45% won 45%
and sides given 64% won 66%; on save percentage, 36% won 36% and 73% won 73%.

That check also says what to expect of these two categories: a week of
goaltending is noisy, and even knowing each goalie's true rate, odds outside
25-75% before a week starts were rare. They firm up as minutes are banked.

**The goalie minimum is applied on top** (`goalie_minimum`): these are the odds
with both sides through it, and a side short of it forfeits. **Not modelled**:
lineups chasing either ratio - the optimiser still values goalies on counting
stats alone.

Author - Jason Druckenmiller
Created - 10/7/2026
Updated - 10/7/2026
"""

import math

import goalie_planning as gp

# Variance per start as a share of Poisson (goals around the minutes played)
# and binomial (saves around the shots faced). Measured over 2025-26 by
# `derive_ratio_dispersion.py`; re-measure between seasons, never mid-season.
GA_DISPERSION = 0.87
SV_DISPERSION = 0.99

RATIOS = ('GAA', 'SVpct')
# What a goalie's line has to carry for the ratios to be built from it. A
# league scoring the ratios alone still projects these, unscored.
PARTS = ('GA', 'SA', 'SV')

# A margin whose remaining spread is nothing - every start banked - is
# decided; this only stops a division by zero.
MIN_SIGMA = 1e-9


def so_far(entered):
    """
    {GA, SA, SV, minutes, minutesFrom} for a side's week so far.

    `entered` is what the page holds - the Matchup scrape's goalie numbers or
    what was typed on Goalie Planning, as {W, GA, SA, SV, SHO, GAA}, strings
    or numbers. Minutes come from GA and GAA, else starts, else shots faced.
    """
    entered = entered if isinstance(entered, dict) else {}
    parts = {c: _number(entered.get(c)) for c in PARTS}
    minutes, source = gp.minutes_played(parts['GA'], entered.get('GAA'),
                                        entered.get('starts'), shots=parts['SA'])
    return {**parts, 'minutes': minutes, 'minutesFrom': source}


def seats(lineups):
    """
    [(start probability, {GA, SA, SV} per start)] for every goalie seated in
    `lineups` ({slot: [player]} each, as the planner sets them).

    A seated goalie's `perGame` is already his per-start line times the odds he
    starts (`goalie_starts.expected_value`), so it is divided back out. A
    goalie with no projection for a part is left out - he cannot be put into a
    ratio he has no numbers for.
    """
    found = []
    for lineup in lineups:
        for players in lineup.values():
            for player in players:
                if 'G' not in str(player.get('positionCode') or '').split(','):
                    continue
                probability = _number(player.get('startProbability'))
                per_game = player.get('perGame') or {}
                if probability <= 0 or any(per_game.get(c) is None for c in PARTS):
                    continue
                found.append((probability,
                              {c: _number(per_game[c]) / probability for c in PARTS}))
    return found


def side(entered, lineups):
    """
    One side's ratios: {GAA: {value, variance, soFar}, SVpct: {...}, minutes,
    minutesFrom, starts}.

    `value` is None when there is nothing to divide - no minutes, or no shots -
    and then so is the variance. `minutes` and `minutesFrom` describe the week
    so far, `starts` the expected starts still to come.
    """
    banked = so_far(entered)
    upcoming = seats(lineups)
    start = gp.AVERAGE_START_MINUTES

    goals = banked['GA'] + sum(p * line['GA'] for p, line in upcoming)
    shots = banked['SA'] + sum(p * line['SA'] for p, line in upcoming)
    saves = banked['SV'] + sum(p * line['SV'] for p, line in upcoming)
    minutes = banked['minutes'] + sum(gp.projected_minutes(p) for p, _line in upcoming)
    before = gp.rates(banked, banked['minutes'])

    gaa = {'value': None, 'variance': None, 'soFar': before['GAA']}
    if minutes > 0:
        rate = goals / minutes
        spread = sum(p * GA_DISPERSION * line['GA']
                     + p * (1 - p) * (line['GA'] - rate * start) ** 2
                     for p, line in upcoming)
        gaa.update(value=goals * gp.GAA_MINUTES / minutes,
                   variance=(gp.GAA_MINUTES / minutes) ** 2 * spread)

    save_pct = {'value': None, 'variance': None, 'soFar': before['SVpct']}
    if shots > 0:
        rate = saves / shots
        spread = 0.0
        for p, line in upcoming:
            stopped = line['SV'] / line['SA'] if line['SA'] > 0 else rate
            spread += (p * SV_DISPERSION * line['SA'] * stopped * (1 - stopped)
                       + p * (1 - p) * (line['SV'] - rate * line['SA']) ** 2)
        save_pct.update(value=rate, variance=spread / shots ** 2)

    return {
        'GAA': gaa,
        'SVpct': save_pct,
        'minutes': banked['minutes'],
        'minutesFrom': banked['minutesFrom'],
        'starts': sum(p for p, _line in upcoming),
    }


def odds(mine, theirs, sign):
    """
    (win probability, contested) for one ratio, or None when either side has
    no value for it. `mine` and `theirs` are `side()` entries for the
    category; `sign` is -1 where lower wins (GAA). `contested` is the same
    `φ(z) / φ(0)` the counting categories report.
    """
    if not mine or not theirs or mine.get('value') is None or theirs.get('value') is None:
        return None
    sigma = max(math.sqrt(mine['variance'] + theirs['variance']), MIN_SIGMA)
    z = sign * (mine['value'] - theirs['value']) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), math.exp(-0.5 * z * z)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number
