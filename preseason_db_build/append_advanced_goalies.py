"""
Fetches past 5 years of NHL Goalie Advanced Stats from MoneyPuck CSV
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""


import pandas as pd
from db_config import engine
import time
from datetime import datetime

def generate_rolling_seasons(num_years=5):
    """
    Generates rolling (num_years)-year season list.
    If run before July 1, considers current year the active season and starts one season earlier
    """
    current_date = datetime.now()
    current_year = current_date.year
    if current_date.month < 7:
        current_year -= 1

    seasons = []
    for i in range(num_years):
        end_year = current_year - i
        start_year = end_year - 1
        seasons.append(f"{start_year}{end_year}")
    return seasons

def fetch_moneypuck_goalies(seasons):
    """
    Downloads the master goalie CSV from MoneyPuck's servers.
    """
    all_mp_df = pd.DataFrame()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    for season in seasons:
        start_year = season[:4]
        print(f" -> Downloading MoneyPuck Goalie Data for {season}...")
        url = f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{start_year}/regular/goalies.csv"

        try:
            df = pd.read_csv(url, storage_options=headers)
            df = df[df['situation'] == 'all'].copy()
            df['seasonId'] = season
            df['playerId'] = df['playerId'].astype(int)

            all_mp_df = pd.concat([all_mp_df, df], ignore_index=True)
            time.sleep(1)

        except Exception as e:
            print(f"Failed to fetch MoneyPuck data for {start_year}: {e}")

    return all_mp_df

def safe_merge(base_df, new_df, desired_columns):
    """
    Safely merges new_df into base_df based on playerId and seasonId.
    """
    if new_df is None or new_df.empty:
        return base_df

    merge_keys = ['playerId', 'seasonId']
    existing_cols = [col for col in desired_columns if col in new_df.columns]

    if not existing_cols:
        return base_df

    cols_to_pull = merge_keys + existing_cols
    cols_to_drop = [col for col in existing_cols if col in base_df.columns]
    base_df = base_df.drop(columns=cols_to_drop)

    return pd.merge(base_df, new_df[cols_to_pull], on=merge_keys, how='left')



#EXECUTION
table_name = "historic_goalies_baseline"

print("--- CONNECTING TO POSTGRESQL DATABASE ---")

try:
    baseline_df = pd.read_sql(f"SELECT * FROM {table_name}", con=engine)
    baseline_df['playerId'] = baseline_df['playerId'].astype(int)
    baseline_df['seasonId'] = baseline_df['seasonId'].astype(str)
except Exception as e:
    print(f"Error loading database: {e}")
    exit()

seasons_to_pull = generate_rolling_seasons(5)
print(f"\n--- FETCHING ADVANCED STATS FROM MONEYPUCK ---")
mp_df = fetch_moneypuck_goalies(seasons_to_pull)

print("\n--- MERGING ADVANCED STATS INTO BASELINE ---")
mp_wish_list = [
    'xGoalsAgainst',
    'goalsSavedAboveExpected',
    'lowDangerShots',
    'mediumDangerShots',
    'highDangerShots'
]

master_df = safe_merge(baseline_df, mp_df, mp_wish_list)

print("\n--- SAVING TO DATABASE ---")
master_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"SUCCESS! Advanced stats appended to your {table_name} database.")
