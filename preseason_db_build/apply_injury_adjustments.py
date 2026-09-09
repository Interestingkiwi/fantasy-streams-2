"""
Adjusts projections based on injuries, creates final_projections table
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 9/6/2026
"""


import pandas as pd
from db_config import engine
from season_config import days_per_game, season_start_date
from datetime import datetime
from sqlalchemy import inspect
import numpy as np

REQUIRED_TABLES = (
    "projected_skaters_baseline",
    "projected_goalies_baseline",
    "current_injuries",
)

# Opening night and the pace of play both come from the scraped schedule rather
# than being written down here. Both constants that used to sit in this file had
# gone stale against the 2026-27 season and they pulled in opposite directions:
# the hardcoded 8 October was nine days after the real opener, so every absence
# was measured from too late a start, while dividing the days missed by 2 assumed
# a game every other day when 84 games over that calendar is one every 2.3.
SEASON_START_DATE = pd.Timestamp(season_start_date())
DAYS_PER_GAME = days_per_game()

# Rate stats, not counting stats. Missing games does not change a goalie's save
# percentage, and rounding one to a single decimal flattens the whole league
# onto .9 - so these are held out of the injury scaling and keep their precision.
RATE_COLUMNS = {
    "proj_savePct": 3,
    "proj_winPct": 3,
    "proj_startPct": 3,
    "proj_goalsAgainstAverage": 2,
}
COUNTING_DECIMALS = 1

def get_return_date(details_str):
    """Extracts the returnDate from the dictionary-like string."""
    try:
        details_dict = eval(details_str)
        return details_dict.get('returnDate')
    except:
        return None

print("--- APPLYING INJURY ADJUSTMENTS (VIA CROSSWALK) ---")
print(f" -> Season opens {SEASON_START_DATE.date()}, one game every "
      f"{DAYS_PER_GAME:.2f} days.")

#0. Fail clearly if an earlier step didn't produce its table
missing = [t for t in REQUIRED_TABLES if not inspect(engine).has_table(t)]
if missing:
    raise SystemExit(
        f"Missing upstream table(s): {', '.join(missing)}.\n"
        "An earlier pipeline step did not complete. Run build_database.py, "
        "which executes every step in order."
    )

#1. Load All Tables
skaters = pd.read_sql("SELECT * FROM projected_skaters_baseline", con=engine)
goalies = pd.read_sql("SELECT * FROM projected_goalies_baseline", con=engine)
injuries = pd.read_sql("SELECT * FROM current_injuries", con=engine)

#1b. Say how old the injury feed is.
# A stale feed fails silently and convincingly: every player still gets a
# projection, the ones with old injuries are still docked, and the only symptom
# is that someone hurt last week looks healthy. That is very hard to spot on a
# draft board and very easy to act on. Bedard was the real case - the feed was
# two months old, his shoulder was reported the day before, and he ranked as a
# fit 21-year-old.
STALE_INJURY_DAYS = 7

if 'injuryDate' in injuries.columns and not injuries.empty:
    latest_report = pd.to_datetime(injuries['injuryDate'], errors='coerce', utc=True).max()
    if pd.notna(latest_report):
        age_days = (pd.Timestamp.now(tz='UTC') - latest_report).days
        print(f" -> Injury feed's newest report is {age_days} day(s) old "
              f"({latest_report.date()}), {len(injuries)} players listed.")
        if age_days > STALE_INJURY_DAYS:
            print(f"    [WARN] That is over {STALE_INJURY_DAYS} days stale. Anyone hurt "
                  "since then is being projected healthy. Re-run scrape_injuries.py "
                  "before trusting this board.")

#2. Extract return dates
injuries['cleanReturnDate'] = injuries['injuryDetails'].apply(get_return_date)
injuries = injuries.dropna(subset=['cleanReturnDate'])
injuries['cleanReturnDate'] = pd.to_datetime(injuries['cleanReturnDate'])

#3. Calculate Games Missed
injuries['gamesMissed'] = (injuries['cleanReturnDate'] - SEASON_START_DATE).dt.days
injuries['gamesMissed'] = (injuries['gamesMissed'] / DAYS_PER_GAME).clip(lower=0).astype(int)

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
        stat_cols = [col for col in skaters.columns
                     if col.startswith('proj_') and col not in RATE_COLUMNS]
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
        stat_cols = [col for col in goalies.columns
                     if col.startswith('proj_') and col not in RATE_COLUMNS]
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
        final_df[col] = final_df[col].round(RATE_COLUMNS.get(col, COUNTING_DECIMALS))

#8. Save final adjusted projections
final_df.to_sql("final_projections", con=engine, if_exists='replace', index=False)

print(f"SUCCESS! 'final_projections' table created with {len(final_df)} players (Injury adjusted).")
