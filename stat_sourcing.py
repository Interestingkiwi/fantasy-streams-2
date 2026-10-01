"""
Stat Sourcing: which numbers League Home values players on.

The settings bar offers three, as the old site did:

- **Projected** - the preseason projections in `final_projections`.
- **Season to date** - each player's rate so far this season, from
  `player_game_stats`: per game for a skater, per start for a goalie.
- **Combined** - a blend of the two; not built yet.

**The seam is a projection-shaped row.** `season_rows()` rewrites each
`final_projections` row so its counting columns hold the player's season rate
times his projected games (projected starts, for a goalie). `daily_value`
divides by exactly those, so it reads back his rate so far - and nothing
downstream, the lineup matcher, matchup weighting, the free-agent search or
goalie planning, needs to know which source it is reading. Projected games
and starts are left as they are: goalie start odds are balanced against them,
and a goalie's share of his team's starts is better projected than read off a
few weeks.

**The category weights stay on the projections.** σ is what makes a goal and
a hit commensurable, and rates over a few weeks spread far wider than the
true ones - early noise would quietly re-weight the league. `Week` takes the
projections for the scales and these rows for the values. The draft board's
rank (drop suggestions, the roster view's Rank) stays on projections too: it
prices the rest of a season, which a few weeks of play do not settle.

**A player who has not played keeps his projection**, marked so the page can
say so. An injured starter would otherwise be planned at nothing, and a
season of no games tells the source nothing about him. A goalie's relief
outings are left out of his per-start line - twenty minutes in mop-up is not
a start - so a goalie with only relief appearances keeps his projection too.

**When it opens.** Not on opening night: a rate over a game or two is noise,
and a lineup planned on it would chase it. Season to date opens the day after
every NHL team has played `OPEN_AFTER_TEAM_GAMES` games - the shortest trend
window - read from `nhl_schedule` the way the nightly job finds opening night,
so next season opens the same way with nothing to change. For 2026-27 that is
Wednesday 2026-10-14 (four games would have been the 11th, six the 18th).
`STAT_SOURCING_PREVIEW`, on outside production, opens it early for
development.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

import daily_value as dv
from db import fetch_all

log = logging.getLogger(__name__)

PROJECTED, SEASON = 'projected', 'todate'
SOURCES = (PROJECTED, SEASON)

# Games every team must have played before Season to date opens
OPEN_AFTER_TEAM_GAMES = 5

# Projection column -> the `player_game_stats` column whose season rate replaces
# it. Games started is left out on purpose: it is the projection's, and start
# odds are balanced against it.
SOURCE_COLUMNS = {
    'proj_goals': 'goals',
    'proj_assists': 'assists',
    'proj_points': 'points',
    'proj_plusMinus': 'plusMinus',
    'proj_penaltyMinutes': 'penaltyMinutes',
    'proj_ppGoals': 'ppGoals',
    'proj_ppPoints': 'ppPoints',
    'proj_shGoals': 'shGoals',
    'proj_shPoints': 'shPoints',
    'proj_shots': 'shots',
    'proj_hits': 'hits',
    'proj_blockedShots': 'blockedShots',
    'proj_totalFaceoffWins': 'faceoffWins',
    'proj_totalFaceoffLosses': 'faceoffLosses',
    'proj_totalFaceoffs': 'totalFaceoffs',
    'proj_wins': 'wins',
    'proj_losses': 'losses',
    'proj_otLosses': 'otLosses',
    'proj_shutouts': 'shutouts',
    'proj_goalsAgainst': 'goalsAgainst',
    'proj_shotsAgainst': 'shotsAgainst',
    'proj_saves': 'saves',
    'proj_timeOnIce': 'timeOnIce',
}
# Goalie columns. A skater takes every other one and none of these - above all
# not time on ice, which is a goalie category and would put every skater in
# its pool.
GOALIE_COLUMNS = frozenset({'proj_wins', 'proj_losses', 'proj_otLosses', 'proj_shutouts',
                            'proj_goalsAgainst', 'proj_shotsAgainst', 'proj_saves',
                            'proj_timeOnIce'})


def opens_on(schedule, games=OPEN_AFTER_TEAM_GAMES):
    """
    The date Season to date opens: the day after the last NHL team plays its
    `games`th game, from [(date, home, away)] for the season. None with no
    schedule, or one in which some team never gets there.
    """
    played, reached = defaultdict(int), {}
    for day, home, away in sorted(schedule):
        for team in (home, away):
            played[team] += 1
            if played[team] == games:
                reached[team] = day
    if not played or len(reached) < len(played):
        return None
    return (date.fromisoformat(max(reached.values())) + timedelta(days=1)).isoformat()


def status(schedule, today, preview=False):
    """
    {'open', 'opensOn', 'preview'} for the page: whether Season to date can be
    chosen today, the date it opens, and whether it is open only because the
    preview setting is on.
    """
    opening = opens_on(schedule)
    due = bool(opening) and today.isoformat() >= opening
    return {'open': due or bool(preview), 'opensOn': opening, 'preview': bool(preview) and not due}


def schedule_season(schedule_rows):
    """The season the schedule is for (2026 for 2026-27), from its first game's date."""
    first = min((r[0] for r in schedule_rows), default=None)
    if not first:
        return None
    day = date.fromisoformat(first)
    # A season opens in the autumn; anything before July belongs to the one before
    return day.year if day.month >= 7 else day.year - 1


