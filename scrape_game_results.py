"""
Scrapes per-game player results into `player_game_stats`.

The port of the old repo's nightly job (`jobs/toi_script.py`) down to the part
everything else was derived from: one row per player per game, carrying who
they played and whether they were at home.

Run nightly for last night, or over any range to backfill:

    python scrape_game_results.py                      # yesterday
    python scrape_game_results.py --start 2025-10-07 --end 2026-04-16

**The historical range is the point.** `api.nhle.com` serves completed dates
as readily as last night's, so a finished season can be pulled on demand -
which is what lets the optimizer's assumptions be checked against real
outcomes before a new season has played a single game. Two of them were
waiting on exactly this data:

- `matchup_weights` approximates category variance as Poisson because nothing
  measured was available. These rows give the real per-game spread.
- `opponent_strength` is calibrated from team rates, but whether the
  adjustment actually tracks player production could not be tested. Every row
  here carries `opponentTeamAbbrev` and `homeRoad`, so now it can.

Skaters and goalies share the table, with the columns the other one does not
use left NULL - the same shape `final_projections` uses.

Hits and blocked shots come from a **third** endpoint, `skater/realtime`;
`skater/summary` does not carry them. They are worth the extra pass because 21
of the 25 imported leagues score at least one of them, and because
`opponent_strength` deliberately leaves both unadjusted for want of anything
that predicts them - a claim only per-game data can ever revisit.

**The API stops paging at an offset of 10,000**, and does so silently - a
season-long request comes back looking successful with 9,600 rows spanning the
right dates, because the result is sorted before it is truncated. A sparse,
top-heavy sample presented as a complete one is the worst possible input to a
calibration, so the range is split into chunks small enough that no single
query approaches the ceiling, and `PAGE_CEILING` raises if one ever does.

**Power-play share needs two more reports.** A skater's `ppTimeOnIce` comes
from `skater/timeonice`, and his team's power-play time in the same game from
`team/powerplaytime` - a team report, so it is keyed onto each player row by
(gameId, opponentTeamAbbrev): the team whose opponent that is. Their ratio is
the player's share of his team's power play, which is the PP Util column on
League Home's Lineups tab. `--pp-only` fills just these two columns over a range
already scraped, for the season that predates them.

Pages are capped at 100 rows however large a limit is asked for, so a full
season is roughly 670 requests and takes about twenty minutes. Re-running a
range replaces it rather than duplicating, so an interrupted backfill can
simply be run again.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/21/2026
"""

import argparse
import json
import logging
import time
from datetime import date, timedelta

import requests

from db import engine, text

log = logging.getLogger(__name__)

BASE_URL = 'https://api.nhle.com/stats/rest/en/'
SUMMARY_URL = BASE_URL + '{kind}/summary'
PAGE = 100
TIMEOUT = 60
PAUSE = 0.15          # polite gap between requests
# A season is hundreds of requests, and the API times out now and then; one
# timeout used to throw away a twenty-minute backfill.
RETRIES = 3
RETRY_PAUSE = 5.0
# A power-play backfill writes every this many days, so a failure costs a chunk.
BACKFILL_DAYS = 14

# The API refuses offsets past this and just stops, without an error. Anything
# reaching it has been truncated, so treat it as a failure rather than a result.
PAGE_CEILING = 10000

# Paging is only stable if the sort is total - the tiebreaker has to be unique
# across the whole result set, not merely within a game. Without any sort, one
# busy night returned 576 rows of which 15 were duplicates, losing 15 rows
# outright. Sorting on playerId alone fixes a single day but still loses about
# five rows a week, because a player appears in several games in a window and
# those rows tie with each other. (playerId, gameId) is the table's own primary
# key and settles every tie.
STABLE_SORT = json.dumps([{'property': 'playerId', 'direction': 'ASC'},
                          {'property': 'gameId', 'direction': 'ASC'}])

# Days per query. A week of skater rows is about 1,950, so this leaves a wide
# margin under the ceiling while keeping the request count reasonable.
CHUNK_DAYS = 7

