"""
Adds imported rookie/prospect projections to final_projections.

The projection engine needs 40+ NHL games across three seasons before it
will project a skater, which excludes every incoming rookie by definition -
they have no NHL history to extrapolate from. Those projections have to come
from outside, so rookie_projections.csv holds per-game rates and an assigned
games-played for each one, and this step paces them out and appends them.

Rows are tagged projectionSource='imported' so they stay distinguishable
from the modelled players.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import os

import pandas as pd
from db_config import engine
from goalie_workload import flatten_starts

ROOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "rookie_projections.csv")
TABLE = "final_projections"

# The source writes four teams with dots; the rest of the database uses NHL
# tricodes, and sync_current_rosters matches on them.
TEAM_FIX = {"L.A": "LAK", "N.J": "NJD", "S.J": "SJS", "T.B": "TBL"}

# Skater rate columns pace straight out to the season; goalie rates do too, but
# their games played is flattened first the same way assigned starts are.
RATE_PREFIX = "pg_"


def main():
    print("--- APPLYING IMPORTED ROOKIE PROJECTIONS ---")

    if not os.path.exists(ROOKIES_FILE):
        print(f" -> No {os.path.basename(ROOKIES_FILE)}; nothing to import.")
        return

    rookies = pd.read_csv(ROOKIES_FILE)
    existing = pd.read_sql(f"SELECT * FROM {TABLE}", con=engine)

    if "projectionSource" not in existing.columns:
        existing["projectionSource"] = "model"

    rate_cols = [c for c in rookies.columns if c.startswith(RATE_PREFIX)]
    already = set(existing["playerId"].astype(int))

    built = []
    skipped = 0
    for _, r in rookies.iterrows():
        pid = int(r["playerId"])
        if pid in already:
            # The engine already projects this player from real NHL history;
            # that projection wins over the imported one.
            skipped += 1
            continue

        goalie = bool(r["isGoalie"])
        games = int(r["gamesPlayed"])
        if goalie:
            games = flatten_starts(games)

        row = {
            "playerId": pid,
            "fullName": r["playerName"],
            "positionCode": r["positionCode"],
            "teamAbbrevs": TEAM_FIX.get(str(r["team"]).strip(), str(r["team"]).strip()),
            "projectedGames": games,
            "productionTrend": "Rookie",
            "peripheralTrend": "Rookie",
            "projectionSource": "imported",
        }
        for col in rate_cols:
            value = r[col]
            if pd.notna(value):
                row[f"proj_{col[len(RATE_PREFIX):]}"] = round(float(value) * games, 1)

        if goalie and pd.notna(r.get("savePct")):
            row["proj_savePct"] = round(float(r["savePct"]), 3)
            if row.get("proj_saves") and row.get("proj_goalsAgainst"):
                shots = row["proj_saves"] + row["proj_goalsAgainst"]
                row["proj_shotsAgainst"] = round(shots, 1)

        built.append(row)

    if not built:
        print(f" -> Nothing new to add ({skipped} already projected from NHL history).")
        return

    combined = pd.concat([existing, pd.DataFrame(built)], ignore_index=True)
    combined["projectionSource"] = combined["projectionSource"].fillna("model")
    combined.to_sql(TABLE, con=engine, if_exists="replace", index=False)

    goalies = sum(1 for r in built if r["positionCode"] == "G")
    print(f" -> Added {len(built)} imported players ({goalies} goalies, "
          f"{len(built) - goalies} skaters).")
    if skipped:
        print(f" -> Skipped {skipped} already projected from NHL history.")
    print(f"'{TABLE}' now holds {len(combined)} players.")


if __name__ == "__main__":
    main()
