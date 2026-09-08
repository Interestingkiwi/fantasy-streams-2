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

Pages are capped at 100 rows however large a limit is asked for, so a full
season is roughly 670 requests and takes about twenty minutes. Re-running a
range replaces it rather than duplicating, so an interrupted backfill can
simply be run again.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/8/2026
"""

import argparse
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

# The API refuses offsets past this and just stops, without an error. Anything
# reaching it has been truncated, so treat it as a failure rather than a result.
PAGE_CEILING = 10000

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
    for mapping in (SHARED, SKATER, REALTIME, GOALIE):
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
    PRIMARY KEY ("playerId", "gameId")
)
'''
INDEX = ('CREATE INDEX IF NOT EXISTS player_game_stats_date '
         'ON player_game_stats ("gameDate")')


def fetch(kind, start, end, kind_path=None):
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
                              window_end.isoformat(), kind_path)
        window_start = window_end + timedelta(days=1)

    return rows


def _fetch_window(kind, start, end, kind_path=None):
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
        }
        url = (BASE_URL + kind_path) if kind_path else SUMMARY_URL.format(kind=kind)
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        total = payload.get('total', 0)
        batch = payload.get('data', [])
        if not batch:
            break

        rows += batch
        offset += len(batch)
        time.sleep(PAUSE)

    return rows


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
        for column in REALTIME.values():
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', help='First game date (YYYY-MM-DD).')
    parser.add_argument('--end', help='Last game date (YYYY-MM-DD).')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    start = args.start or yesterday
    end = args.end or start
    if start > end:
        raise SystemExit('--start must not be after --end.')

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

    goalies = [shape(row, GOALIE) for row in fetch('goalie', start, end)]
    log.info('Goalie rows: %d', len(goalies))

    # A goalie who took a shift would appear in both; the goalie row is the
    # useful one, so it wins.
    merged.update({(row['playerId'], row['gameId']): row for row in goalies})

    log.info('Wrote %d rows.', write(list(merged.values()), start, end))


if __name__ == '__main__':
    main()
