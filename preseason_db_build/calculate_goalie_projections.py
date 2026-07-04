"""
Projects Goalie Data using up to 5 years of NHL Data
Author - Jason Druckenmiller
Created - 7/4/2026
Updated - 7/4/2026
"""

import pandas as pd
from db_config import engine
import numpy as np

print("--- INITIATING GOALIE PROJECTION ENGINE ---")

#1. Connect to Database via Postgres Engine
df = pd.read_sql("SELECT * FROM historic_goalies_baseline", con=engine)

#2. Setup Season Weights
seasons = sorted(df['seasonId'].unique(), reverse=True)
y1, y2, y3 = seasons[0], seasons[1], seasons[2]
print(f"Target Seasons: Y1={y1} (60%), Y2={y2} (30%), Y3={y3} (10%)")

df_3yr = df[df['seasonId'].isin([y1, y2, y3])].copy()
df_3yr = df_3yr.drop_duplicates(subset=['playerId', 'seasonId'], keep='first')

#3. Projection Logic
projected_data = []

for pid in df_3yr['playerId'].unique():
    player_data = df_3yr[df_3yr['playerId'] == pid]
    latest = player_data.iloc[0]

    # Calculate historical games played weighted average for Tandem scaling
    gp_weighted_sum = 0
    total_weight = 0
    for i, year in enumerate([y1, y2, y3]):
        weight = [0.6, 0.3, 0.1][i]
        year_data = player_data[player_data['seasonId'] == year]
        if not year_data.empty:
            gp_weighted_sum += (year_data['gamesPlayed'].values[0] * weight)
            total_weight += weight

    proj_gp = int(round(gp_weighted_sum / total_weight)) if total_weight > 0 else 0

    proj = {
        'playerId': int(pid),
        'goalieFullName': latest['goalieFullName'],
        'positionCode': 'G',
        'teamAbbrevs': latest['teamAbbrevs'],
        'projectedGames': proj_gp
    }

    #Step 1: Weighted Average Math (60/30/10) for COUNTING stats
    stats_to_weight = [
        'wins', 'losses', 'shutouts', 'gamesStarted',
        'shotsAgainst', 'goalsAgainst', 'saves', 'timeOnIce', 'otLosses'
    ]

    for stat in stats_to_weight:
        weighted_sum = 0
        total_weight = 0

        for i, year in enumerate([y1, y2, y3]):
            weight = [0.6, 0.3, 0.1][i]
            year_data = player_data[player_data['seasonId'] == year]
            if not year_data.empty and pd.notna(year_data[stat].values[0]):
                weighted_sum += (year_data[stat].values[0] * weight)
                total_weight += weight

        proj[f"proj_{stat}"] = round(weighted_sum / total_weight if total_weight > 0 else 0, 1)

    #Step 2: Algebra for DERIVED RATE stats

    #savePct = saves / shotsAgainst
    proj['proj_savePct'] = round(proj['proj_saves'] / proj['proj_shotsAgainst'], 3) if proj.get('proj_shotsAgainst', 0) > 0 else 0.0

    #GAA = (goalsAgainst * 3600) / timeOnIce
    proj['proj_goalsAgainstAverage'] = round((proj['proj_goalsAgainst'] * 3600) / proj['proj_timeOnIce'], 2) if proj.get('proj_timeOnIce', 0) > 0 else 0.0
    #winPct = wins / gamesStarted
    proj['proj_winPct'] = round(proj['proj_wins'] / proj['proj_gamesStarted'], 3) if proj.get('proj_gamesStarted', 0) > 0 else 0.0

    #startPct = gamesStarted / projectedGames
    proj['proj_startPct'] = round(proj['proj_gamesStarted'] / proj['projectedGames'], 3) if proj.get('projectedGames', 0) > 0 else 0.0

    projected_data.append(proj)

#4. Save to Database
final_df = pd.DataFrame(projected_data)
final_df.to_sql("projected_goalies_baseline", con=engine, if_exists='replace', index=False)

print(f"SUCCESS! {len(final_df)} dynamically scaled goalie projections saved.")