def season_sums(season):
    """
    {playerId: {column: season total, 'games': n}} for one season, a goalie's
    over his starts only. {} before the table exists or the season has a game.
    """
    if not season:
        return {}
    columns = sorted(set(SOURCE_COLUMNS.values()))
    # A goalie row is one with no positionCode; his relief outings count for nothing
    counted = 'NOT ("positionCode" IS NULL AND COALESCE("gamesStarted", 0) < 1)'
    sums = ', '.join(f'SUM(CASE WHEN {counted} THEN "{c}" END) AS "{c}"' for c in columns)
    try:
        rows = fetch_all(
            f'SELECT "playerId", COUNT(*) FILTER (WHERE {counted}) AS games, {sums} '
            'FROM player_game_stats WHERE "gameId" >= :first AND "gameId" < :next '
            'GROUP BY "playerId"',
            {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    except Exception:                             # noqa: BLE001 - no table yet
        log.warning("No player_game_stats yet - every player keeps his projection.")
        return {}
    return {str(r['playerId']): dict(r) for r in rows}


def season_rows(projections, sums):
    """
    The projection rows rewritten to each player's season rate, as new dicts.

    Each carries `statSource` - 'season', or 'projection' for a player with no
    games yet, who keeps his projected line - and `seasonGames`.
    """
    rows = []
    for projection in projections:
        row = dict(projection)
        found = sums.get(str(projection.get('playerId'))) or {}
        games = int(found.get('games') or 0)
        row['seasonGames'] = games
        if not games:
            row['statSource'] = 'projection'
            rows.append(row)
            continue

        row['statSource'] = 'season'
        goalie = projection.get('positionCode') == 'G'
        divisor = dv.games_for(projection)
        if divisor <= 0:
            # No projected games to scale by: stand his own games in for them
            divisor = games
            row['projectedGames'] = games
            if goalie:
                row['proj_gamesStarted'] = games
        for column, source in SOURCE_COLUMNS.items():
            if (column in GOALIE_COLUMNS) != goalie:
                continue
            total = found.get(source)
            row[column] = None if total is None else float(total) / games * divisor

        if goalie:
            saves, shots = found.get('saves'), found.get('shotsAgainst')
            allowed, seconds = found.get('goalsAgainst'), found.get('timeOnIce')
            row['proj_savePct'] = float(saves or 0) / float(shots) if shots else None
            row['proj_goalsAgainstAverage'] = (float(allowed or 0) * 3600 / float(seconds)
                                               if seconds else None)
        rows.append(row)
    return rows
