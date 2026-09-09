"""
How long the upcoming season is, read from the scraped schedule.

The season stopped being 82 games: the 2026-27 CBA moved it to 84, and pacing
per-game rates against a hardcoded 82 quietly clipped every counting stat about
2.4% low. Hardcoding 84 would just move the staleness one season down the road,
so the number comes from nhl_schedule - whatever the API says each team is
actually scheduled to play.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

from datetime import date

from sqlalchemy import text

from db_config import engine

# Seasons the historic feeds cover were all 82 games. Durability drawn from them
# is a share of *that* season, which is why it is converted to a rate before
# being applied to a season of a different length.
HISTORICAL_SEASON_GAMES = 82

# Used only if nhl_schedule has not been built yet
FALLBACK_SEASON_GAMES = 82

_TEAM_GAME_COUNTS = text("""
    SELECT MAX(played) FROM (
        SELECT team, COUNT(*) AS played FROM (
            SELECT "homeTeam" AS team FROM nhl_schedule
            UNION ALL
            SELECT "awayTeam" AS team FROM nhl_schedule
        ) appearances
        GROUP BY team
    ) per_team
""")


_cached_count = None


def season_game_count():
    """Games each team is scheduled to play this season."""
    global _cached_count
    if _cached_count is not None:
        return _cached_count

    _cached_count = _read_season_game_count()
    return _cached_count


_SEASON_DATES = text('SELECT MIN("gameDate"), MAX("gameDate") FROM nhl_schedule')

# Used only if nhl_schedule has not been built yet. The 2026-27 dates, so a
# fallback run is stale rather than absurd - and it says so.
FALLBACK_SEASON_START = date(2026, 10, 8)
FALLBACK_DAYS_PER_GAME = 2.0

_cached_dates = None


def season_date_range():
    """(first game, last game) of the coming season, from the scraped schedule."""
    global _cached_dates
    if _cached_dates is not None:
        return _cached_dates

    _cached_dates = _read_season_dates()
    return _cached_dates


def _read_season_dates():
    try:
        with engine.connect() as conn:
            first, last = conn.execute(_SEASON_DATES).fetchone()
    except Exception as exc:
        print(f" -> [WARN] Could not read nhl_schedule ({exc}); assuming the season "
              f"opens {FALLBACK_SEASON_START}.")
        return None, None

    if not first or not last:
        print(" -> [WARN] nhl_schedule is empty; assuming the season opens "
              f"{FALLBACK_SEASON_START}. Run scrape_nhl_schedule.py first.")
        return None, None

    return date.fromisoformat(str(first)), date.fromisoformat(str(last))


def season_start_date():
    """Opening night. Falls back to a stale constant if the schedule is missing."""
    first, _ = season_date_range()
    return first or FALLBACK_SEASON_START


def days_per_game():
    """Calendar days between one team's games, on average.

    An injury is reported as a return *date*, so turning it into games missed
    needs the rate at which a team actually plays. Hardcoding 2.0 assumed an
    82-game season packed into a shorter calendar; 84 games from 2026-09-29 to
    2027-04-10 is one game every 2.30 days, so a flat 2.0 overstated every
    absence by about 15%.
    """
    first, last = season_date_range()
    if not first or not last:
        return FALLBACK_DAYS_PER_GAME

    span = (last - first).days
    games = season_game_count()
    if span <= 0 or games <= 0:
        return FALLBACK_DAYS_PER_GAME

    return span / games


def _read_season_game_count():
    try:
        with engine.connect() as conn:
            games = conn.execute(_TEAM_GAME_COUNTS).scalar()
    except Exception as exc:
        print(f" -> [WARN] Could not read nhl_schedule ({exc}); "
              f"assuming {FALLBACK_SEASON_GAMES} games.")
        return FALLBACK_SEASON_GAMES

    if not games:
        print(f" -> [WARN] nhl_schedule is empty; assuming {FALLBACK_SEASON_GAMES} games. "
              "Run scrape_nhl_schedule.py first.")
        return FALLBACK_SEASON_GAMES

    return int(games)
