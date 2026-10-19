"""
Everyone an outside source could name, for matching its names to NHL ids.

Yahoo's lineups, its transactions and ESPN's injury report all name players
by name (with a team and positions to settle look-alikes), and all of them are
matched with `yahoo_rosters.match`. This is what they are matched against: the
projected pool, plus anyone who has played a game this data covers but is not
projected - a call-up, a rookie - and the hand-kept aliases.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/19/2026
"""

from db import fetch_all
from in_season import placeholder_ids


def pool():
    """`final_projections` rows (playerId, fullName, teamAbbrevs, positionCode),
    and the latest row of every player with games but no projection. An
    imported rookie's stand-in id gives way to his NHL id once he has played
    (`in_season.placeholder_ids`), so a name finds the id his games are under."""
    rows = fetch_all('SELECT "playerId", "fullName", "teamAbbrevs", "positionCode" FROM final_projections')
    seen = {row["playerId"] for row in rows}
    try:
        extra = fetch_all(
            'SELECT DISTINCT ON ("playerId") "playerId", "fullName", "teamAbbrev" AS "teamAbbrevs",'
            ' COALESCE("positionCode", \'G\') AS "positionCode"'
            ' FROM player_game_stats ORDER BY "playerId", "gameDate" DESC')
    except Exception:                             # noqa: BLE001 - no games scraped yet
        extra = []
    extra = [row for row in extra if row["playerId"] not in seen]
    replaced = placeholder_ids(rows, [(r["playerId"], r["fullName"], r["positionCode"], r["teamAbbrevs"])
                                      for r in extra])
    return [row for row in rows if row["playerId"] not in replaced] + extra


def aliases():
    """{alias name: playerId}, or {} where the table does not exist."""
    try:
        return {r["alias_name"]: r["player_id"]
                for r in fetch_all("SELECT player_id, alias_name FROM player_aliases")}
    except Exception:                             # noqa: BLE001 - the table is optional
        return {}