# API field -> column. The identity block is shared; the rest is whichever of
# the two endpoints supplied the row.
SHARED = {
    'playerId': 'playerId',
    'gameId': 'gameId',
    'gameDate': 'gameDate',
    'teamAbbrev': 'teamAbbrev',
    'opponentTeamAbbrev': 'opponentTeamAbbrev',
    'homeRoad': 'homeRoad',
}
SKATER = {
    'skaterFullName': 'fullName',
    'positionCode': 'positionCode',
    'goals': 'goals',
    'assists': 'assists',
    'points': 'points',
    'plusMinus': 'plusMinus',
    'penaltyMinutes': 'penaltyMinutes',
    'ppGoals': 'ppGoals',
    'ppPoints': 'ppPoints',
    'shGoals': 'shGoals',
    'shPoints': 'shPoints',
    'shots': 'shots',
    'gameWinningGoals': 'gameWinningGoals',
    'timeOnIcePerGame': 'timeOnIce',
}
# hits and blocks live on skater/realtime rather than skater/summary, so they
# need their own pass, merged onto the same (playerId, gameId).
REALTIME = {
    'hits': 'hits',
    'blockedShots': 'blockedShots',
    'takeaways': 'takeaways',
    'giveaways': 'giveaways',
}
# A skater's power-play ice time, from skater/timeonice - a fourth pass.
TIMEONICE = {
    'ppTimeOnIce': 'ppTimeOnIce',
}
# His team's power-play time in that game, from the team/powerplaytime report.
TEAM_PP_COLUMN = 'teamPpTimeOnIce'

# A team report has no playerId; (teamId, gameId) is its unique key.
TEAM_SORT = json.dumps([{'property': 'teamId', 'direction': 'ASC'},
                        {'property': 'gameId', 'direction': 'ASC'}])

GOALIE = {
    'goalieFullName': 'fullName',
    'gamesStarted': 'gamesStarted',
    'wins': 'wins',
    'losses': 'losses',
    'otLosses': 'otLosses',
    'saves': 'saves',
    'shotsAgainst': 'shotsAgainst',
    'goalsAgainst': 'goalsAgainst',
    'shutouts': 'shutouts',
    'timeOnIce': 'timeOnIce',
}

def _columns():
    seen, columns = set(), []
    for mapping in (SHARED, SKATER, REALTIME, TIMEONICE, GOALIE,
                    {TEAM_PP_COLUMN: TEAM_PP_COLUMN}):
        for column in mapping.values():
            if column not in seen:
                seen.add(column)
                columns.append(column)
    return columns


COLUMNS = _columns()

CREATE = '''
CREATE TABLE IF NOT EXISTS player_game_stats (
    "playerId"           BIGINT NOT NULL,
    "gameId"             BIGINT NOT NULL,
    "gameDate"           TEXT,
    "teamAbbrev"         TEXT,
    "opponentTeamAbbrev" TEXT,
    "homeRoad"           TEXT,
    "fullName"           TEXT,
    "positionCode"       TEXT,
    "goals"              DOUBLE PRECISION,
    "assists"            DOUBLE PRECISION,
    "points"             DOUBLE PRECISION,
    "plusMinus"          DOUBLE PRECISION,
    "penaltyMinutes"     DOUBLE PRECISION,
    "ppGoals"            DOUBLE PRECISION,
    "ppPoints"           DOUBLE PRECISION,
    "shGoals"            DOUBLE PRECISION,
    "shPoints"           DOUBLE PRECISION,
    "shots"              DOUBLE PRECISION,
    "hits"               DOUBLE PRECISION,
    "blockedShots"       DOUBLE PRECISION,
    "takeaways"          DOUBLE PRECISION,
    "giveaways"          DOUBLE PRECISION,
    "gameWinningGoals"   DOUBLE PRECISION,
    "timeOnIce"          DOUBLE PRECISION,
    "gamesStarted"       DOUBLE PRECISION,
    "wins"               DOUBLE PRECISION,
    "losses"             DOUBLE PRECISION,
    "otLosses"           DOUBLE PRECISION,
    "saves"              DOUBLE PRECISION,
    "shotsAgainst"       DOUBLE PRECISION,
    "goalsAgainst"       DOUBLE PRECISION,
    "shutouts"           DOUBLE PRECISION,
    "ppTimeOnIce"        DOUBLE PRECISION,
    "teamPpTimeOnIce"    DOUBLE PRECISION,
    PRIMARY KEY ("playerId", "gameId")
)
'''
INDEX = ('CREATE INDEX IF NOT EXISTS player_game_stats_date '
         'ON player_game_stats ("gameDate")')


