"""
The nightly NHL update: last night's results, then every team-strength window.

    python nightly_update.py               # yesterday
    python nightly_update.py --date 2026-10-05
    python nightly_update.py --force       # ignore the season gate

Runs `scrape_game_results` for one date and then `scrape_team_stats` for all
six windows, in that order - the team windows are rolled up from games that
have to be in already.

**It waits for the season, so it can be scheduled now.** Before the season's
first game the job exits cleanly having done nothing, so the cron can be
created today rather than remembered in October. The first real run is
therefore the morning after opening night: the season opens 2026-09-29, the
job runs on the 30th and scrapes the 29th. The gate reads the first game date
out of `nhl_schedule` rather than hardcoding it, the same way
`season_config.season_game_count()` reads the season length rather than
assuming 82.

`--force` overrides the gate, which is what backfills and the tests use.

**No Redis and no worker.** This is a plain script, not an `enqueue()` job:
it runs on a wall clock rather than in response to anything, needs no dedup
across processes, and asking for an always-on worker to run one command a day
would be the wrong trade. Deployed as a Render cron service - see
`render.yaml`. The RQ path in `jobs.py` stays for the Phase 2 league sync,
which really is triggered by users and really does need dedup.

Exit codes: 0 on success or a deliberate no-op, 1 on failure - so a cron
alerting on non-zero does not fire every night before the season starts.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 9/8/2026
"""

import argparse
import logging
import sys
from datetime import date, timedelta

import scrape_game_results
import scrape_team_stats
from db import engine, text

log = logging.getLogger("nightly")


def season_first_game():
    """
    The season's first game date, as a `date`, or None if no schedule loaded.

    Read rather than hardcoded: the preseason pipeline fills `nhl_schedule`
    from the API, so this follows whatever season it holds.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            'SELECT min("gameDate") AS opening FROM nhl_schedule')).fetchone()

    if not row or not row.opening:
        return None
    try:
        return date.fromisoformat(row.opening)
    except (TypeError, ValueError):
        return None


def should_run(target, opening):
    """
    Whether there is anything to scrape for `target`.

    False before opening night, when the previous day has no games and every
    endpoint would return nothing. Also false when no schedule is loaded at
    all - better a clean no-op than a nightly failure against an empty table.
    """
    if opening is None:
        return False
    return target >= opening


def run(target, force=False):
    """Scrape one night. Returns True if it did work, False if it stood down."""
    opening = season_first_game()

    if not force and not should_run(target, opening):
        if opening is None:
            log.info("No schedule in nhl_schedule - standing down. "
                     "Run the preseason pipeline first.")
        else:
            days = (opening - target).days
            log.info("Season opens %s, %d day(s) after %s - nothing to scrape yet.",
                     opening, days, target)
        return False

    stamp = target.isoformat()
    log.info("Nightly update for %s", stamp)

    games = scrape_game_results.run(stamp, stamp)
    log.info("Game results: %d rows.", games)

    # Team windows are rolled up from games, so they follow rather than lead.
    # week_end is the night just scraped, which keeps the trailing windows
    # aligned with the data behind them.
    teams = scrape_team_stats.run(week_end=target)
    log.info("Team stats: %d rows.", teams)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Night to scrape (YYYY-MM-DD). "
                                       "Defaults to yesterday.")
    parser.add_argument("--force", action="store_true",
                        help="Run even before the season has started.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    target = (date.fromisoformat(args.date) if args.date
              else date.today() - timedelta(days=1))

    try:
        run(target, force=args.force)
    except Exception:                                       # noqa: BLE001
        # Non-zero so a cron alert fires on a real failure - and only then,
        # since standing down before the season is a success.
        log.exception("Nightly update failed for %s.", target)
        sys.exit(1)


if __name__ == "__main__":
    main()
