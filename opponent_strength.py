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

**Home ice, measured rather than assumed.** It is the larger effect: on the
completed 2025-26 season teams scored 2.2% more at home and won 4.4% more
often, against an opponent adjustment that is typically 1.5%. `VENUE_DRIVERS`
maps each category onto the quantity that actually moves it, and the
multipliers are derived at run time from the scraped `season-home` and
`season-road` windows, so they recalibrate every season instead of ageing into
a constant.

**Venue and opponent strength cannot double-count, by construction.** The
worry is real: a team allows more goals on the road, so using an opponent's
road numbers *and* adding a home boost for your own player would count the
league-wide home effect twice. It cannot happen here, because the opponent's z
is standardised **within its own split** - against the mean of all 32 teams'
road numbers, not against the overall mean. The league-wide part of the split
is therefore exactly zero in z terms, and lives only in the venue multiplier.
There is a test.

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

# Which of the scraped windows to read an opponent's strength from, given
# where the game is played. A visiting opponent is judged on its road record.
OPPONENT_WINDOW = {True: 'season-road', False: 'season-home'}

# category -> (league quantity whose home/road split drives it, direction).
# +1 uses the split ratio as-is; -1 inverts it, for categories that move
# opposite to the quantity - a shutout is likelier where goals against are
# fewer, a loss likelier where wins are rarer.
VENUE_DRIVERS = {
    'G': ('goalsForPerGame', 1),
    'A': ('goalsForPerGame', 1),
    'P': ('goalsForPerGame', 1),
    'GWG': ('goalsForPerGame', 1),
    'PPG': ('goalsForPerGame', 1),
    'PPA': ('goalsForPerGame', 1),
    'PPP': ('goalsForPerGame', 1),
    'SHG': ('goalsForPerGame', 1),
    'SHP': ('goalsForPerGame', 1),
    'SHA': ('goalsForPerGame', 1),
    'SOG': ('shotsForPerGame', 1),
    # Goalies. Fewer goals and fewer shots at home, but more wins.
    'GA': ('goalsAgainstPerGame', 1),
    'SV': ('shotsAgainstPerGame', 1),
    'SA': ('shotsAgainstPerGame', 1),
    'SHO': ('goalsAgainstPerGame', -1),
    'W': ('winRate', 1),
    'L': ('winRate', -1),
    'OTL': ('winRate', -1),
}

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


def venue_multipliers(rows):
    """
    {quantity: {'home': m, 'road': m}} from the scraped home/road windows.

    Each is that split's league mean over the overall league mean, so the pair
    straddles 1.0 and applying it across a full season - 41 home, 41 road -
    is close to neutral. Derived rather than hardcoded so a season with less
    home advantage than 2025-26 produces smaller numbers on its own.

    `rows` is the whole `team_stats` table; the windows are picked out here.
    """
    windows = {}
    for row in rows or []:
        windows.setdefault(row.get('statWindow'), []).append(row)

    overall = windows.get('season') or []
    home = windows.get('season-home') or []
    road = windows.get('season-road') or []
    if not (overall and home and road):
        return {}

    quantities = {stat for stat, _ in VENUE_DRIVERS.values()}
    effects = {}
    for quantity in quantities:
        base = _league_mean(overall, quantity)
        if not base:
            continue
        effects[quantity] = {
            'home': (_league_mean(home, quantity) or base) / base,
            'road': (_league_mean(road, quantity) or base) / base,
        }
    return effects


def venue_multiplier(category, is_home, effects):
    """
    What playing at home (or away) does to one category. 1.0 if unknown.

    A category with no venue driver, or a run with no split data scraped,
    passes through unchanged rather than guessing.
    """
    driver = VENUE_DRIVERS.get(category)
    if not driver or not effects:
        return 1.0

    quantity, direction = driver
    split = (effects.get(quantity) or {}).get('home' if is_home else 'road')
    if not split:
        return 1.0
    return split if direction > 0 else (1.0 / split if split else 1.0)


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


def adjust(per_game, opponent, z_scores, is_home=None, venue=None,
           per_sd=PER_STANDARD_DEVIATION, cap=MAX_ADJUSTMENT):
    """
    A copy of a player's per-category line, scaled for tonight's game.

    Pass `is_home` and `venue` (from `venue_multipliers`) to include home ice;
    omit them and only the opponent adjustment applies. Rate categories are
    passed through untouched - they are not summed into the value anyway, and
    scaling a percentage by an opponent's strength would not mean anything.
    """
    from daily_value import RATE_COLUMNS

    adjusted = {}
    for category, value in (per_game or {}).items():
        if category in RATE_COLUMNS:
            adjusted[category] = value
            continue
        scale = multiplier(category, opponent, z_scores, per_sd, cap)
        if is_home is not None:
            scale *= venue_multiplier(category, is_home, venue or {})
        adjusted[category] = value * scale
    return adjusted


def split_z_scores(rows, games_key='gamesPlayed'):
    """
    {window: {team: {stat: z}}} for every window in `rows`.

    Standardising inside each window is what stops venue and opponent strength
    double-counting: a road split's z is measured against the other 31 teams'
    road records, so the league-wide fact that everyone concedes more away
    from home cancels out and survives only in the venue multiplier.
    """
    windows = {}
    for row in rows or []:
        windows.setdefault(row.get('statWindow'), []).append(row)
    return {window: team_z_scores(split, games_key)
            for window, split in windows.items()}


def opponent_z_for(is_home, splits):
    """
    The z table to judge tonight's opponent by, given where the game is.

    Falls back to the plain season window when the splits were not scraped,
    so the adjustment degrades to venue-blind rather than to nothing.
    """
    window = OPPONENT_WINDOW.get(bool(is_home))
    return splits.get(window) or splits.get('season') or {}


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


def _league_mean(rows, quantity):
    """
    The league mean of a quantity in one window. `winRate` is derived, since
    the API reports wins and games rather than a rate.
    """
    if quantity == 'winRate':
        wins = sum(_number(row.get('wins')) for row in rows)
        played = sum(_number(row.get('gamesPlayed')) for row in rows)
        return (wins / played) if played else None

    values = [_number(row.get(quantity)) for row in rows
              if row.get(quantity) is not None]
    return (sum(values) / len(values)) if values else None


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
