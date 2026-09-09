"""
Fetches past 5 years of NHL Goalie Data using NHL.com API
Author - Jason Druckenmiller
Created - 7/1/2026
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

# Paging is only stable if the server has a total order to page through. With no
# sort the API returns ties in whatever order it pleases *per request*, so pages
# overlap and, for every row served twice, one is never served at all. Sorting on
# playerId - unique, so no ties are left to break - makes a run reproducible.
# Same failure the game-results scraper hit; see CLAUDE.md.
PAGE_SORT = '[{"property":"playerId","direction":"ASC"}]'


def fetch_nhl_goalie_report(report_type, seasons):
    """
    Fetches a specific stat report from the NHL API for multiple seasons.
    """
    url = f"https://api.nhle.com/stats/rest/en/goalie/{report_type}"
    all_seasons_df = pd.DataFrame()

    for season in seasons:
        print(f" -> Pulling goalie '{report_type}' for {season}...")
        start = 0
        limit = 100
        all_players = []
        reported_total = None

        while True:
            params = {
                "isAggregate": "false",
                "isGame": "false",
                "start": start,
                "limit": limit,
                "sort": PAGE_SORT,
                "factCayenneExp": "gamesPlayed>=1",
                "cayenneExp": f"gameTypeId=2 and seasonId<={season} and seasonId>={season}"
            }

            response = requests.get(url, params=params)
            if response.status_code != 200:
                print(f"Failed to fetch data at start={start}: HTTP {response.status_code}")
                break

            data = response.json()
            players = data.get('data', [])
            reported_total = data.get('total', reported_total)

            if not players:
                break

            all_players.extend(players)
            start += limit
            time.sleep(0.3)

        if all_players:
            df = pd.DataFrame(all_players)
            df['seasonId'] = season

            # The count the API says it holds, against what actually arrived
            # distinctly. Silence here is the whole point of the sort above.
            distinct = df['playerId'].nunique()
            if reported_total is not None and distinct != reported_total:
                print(f"    [WARN] goalie '{report_type}' {season}: {distinct} distinct "
                      f"goalies but the API reports {reported_total}. Paging lost rows.")

            all_seasons_df = pd.concat([all_seasons_df, df], ignore_index=True)

    return all_seasons_df



# EXECUTION SCRIPT
seasons_to_pull = generate_rolling_seasons(5)
print(f"Target Seasons: {seasons_to_pull}\n")

print("--- FETCHING GOALIE SUMMARY STATS ---")
master_df = fetch_nhl_goalie_report("summary", seasons_to_pull)

# Goalie rates are not aged - see aging.py for why the measurement comes back
# empty - but the birthdates are what that measurement is made against, and
# they cost one extra paged report to collect alongside the summary.
print("\n--- FETCHING GOALIE BIOS (Birthdates) ---")
df_bios = fetch_nhl_goalie_report("bios", seasons_to_pull)
# Keyed on the player, not on player-and-season: a birthdate does not change,
# and the bios report drops the odd goalie from the odd season.
if not df_bios.empty and 'birthDate' in df_bios.columns:
    known = df_bios.dropna(subset=['birthDate']).drop_duplicates(subset=['playerId'])
    master_df['birthDate'] = pd.to_datetime(
        master_df['playerId'].map(dict(zip(known['playerId'], known['birthDate']))),
        errors='coerce')

columns_to_keep = [
    'seasonId', 'playerId', 'lastName', 'goalieFullName', 'birthDate',
    'teamAbbrevs', 'gamesPlayed', 'gamesStarted', 'wins', 'losses',
    'otLosses', 'shotsAgainst', 'goalsAgainst', 'goalsAgainstAverage',
    'saves', 'savePct', 'shutouts', 'timeOnIce'
]

existing_columns = [col for col in columns_to_keep if col in master_df.columns]
final_df = master_df[existing_columns].copy()

if all(col in final_df.columns for col in ['wins', 'losses', 'otLosses']):
    decisions = final_df['wins'] + final_df['losses'] + final_df['otLosses']
    final_df['winPct'] = (final_df['wins'] / decisions).fillna(0)

if 'gamesStarted' in final_df.columns:
    final_df['startPct'] = (final_df['gamesStarted'] / 82.0).fillna(0)

# Write to Database
table_name = "historic_goalies_baseline"

final_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

print(f"\n{len(final_df)} goalie-seasons safely written to '{table_name}'.")
