"""
How a player has actually been playing, from `player_game_stats`.

League Home's Lineups tab shows three things beside each projection, the ones
the old site's roster view carried:

- **PP Util** - the player's share of his team's power-play time over his last
  `PP_GAMES` games, and in his last game alone. Power-play time is where
  points come from, and a player bumped to the top unit shows it here before
  it shows anywhere else.
- **Trends** - whether his last 20, 10 and 5 games are clearly above or below
  his own season average, in the league's own categories and weights.
- **Home/road** - which venue he has done better at, so the page can say
  whether his next game is at the better one.

**All of it is descriptive, not predictive.** The trends in particular are
not fed into any projection, and should not be: see *Hot goalies do not stay
hot* in CLAUDE.md, where a fortnight's form turned out to carry nothing
beyond the player's own quality. They answer "is something going on?", which
is the question a manager scanning a roster is asking.

**Which season.** The latest one the table holds, from the gameId prefix
(2025021302 is 2025-26). Before opening night that is last season, so the page
labels it; from the first night of a new season it is the new one, and
windows a player has not yet filled come back empty rather than borrowing from
last year.

**A trend is a standard-error test, not a percentage.** The recent mean is
compared to the season mean in units of `sd / sqrt(N)`, the uncertainty of an
N-game average, and only past `TREND_Z` is it an arrow. A percentage threshold
would flag a depth player's every two-point week and never a star's slump, and
five games are noisy enough that most of what a percentage flags is noise.

Author - Jason Druckenmiller
Created - 9/21/2026
Updated - 9/21/2026
"""

import logging
import math

from daily_value import RATE_COLUMNS
from db import fetch_all

log = logging.getLogger(__name__)

# Yahoo category code -> player_game_stats column.
GAME_COLUMNS = {
    'G': 'goals', 'A': 'assists', 'P': 'points', '+/-': 'plusMinus',
    'PIM': 'penaltyMinutes', 'PPG': 'ppGoals', 'PPP': 'ppPoints',
    'SHG': 'shGoals', 'SHP': 'shPoints', 'SOG': 'shots', 'HIT': 'hits',
    'BLK': 'blockedShots', 'GWG': 'gameWinningGoals', 'W': 'wins', 'L': 'losses',
    'OTL': 'otLosses', 'GA': 'goalsAgainst', 'SA': 'shotsAgainst', 'SV': 'saves',
    'SHO': 'shutouts', 'GS': 'gamesStarted',
}
# Categories the table carries only as a difference of two columns.
DERIVED = {'PPA': ('ppPoints', 'ppGoals'), 'SHA': ('shPoints', 'shGoals')}

TREND_WINDOWS = (20, 10, 5)
TREND_Z = 1.5          # standard errors from the season mean before it is an arrow
PP_GAMES = 5
MIN_SPLIT_GAMES = 5    # games at each venue before a home/road preference is claimed

COLUMNS = sorted({*GAME_COLUMNS.values(), 'ppTimeOnIce', 'teamPpTimeOnIce'})


def latest_season():
    """
    The season the table's newest game belongs to (2025 for 2025-26), or None.

    None as well when there is no table: a deployment whose nightly job has
    not run yet has no games, and a roster view without trends is the right
    answer there rather than a failed request.
    """
    try:
        rows = fetch_all('SELECT MAX("gameId") AS latest FROM player_game_stats')
    except Exception:                             # noqa: BLE001
        log.warning("No player_game_stats yet - no form to show.")
        return None
    latest = rows[0]['latest'] if rows else None
    return int(latest) // 1_000_000 if latest else None


