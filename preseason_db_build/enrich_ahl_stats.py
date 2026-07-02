"""
Scrapes more stats from AHL DB
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""


import requests
import pandas as pd
from db_config import engine
import difflib
import json

def fetch_ahl_official_api(limit=100):
    """
    Hits the official AHL HockeyTech backend API to bypass paywalls.
    """
    print("--- FETCHING OFFICIAL AHL API ---")
    url = "https://lscluster.hockeytech.com/feed/index.php"

    params = {
        "feed": "statviewfeed",
        "view": "players",
        "season": "90", # Confirmed 2025-2026 Season ID
        "site_id": "1",
        "key": "50c2cd9b5e18e390",
        "client_code": "ahl",
        "league_id": "4",
        "lang": "en",
        "statsType": "standard",
        "sort": "points",
        "limit": limit
    }

    response = requests.get(url, params=params)

    if response.status_code == 200:
        try:
            raw_text = response.text.strip()
            if raw_text.startswith('(') and raw_text.endswith(')'):
                raw_text = raw_text[1:-1]

            raw_data = json.loads(raw_text)
            players = raw_data[0]['sections'][0]['data']

            ahl_df = pd.DataFrame(players)

            if 'row' in ahl_df.columns:
                ahl_df = pd.json_normalize(ahl_df['row'])

            ahl_df['name'] = ahl_df['name'].str.strip()

            # 1. Look for the exact snake_case columns you found
            cols_to_pull = ['shots', 'power_play_goals', 'plus_minus']
            existing_cols = [col for col in cols_to_pull if col in ahl_df.columns]

            for col in existing_cols:
                ahl_df[col] = pd.to_numeric(ahl_df[col], errors='coerce').fillna(0).astype(int)

            # 2. Rename them to match your database schema so everything stays clean
            rename_map = {
                'power_play_goals': 'ppGoals',
                'plus_minus': 'plusMinus'
            }
            ahl_df = ahl_df.rename(columns=rename_map)

            print(f"Successfully pulled {len(ahl_df)} players from the AHL API.")

            # Map out our final columns using the renamed versions
            final_api_cols = ['name'] + [rename_map.get(col, col) for col in existing_cols]
            return ahl_df[final_api_cols]

        except Exception as e:
            print(f"\n[API ERROR] Something went wrong parsing the data: {e}")
            return None
    else:
        print(f"Failed to connect to AHL API. HTTP: {response.status_code}")
        return None

def fuzzy_merge_stats(db_df, api_df):
    """
    Merges the API data into the Database using Fuzzy String Matching.
    Uses combine_first() to safely coalesce duplicate columns without data loss.
    """
    print("\n--- INITIATING FUZZY MATCH MERGE ---")

    ahl_db_players = db_df[db_df['league'] == 'AHL']['playerName'].tolist()
    api_players = api_df['name'].tolist()

    name_map = {}
    for api_name in api_players:
        matches = difflib.get_close_matches(api_name, ahl_db_players, n=1, cutoff=0.8)
        if matches:
            ep_name = matches[0]
            name_map[api_name] = ep_name
            if api_name != ep_name:
                print(f" [Fuzzy Match] '{api_name}' (API)  -->  '{ep_name}' (Database)")

    api_df['matched_name'] = api_df['name'].map(name_map)
    api_df = api_df.dropna(subset=['matched_name'])

    cols_to_merge = [col for col in api_df.columns if col != 'name']

    # 3. Merge without dropping anything from the database first
    merged_df = pd.merge(
        db_df,
        api_df[cols_to_merge],
        left_on='playerName',
        right_on='matched_name',
        how='left'
    )

    merged_df = merged_df.drop(columns=['matched_name'])

    # 4. THE COALESCE TRICK
    new_numeric_cols = [col for col in cols_to_merge if col != 'matched_name']

    for col in new_numeric_cols:
        # If the column already existed in the DB (like plusMinus), pandas created _x and _y
        if f"{col}_x" in merged_df.columns and f"{col}_y" in merged_df.columns:
            # Combine them: Use AHL API value (_y), but if it's NaN, fallback to DB value (_x)
            merged_df[col] = merged_df[f"{col}_y"].combine_first(merged_df[f"{col}_x"])

            # Clean up the duplicate columns
            merged_df = merged_df.drop(columns=[f"{col}_x", f"{col}_y"])
        else:
            # If it's a completely new column (like 'shots'), just fill the NaNs with 0
            merged_df[col] = merged_df[col].fillna(0)

    return merged_df


# EXECUTION SCRIPT
table_name = "prospects_baseline"

try:
    print(f"Loading '{table_name}' from SQLite...")
    prospects_db = pd.read_sql(f"SELECT * FROM {table_name}", con=engine)
except Exception as e:
    print("Could not load database. Make sure you ran the EP scraper first!")
    exit()

ahl_api_data = fetch_ahl_official_api(limit=150)

if ahl_api_data is not None:
    enriched_prospects = fuzzy_merge_stats(prospects_db, ahl_api_data)

    print("\n--- SAVING ENRICHED DATA TO DATABASE ---")
    enriched_prospects.to_sql(table_name, con=engine, if_exists='replace', index=False)
    print(f"AHL Shots, PP Goals, and Plus/Minus injected into '{table_name}'.")
