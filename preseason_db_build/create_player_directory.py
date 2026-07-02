"""
Creates a Player Directory for Name Variations to Crosswalk to NHL PlayerID
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/1/2026
"""


import pandas as pd
import sqlite3

def build_player_directory():
    print("--- BUILDING MASTER PLAYER DIRECTORY (WITH POSITION FIX) ---")
    conn = sqlite3.connect("projections.db")

    #Load data
    skaters = pd.read_sql("SELECT playerId, skaterFullName AS fullName, positionCode, teamAbbrevs, seasonId FROM historic_skaters_baseline", conn)
    goalies = pd.read_sql("SELECT playerId, goalieFullName AS fullName, teamAbbrevs, seasonId FROM historic_goalies_baseline", conn)

    goalies['positionCode'] = 'G'

    directory = pd.concat([skaters, goalies], ignore_index=True)
    directory = directory.sort_values(['playerId', 'seasonId'], ascending=[True, False])

    directory = directory.drop_duplicates(subset=['playerId'], keep='first')

    directory = directory[['playerId', 'fullName', 'positionCode', 'teamAbbrevs']]
    directory.to_sql("player_directory", conn, if_exists='replace', index=False)
    conn.close()

    print(f"{len(directory)} players indexed with team/pos context (Goalies assigned 'G').")

if __name__ == "__main__":
    build_player_directory()
