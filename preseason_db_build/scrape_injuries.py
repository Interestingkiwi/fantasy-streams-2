"""
Identifies players who are injured to start the season
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 9/6/2026
"""


import time

import pandas as pd
import requests
from db_config import engine
from player_utils import find_player_id

# DO NOT "fix" this User-Agent into a full browser string. ESPN's WAF returns
# 403 for anything claiming to be Chrome (presumably a TLS-fingerprint
# mismatch); this deliberately truncated UA - and even no UA at all - returns
# 200. Verified 9/6/2026: 'Chrome/120.0.0.0 Safari/537.36' -> 403, this -> 200.
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3

def fetch_with_retries(url):
    """GET with a few retries, so a transient blip doesn't halt the pipeline."""
    response = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code == 200:
                return response
            print(f" -> attempt {attempt}/{RETRY_ATTEMPTS}: HTTP {response.status_code}")
        except requests.RequestException as e:
            print(f" -> attempt {attempt}/{RETRY_ATTEMPTS}: {e}")

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return response

def fetch_espn_injury_report():
    print("--- FETCHING LIVE INJURY REPORT FROM ESPN ---")
    url = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"

    response = fetch_with_retries(url)

    if response is None:
        print("Could not reach the ESPN API (network error).")
        return None

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

# Halt here rather than exiting 0 with no table written. Continuing would either
# blow up three steps later in apply_injury_adjustments.py with a confusing
# "relation current_injuries does not exist", or - worse - quietly publish
# projections with no injury adjustments applied at all.
if injuries_df is None:
    raise SystemExit(
        "Could not fetch the ESPN injury report - halting the pipeline.\n"
        "A 403 usually means the request looked like a bot: check that the "
        "User-Agent in HEADERS is still the truncated one (see the note above "
        "it). Otherwise ESPN may be rate-limiting this IP - wait and retry."
    )

print("\n--- CURRENT INJURY TIMELINES ---")

table_name = "current_injuries"

injuries_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"\nInjury data safely synced to '{table_name}' using official Player IDs.")
