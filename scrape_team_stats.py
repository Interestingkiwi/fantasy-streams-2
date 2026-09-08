"""
Scrapes every NHL team's strength into `team_stats`.

Feeds `opponent_strength.py`, which nudges a player's projection according to
who he is playing. Two windows are written:

- `season`  - the season to date (or a completed season, given `--season`).
- `week`    - a trailing seven days, which is noisy and is kept for display
              rather than for the optimizer. See opponent_strength.py.

Ported from the old repo's `jobs/toi_script.py` (`fetch_team_stats_summary` /
`fetch_team_stats_weekly`) with two changes:

- **The season is a parameter, not a constant.** The old version hardcoded
  `seasonId=20252026`, which is the same trap `scrape_nhl_schedule.py` was
  written to avoid. Default is the season `nhl_schedule` covers.
- **Team codes are looked up, not hardcoded.** The old `FRANCHISE_TO_TRICODE_MAP`
  had to be edited whenever a franchise moved. The standings endpoint carries
  both the full name and the tricode, so the map is built at run time; all 32
  teams join on full name, and the codes match `nhl_schedule` exactly.

    python scrape_team_stats.py                  # current season, both windows
    python scrape_team_stats.py --season 20252026
    python scrape_team_stats.py --week-end 2026-01-18

Run from the repo root; it uses the web app's `db.py`, not the pipeline's.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/8/2026
"""

import argparse
import logging
from datetime import date, timedelta

import requests

from db import engine, text

log = logging.getLogger(__name__)

SUMMARY_URL = 'https://api.nhle.com/stats/rest/en/team/summary'
STANDINGS_URL = 'https://api-web.nhle.com/v1/standings/{on_date}'
TIMEOUT = 30

# API field -> our column. Everything here is a per-game rate or a percentage,
# so no column needs dividing by games played later.
FIELDS = {
    'gamesPlayed': 'gamesPlayed',
    'powerPlayPct': 'powerPlayPct',
    'penaltyKillPct': 'penaltyKillPct',
    'goalsForPerGame': 'goalsForPerGame',
    'goalsAgainstPerGame': 'goalsAgainstPerGame',
    'shotsForPerGame': 'shotsForPerGame',
    'shotsAgainstPerGame': 'shotsAgainstPerGame',
}

CREATE = '''
CREATE TABLE IF NOT EXISTS team_stats (
    "teamCode"            TEXT NOT NULL,
    "statWindow"          TEXT NOT NULL,
    "gamesPlayed"         DOUBLE PRECISION,
    "powerPlayPct"        DOUBLE PRECISION,
    "penaltyKillPct"      DOUBLE PRECISION,
    "goalsForPerGame"     DOUBLE PRECISION,
    "goalsAgainstPerGame" DOUBLE PRECISION,
    "shotsForPerGame"     DOUBLE PRECISION,
    "shotsAgainstPerGame" DOUBLE PRECISION,
    "updatedAt"           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY ("teamCode", "statWindow")
)
'''


def season_from_schedule():
    """
    The season `nhl_schedule` covers, as Yahoo-style `20262027`.

    Reads the schedule rather than the clock so a rebuild in August does not
    quietly scrape the wrong season.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            'SELECT min("gameDate") AS lo, max("gameDate") AS hi FROM nhl_schedule'
        )).fetchone()

    if not row or not row.lo:
        raise RuntimeError('nhl_schedule is empty - run the preseason pipeline first.')

    start = date.fromisoformat(row.lo)
    return int(f'{start.year}{start.year + 1}')


def team_codes(on_date):
    """{team full name: tricode}, from the standings on `on_date`."""
    response = requests.get(STANDINGS_URL.format(on_date=on_date), timeout=TIMEOUT)
    response.raise_for_status()
    rows = response.json().get('standings', [])

    codes = {}
    for row in rows:
        name = (row.get('teamName') or {}).get('default')
        code = (row.get('teamAbbrev') or {}).get('default')
        if name and code:
            codes[name] = code

    if not codes:
        raise RuntimeError(
            f'No standings for {on_date}; pick a date inside a played season.')
    return codes


def fetch_summary(cayenne):
    """The team summary rows for a cayenne filter expression."""
    params = {'isAggregate': 'false', 'isGame': 'false', 'start': 0,
              'limit': 50, 'cayenneExp': cayenne}
    if 'gameDate' in cayenne:
        params['isAggregate'] = 'true'

    response = requests.get(SUMMARY_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json().get('data', [])


def collect(cayenne, codes, window):
    """Summary rows turned into `team_stats` rows, keyed by tricode."""
    rows = []
    unmatched = []

    for team in fetch_summary(cayenne):
        # The season endpoint names the team, the aggregate one names the
        # franchise; they agree for every current club.
        name = team.get('teamFullName') or team.get('franchiseName')
        code = codes.get(name)
        if not code:
            unmatched.append(name)
            continue

        row = {'teamCode': code, 'statWindow': window}
        row.update({column: team.get(field) for field, column in FIELDS.items()})
        rows.append(row)

    if unmatched:
        # Loudly, not silently: a missing team means a lopsided league mean,
        # which is exactly what opponent_strength must not be handed.
        log.warning('%d team(s) had no tricode and were dropped: %s',
                    len(unmatched), ', '.join(str(n) for n in unmatched))
    return rows


def write(rows):
    """Replace the rows for the windows present, leaving other windows alone."""
    if not rows:
        log.warning('Nothing to write.')
        return 0

    windows = sorted({row['statWindow'] for row in rows})
    columns = ['teamCode', 'statWindow'] + list(FIELDS.values())
    placeholders = ', '.join(f':{c}' for c in columns)
    quoted = ', '.join(f'"{c}"' for c in columns)

    with engine.begin() as conn:
        conn.execute(text(CREATE))
        conn.execute(text('DELETE FROM team_stats WHERE "statWindow" = ANY(:windows)'),
                     {'windows': windows})
        conn.execute(text(f'INSERT INTO team_stats ({quoted}) VALUES ({placeholders})'),
                     rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int,
                        help='Season id, e.g. 20252026. Defaults to the one '
                             'nhl_schedule covers.')
    parser.add_argument('--week-end', help='Last day of the trailing week '
                                           '(YYYY-MM-DD). Defaults to yesterday.')
    parser.add_argument('--skip-week', action='store_true',
                        help='Season window only.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    season = args.season or season_from_schedule()
    week_end = date.fromisoformat(args.week_end) if args.week_end \
        else date.today() - timedelta(days=1)
    week_start = week_end - timedelta(days=6)

    # Standings need a date inside a season that has actually been played.
    # The season's own back half is a safe bet for a completed season, and
    # today works for one in progress.
    standings_date = min(date.today(), date(season // 10000 + 1, 4, 1))
    codes = team_codes(standings_date.isoformat())
    log.info('Resolved %d team codes from the standings on %s.',
             len(codes), standings_date)

    rows = collect(f'seasonId={season} and gameTypeId=2', codes, 'season')
    log.info('Season %s: %d teams.', season, len(rows))

    if not args.skip_week:
        weekly = collect(
            f'gameDate>="{week_start}" and gameDate<="{week_end}" and gameTypeId=2',
            codes, 'week')
        log.info('Week %s..%s: %d teams.', week_start, week_end, len(weekly))
        rows += weekly

    log.info('Wrote %d rows to team_stats.', write(rows))


if __name__ == '__main__':
    main()