def load(player_ids, season=None):
    """{playerId: [game rows, oldest first]} for one season."""
    ids = sorted({int(i) for i in player_ids if str(i).lstrip('-').isdigit()})
    season = season or latest_season()
    if not ids or not season:
        return {}, season
    quoted = ', '.join(f'"{c}"' for c in COLUMNS)
    try:
        rows = fetch_all(
            f'SELECT "playerId", "gameId", "gameDate", "homeRoad", "positionCode", {quoted} '
            'FROM player_game_stats WHERE "playerId" = ANY(:ids) '
            'AND "gameId" >= :first AND "gameId" < :next ORDER BY "gameDate", "gameId"',
            {'ids': ids, 'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    except Exception:                             # noqa: BLE001
        log.warning("Could not read player_game_stats - no form to show.")
        return {}, season
    games = {}
    for row in rows:
        games.setdefault(str(row['playerId']), []).append(row)
    return games, season


def forms(player_ids, weights, season=None):
    """
    ({playerId: form}, season) for every player asked about. A player with no
    games that season gets an empty form rather than being left out.
    """
    games, season = load(player_ids, season)
    return {str(i): form(games.get(str(i), []), weights) for i in player_ids}, season


def form(games, weights):
    """
    One player's form from his games, oldest first. `weights` is {category:
    weight} - the league's flat category weights, or its points per stat - so
    "better" means better in this league's scoring.
    """
    goalie = any(str(g.get('positionCode') or '') == 'G' for g in games)
    if goalie:
        # Projections are per start, so relief outings would drag the mean down
        games = [g for g in games if _number(g.get('gamesStarted')) >= 1]
    values = [game_value(g, weights) for g in games]

    return {
        'games': len(games),
        'trends': {f'L{n}': trend(values, n) for n in TREND_WINDOWS},
        'venue': venue_split(games, values),
        'pp': None if goalie else pp_share(games),
    }


def game_value(game, weights):
    """One game's line under the league's weights."""
    total = 0.0
    for category, weight in (weights or {}).items():
        if not weight or category in RATE_COLUMNS:
            continue
        if category in DERIVED:
            first, second = DERIVED[category]
            stat = _number(game.get(first)) - _number(game.get(second))
        elif category in GAME_COLUMNS:
            stat = _number(game.get(GAME_COLUMNS[category]))
        else:
            continue
        total += weight * stat
    return total


def trend(values, window):
    """'up', 'down' or 'flat' for the last `window` games, or None without that many."""
    if len(values) < window or len(values) < 2:
        return None
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    if sd == 0:
        return 'flat'
    recent = sum(values[-window:]) / window
    z = (recent - mean) / (sd / math.sqrt(window))
    if z >= TREND_Z:
        return 'up'
    if z <= -TREND_Z:
        return 'down'
    return 'flat'


def venue_split(games, values):
    """{'better': 'H' | 'A' | None, home/road means and counts}."""
    home = [v for g, v in zip(games, values) if g.get('homeRoad') == 'H']
    road = [v for g, v in zip(games, values) if g.get('homeRoad') == 'R']
    split = {'homeGames': len(home), 'roadGames': len(road), 'better': None}
    if len(home) >= MIN_SPLIT_GAMES and len(road) >= MIN_SPLIT_GAMES:
        home_mean, road_mean = sum(home) / len(home), sum(road) / len(road)
        split['better'] = 'H' if home_mean >= road_mean else 'A'
    return split


def pp_share(games):
    """
    {'recent', 'last'}: his share of his team's power-play time over his last
    `PP_GAMES` games and his last game, or None where the team had none (or
    the game predates the column).
    """
    # His last games, then whichever of them carry the data - never older
    # games standing in for missing recent ones, which would pass off
    # October's usage as this week's.
    recent = [g for g in games[-PP_GAMES:] if g.get('teamPpTimeOnIce') is not None]
    if not recent:
        return None

    def share(window):
        team = sum(_number(g.get('teamPpTimeOnIce')) for g in window)
        mine = sum(_number(g.get('ppTimeOnIce')) for g in window)
        return round(mine / team, 3) if team > 0 else None

    last = games[-1:] if games[-1].get('teamPpTimeOnIce') is not None else []
    return {'recent': share(recent), 'last': share(last) if last else None,
            'games': len(recent)}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number
