"""
Runs all DB Scripts to create projection DB.
Author - Jason Druckenmiller
Created - 7/2/2026
Updated - 7/4/2026
"""

import subprocess
import time
import sys

def run_script(script_name):
    """Helper function to run a script and handle errors."""
    print(f"\n{'='*50}")
    print(f"EXECUTING: {script_name}")
    print(f"{'='*50}")

    try:
        # Runs the script and waits for it to finish before moving on
        subprocess.run([sys.executable, script_name], check=True)
        time.sleep(1)
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: {script_name} failed. Halting the pipeline.")
        exit(1)

def build_database():
    print("STARTING FULL PIPELINE BUILD...")
    start_time = time.time()

    # 1. Infrastructure (Tables & Logs)
    run_script("add_tables.py")

    # 2. Base Historic Data
    run_script("historic_data_skaters.py")
    run_script("historic_data_goalies.py")

    # 3. Append Advanced Stats
    run_script("append_advanced_skaters.py")
    run_script("append_advanced_goalies.py")

    # 4. Create Directory
    run_script("create_player_directory.py")

    # 5. Rookies & Minor Leaguers
    run_script("scrape_ep_rookies.py")
    run_script("enrich_ahl_stats.py")

    # 6. Live Status / Context
    run_script("scrape_injuries.py")

    # 7. Math & Projections
    run_script("calculate_skater_projections.py")
    run_script("calculate_goalie_projections.py")

    # 8. Final Adjustments
    run_script("apply_injury_adjustments.py")

    end_time = time.time()
    elapsed = round((end_time - start_time) / 60, 2)

    print(f"\n{'='*50}")
    print(f"Database built in {elapsed} minutes.")
    print(f"{'='*50}")

if __name__ == "__main__":
    build_database()
