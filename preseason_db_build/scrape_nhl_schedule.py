"""
Builds the nhl_schedule table from the NHL API.

Playoff-schedule comparison needs to know which teams actually play during a
manager's fantasy playoff weeks, and how many of those games fall on nights
when the rest of the league is idle. Both come out of the regular-season
schedule, so this pulls every team's season from api-web.nhle.com and stores
one row per game.

The season is read from the API rather than hardcoded - the old app pinned
START_DATE/END_DATE to a single season and silently went stale the moment the
calendar turned over. Preseason and playoff games are dropped; only gameType 2
counts toward a fantasy week.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import time

import pandas as pd
import requests
from sqlalchemy import text

from db_config import engine

HEADERS = {"User-Agent": "Mozilla/5.0"}
TABLE = "nhl_schedule"

# gameType 1 is preseason, 2 is regular season, 3 is playoffs
REGULAR_SEASON = 2

# Fallback list of the 32 current franchises, used only if the standings
# lookup fails for some reason.
FALLBACK_TRICODES = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WPG", "WSH",
]


def get_current_tricodes():
    """Returns the list of tricodes for the 32 active franchises."""
    try:
        resp = requests.get(
            "https://api-web.nhle.com/v1/standings/now", headers=HEADERS, timeout=25
        )
        resp.raise_for_status()
        tricodes = sorted({row["teamAbbrev"]["default"] for row in resp.json()["standings"]})
        if len(tricodes) >= 30:
            return tricodes
        print(f" -> Standings returned only {len(tricodes)} teams; using fallback list.")
    except Exception as exc:
        print(f" -> Could not read standings ({exc}); using fallback list.")
    return FALLBACK_TRICODES


def fetch_team_schedule(tricode):
    """Every regular-season game on one club's card. (season, [game dicts])"""
    url = f"https://api-web.nhle.com/v1/club-schedule-season/{tricode}/now"

    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for game in data.get("games", []):
        if game.get("gameType") != REGULAR_SEASON:
            continue

        home = (game.get("homeTeam") or {}).get("abbrev")
        away = (game.get("awayTeam") or {}).get("abbrev")
        if not home or not away:
            continue

        games.append({
            "gameId": int(game["id"]),
            "season": int(game.get("season") or 0),
            "gameDate": game.get("gameDate"),
            "homeTeam": home,
            "awayTeam": away,
        })

    return data.get("currentSeason"), games


def main():
    print("--- BUILDING NHL SCHEDULE ---")

    tricodes = get_current_tricodes()
    print(f" -> {len(tricodes)} franchises.")

    # Each game shows up on both clubs' schedules, so key by id to keep one copy
    games = {}
    season = None
    failed = []

    for tri in tricodes:
        try:
            club_season, club_games = fetch_team_schedule(tri)
        except Exception as exc:
            print(f" -> [WARN] {tri}: {exc}")
            failed.append(tri)
            continue

        season = season or club_season
        for game in club_games:
            games[game["gameId"]] = game

        time.sleep(0.1)

    if failed:
        raise SystemExit(
            f"Could not read a schedule for: {', '.join(failed)}.\n"
            "The table was left untouched rather than written half-built."
        )

    if not games:
        raise SystemExit("The NHL API returned no regular-season games; nothing written.")

    frame = pd.DataFrame(sorted(games.values(), key=lambda g: (g["gameDate"], g["gameId"])))
    frame.to_sql(TABLE, con=engine, if_exists="replace", index=False)

    with engine.begin() as conn:
        conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_schedule_date ON {TABLE} ("gameDate")'))

    # A team with an unusual game count usually means the feed changed shape
    per_team = pd.concat([frame["homeTeam"], frame["awayTeam"]]).value_counts()
    odd = per_team[(per_team < 80) | (per_team > 84)]

    print(f" -> Season {season}: {len(frame)} games, "
          f"{frame['gameDate'].min()} to {frame['gameDate'].max()}.")
    print(f" -> Games per team: {per_team.min()}-{per_team.max()} "
          f"across {len(per_team)} teams.")
    if not odd.empty:
        print(f" -> [WARN] Unexpected game counts: {odd.to_dict()}")
    print(f" -> Wrote {len(frame)} rows to {TABLE}.")


if __name__ == "__main__":
    main()
