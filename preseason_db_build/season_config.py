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
