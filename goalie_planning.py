"""
Whether one more goalie start is worth the risk.

The question this answers is the one a manager asks on Saturday morning with
the goalie minimum already met: **starting him again can only help wins and
saves, but it can lose GAA and save percentage.** How bad a game would it take
to lose them, and how likely is that?

Everything is tied to the projections rather than to generic numbers, which is
what the old site used. A start is described by the shots the goalie is
projected to face *that night, against that opponent, at that venue* (the
`opponent_strength` adjustment the lineups already use) and by his own
projected save percentage. Goals against are then binomial: each shot is a
save or a goal. That gives an honest distribution instead of a hand-written
"good game / bad game" table - a start against a shot-heavy opponent really is
a different risk from one against a defensive team, and this says so.

**Ratios need minutes, and Yahoo's matchup page does not show them.** It shows
GA and GAA, and `GAA = GA x 60 / TOI`, so `TOI = 60 x GA / GAA` recovers them
exactly - see `minutes_played`. What comes back already carries every pull,
empty net and overtime minute, because Yahoo worked its GAA out from the real
ones. With nothing conceded yet there is nothing to divide, and the caller
falls back to counting starts at the measured average below.

**A start is not 60 minutes, and nothing here assumes it is.** Measured over
the 2,624 starts of 2025-26 in `player_game_stats`, the table the nightly job
fills: the mean is **58.6 minutes**, 24% of starts run past 60 into overtime,
5.6% end before 55, and the shortest was two and a half minutes. Minutes also
move with how the night goes - 59.8 on a one or two goal night, 56.6 on a five
- so `MINUTES_BY_GOALS` carries the measured curve and every outcome is
divided by its own minutes rather than by a flat hour. Projected starts use
the 58.6 average, not 60.

**A pull is a variant, not an extra outcome.** The 147 starts that ended
before 55 minutes averaged 30.3 minutes, 3.7 goals and 15.7 shots. It is
offered beside the distribution carrying that measured 5.6% share rather than
inside it, because a pulled night is one of the bad rows and not another one.
Those 30 minutes are what makes a blow-up cost GAA far more than it costs save
percentage.

**Nothing here predicts a hot goalie.** Form does not carry (see *Hot goalies
do not stay hot*): the save percentage used is his projection, adjusted for
the opponent, and that is the whole model.

Author - Jason Druckenmiller
Created - 9/22/2026
Updated - 9/22/2026
"""

import math

# Minutes a start really lasts, by the goals it conceded, measured over
# 2025-26. A shutout is the short one because nothing sends it to overtime and
# an injury-shortened start concedes nothing. Re-measure between seasons, as
# with every other calibration in this repo.
MINUTES_BY_GOALS = {0: 57.6, 1: 59.8, 2: 59.8, 3: 58.7, 4: 57.3, 5: 56.6, 6: 57.3, 7: 59.1}
AVERAGE_START_MINUTES = 58.6      # every start, however it went
GAA_MINUTES = 60.0                # the hour GAA is quoted per, not a start's length

# A start that ended early: the average of the 147 that ran under 55 minutes.
PULL_MINUTES = 30.3
PULL_GOALS = 4
PULL_SHOT_SHARE = 0.59            # 15.7 shots against a typical 26.6
PULL_SHARE = 0.056                # how often a start ends this way at all

# Goals against a start is described by, before the tail folds into the last row.
MAX_GOALS = 7


def minutes_played(goals_against, gaa, starts=None):
    """
    (minutes, how it was worked out) for a week of goaltending so far.

    From GA and GAA where both are real, since `GAA = GA x 60 / TOI` inverts
    exactly and carries the real minutes with it. Otherwise from starts at the
    measured average, which is all a page has when nothing has been conceded.
    """
    goals_against = _number(goals_against)
    gaa = _number(gaa)
    if goals_against > 0 and gaa > 0:
        return goals_against * GAA_MINUTES / gaa, 'gaa'
    if starts:
        return _number(starts) * AVERAGE_START_MINUTES, 'starts'
    return 0.0, 'none'


def start_minutes(goals):
    """How long a start conceding `goals` lasts, from the measured curve."""
    if goals is None:
        return AVERAGE_START_MINUTES
    return MINUTES_BY_GOALS.get(min(int(goals), max(MINUTES_BY_GOALS)), AVERAGE_START_MINUTES)


def projected_minutes(starts):
    """Minutes to expect from `starts` projected starts - never a flat hour each."""
    return _number(starts) * AVERAGE_START_MINUTES


def rates(totals, minutes):
    """{GAA, SVpct} from counting totals - None where there is nothing to divide."""
    shots = _number(totals.get('SA'))
    saves = _number(totals.get('SV'))
    return {
        'GAA': (_number(totals.get('GA')) * GAA_MINUTES / minutes) if minutes > 0 else None,
        'SVpct': (saves / shots) if shots > 0 else None,
    }


