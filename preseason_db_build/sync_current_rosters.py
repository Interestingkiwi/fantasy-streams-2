"""
Syncs current NHL team assignments onto the projection tables.

The historic stat feeds only report the team(s) a player recorded stats for
in a given season, so offseason trades and free-agent signings are never
reflected. This script pulls every current NHL roster from the NHL API and
overwrites "teamAbbrevs" in final_projections and player_directory with the
player's present team.

Author - Jason Druckenmiller
Created - 9/3/2026
Updated - 9/3/2026
"""


import time
import requests
import pandas as pd
from db_config import engine

HEADERS = {"User-Agent": "Mozilla/5.0"}

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


def fetch_roster_map(tricodes):
    """Builds a {playerId: tricode} map from every current roster."""
    roster_map = {}
    for tri in tricodes:
        url = f"https://api-web.nhle.com/v1/roster/{tri}/current"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f" -> [WARN] {tri}: {exc}")
            continue

        count = 0
        for group in ("forwards", "defensemen", "goalies"):
            for player in data.get(group, []):
                pid = player.get("id")
                if pid is not None:
                    roster_map[int(pid)] = tri
                    count += 1
        print(f" -> {tri}: {count} players")
        time.sleep(0.2)

    return roster_map


def apply_roster_map(table_name, roster_map):
    """Overwrites teamAbbrevs on the given table for players found on a roster."""
    df = pd.read_sql(f'SELECT * FROM {table_name}', con=engine)
    if "playerId" not in df.columns or "teamAbbrevs" not in df.columns:
        print(f" -> [SKIP] {table_name} has no playerId/teamAbbrevs columns.")
        return

    df["playerId"] = df["playerId"].astype("Int64")
    mapped_team = df["playerId"].map(roster_map)

    changed = ((mapped_team.notna()) & (mapped_team != df["teamAbbrevs"])).sum()
    matched = mapped_team.notna().sum()
    unmatched = len(df) - matched

    df["teamAbbrevs"] = mapped_team.fillna(df["teamAbbrevs"])
    # Record roster membership rather than only using it to fix teams. Being
    # absent is not proof a player is finished - unsigned RFAs drop off these
    # feeds every offseason - so it's stored as a signal, not acted on here.
    df["onNhlRoster"] = mapped_team.notna()
    df.to_sql(table_name, con=engine, if_exists="replace", index=False)

    print(
        f" -> {table_name}: {matched} on current rosters, "
        f"{changed} team values changed, {unmatched} left as-is "
        f"(UFA / retired / minors / rookies)."
    )


def main():
    print("--- SYNCING CURRENT NHL ROSTERS ---")
    tricodes = get_current_tricodes()
    print(f"Fetching {len(tricodes)} rosters...")

    roster_map = fetch_roster_map(tricodes)
    print(f"\nCollected {len(roster_map)} rostered players.")

    if not roster_map:
        print("No roster data retrieved; leaving tables untouched.")
        return

    for table_name in ("final_projections", "player_directory"):
        apply_roster_map(table_name, roster_map)

    print("\nRoster sync complete.")


if __name__ == "__main__":
    main()
