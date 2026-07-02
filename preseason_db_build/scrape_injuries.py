"""
Identifies players who are injured to start the season
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/1/2026
"""


import pandas as pd
import requests
import sqlite3
from player_utils import find_player_id

def fetch_espn_injury_report():
    print("--- FETCHING LIVE INJURY REPORT FROM ESPN ---")
    url = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        try:
            raw_data = response.json()
            injuries_list = []

            for team_data in raw_data.get('injuries', []):
                for player_data in team_data.get('injuries', []):
                    player_info = player_data.get('athlete', {})
                    raw_name = player_info.get('displayName', 'Unknown')

                    player_id = find_player_id(raw_name)

                    injuries_list.append({
                        'playerId': player_id,
                        'playerName': raw_name,
                        'injuryStatus': player_data.get('status', 'Unknown'),
                        'injuryDetails': str(player_data.get('details', 'Unknown')),
                        'injuryDate': player_data.get('date', 'Unknown')
                    })

            injury_df = pd.DataFrame(injuries_list)

            if injury_df.empty:
                print(" -> The ESPN API returned 0 injuries.")
                return pd.DataFrame(columns=['playerId', 'playerName', 'injuryStatus', 'injuryDetails', 'injuryDate'])

            print(f"Successfully pulled {len(injury_df)} injured players from ESPN.")
            return injury_df

        except Exception as e:
            print(f"Failed to parse ESPN JSON: {e}")
            return None
    else:
        print(f"Failed to connect to ESPN API. HTTP Status: {response.status_code}")
        return None


# EXECUTION SCRIPT
injuries_df = fetch_espn_injury_report()

if injuries_df is not None:
    print("\n--- CURRENT INJURY TIMELINES ---")

    db_name = "projections.db"
    conn = sqlite3.connect(db_name)
    table_name = "current_injuries"

    injuries_df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()

    print(f"\nInjury data safely synced to '{table_name}' using official Player IDs.")
