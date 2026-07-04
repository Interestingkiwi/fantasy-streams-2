"""
Projects Skater Data using up to 5 years of NHL Data
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/4/2026
"""


import pandas as pd
from db_config import engine
import numpy as np

def calculate_trend(y1_pts_pg, y2_pts_pg):
    """
    Calculates the production trend by comparing Last Year (Y1) to Two Years Ago (Y2).
    """
    if pd.isna(y2_pts_pg) or y2_pts_pg == 0:
        return "Not Enough Data"

    change = (y1_pts_pg - y2_pts_pg) / y2_pts_pg

    if change >= 0.10:
        return "Trending Up"
    elif change <= -0.10:
        return "Trending Down"
    else:
        return "Stable"

def calculate_peripheral_trend(row, y1, y2):
    """
    Evaluates peripheral stats (Hits, Blocks, FOW, Shots) for trends.
    Uses volume thresholds to filter out noise (e.g., a winger winning 2 faceoffs instead of 1).
    """
    thresholds = {
        'hits': 1.0,
        'blockedShots': 0.8,
        'totalFaceoffWins': 1.2,
        'shots': 1.5
    }

    trend_score = 0
    stats_evaluated = 0

    for stat, threshold in thresholds.items():
        y1_val = row.get(f"{stat}_pg_{y1}", np.nan)
        y2_val = row.get(f"{stat}_pg_{y2}", np.nan)

        if pd.isna(y1_val) or pd.isna(y2_val) or y2_val == 0:
            continue

        if max(y1_val, y2_val) < threshold:
            continue

        change = (y1_val - y2_val) / y2_val

        if change >= 0.10:
            trend_score += 1
        elif change <= -0.10:
            trend_score -= 1

        stats_evaluated += 1

    if stats_evaluated == 0:
        return "Not Enough Volume"
    elif trend_score > 0:
        return "Trending Up"
    elif trend_score < 0:
        return "Trending Down"
    else:
        return "Stable"

print("--- INITIATING PROJECTION ENGINE ---")

df = pd.read_sql("SELECT * FROM historic_skaters_baseline", con=engine)

seasons = sorted(df['seasonId'].unique(), reverse=True)
y1, y2, y3 = seasons[0], seasons[1], seasons[2]
print(f"Target Seasons: Y1={y1} (60%), Y2={y2} (30%), Y3={y3} (10%)")

df_3yr = df[df['seasonId'].isin([y1, y2, y3])].copy()
df_3yr = df_3yr.drop_duplicates(subset=['playerId', 'seasonId'], keep='first')

# --- 40-GAME THRESHOLD CHECK ---
gp_totals = df_3yr.groupby('playerId')['gamesPlayed'].sum().reset_index()
valid_skaters = gp_totals[gp_totals['gamesPlayed'] >= 40]['playerId']
df_3yr = df_3yr[df_3yr['playerId'].isin(valid_skaters)]
# -------------------------------

stats_to_project = [
    'goals', 'assists', 'points', 'plusMinus', 'penaltyMinutes',
    'ppGoals', 'ppPoints', 'shGoals', 'shPoints', 'shots',
    'hits', 'blockedShots', 'totalFaceoffs', 'totalFaceoffWins', 'totalFaceoffLosses'
]

for stat in stats_to_project:
    df_3yr[f'{stat}_pg'] = np.where(df_3yr['gamesPlayed'] > 0, df_3yr[stat] / df_3yr['gamesPlayed'], 0)

latest_metadata = df_3yr.sort_values('seasonId', ascending=False).drop_duplicates(subset=['playerId'])[['playerId', 'skaterFullName', 'positionCode', 'teamAbbrevs']]

pivot_df = df_3yr.pivot(
    index='playerId',
    columns='seasonId',
    values=[f'{s}_pg' for s in stats_to_project]
)

pivot_df.columns = [f"{col[0]}_{col[1]}" for col in pivot_df.columns]
pivot_df.reset_index(inplace=True)

pivot_df = pd.merge(latest_metadata, pivot_df, on='playerId', how='left')

projected_data = []

for index, row in pivot_df.iterrows():
    player_proj = {
        'playerId': row['playerId'],
        'skaterFullName': row['skaterFullName'],
        'positionCode': row['positionCode'],
        'teamAbbrevs': row['teamAbbrevs'],
        'projectedGames': 82
    }

    # Derive Trends
    y1_pts = row.get(f"points_pg_{y1}", np.nan)
    y2_pts = row.get(f"points_pg_{y2}", np.nan)

    player_proj['productionTrend'] = calculate_trend(y1_pts, y2_pts)
    player_proj['peripheralTrend'] = calculate_peripheral_trend(row, y1, y2)

    for stat in stats_to_project:
        val_y1 = row.get(f"{stat}_pg_{y1}", np.nan)
        val_y2 = row.get(f"{stat}_pg_{y2}", np.nan)
        val_y3 = row.get(f"{stat}_pg_{y3}", np.nan)

        total_weight = 0
        weighted_sum = 0

        if not pd.isna(val_y1):
            weighted_sum += (val_y1 * 6)
            total_weight += 6
        if not pd.isna(val_y2):
            weighted_sum += (val_y2 * 3)
            total_weight += 3
        if not pd.isna(val_y3):
            weighted_sum += (val_y3 * 1)
            total_weight += 1

        if total_weight > 0:
            proj_pg = weighted_sum / total_weight
        else:
            proj_pg = 0

        player_proj[f"proj_{stat}"] = round(proj_pg * 82, 1)

    projected_data.append(player_proj)

final_projections_df = pd.DataFrame(projected_data)
final_projections_df.fillna(0, inplace=True)

table_name = "projected_skaters_baseline"
final_projections_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"\n{len(final_projections_df)} player projections written to '{table_name}'.")
print("Math applied: 60/30/10 Dynamic Time-Decay + Paced to 82 Games + Trend Analyzed.")
