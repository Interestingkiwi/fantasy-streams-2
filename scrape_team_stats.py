"""
Scrapes every NHL team's strength into `team_stats`.

Feeds `opponent_strength.py`, which nudges a player's projection according to
who he is playing. Six windows are written:

- `season`       - the season to date (or a completed season, given `--season`).
- `season-home`  - the same, restricted to home games.
- `season-road`  - and to road games.
- `last-1w`      - trailing 7 days.
- `last-2w`      - trailing 14 days.
- `last-4w`      - trailing 28 days.

The trailing windows are for form. Measured on 2025-26 they add little that
season-to-date does not already carry - partial correlations of 0.03 to 0.08
against next week's rate - so `opponent_strength` blends them in at a low
weight rather than treating them as a separate signal. They are also what a
manager wants to *look* at, and they accumulate for a better answer once more
than one season is in hand.

The home/road pair is what `opponent_strength` uses for venue: measured on the
completed 2025-26 season, teams score 2.2% more at home, take 2.0% more shots,
and win 4.4% more often. That is a larger effect than the opponent adjustment
itself, and it is free from data already being fetched.

Ported from the old repo's `jobs/toi_script.py` (`fetch_team_stats_summary` /
`fetch_team_stats_weekly`) with two changes:

- **The season is a parameter, not a constant.** The old version hardcoded
  `seasonId=20252026`, which is the same trap `scrape_nhl_schedule.py` was
  written to avoid. Default is the season `nhl_schedule` covers.
- **Team codes are looked up, not hardcoded.** The old `FRANCHISE_TO_TRICODE_MAP`
  had to be edited whenever a franchise moved. The standings endpoint carries
  both the full name and the tricode, so the map is built at run time; all 32
  teams join on full name, and the codes match `nhl_schedule` exactly.

    python scrape_team_stats.py                  # current season, all windows
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
    'wins': 'wins',
    'powerPlayPct': 'powerPlayPct',
    'penaltyKillPct': 'penaltyKillPct',
    'goalsForPerGame': 'goalsForPerGame',
    'goalsAgainstPerGame': 'goalsAgainstPerGame',
    'shotsForPerGame': 'shotsForPerGame',
    'shotsAgainstPerGame': 'shotsAgainstPerGame',
}

# Window names this script no longer writes. Cleared on every run, or a
# renamed window would sit in the table forever - write() only replaces the
# windows it is given.
LEGACY_WINDOWS = ('week',)

CREATE = '''
CREATE TABLE IF NOT EXISTS team_stats (
    "teamCode"            TEXT NOT NULL,
    "statWindow"          TEXT NOT NULL,
    "gamesPlayed"         DOUBLE PRECISION,
    "wins"                DOUBLE PRECISION,
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


def _standings_on(on_date):
    """{team full name: tricode} from the standings on one date, or {}."""
    response = requests.get(STANDINGS_URL.format(on_date=on_date), timeout=TIMEOUT)
    response.raise_for_status()

    codes = {}
    for row in response.json().get('standings', []):
        name = (row.get('teamName') or {}).get('default')
        code = (row.get('teamAbbrev') or {}).get('default')
        if name and code:
            codes[name] = code
    return codes


def team_codes(on_date):
    """
    {team full name: tricode}, from the standings.

    Tries `on_date` first, then falls back through earlier dates. The endpoint
    returns nothing for a day before a season has been played, which is the
    normal state in September and would otherwise make the first run of the
    year - the morning after opening night - the one most likely to fail. The
    mapping barely changes between seasons, so an older date is a fine source
    for it.
    """
    asked = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    candidates = [asked,
                  date(asked.year, 4, 1),
                  date(asked.year - 1, 4, 1)]

    for candidate in candidates:
        codes = _standings_on(candidate.isoformat())
        if codes:
            if candidate != asked:
                log.info('No standings on %s; used %s for team codes.',
                         asked, candidate)
            return codes

    raise RuntimeError(
        f'No standings on any of {[c.isoformat() for c in candidates]}.')


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
        # The table predates the wins column, and CREATE IF NOT EXISTS will
        # not add it to one that already exists.
        conn.execute(text('ALTER TABLE team_stats '
                          'ADD COLUMN IF NOT EXISTS "wins" DOUBLE PRECISION'))
        conn.execute(text('DELETE FROM team_stats WHERE "statWindow" = ANY(:windows)'),
                     {'windows': windows + list(LEGACY_WINDOWS)})
        conn.execute(text(f'INSERT INTO team_stats ({quoted}) VALUES ({placeholders})'),
                     rows)
    return len(rows)


def run(season=None, week_end=None, skip_week=False, skip_splits=False):
    """Scrape every window into team_stats. Returns the row count."""
    season = season or season_from_schedule()
    week_end = week_end or (date.today() - timedelta(days=1))

    # Standings need a date inside a season that has actually been played.
    # The season's own back half is a safe bet for a completed season, and
    # today works for one in progress.
    standings_date = min(date.today(), date(season // 10000 + 1, 4, 1))
    codes = team_codes(standings_date.isoformat())
    log.info('Resolved %d team codes from the standings on %s.',
             len(codes), standings_date)

    rows = collect(f'seasonId={season} and gameTypeId=2', codes, 'season')
    log.info('Season %s: %d teams.', season, len(rows))

    if not skip_splits:
        for side, window in (('H', 'season-home'), ('R', 'season-road')):
            split = collect(
                f'seasonId={season} and gameTypeId=2 and homeRoad="{side}"',
                codes, window)
            log.info('%s: %d teams.', window, len(split))
            rows += split

    if not skip_week:
        for weeks, window in ((1, 'last-1w'), (2, 'last-2w'), (4, 'last-4w')):
            span_start = week_end - timedelta(days=7 * weeks - 1)
            form = collect(
                f'gameDate>="{span_start}" and gameDate<="{week_end}" '
                f'and gameTypeId=2', codes, window)
            log.info('%s (%s..%s): %d teams.', window, span_start, week_end, len(form))
            rows += form

    written = write(rows)
    log.info('Wrote %d rows to team_stats.', written)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int,
                        help='Season id, e.g. 20252026. Defaults to the one '
                             'nhl_schedule covers.')
    parser.add_argument('--week-end', help='Last day of the trailing windows '
                                           '(YYYY-MM-DD). Defaults to yesterday.')
    parser.add_argument('--skip-week', action='store_true',
                        help='Skip the trailing-form windows.')
    parser.add_argument('--skip-splits', action='store_true',
                        help='Skip the home/road windows.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    run(season=args.season,
        week_end=date.fromisoformat(args.week_end) if args.week_end else None,
        skip_week=args.skip_week, skip_splits=args.skip_splits)


if __name__ == '__main__':
    main()
