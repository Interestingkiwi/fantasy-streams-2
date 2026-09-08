"""
Nudging a projection by who the player is actually facing.

Step 4 of docs/OPTIMIZER.md. A player's projection is an average over average
opposition; on any given night he is facing someone specific. This applies a
small, per-category correction for that, from the team strengths
`scrape_team_stats.py` collects.

**Per category, not one blanket multiplier.** The opponent stat that drives a
category differs by category, and for goalies two of them move in *opposite*
directions off the same input: a high-volume opponent means more saves (good)
and more goals against (bad). A single "weak opponent" boost would push both
the same way, which is not imprecise but backwards. Categories nothing in the
data drives - hits, blocks, PIM, faceoffs - are deliberately left alone rather
than given a goals-based nudge that would be noise wearing a signal's clothes.

**Standard deviations, not ratios.** OPTIMIZER.md sketched this as
`league_mean / opponent_value`, and the real numbers argue against it. Across
the completed 2025-26 season the spread of goals against per game is +24%/-22%
about the mean while penalty kill is only +7%/-9%. Under a shared cap, a ratio
would leave goals-against permanently clamped - the cap doing all the work and
erasing every distinction between merely bad defences and terrible ones -
while penalty kill barely moved. A z-score is comparable across categories
whatever their spread, and it is exactly mean-neutral, which a ratio is not.

**Calibration, measured rather than guessed.** `PER_STANDARD_DEVIATION` is
1.5% per σ, capped at 4%. Against the real 2025-26 spread that puts the
extreme teams at 2.6-4.3% - so the cap bites only on genuine outliers - and a
typical ±1σ opponent at 1.5%. Enough to break ties between near-equals, never
enough to lift a fourth-liner over McDavid.

**Mean-neutral, and there is a test.** If the average multiplier across the
league is not 1.0, every projected total drifts, and since the whole point is
comparing your total against an opponent's, an asymmetric drift silently
biases every matchup. Apply this to both sides or to neither.

**It fades in on its own.** z is shrunk by games played toward zero, so in
October the adjustment is nearly nothing and grows as the sample does. No
hardcoded "off until November" gate, and the same regression-to-the-mean trick
`calculate_goalie_projections.REGRESSION_GAMES` already uses.

Not done here, deliberately: **home ice**, which is plausibly bigger than any
of this and is free from `nhl_schedule`. It is left out because sizing it
needs home/road splits this module does not fetch, and guessing the number
would undo the point of measuring the rest.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/8/2026
"""

import math

# How much a one-standard-deviation opponent moves a projection, and the most
# any opponent may move it. See the calibration note above - both are measured
# against the real spread, and both are meant to be re-tuned once a season of
# play is in hand.
PER_STANDARD_DEVIATION = 0.015
MAX_ADJUSTMENT = 0.04

# Games before an opponent's rate is taken at anything like face value. Below
# it, z shrinks toward zero and the adjustment fades out with it.
REGRESSION_GAMES = 20

# category -> (team stat that drives it, which way it points).
# +1 means a higher opponent value helps the category; -1 means a lower one
# does. Categories absent from this map are never adjusted.
CATEGORY_DRIVERS = {
    # Skater scoring, against how many goals the opponent allows.
    'G': ('goalsAgainstPerGame', 1),
    'A': ('goalsAgainstPerGame', 1),
    'P': ('goalsAgainstPerGame', 1),
    'GWG': ('goalsAgainstPerGame', 1),
    'PPG': ('penaltyKillPct', -1),
    'PPA': ('penaltyKillPct', -1),
    'PPP': ('penaltyKillPct', -1),
    'SHG': ('powerPlayPct', 1),
    'SHP': ('powerPlayPct', 1),
    'SHA': ('powerPlayPct', 1),
    # Shots, against how many the opponent gives up.
    'SOG': ('shotsAgainstPerGame', 1),
    # Goalies. Note SV and GA take the same inputs in opposite directions:
    # a shot-heavy opponent is good for save volume and bad for goals against.
    'W': ('goalsForPerGame', -1),
    'L': ('goalsForPerGame', 1),
    'OTL': ('goalsForPerGame', 1),
    'SHO': ('goalsForPerGame', -1),
    'GA': ('goalsForPerGame', 1),
    'SV': ('shotsForPerGame', 1),
    'SA': ('shotsForPerGame', 1),
}

# Named so the omission reads as a decision rather than an oversight. Nothing
# collected predicts these, and a goals-based multiplier on hits would be
# noise presented as insight.
UNDRIVEN_CATEGORIES = frozenset({'HIT', 'BLK', 'PIM', 'FW', 'FL', 'FOT', 'TOI'})


def team_z_scores(team_stats, games_key='gamesPlayed'):
    """
    {team code: {stat: z}} across the league, shrunk by games played.

    `team_stats` is rows from the `team_stats` table for one window. A stat
    with no spread - every team identical, or a single team - yields zero for
    everyone rather than a division by zero.
    """
    rows = {row['teamCode']: row for row in team_stats if row.get('teamCode')}
    if not rows:
        return {}

    stats = {stat for stat, _ in CATEGORY_DRIVERS.values()}
    scores = {code: {} for code in rows}

    for stat in stats:
        values = [(code, _number(row.get(stat)))
                  for code, row in rows.items() if row.get(stat) is not None]
        if len(values) < 2:
            continue

        numbers = [value for _code, value in values]
        mean = sum(numbers) / len(numbers)
        deviation = _stdev(numbers, mean)
        if deviation <= 0:
            continue

        for code, value in values:
            played = _number(rows[code].get(games_key))
            shrink = played / (played + REGRESSION_GAMES) if played > 0 else 0.0
            scores[code][stat] = ((value - mean) / deviation) * shrink

    return scores


def multiplier(category, opponent, z_scores,
               per_sd=PER_STANDARD_DEVIATION, cap=MAX_ADJUSTMENT):
    """
    What to scale one category by, against one opponent. 1.0 means no change.

    Returns 1.0 for any category nothing in the data drives, for an unknown
    opponent, and for a team with too few games for its rates to mean anything.
    """
    driver = CATEGORY_DRIVERS.get(category)
    if not driver:
        return 1.0

    stat, direction = driver
    z = (z_scores.get(opponent) or {}).get(stat)
    if z is None:
        return 1.0

    shift = per_sd * z * direction
    return 1.0 + max(-cap, min(cap, shift))


def adjust(per_game, opponent, z_scores,
           per_sd=PER_STANDARD_DEVIATION, cap=MAX_ADJUSTMENT):
    """
    A copy of a player's per-category line, scaled for tonight's opponent.

    Rate categories are passed through untouched: they are not summed into the
    value anyway, and scaling a percentage by an opponent's strength would not
    mean anything.
    """
    from daily_value import RATE_COLUMNS

    return {
        category: (value if category in RATE_COLUMNS
                   else value * multiplier(category, opponent, z_scores, per_sd, cap))
        for category, value in (per_game or {}).items()
    }


def opponents_on(schedule_rows):
    """
    {date: {team: opponent}} from (game_date, home, away) rows.

    Both directions, so a player's team looks up who he is facing without the
    caller caring which side of the fixture he is on.
    """
    facing = {}
    for game_date, home, away in schedule_rows:
        night = facing.setdefault(game_date, {})
        night[home] = away
        night[away] = home
    return facing


def _stdev(values, mean):
    if len(values) < 2:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number
