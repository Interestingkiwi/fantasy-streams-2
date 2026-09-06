"""
Removes players who have finished in the NHL from final_projections.

The projection engine works from a rolling three-season window, so a player
who retired last summer still has enough history to be projected for a full
season - Joe Pavelski at 67 points, on a draft board, in 2026.

Absence from an NHL roster is NOT sufficient grounds on its own. These feeds
are read in the offseason, when unsigned RFAs simply aren't listed yet:
Cutter Gauthier and Adam Fantilli both fail that test despite playing a full
season. So a player is only dropped when he is BOTH off every current roster
AND played no games at all last season. That pair is decisive - it clears
Pavelski, Oshie, Wheeler and Pacioretty while leaving anyone who saw ice.

Imported rookies are never pruned; they're prospects by definition and were
added deliberately.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import pandas as pd
from db_config import engine

TABLE = "final_projections"

# Raise to also drop players who barely appeared (e.g. 20 catches fringe
# call-ups). 0 is the conservative setting: only players who never dressed.
MAX_GAMES_LAST_SEASON = 0


def last_season_games():
    """{playerId: games played in the most recent season} across skaters + goalies."""
    frames = []
    for table in ("historic_skaters_baseline", "historic_goalies_baseline"):
        frames.append(pd.read_sql(
            f'SELECT "playerId", "seasonId", "gamesPlayed" FROM {table}', con=engine))
    history = pd.concat(frames, ignore_index=True)
    if history.empty:
        return pd.Series(dtype=float), None

    latest = sorted(history["seasonId"].unique())[-1]
    # A traded player has one row per team, so sum rather than take a single row.
    games = history[history["seasonId"] == latest].groupby("playerId")["gamesPlayed"].sum()
    return games, latest


def main():
    print("--- PRUNING INACTIVE PLAYERS ---")

    df = pd.read_sql(f"SELECT * FROM {TABLE}", con=engine)

    if "onNhlRoster" not in df.columns:
        print(" -> No onNhlRoster column; run sync_current_rosters.py first. Skipping.")
        return

    games, latest = last_season_games()
    if latest is None:
        print(" -> No historical seasons available. Skipping.")
        return

    played = df["playerId"].astype("Int64").map(games).fillna(0)
    source = df.get("projectionSource", pd.Series("model", index=df.index)).fillna("model")

    drop = (~df["onNhlRoster"].fillna(False)) & (played <= MAX_GAMES_LAST_SEASON) & (source == "model")

    if not drop.any():
        print(f" -> Nothing to prune; all {len(df)} players are active.")
        return

    removed = df[drop].copy()
    removed["proj_points"] = removed.get("proj_points", pd.Series(dtype=float))
    print(f" -> Dropping {int(drop.sum())} players: off every current NHL roster "
          f"and 0 games in {latest}.")
    for _, r in removed.sort_values("proj_points", ascending=False).head(10).iterrows():
        pts = r.get("proj_points")
        print(f"      {r['fullName']} ({r['teamAbbrevs']})"
              + (f" - would have projected {pts:.0f} pts" if pd.notna(pts) else ""))
    if drop.sum() > 10:
        print(f"      ... and {int(drop.sum()) - 10} more")

    kept_off_roster = int(((~df["onNhlRoster"].fillna(False)) & ~drop).sum())
    df[~drop].to_sql(TABLE, con=engine, if_exists="replace", index=False)

    print(f"'{TABLE}' now holds {int((~drop).sum())} players "
          f"({kept_off_roster} kept despite being off a roster - they played last season).")


if __name__ == "__main__":
    main()