def add(*totals):
    """Sum counting totals, ignoring the rates."""
    combined = {}
    for entry in totals:
        for category, value in (entry or {}).items():
            if category in ('GAA', 'SVpct'):
                continue
            combined[category] = combined.get(category, 0.0) + _number(value)
    return combined


def outcomes(shots, save_pct, max_goals=MAX_GOALS):
    """
    [{goals, shots, saves, minutes, probability, label}] for one start.

    Goals against are binomial in the shots faced, so the spread comes from
    the projection rather than from taste, and each row carries the minutes a
    night like that really lasts. The last row holds the tail. The pull is
    appended as a `variant`, outside the distribution - see the docstring.
    """
    shots = max(0.0, _number(shots))
    save_pct = min(0.999, max(0.5, _number(save_pct)))
    whole = max(1, int(round(shots)))
    goal_odds = 1.0 - save_pct

    rows = []
    for goals in range(0, max_goals + 1):
        if goals == max_goals:
            probability = max(0.0, 1.0 - sum(r['probability'] for r in rows))
        else:
            probability = _binomial(whole, goals, goal_odds)
        rows.append({
            'goals': goals,
            'shots': whole,
            'saves': max(0, whole - goals),
            'minutes': start_minutes(goals),
            'probability': round(probability, 4),
            'label': _label(goals, max_goals),
            'pulled': False,
            'variant': False,
        })

    pulled_shots = max(1, int(round(shots * PULL_SHOT_SHARE)))
    rows.append({
        'goals': PULL_GOALS,
        'shots': pulled_shots,
        'saves': max(0, pulled_shots - PULL_GOALS),
        'minutes': PULL_MINUTES,
        'probability': PULL_SHARE,
        'label': 'Pulled early',
        'pulled': True,
        'variant': True,
    })
    return rows


def apply_start(totals, minutes, outcome, win=None, shutout=None):
    """
    (totals, minutes) after one more start, so the caller can re-rate them.

    `win` and `shutout` are the odds of each, since what a start adds to those
    categories is not settled by the goals it concedes - though a shutout is,
    when it concedes none.
    """
    after = add(totals, {
        'GA': outcome['goals'],
        'SA': outcome['shots'],
        'SV': outcome['saves'],
        'W': 0.0 if win is None else win,
        'SHO': (1.0 if outcome['goals'] == 0 else 0.0) if shutout is None else shutout,
    })
    return after, minutes + outcome['minutes']


def worst_start(totals, minutes, shots, target, places=3):
    """
    The most goals a start can concede and still keep each ratio ahead.

    `target` is the opponent's projected final {GAA, SVpct}. Returns
    {GAA: {maxGoals, ...}, SVpct: {...}}, `maxGoals` None when even a shutout
    does not get there - which is the useful answer in a category already
    lost.

    Walked goal by goal rather than solved in closed form, because the minutes
    a start lasts depend on how many it concedes (`MINUTES_BY_GOALS`): solving
    it would mean assuming a fixed hour, which is the thing this module is
    careful not to do.
    """
    shots = max(0.0, _number(shots))
    goals_so_far = _number(totals.get('GA'))
    saves_so_far = _number(totals.get('SV'))
    shots_so_far = _number(totals.get('SA'))
    whole = max(1, int(round(shots)))

    gaa_target, save_target = target.get('GAA'), target.get('SVpct')
    result = {
        'GAA': {'maxGoals': None,
                'reason': 'no opponent GAA yet' if gaa_target is None else 'behind whatever he does'},
        'SVpct': {'maxGoals': None,
                  'reason': ('no opponent save percentage yet' if save_target is None
                             else 'behind whatever he does')},
    }

    for goals in range(0, whole + 1):
        if gaa_target is not None:
            gaa = (goals_so_far + goals) * GAA_MINUTES / (minutes + start_minutes(goals))
            if gaa <= gaa_target:
                result['GAA'] = {'maxGoals': goals}
        if save_target is not None:
            saved = (saves_so_far + max(0, whole - goals)) / (shots_so_far + whole)
            if saved >= save_target:
                result['SVpct'] = {'maxGoals': goals}

    for entry in result.values():
        if entry.get('maxGoals') is not None and whole:
            entry['savePct'] = round(max(0.0, (whole - entry['maxGoals']) / whole), places)
    return result


def chance_of_at_most(shots, save_pct, goals):
    """The odds of conceding `goals` or fewer on `shots` - how safe a limit is."""
    if goals is None:
        return 0.0
    whole = max(1, int(round(_number(shots))))
    odds = 1.0 - min(0.999, max(0.5, _number(save_pct)))
    return min(1.0, sum(_binomial(whole, g, odds) for g in range(0, int(goals) + 1)))


def _label(goals, max_goals):
    if goals == 0:
        return 'Shutout'
    if goals == 1:
        return 'One goal'
    if goals >= max_goals:
        return f'{goals}+ goals'
    return f'{goals} goals'


def _binomial(trials, successes, probability):
    if successes > trials:
        return 0.0
    return (math.comb(trials, successes) * probability ** successes
            * (1.0 - probability) ** (trials - successes))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number
