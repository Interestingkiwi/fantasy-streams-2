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

# Paging is only stable if the server has a total order to page through. With no
# sort the API returns ties in whatever order it pleases *per request*, so pages
# overlap and, for every row served twice, one is never served at all: two
# identical runs of this script came back with 940 rows containing 931 and 928
# distinct players. Sorting on playerId - unique, so no ties are left to break -
# returns all 940. Same failure the game-results scraper hit; see CLAUDE.md.
PAGE_SORT = '[{"property":"playerId","direction":"ASC"}]'


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
                print(f"    [WARN] '{report_type}' {season}: {distinct} distinct players "
                      f"but the API reports {reported_total}. Paging lost rows.")

            all_seasons_df = pd.concat([all_seasons_df, df], ignore_index=True)

    return all_seasons_df

def birthdate_lookup(bios_df):
    """{playerId: birthDate} from the bios report, pooled across every season."""
    if bios_df is None or bios_df.empty or 'birthDate' not in bios_df.columns:
        return {}

    known = bios_df.dropna(subset=['birthDate']).drop_duplicates(subset=['playerId'])
    return dict(zip(known['playerId'], known['birthDate']))


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

# Birthdates come from the 'bios' report rather than a per-player lookup: it is
# the same paged season query as everything else here, so it costs one more
# report instead of ~1,000 requests. The projection engine needs them to know
# how old each of these seasons was played at - see aging.py.
print("\n--- FETCHING BIOS (Birthdates) ---")
df_bios = fetch_nhl_report("bios", seasons_to_pull)

print("\n--- MERGING DATA ---")
master_df = df_summary.copy()

master_df = safe_merge(master_df, df_realtime, ['hits', 'blockedShots'])

# Keyed on the player, not on player-and-season: a birthdate does not change,
# and the bios report drops the odd player from the odd season, which a
# season-keyed join would turn into a missing age rather than a known one.
# Stored as a real date so the age maths never has to parse strings back out.
master_df['birthDate'] = pd.to_datetime(
    master_df['playerId'].map(birthdate_lookup(df_bios)), errors='coerce')
missing_dob = master_df['birthDate'].isna().sum()
if missing_dob:
    print(f" -> [WARN] {missing_dob} player-seasons have no birthdate; "
          "those players are projected without an age adjustment.")


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
    'birthDate',
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
