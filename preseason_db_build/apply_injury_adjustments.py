"""
Adjusts projections based on injuries, creates final_projections table
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/4/2026
"""


import pandas as pd
from db_config import engine
from datetime import datetime
import numpy as np

#NHL Season start date (yyyy, m, d)
SEASON_START_DATE = datetime(2026, 10, 8)

def get_return_date(details_str):
    """Extracts the returnDate from the dictionary-like string."""
    try:
        details_dict = eval(details_str)
        return details_dict.get('returnDate')
    except:
        return None

print("--- APPLYING INJURY ADJUSTMENTS (VIA CROSSWALK) ---")

#1. Load All Tables
skaters = pd.read_sql("SELECT * FROM projected_skaters_baseline", con=engine)
goalies = pd.read_sql("SELECT * FROM projected_goalies_baseline", con=engine)
injuries = pd.read_sql("SELECT * FROM current_injuries", con=engine)

#2. Extract return dates
injuries['cleanReturnDate'] = injuries['injuryDetails'].apply(get_return_date)
injuries = injuries.dropna(subset=['cleanReturnDate'])
injuries['cleanReturnDate'] = pd.to_datetime(injuries['cleanReturnDate'])

#3. Calculate Games Missed
injuries['gamesMissed'] = (injuries['cleanReturnDate'] - SEASON_START_DATE).dt.days
injuries['gamesMissed'] = (injuries['gamesMissed'] / 2).clip(lower=0).astype(int)

valid_injuries = injuries.dropna(subset=['playerId'])

#4. Map and Adjust Skaters
for index, injury in valid_injuries.iterrows():
    pid = int(injury['playerId'])
    missed = injury['gamesMissed']

    if pid in skaters['playerId'].values:
        name = skaters.loc[skaters['playerId'] == pid, 'skaterFullName'].values[0]
        original_games = skaters.loc[skaters['playerId'] == pid, 'projectedGames'].values[0]

        print(f" -> Adjusting Skater {name} (ID: {pid}): Misses ~{missed} games. (Base: {original_games})")

        new_games = max(original_games - missed, 0)
        skaters.loc[skaters['playerId'] == pid, 'projectedGames'] = new_games

        scaling_factor = new_games / original_games if original_games > 0 else 0
        stat_cols = [col for col in skaters.columns if col.startswith('proj_')]
        for col in stat_cols:
            skaters.loc[skaters['playerId'] == pid, col] *= scaling_factor

#5. Map and Adjust Goalies
for index, injury in valid_injuries.iterrows():
    pid = int(injury['playerId'])
    missed = injury['gamesMissed']

    if pid in goalies['playerId'].values:
        name = goalies.loc[goalies['playerId'] == pid, 'goalieFullName'].values[0]
        original_games = goalies.loc[goalies['playerId'] == pid, 'projectedGames'].values[0]

        print(f" -> Adjusting Goalie {name} (ID: {pid}): Misses ~{missed} games. (Base: {original_games})")

        new_games = max(original_games - missed, 0)
        goalies.loc[goalies['playerId'] == pid, 'projectedGames'] = new_games

        scaling_factor = new_games / original_games if original_games > 0 else 0
        stat_cols = [col for col in goalies.columns if col.startswith('proj_')]
        for col in stat_cols:
            goalies.loc[goalies['playerId'] == pid, col] *= scaling_factor

print("\n--- MERGING SKATER AND GOALIE PROJECTIONS ---")

#6. Standardize columns for the unified UI
skaters = skaters.rename(columns={'skaterFullName': 'fullName'})
goalies = goalies.rename(columns={'goalieFullName': 'fullName'})

#7. Clean up the math and combine
final_df = pd.concat([skaters, goalies], ignore_index=True)

for col in final_df.columns:
    if col.startswith('proj_'):
        final_df[col] = final_df[col].round(1)

#8. Save final adjusted projections
final_df.to_sql("final_projections", con=engine, if_exists='replace', index=False)

print(f"SUCCESS! 'final_projections' table created with {len(final_df)} players (Injury adjusted).")
