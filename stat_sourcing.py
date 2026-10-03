"""
Stat Sourcing: which numbers League Home values players on.

The settings bar offers three, as the old site did:

- **Projected** - the preseason projections in `final_projections`.
- **Season to date** - each player's rate so far this season, from
  `player_game_stats`: per game for a skater, per start for a goalie.
- **Combined** - the two blended stat by stat, the season counting for more
  as his games pile up.

**The seam is a projection-shaped row.** `season_rows()` and `combined_rows()`
rewrite each `final_projections` row so its counting columns hold the rate
being valued times his projected games (projected starts, for a goalie).
`daily_value` divides by exactly those, so it reads the rate back - and nothing
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

**Combined is `(k x projected rate + n x season rate) / (k + n)`** after n
games: the projection counts as k games of evidence. k is each stat's own,
measured by `derive_combined_weights.py` - the k whose blend after a player's
first 5-40 games of 2025-26 best predicted the rest of his season. Shots and
hits are about half season after 20 games; goals a quarter; PIM and
plus/minus barely move. On every stat the blend beat both the projection
alone and the season alone. Points, total faceoffs and saves are built from
their blended parts, and save percentage and GAA from blended goals against,
shots against and minutes, so they cannot disagree with them.

**When they open.** Combined from opening night: with a game or two its line
is nearly all projection, which is the point of it. Season to date not until
a rate means something - the day after every NHL team has played
`OPEN_AFTER_TEAM_GAMES` games (the shortest trend window), read from
`nhl_schedule` the way the nightly job finds opening night, so next season
opens the same way with nothing to change. For 2026-27 that is Wednesday
2026-10-14 (four games would have been the 11th, six the 18th).
`STAT_SOURCING_PREVIEW`, on outside production, opens it early for
development.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/3/2026
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

import daily_value as dv
from db import fetch_all

log = logging.getLogger(__name__)

PROJECTED, SEASON, COMBINED = 'projected', 'todate', 'combined'
SOURCES = (PROJECTED, SEASON, COMBINED)

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

# Combined's games of trust in the projection, per stat - printed by
# `derive_combined_weights.py` on 2025-26 and rounded inside each stat's range
# within 1% of the best fit. Re-measure between seasons, not mid-season.
PRIOR_GAMES = {
    'G': 60, 'A': 65, 'PPG': 45, 'PPP': 50, 'SHP': 90, 'SOG': 20, 'HIT': 15,
    'BLK': 40, 'PIM': 130, '+/-': 100, 'FW': 7, 'FL': 6,
    'W': 30, 'L': 30, 'OTL': 25, 'SHO': 30, 'GA': 30, 'SA': 7, 'TOI': 10,
}

# The stat each blended column takes its k from. Short-handed goals share
# short-handed points' (too rare to measure alone). Columns missing here are
# built from blended ones instead: points, total faceoffs and saves.
BLEND_CODES = {
    'proj_goals': 'G', 'proj_assists': 'A', 'proj_ppGoals': 'PPG', 'proj_ppPoints': 'PPP',
    'proj_shGoals': 'SHP', 'proj_shPoints': 'SHP', 'proj_shots': 'SOG', 'proj_hits': 'HIT',
    'proj_blockedShots': 'BLK', 'proj_penaltyMinutes': 'PIM', 'proj_plusMinus': '+/-',
    'proj_totalFaceoffWins': 'FW', 'proj_totalFaceoffLosses': 'FL',
    'proj_wins': 'W', 'proj_losses': 'L', 'proj_otLosses': 'OTL', 'proj_shutouts': 'SHO',
    'proj_goalsAgainst': 'GA', 'proj_shotsAgainst': 'SA', 'proj_timeOnIce': 'TOI',
}


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
    return [_rewrite(projection, sums, blend=False) for projection in projections]


def combined_rows(projections, sums):
    """
    The projection rows rewritten to Combined's blend of projected and season
    rates (`PRIOR_GAMES`), as new dicts. `statSource` is 'combined', or
    'projection' for a player with no games yet.
    """
    return [_rewrite(projection, sums, blend=True) for projection in projections]


def _rewrite(projection, sums, blend):
    row = dict(projection)
    found = sums.get(str(projection.get('playerId'))) or {}
    games = int(found.get('games') or 0)
    row['seasonGames'] = games
    if not games:
        row['statSource'] = 'projection'
        return row

    row['statSource'] = 'combined' if blend else 'season'
    goalie = projection.get('positionCode') == 'G'
    projected = dv.games_for(projection)
    divisor = projected
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
        rate = None if total is None else float(total) / games
        if blend and column in BLEND_CODES:
            rate = _blend(projection.get(column), projected, rate, games,
                          PRIOR_GAMES[BLEND_CODES[column]])
        row[column] = None if rate is None else rate * divisor

    if blend:
        # Built from their blended parts, so they cannot disagree with them
        for whole, parts in (('proj_points', ('proj_goals', 'proj_assists')),
                             ('proj_totalFaceoffs', ('proj_totalFaceoffWins',
                                                     'proj_totalFaceoffLosses'))):
            if not goalie and all(row.get(part) is not None for part in parts):
                row[whole] = sum(row[part] for part in parts)
        if goalie and row.get('proj_shotsAgainst') is not None \
                and row.get('proj_goalsAgainst') is not None:
            row['proj_saves'] = row['proj_shotsAgainst'] - row['proj_goalsAgainst']

    if goalie:
        allowed, shots, seconds = (row.get('proj_goalsAgainst'), row.get('proj_shotsAgainst'),
                                   row.get('proj_timeOnIce'))
        row['proj_savePct'] = 1 - float(allowed or 0) / float(shots) if shots else None
        row['proj_goalsAgainstAverage'] = (float(allowed or 0) * 3600 / float(seconds)
                                           if seconds else None)
    return row


def _blend(projected_total, projected_games, season_rate, games, prior_games):
    """
    (k x projected rate + n x season rate) / (k + n) per game, or whichever of
    the two exists when one does not.
    """
    projected_rate = (float(projected_total) / projected_games
                      if projected_total is not None and projected_games > 0 else None)
    if projected_rate is None:
        return season_rate
    if season_rate is None:
        return projected_rate
    return (prior_games * projected_rate + games * season_rate) / (prior_games + games)
