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
2.5% per σ, capped at 4%. Two independent measurements set it. The league
spread says a ±1σ opponent is a typical case and ±2.5σ the extremes; a
split-half test - strength from the first half of 2025-26, production from the
second - says the real effect is 3.5%/σ on points and 8.4% on shots. The
setting sits deliberately below that, because those come from one season and
an adjustment that reorders the board is a worse failure than one that
under-reacts.

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
# any opponent may move it.
#
# **Deliberately below what the evidence supports.** Split-half on 2025-26 -
# team strength built from the first half of the season, tested on the second,
# so nothing in the test window fed the estimate - says a weak-defence opponent
# is worth 3.5%/sigma on points, 4.9% on goals and 8.4% on shots. Shots being
# the largest is not noise: shot volume is far more predictable than finishing.
#
# 2.5% is roughly halfway. The conservatism is a choice, not an oversight: the
# figures above come from one season and a single split, and an adjustment that
# reorders the board is a worse failure than one that under-reacts. Extreme
# opponents now reach MAX_ADJUSTMENT rather than sitting under it.
PER_STANDARD_DEVIATION = 0.025
MAX_ADJUSTMENT = 0.04

# Games before an opponent's rate is taken at anything like face value. Below
# it, z shrinks toward zero and the adjustment fades out with it.
REGRESSION_GAMES = 20

# How much of an opponent's strength comes from recent form rather than the
# season to date.
#
# **Deliberately small, and the evidence is why.** Tested on 2025-26 by
# predicting each team-week from windows strictly before it: season-to-date
# correlates 0.35 with next week's shots allowed, while the trailing 4, 2 and 1
# week windows manage 0.31, 0.24 and 0.21 - all *worse* on their own. What
# recent form adds beyond season-to-date is a partial correlation of 0.03 to
# 0.08. Real, consistently positive, and small.
#
# So this is a re-weighting of two estimates of the same quantity, not a new
# effect layered on top, which is what makes a modest weight safe: getting it
# wrong costs a little precision rather than introducing a bias. The 4-week
# window carries it, being the steadiest of the three.
RECENT_WINDOW = 'last-4w'
RECENT_WEIGHT = 0.25

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

# No OPPONENT adjustment for these: nothing collected predicts them, and a
# goals-based multiplier on hits would be noise presented as insight. Note this
# says nothing about venue - measured on the 2025-26 season, hits, blocks and
# penalty minutes all have a real home/road effect, and PERIPHERAL_VENUE below
# applies it.
UNDRIVEN_CATEGORIES = frozenset({'HIT', 'BLK', 'PIM', 'FW', 'FL', 'FOT', 'TOI'})

# Categories whose venue effect cannot come from `team_stats` - the team
# endpoint carries no hits, blocks or penalty minutes - so it is measured from
# `player_game_stats` instead. Worth the extra source: on 2025-26 the home
# effect on hits was +4.7%, larger than the +4.5% on goals that was already
# modelled, and blocks and PIM both run the other way.
PERIPHERAL_VENUE = {
    'HIT': 'hits',
    'BLK': 'blockedShots',
    'PIM': 'penaltyMinutes',
}


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


def peripheral_venue(game_rows):
    """
    {quantity: {'home': m, 'road': m}} for hits, blocks and PIM.

    Measured straight off per-game player rows, since `team_stats` has no such
    columns. Each is the league's home per-game average over the overall
    average, the same shape `venue_multipliers` produces, so the two merge.

    Some of this is scorekeeper bias rather than play - home rinks are known to
    be generous with hits - but the recorded stat is what a league scores, so
    modelling it is right whatever its cause.
    """
    totals = {column: {'H': [0.0, 0], 'R': [0.0, 0]}
              for column in PERIPHERAL_VENUE.values()}

    for row in game_rows or []:
        side = row.get('homeRoad')
        if side not in ('H', 'R'):
            continue
        for column in totals:
            value = row.get(column)
            if value is None:
                continue
            totals[column][side][0] += _number(value)
            totals[column][side][1] += 1

    effects = {}
    for column, sides in totals.items():
        home_sum, home_n = sides['H']
        road_sum, road_n = sides['R']
        if home_n < 100 or road_n < 100:
            continue
        home_rate = home_sum / home_n
        road_rate = road_sum / road_n
        overall = (home_sum + road_sum) / (home_n + road_n)
        if overall <= 0:
            continue
        effects[column] = {'home': home_rate / overall, 'road': road_rate / overall}
    return effects


def venue_multiplier(category, is_home, effects):
    """
    What playing at home (or away) does to one category. 1.0 if unknown.

    A category with no venue driver, or a run with no split data scraped,
    passes through unchanged rather than guessing.
    """
    driver = VENUE_DRIVERS.get(category)
    if driver is None and category in PERIPHERAL_VENUE:
        driver = (PERIPHERAL_VENUE[category], 1)
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

    shift = _clamp(per_sd * z * direction, cap)
    # Clamping is what breaks neutrality: the z distribution is not symmetric,
    # so once the cap bites on one tail the league mean drifts off 1.0 - about
    # 6 basis points at the current setting. Small, and it cancels in a matchup
    # because both sides use the same table, but the guarantee is cheap to keep
    # exact and much easier to reason about when it is.
    return 1.0 + shift - _mean_shift(stat, direction, z_scores, per_sd, cap)


def _clamp(value, cap):
    return max(-cap, min(cap, value))


def _mean_shift(stat, direction, z_scores, per_sd, cap):
    """
    The league-average clamped shift for one stat, which is what has to come
    back off to leave the mean multiplier at exactly 1.0.
    """
    shifts = [_clamp(per_sd * scores[stat] * direction, cap)
              for scores in z_scores.values() if stat in scores]
    return (sum(shifts) / len(shifts)) if shifts else 0.0


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


def blended_z_scores(rows, recent_weight=RECENT_WEIGHT,
                     recent_window=RECENT_WINDOW, games_key='gamesPlayed'):
    """
    {window: {team: {stat: z}}}, with recent form folded into each window.

    Both the season windows and the trailing one estimate the same thing - how
    much this opponent concedes - so blending them is a precision question, not
    a modelling one. Recent form is standardised separately before blending, so
    the blend mixes comparable quantities rather than raw rates.

    Falls through to the plain split z-scores when no trailing window has been
    scraped, so an old `team_stats` still works.
    """
    splits = split_z_scores(rows, games_key)
    recent = splits.get(recent_window)
    if not recent or recent_weight <= 0:
        return splits

    blended = {}
    for window, teams in splits.items():
        if window == recent_window:
            blended[window] = teams
            continue
        merged = {}
        for team, scores in teams.items():
            form = recent.get(team) or {}
            merged[team] = {
                stat: (1 - recent_weight) * value
                      + recent_weight * form.get(stat, value)
                for stat, value in scores.items()
            }
        blended[window] = merged
    return blended


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
