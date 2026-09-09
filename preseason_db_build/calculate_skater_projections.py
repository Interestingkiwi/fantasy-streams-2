"""
Projects Skater Data using up to 5 years of NHL Data
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/5/2026
"""

import pandas as pd
import aging
from db_config import engine
from season_config import HISTORICAL_SEASON_GAMES, season_game_count
import numpy as np

def calculate_trend(y1_pts_pg, y2_pts_pg):
    """
    Calculates the production trend by comparing Last Year (Y1) to Two Years Ago (Y2).
    """
    # Safely handle missing seasons so it doesn't crash on NaNs
    if pd.isna(y1_pts_pg) or pd.isna(y2_pts_pg) or y2_pts_pg == 0:
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
    Uses volume thresholds to filter out noise.
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

# The season being projected is the one after the newest one on file, derived
# rather than written down so it moves with the data every summer.
TARGET_SEASON = int(f"{int(str(y1)[4:])}{int(str(y1)[4:]) + 1}")
print(f"Projecting {TARGET_SEASON}.")

df_3yr = df[df['seasonId'].isin([y1, y2, y3])].copy()
df_3yr = df_3yr.drop_duplicates(subset=['playerId', 'seasonId'], keep='first')

# --- 40-GAME GLOBAL THRESHOLD CHECK ---
gp_totals = df_3yr.groupby('playerId')['gamesPlayed'].sum().reset_index()
valid_skaters = gp_totals[gp_totals['gamesPlayed'] >= 40]['playerId']
df_3yr = df_3yr[df_3yr['playerId'].isin(valid_skaters)]

stats_to_project = [
    'goals', 'assists', 'points', 'plusMinus', 'penaltyMinutes',
    'ppGoals', 'ppPoints', 'shGoals', 'shPoints', 'shots',
    'hits', 'blockedShots', 'totalFaceoffs', 'totalFaceoffWins', 'totalFaceoffLosses'
]

# --- PER-SEASON OUTLIER CHECK ---
MIN_GAMES_PER_SEASON = 10

for stat in stats_to_project:
    df_3yr[f'{stat}_pg'] = np.where(df_3yr['gamesPlayed'] >= MIN_GAMES_PER_SEASON,
                                    df_3yr[stat] / df_3yr['gamesPlayed'],
                                    np.nan)

# --- PROJECTED GAMES ---
# Everyone used to get a flat full season, which paced every counting stat about
# 5% high. Straight historical GP is worse in the other direction: it bakes in
# resolved injuries, mid-season call-ups and old roles, and lands ~60 games. So
# blend a full season against the player's own durability, weighted toward
# optimism. Only seasons where the player was already a regular (40+ GP) count,
# so a call-up year doesn't brand someone fragile.
#
# The season length comes from the scraped schedule rather than a constant - it
# is 84 games from 2026-27 on, and durability measured against 82-game seasons
# has to be rescaled rather than carried across as a raw game count.
FULL_SEASON = season_game_count()
FULL_SEASON_WEIGHT = 0.75          # remainder comes from the player's own history
REGULAR_SEASON_MIN_GP = 40

print(f" -> Pacing projections to a {FULL_SEASON}-game season.")

gp_num = 0.0
gp_den = 0.0
for season, weight in [(y1, 0.6), (y2, 0.3), (y3, 0.1)]:
    season_gp = df_3yr[df_3yr['seasonId'] == season].set_index('playerId')['gamesPlayed']
    season_gp = season_gp.reindex(df_3yr['playerId'].unique())
    counts = season_gp.notna() & (season_gp >= REGULAR_SEASON_MIN_GP)
    gp_num = gp_num + season_gp.where(counts, 0) * weight
    gp_den = gp_den + counts * weight

durability_gp = gp_num / gp_den.replace(0, np.nan)

# Share of the season the player has been available for, so an 82-game history
# maps onto whatever length the coming season is
durability_rate = (durability_gp / HISTORICAL_SEASON_GAMES).clip(upper=1.0)

