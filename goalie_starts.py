"""
How likely a goalie is to start a particular night.

Step 3 of docs/OPTIMIZER.md. The old app started every rostered goalie whose
NHL team played, so two goalies from one team both "started" in a 2-G league
and both contributed a full game's projection.

**The invariant: exactly one goalie starts each NHL game.** So for any team
game, the start probabilities across that team's goalies sum to one. That
single constraint is what removes the double count, and it generalises the
back-to-back case rather than special-casing it - a back-to-back simply
distributes two starts across the tandem instead of handing each goalie two.

There is a second constraint pulling the other way: over the season, a
goalie's probabilities should add back up to the starts he was projected for.
Both at once is a matrix-balancing problem, solved here by iterative
proportional fitting - alternately scale the rows to hit each goalie's
projected total and the columns to hit one start per game. The columns are
normalised last, so **the one-start-per-game constraint is exact and the
season-total constraint is approximate**; that is the right way round, because
a lineup is set one night at a time.

**Why per-team normalisation matters more than the totals suggest.** Across
the league, projected starts already come to 2,697 against 2,688 real team
games - a 0.3% overcount that looks harmless. Per team it is nothing of the
kind: DET's goalies are projected for 113 starts across 84 games.

The teams that fall *short* mostly do so for a fixable reason: the four
rookie-imported goalies carry counting stats but no `proj_gamesStarted`, so
`_starts` falls back to projected appearances rather than reading them as
never starting. Populating that column in `apply_rookie_projections.py` would
retire the fallback.

Not modelled, because it needs live data: confirmed starters. When that feed
exists it belongs here as a column override - fix the known starter at 1.0 and
let the balancing redistribute the rest.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

from collections import defaultdict
from datetime import date, timedelta

# What the presumed starter's weight is multiplied by on the second night of a
# back-to-back, before balancing. Not a probability - the balancing turns it
# into one. At 0.35, a 70/30 tandem splits a second night roughly 45/55, which
# is the right neighbourhood: a number one starts some second nights, just not
# most of them.
B2B_STARTER_DAMPING = 0.35

# Iterative proportional fitting converges geometrically here; the matrices are
# at most a handful of goalies by 84 games.
BALANCE_ITERATIONS = 40


def second_nights(game_dates):
    """
    The dates in `game_dates` that fall the day after another game.

    A team's back-to-backs, which is the schedule fact that actually moves a
    goalie's start probability around.
    """
    played = {_as_date(value) for value in game_dates}
    played.discard(None)
    return {value for value in played if value - timedelta(days=1) in played}


def start_probabilities(goalies, game_dates, starts_key='proj_gamesStarted',
                        fallback_key='projectedGames',
                        damping=B2B_STARTER_DAMPING):
    """
    [{date: probability}] for one team's goalies, aligned to `goalies`.

    `goalies` is that team's goalies as dicts; identity is the index, as in
    `lineup_utils`. Every date carries exactly one start between them. A
    goalie projected for no starts gets none, unless nobody on the team is
    projected for any, in which case the work is shared evenly rather than
    left at zero.
    """
    dates = sorted({d for d in (_as_date(v) for v in game_dates) if d})
    if not goalies or not dates:
        return [{} for _ in goalies]

    targets = _targets(goalies, starts_key, fallback_key, len(dates))
    tilt = _tilt(targets, dates, damping)
    real = len(goalies)

    # Start from the tilt, then alternately satisfy each constraint. Columns
    # go last so the one-start-per-game invariant holds exactly.
    grid = [row[:] for row in tilt]
    for _ in range(BALANCE_ITERATIONS):
        _scale_rows(grid, targets)
        _scale_columns(grid)

    # The residual goalie, if there is one, was only ever there to absorb
    # starts nobody real was projected for. Drop him from the answer.
    return [dict(zip(dates, row)) for row in grid[:real]]


def start_probabilities_by_team(goalies, schedule_rows, team_key='teamAbbrevs',
                                starts_key='proj_gamesStarted',
                                fallback_key='projectedGames',
                                damping=B2B_STARTER_DAMPING):
    """
    {index in `goalies`: {date: probability}}, grouping by NHL team.

    `schedule_rows` is any iterable of (game_date, home, away), the same shape
    `schedule_utils` takes. Goalies whose team has no games get an empty map
    rather than being dropped, so indexes stay aligned.
    """
    team_dates = defaultdict(set)
    for game_date, home, away in schedule_rows:
        for team in (home, away):
            team_dates[team].add(game_date)

    by_team = defaultdict(list)
    for index, goalie in enumerate(goalies):
        by_team[primary_team(goalie.get(team_key))].append(index)

    probabilities = {index: {} for index in range(len(goalies))}
    for team, indexes in by_team.items():
        dates = team_dates.get(team)
        if not dates:
            continue
        rows = start_probabilities([goalies[i] for i in indexes], dates,
                                   starts_key=starts_key,
                                   fallback_key=fallback_key, damping=damping)
        for index, row in zip(indexes, rows):
            probabilities[index] = row
    return probabilities


def primary_team(value):
    """`teamAbbrevs` can hold two teams for someone who moved mid-season."""
    return str(value or '').split(',')[0].strip()


def expected_value(player, probability, value_key='value'):
    """
    A copy of `player` with its start-conditional value turned into an
    expected one.

    `daily_value` produces per-start numbers for goalies, so this is the
    multiply that makes them per-scheduled-game. Counting categories scale;
    rate categories (GAA, SVpct) do not, because a rate does not become
    smaller when a start becomes less likely - it simply may not happen.
    """
    from daily_value import RATE_COLUMNS

    scaled = dict(player)
    scaled[value_key] = _number(player.get(value_key)) * probability
    scaled['startProbability'] = probability

    per_game = player.get('perGame')
    if isinstance(per_game, dict):
        scaled['perGame'] = {
            category: (value if category in RATE_COLUMNS else value * probability)
            for category, value in per_game.items()
        }
    return scaled


def _targets(goalies, starts_key, fallback_key, game_count):
    """
    Each goalie's projected starts, reconciled against the team's game count,
    plus a residual for the starts nobody accounts for.

    Projections are built one goalie at a time, so a team's totals do not add
    up: across the same 84 games, DET's goalies are projected for 113 starts
    and PIT's for 43.

    The two directions are not symmetric, and treating them the same is what
    produces nonsense. **Over** the game count means real competition for
    starts, so everyone scales down. **Under** it means the pipeline has no
    projection for whoever takes the rest - PIT's second goalie was pruned as
    inactive - and scaling up would hand a single goalie all 84 starts. So the
    shortfall goes to a residual goalie who exists only to absorb it, leaving
    the real ones on the totals they were projected for.
    """
    raw = [max(0.0, _starts(goalie, starts_key, fallback_key)) for goalie in goalies]
    total = sum(raw)

    if total <= 0:
        return [game_count / len(goalies)] * len(goalies)
    if total >= game_count:
        return [value * game_count / total for value in raw]
    return raw + [game_count - total]


def _tilt(targets, dates, damping):
    """
    Prior weights before balancing: flat, except that the goalie with the most
    projected starts is damped on the second night of a back-to-back.

    The tilt says "the number one rests on the second night", which means
    nothing without a number one. When the top two projections are level - a
    true 50/50 tandem, or a team nobody has projected at all - there is no
    starter to rest, and picking one would be inventing a hierarchy out of row
    order. So the tilt sits out and the nights stay flat.
    """
    starter = _clear_starter(targets)
    b2b = second_nights(dates) if starter is not None else set()

    grid = []
    for index in range(len(targets)):
        grid.append([
            damping if (index == starter and game_date in b2b) else 1.0
            for game_date in dates
        ])
    return grid


def _starts(goalie, starts_key, fallback_key):
    """
    A goalie's projected starts, falling back to projected appearances.

    `apply_rookie_projections.py` writes imported rookies with counting stats
    but no `proj_gamesStarted` - 4 of the 82 goalies, one each on BOS, MTL,
    PIT and UTA. Reading that as zero starts is what made PIT look like a
    one-goalie team and handed Silovs all 84 games. Appearances slightly
    overstate starts, which is the right direction to be wrong in: it beats
    leaving a real backup permanently unstartable.
    """
    starts = _number(goalie.get(starts_key))
    if starts > 0:
        return starts
    return _number(goalie.get(fallback_key))


def _clear_starter(targets):
    """The index of the busiest goalie, or None if he is not clearly busiest."""
    if len(targets) < 2:
        return None
    order = sorted(range(len(targets)), key=lambda i: targets[i], reverse=True)
    first, second = order[0], order[1]
    if targets[first] - targets[second] < 1e-9:
        return None
    return first


def _scale_rows(grid, targets):
    for row, target in zip(grid, targets):
        total = sum(row)
        factor = (target / total) if total > 0 else 0.0
        for index in range(len(row)):
            row[index] *= factor


def _scale_columns(grid):
    if not grid:
        return
    for column in range(len(grid[0])):
        total = sum(row[column] for row in grid)
        if total <= 0:
            # Nobody is projected to start: share the night out evenly rather
            # than leaving a team with no goalie at all.
            for row in grid:
                row[column] = 1.0 / len(grid)
            continue
        for row in grid:
            row[column] /= total


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