def fetch(kind, start, end, kind_path=None, sort=STABLE_SORT):
    """
    Every row of one endpoint across a date range.

    Split into `CHUNK_DAYS` windows, because the API silently truncates at an
    offset of 10,000 and a season-long query would quietly return a biased
    subset rather than an error.
    """
    rows = []
    first, last = date.fromisoformat(start), date.fromisoformat(end)

    window_start = first
    while window_start <= last:
        window_end = min(window_start + timedelta(days=CHUNK_DAYS - 1), last)
        rows += _fetch_window(kind, window_start.isoformat(),
                              window_end.isoformat(), kind_path, sort)
        window_start = window_end + timedelta(days=1)

    return rows


def _fetch_window(kind, start, end, kind_path=None, sort=STABLE_SORT):
    """One window, paged until exhausted. Raises rather than truncating."""
    rows, offset, total = [], 0, None

    while total is None or offset < total:
        if offset >= PAGE_CEILING:
            raise RuntimeError(
                f'{kind} {start}..{end} hit the {PAGE_CEILING}-row API ceiling '
                f'({total} available). Lower CHUNK_DAYS - the result would be '
                f'silently truncated.')
        params = {
            'isAggregate': 'false', 'isGame': 'true',
            'start': offset, 'limit': PAGE,
            'cayenneExp': (f'gameDate>="{start}" and gameDate<="{end}" '
                           f'and gameTypeId=2'),
            'factCayenneExp': 'gamesPlayed>=1',
            'sort': sort,
        }
        url = (BASE_URL + kind_path) if kind_path else SUMMARY_URL.format(kind=kind)
        response = _get(url, params)
        payload = response.json()

        total = payload.get('total', 0)
        batch = payload.get('data', [])
        if not batch:
            break

        rows += batch
        offset += len(batch)
        time.sleep(PAUSE)

    return rows