projected_games = (
    FULL_SEASON * (
        FULL_SEASON_WEIGHT
        + (1 - FULL_SEASON_WEIGHT) * durability_rate.fillna(1.0)
    )
).round().clip(upper=FULL_SEASON)

# --- AGE ---
# Each of the three seasons was played at a different age, and the blend below
# averages them as if they were not. Look up every birthdate now so each season
# can be restated at the age being projected before it is weighted - see
# aging.py for the curve and how it was measured.
if 'birthDate' in df_3yr.columns:
    BIRTHDATES = (df_3yr.dropna(subset=['birthDate'])
                        .drop_duplicates(subset=['playerId'])
                        .set_index('playerId')['birthDate']
                        .to_dict())
else:
    BIRTHDATES = {}

if BIRTHDATES:
    print(f" -> Age-adjusting rates for {len(BIRTHDATES)} skaters "
          f"({len(df_3yr['playerId'].unique()) - len(BIRTHDATES)} without a birthdate "
          "are projected unadjusted).")
else:
    print(" -> [WARN] No birthDate column in historic_skaters_baseline; skipping the "
          "age adjustment. Re-run historic_data_skaters.py to collect birthdates.")

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
    games = projected_games.get(row['playerId'], FULL_SEASON)
    if pd.isna(games):
        games = FULL_SEASON
    games = int(games)

    born = BIRTHDATES.get(row['playerId'])
    target_age = aging.season_age(born, TARGET_SEASON)

    # One factor per season per curve, worked out once rather than per stat.
    # Scoring and peripherals age at very different speeds, so a 38-year-old's
    # hits survive a season his goals do not.
    age_factors = {
        group: {season: aging.age_factor(aging.season_age(born, season), target_age, group)
                for season in (y1, y2, y3)}
        for group in (aging.SCORING, aging.PERIPHERAL)
    }

    player_proj = {
        'playerId': row['playerId'],
        'skaterFullName': row['skaterFullName'],
        'positionCode': row['positionCode'],
        'teamAbbrevs': row['teamAbbrevs'],
        'age': target_age,
        'projectedGames': games
    }

    y1_pts = row.get(f"points_pg_{y1}", np.nan)
    y2_pts = row.get(f"points_pg_{y2}", np.nan)

    player_proj['productionTrend'] = calculate_trend(y1_pts, y2_pts)
    player_proj['peripheralTrend'] = calculate_peripheral_trend(row, y1, y2)

    for stat in stats_to_project:
        # plusMinus has no curve and gets none: it is signed, so scaling a
        # negative one toward zero would read as the player improving.
        factors = age_factors.get(aging.group_for(stat))

        total_weight = 0
        weighted_sum = 0

        for season, weight in ((y1, 6), (y2, 3), (y3, 1)):
            value = row.get(f"{stat}_pg_{season}", np.nan)
            if pd.isna(value):
                continue
            if factors:
                value *= factors[season]
            weighted_sum += value * weight
            total_weight += weight

        if total_weight > 0:
            proj_pg = weighted_sum / total_weight
        else:
            proj_pg = 0

        player_proj[f"proj_{stat}"] = round(proj_pg * games, 1)

    projected_data.append(player_proj)

final_projections_df = pd.DataFrame(projected_data)
# Age stays null when it is unknown rather than becoming a zero-year-old
age_column = final_projections_df.pop('age')
final_projections_df.fillna(0, inplace=True)
final_projections_df.insert(4, 'age', age_column.astype('Int64'))

table_name = "projected_skaters_baseline"
final_projections_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"\n{len(final_projections_df)} player projections written to '{table_name}'.")
print(f"Math applied: 60/30/10 Dynamic Time-Decay + GP blend ({FULL_SEASON_WEIGHT:.0%} full season) "
      f"+ age curve + Trend Analyzed.")
