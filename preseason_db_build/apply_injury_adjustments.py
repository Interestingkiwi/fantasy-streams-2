"""
Adjusts projections based on injuries, creates final_projections table
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""


import pandas as pd
from db_config import engine
from datetime import datetime

# NHL Season start date (yyyy, m, d,)
SEASON_START_DATE = datetime(2026, 10, 8)

def get_return_date(details_str):
    """
    Extracts the returnDate from the dictionary-like string.
    """
    try:
        details_dict = eval(details_str)
        return details_dict.get('returnDate')
    except:
        return None

print("--- APPLYING INJURY ADJUSTMENTS (VIA CROSSWALK) ---")

# Load tables
skaters = pd.read_sql("SELECT * FROM projected_skaters_baseline", con=engine)
injuries = pd.read_sql("SELECT * FROM current_injuries", con=engine)

# Extract return dates
injuries['cleanReturnDate'] = injuries['injuryDetails'].apply(get_return_date)
injuries = injuries.dropna(subset=['cleanReturnDate'])
injuries['cleanReturnDate'] = pd.to_datetime(injuries['cleanReturnDate'])

# Calculate Games Missed
injuries['gamesMissed'] = (injuries['cleanReturnDate'] - SEASON_START_DATE).dt.days
injuries['gamesMissed'] = (injuries['gamesMissed'] / 2).clip(lower=0).astype(int)

valid_injuries = injuries.dropna(subset=['playerId'])

# Map and Adjust Skaters via playerId
for index, injury in valid_injuries.iterrows():
    pid = int(injury['playerId'])
    missed = injury['gamesMissed']

    if pid in skaters['playerId'].values:
        name = skaters.loc[skaters['playerId'] == pid, 'skaterFullName'].values[0]
        print(f" -> Adjusting {name} (ID: {pid}): Misses ~{missed} games.")

        skaters.loc[skaters['playerId'] == pid, 'projectedGames'] = (82 - missed)

        scaling_factor = (82 - missed) / 82
        stat_cols = [col for col in skaters.columns if col.startswith('proj_')]
        for col in stat_cols:
            skaters.loc[skaters['playerId'] == pid, col] *= scaling_factor

# Save final adjusted projections
skaters.to_sql("final_projections", con=engine, if_exists='replace', index=False)
print("'final_projections' table created with crosswalk-verified injury adjustments applied.")
