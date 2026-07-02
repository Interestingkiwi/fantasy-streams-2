"""
Fetches past 5 years of NHL Skater Data using NHL.com API
Author - Jason Druckenmiller
Created - 6/30/2026
Updated - 7/2/2026
"""


import requests
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

def fetch_nhl_report(report_type, seasons):
    """
    Fetches a specific stat report from the NHL API for multiple seasons.
    """
    url = f"https://api.nhle.com/stats/rest/en/skater/{report_type}"
    all_seasons_df = pd.DataFrame()

    for season in seasons:
        print(f" -> Pulling '{report_type}' for {season}...")
        start = 0
        limit = 100
        all_players = []

        while True:
            params = {
                "isAggregate": "false",
                "isGame": "false",
                "start": start,
                "limit": limit,
                "factCayenneExp": "gamesPlayed>=1",
                "cayenneExp": f"gameTypeId=2 and seasonId<={season} and seasonId>={season}"
            }

            response = requests.get(url, params=params)
            if response.status_code != 200:
                break

            data = response.json()
            players = data.get('data', [])

            if not players:
                break

            all_players.extend(players)
            start += limit
            time.sleep(0.3)

        if all_players:
            df = pd.DataFrame(all_players)
            df['seasonId'] = season
            all_seasons_df = pd.concat([all_seasons_df, df], ignore_index=True)

    return all_seasons_df

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


#EXECUTION SCRIPT
seasons_to_pull = generate_rolling_seasons(5)
print(f"Target Seasons: {seasons_to_pull}\n")

print("--- FETCHING SUMMARY STATS ---")
df_summary = fetch_nhl_report("summary", seasons_to_pull)

print("\n--- FETCHING REALTIME STATS (Hits & Blocks) ---")
df_realtime = fetch_nhl_report("realtime", seasons_to_pull)

print("\n--- FETCHING FACEOFF STATS ---")
df_faceoffs = fetch_nhl_report("faceoffwins", seasons_to_pull)

print("\n--- MERGING DATA ---")
master_df = df_summary.copy()

master_df = safe_merge(master_df, df_realtime, ['hits', 'blockedShots'])


faceoff_columns = ['totalFaceoffs', 'totalFaceoffWins', 'totalFaceoffLosses', 'faceoffWinPct']
master_df = safe_merge(master_df, df_faceoffs, faceoff_columns)


cols_to_fill = [
    'hits', 'blockedShots',
    'totalFaceoffs', 'totalFaceoffWins', 'totalFaceoffLosses', 'faceoffWinPct'
]


existing_cols_to_fill = [col for col in cols_to_fill if col in master_df.columns]
master_df[existing_cols_to_fill] = master_df[existing_cols_to_fill].fillna(0)


columns_to_keep = [
    'seasonId', 'playerId', 'lastName', 'skaterFullName', 'positionCode',
    'teamAbbrevs', 'gamesPlayed', 'goals', 'assists', 'points',
    'plusMinus', 'penaltyMinutes', 'evGoals', 'evPoints',
    'ppGoals', 'ppPoints', 'shGoals', 'shPoints', 'shots',
    'shootingPctg', 'timeOnIcePerGame',
    'hits', 'blockedShots',
    'totalFaceoffs', 'totalFaceoffWins', 'totalFaceoffLosses', 'faceoffWinPct'
]

existing_columns = [col for col in columns_to_keep if col in master_df.columns]
final_df = master_df[existing_columns].copy()


table_name = "historic_skaters_baseline"

final_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"\n{len(final_df)} player-seasons safely written to '{table_name}'.")