def _get(url, params):
    """One request, retried on a timeout or dropped connection."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == RETRIES:
                raise
            log.warning('%s - retrying (%d of %d).', exc, attempt, RETRIES - 1)
            time.sleep(RETRY_PAUSE * attempt)
    raise RuntimeError('unreachable')


def shape(raw, mapping):
    """One API row as a `player_game_stats` row, with unused columns as None."""
    row = {column: None for column in COLUMNS}
    for field, column in {**SHARED, **mapping}.items():
        row[column] = raw.get(field)
    return row


def write(rows, start, end):
    """Replace the range, so re-running a night or a backfill is safe."""
    if not rows:
        log.warning('Nothing to write for %s..%s.', start, end)
        return 0

    quoted = ', '.join(f'"{c}"' for c in COLUMNS)
    placeholders = ', '.join(f':{c}' for c in COLUMNS)

    with engine.begin() as conn:
        conn.execute(text(CREATE))
        conn.execute(text(INDEX))
        # The table predates the realtime columns; CREATE IF NOT EXISTS will
        # not add them to one that already exists.
        for column in [*REALTIME.values(), *TIMEONICE.values(), TEAM_PP_COLUMN]:
            conn.execute(text(f'ALTER TABLE player_game_stats '
                              f'ADD COLUMN IF NOT EXISTS "{column}" DOUBLE PRECISION'))
        conn.execute(text('DELETE FROM player_game_stats '
                          'WHERE "gameDate" BETWEEN :start AND :end'),
                     {'start': start, 'end': end})
        # Chunked so a season-sized backfill does not build one enormous
        # parameter list.
        for index in range(0, len(rows), 5000):
            conn.execute(text(f'INSERT INTO player_game_stats ({quoted}) '
                              f'VALUES ({placeholders})'), rows[index:index + 5000])
    return len(rows)


def run(start, end):
    """Scrape a date range into player_game_stats. Returns the row count."""
    if start > end:
        raise ValueError('start must not be after end.')

    log.info('Fetching %s..%s in %d-day chunks', start, end, CHUNK_DAYS)
    skaters = [shape(row, SKATER) for row in fetch('skater', start, end)]
    log.info('Skater rows: %d', len(skaters))

    merged = {(row['playerId'], row['gameId']): row for row in skaters}

    # Hits and blocks, merged onto the skater rows already collected. A
    # realtime row with no summary row would be odd, but it is kept rather
    # than dropped so nothing goes missing silently.
    realtime = 0
    for raw in fetch('skater', start, end, kind_path='skater/realtime'):
        key = (raw.get('playerId'), raw.get('gameId'))
        row = merged.get(key)
        if row is None:
            row = shape(raw, REALTIME)
            merged[key] = row
        else:
            for field, column in REALTIME.items():
                row[column] = raw.get(field)
        realtime += 1
    log.info('Realtime rows merged: %d', realtime)

    _merge_power_play(merged, start, end)

    goalies = [shape(row, GOALIE) for row in fetch('goalie', start, end)]
    log.info('Goalie rows: %d', len(goalies))

    # A goalie who took a shift would appear in both; the goalie row is the
    # useful one, so it wins.
    merged.update({(row['playerId'], row['gameId']): row for row in goalies})

    written = write(list(merged.values()), start, end)
    log.info('Wrote %d rows.', written)
    return written


def team_power_play(start, end):
    """{(gameId, opponentTeamAbbrev): the team's power-play seconds}."""
    return {(raw.get('gameId'), raw.get('opponentTeamAbbrev')): raw.get('timeOnIcePp')
            for raw in fetch('team', start, end, kind_path='team/powerplaytime',
                             sort=TEAM_SORT)}


def _merge_power_play(merged, start, end):
    """Put each skater's PP time, and his team's, onto the rows in `merged`."""
    for raw in fetch('skater', start, end, kind_path='skater/timeonice'):
        row = merged.get((raw.get('playerId'), raw.get('gameId')))
        if row is not None:
            for field, column in TIMEONICE.items():
                row[column] = raw.get(field)
    team_pp = team_power_play(start, end)
    for row in merged.values():
        row[TEAM_PP_COLUMN] = team_pp.get((row['gameId'], row['opponentTeamAbbrev']))
    log.info('Power-play time merged for %d team-games.', len(team_pp))


def backfill_power_play(start, end):
    """
    Fill only the two power-play columns over a range already scraped.

    A third of a full re-scrape's requests: the season scraped before these
    columns existed does not need its other twenty re-fetched. Written every
    `BACKFILL_DAYS`, so an interrupted run has kept what it finished and can
    be restarted from where it stopped.
    """
    total = 0
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    while first <= last:
        chunk_end = min(first + timedelta(days=BACKFILL_DAYS - 1), last)
        total += _backfill_power_play(first.isoformat(), chunk_end.isoformat())
        first = chunk_end + timedelta(days=1)
    return total


def _backfill_power_play(start, end):
    rows = {(r['playerId'], r['gameId']): dict(r) for r in _existing(start, end)}
    _merge_power_play(rows, start, end)
    updates = [{'playerId': r['playerId'], 'gameId': r['gameId'],
                'pp': r.get('ppTimeOnIce'), 'team': r.get(TEAM_PP_COLUMN)}
               for r in rows.values()]
    with engine.begin() as conn:
        for column in [*TIMEONICE.values(), TEAM_PP_COLUMN]:
            conn.execute(text(f'ALTER TABLE player_game_stats '
                              f'ADD COLUMN IF NOT EXISTS "{column}" DOUBLE PRECISION'))
        for index in range(0, len(updates), 5000):
            conn.execute(text('UPDATE player_game_stats SET "ppTimeOnIce" = :pp, '
                              '"teamPpTimeOnIce" = :team '
                              'WHERE "playerId" = :playerId AND "gameId" = :gameId'),
                         updates[index:index + 5000])
    log.info('Power-play columns filled on %d rows, %s..%s.', len(updates), start, end)
    return len(updates)


def _existing(start, end):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            'SELECT "playerId", "gameId", "opponentTeamAbbrev" FROM player_game_stats '
            'WHERE "gameDate" BETWEEN :start AND :end'), {'start': start, 'end': end})]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', help='First game date (YYYY-MM-DD).')
    parser.add_argument('--end', help='Last game date (YYYY-MM-DD).')
    parser.add_argument('--pp-only', action='store_true',
                        help='Fill only the power-play columns on rows already scraped.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    start = args.start or yesterday
    if args.pp_only:
        backfill_power_play(start, args.end or start)
    else:
        run(start, args.end or start)


if __name__ == '__main__':
    main()
