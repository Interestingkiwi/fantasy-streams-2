"""
The goalie minimum: how likely each side is to reach it, and what falling
short costs.

Yahoo leagues set a minimum of goalie appearances a week (3 by default; read
from each team's Goaltender Appearances box - see `yahoo_matchup`). An
appearance is any game a goalie in an active slot touches the ice, relief
included. **A side short of it at the end of the week forfeits**: in a
categories league its opponent wins every goalie category, and in a points
league its goalie points count as zero. That second rule cuts both ways - a
manager whose first start was a disaster may sit his goalies to miss the
minimum on purpose and turn a negative goalie week into nothing.

**Appearances so far** come from Yahoo's box (the Matchup scrape stores them as
`GP` beside the other goalie numbers), or were typed on Goalie Planning.
Failing both, they are estimated from the week's minutes so far
(`goalie_ratios.so_far`) at 58.6 a start, and said to be estimated.

**Appearances to come** are the goalies seated from today on. On a night, the
goalies seated from one NHL team appear at most once between them - one goalie
starts each game - so each (night, team) is one chance at an appearance, with
probability `min(1, Σ p)`. Those are independent, so the count is
Poisson-binomial, worked out exactly. Relief appearances are not projected:
they count on Yahoo but nobody plans them, so the chance is a little low
rather than a little high.

**What it does to the odds.** With `q` the odds of a category when both sides
reach the minimum,

    P(win) = Pm x Pt x q  +  Pm x (1 - Pt)  +  1/2 x (1 - Pm) x (1 - Pt)

- you win it outright when only the opponent falls short, and lose it when
only you do. Both short is taken as a tie: neither side can win a category it
has forfeited. In a points league, each side's expected points carry its
goalie points times its chance, and the doubt over reaching the minimum adds
`P(1 - P) x goalie points^2` to the variance.

The chance treats meeting the minimum as independent of how the starts go,
which is close but not exact: a side that makes more starts both reaches the
minimum and piles up wins and saves.

Author - Jason Druckenmiller
Created - 10/7/2026
Updated - 10/7/2026
"""

import math
from collections import defaultdict

import daily_value as dv
import goalie_planning as gp
import goalie_starts as gs
import goalie_ratios as gr

# Yahoo's default, for a league whose own minimum has not been read
DEFAULT_MINIMUM = 3


def appearances_so_far(entered):
    """
    (appearances, where from) for a side's week so far: 'yahoo' when given
    (`GP`, from the Goaltender Appearances box or typed), 'estimated' from its
    minutes, or 'none'.
    """
    entered = entered if isinstance(entered, dict) else {}
    given = entered.get('GP')
    if given not in (None, ''):
        try:
            return max(0, int(round(float(given)))), 'yahoo'
        except (TypeError, ValueError):
            pass
    minutes = gr.so_far(entered)['minutes']
    if minutes > 0:
        return int(round(minutes / gp.AVERAGE_START_MINUTES)), 'estimated'
    return 0, 'none'


def chances(lineups):
    """
    [probability] of an appearance for each (night, NHL team) with a goalie
    seated, from `lineups` as {date: {slot: [player]}}. Two goalies seated
    from one team on one night are one chance, since only one can start.
    """
    groups = defaultdict(float)
    for night, lineup in (lineups or {}).items():
        for players in lineup.values():
            for player in players:
                if 'G' not in str(player.get('positionCode') or '').split(','):
                    continue
                team = gs.primary_team(player.get('teamAbbrevs'))
                groups[(night, team)] += _number(player.get('startProbability'))
    return [min(1.0, p) for p in groups.values() if p > 0]


def reach_chance(so_far, upcoming, minimum):
    """P(so_far + appearances still to come >= minimum), exactly."""
    needed = minimum - so_far
    if needed <= 0:
        return 1.0
    if needed > len(upcoming):
        return 0.0
    # distribution[k] = P(k appearances so far among the chances read)
    distribution = [1.0]
    for p in upcoming:
        grown = [0.0] * (len(distribution) + 1)
        for k, weight in enumerate(distribution):
            grown[k] += weight * (1 - p)
            grown[k + 1] += weight * p
        distribution = grown
    return min(1.0, sum(distribution[needed:]))


def side(entered, lineups, minimum):
    """
    {soFar, soFarFrom, toCome, chance} for one side: appearances so far and
    where they came from, the appearances expected from the seats still to
    come, and the chance of reaching `minimum` by the end of the week.
    """
    so_far, source = appearances_so_far(entered)
    upcoming = chances(lineups)
    return {
        'soFar': so_far,
        'soFarFrom': source,
        'toCome': sum(upcoming),
        'chance': reach_chance(so_far, upcoming, minimum),
    }


def category_odds(through, mine, theirs):
    """A goalie category's odds once either side may forfeit (see the docstring)."""
    return mine * theirs * through + mine * (1 - theirs) + 0.5 * (1 - mine) * (1 - theirs)


def is_goalie_category(category):
    return category in dv.GOALIE_CATEGORIES


def minimum_from(raw):
    """The league's minimum from a request: a whole number, 0 for none, None if not sent."""
    if raw in (None, ''):
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return max(0, min(value, 20))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number
