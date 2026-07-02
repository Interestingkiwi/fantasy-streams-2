"""
Utility Functions for Player DB
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/1/2026
"""


import sqlite3
import difflib
import pandas as pd
from datetime import datetime

def log_match(source_name, target_name, player_id, match_type):
    """Logs only Fuzzy Matches and Failures."""
    if match_type in ["Fuzzy", "Failed"]:
        conn = sqlite3.connect("projections.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO match_audit_log VALUES (?, ?, ?, ?, ?, ?)",
                       (datetime.now(), source_name, target_name, player_id, match_type, 1.0))
        conn.commit()
        conn.close()

def find_player_id(name, team=None, position=None):
    conn = sqlite3.connect("projections.db")
    df = pd.read_sql("SELECT * FROM player_directory", conn)
    aliases = pd.read_sql("SELECT * FROM player_aliases", conn)
    conn.close()

    #Filter by Name (Exact or Alias)
    match = df[df['fullName'].str.lower() == name.lower()]

    if match.empty:
        alias_match = aliases[aliases['alias_name'].str.lower() == name.lower()]
        if not alias_match.empty:
            pid = alias_match.iloc[0]['player_id']
            match = df[df['playerId'] == pid]

    #Context Disambiguation (The Safety Step)

    if len(match) > 1:
        if team:
            match = match[match['teamAbbrevs'].str.contains(team, na=False)]
        if position:
            match = match[match['positionCode'] == position]

    #Final Match Verification
    if not match.empty:
        if len(match) == 1:
            return match.iloc[0]['playerId']
        else:

            log_match(name, "Ambiguous", None, "Failed")
            return None

    #Fuzzy Matching
    all_names = df['fullName'].tolist()
    fuzzy = difflib.get_close_matches(name, all_names, n=1, cutoff=0.8)
    if fuzzy:
        best_name = fuzzy[0]
        pid = df[df['fullName'] == best_name].iloc[0]['playerId']
        log_match(name, best_name, pid, "Fuzzy")
        return pid

    log_match(name, "None", None, "Failed")
    return None

def add_player_alias(player_id, alias_name):
    """
    Adds a name alias to the player_aliases table.
    Ensures the player exists in the directory first.
    """
    conn = sqlite3.connect("projections.db")
    cursor = conn.cursor()


    cursor.execute("SELECT fullName FROM player_directory WHERE playerId = ?", (player_id,))
    result = cursor.fetchone()

    if not result:
        print(f"Error: Player ID {player_id} not found in player_directory. Cannot add alias.")
        conn.close()
        return

    try:
        cursor.execute("INSERT OR IGNORE INTO player_aliases (player_id, alias_name) VALUES (?, ?)",
                       (player_id, alias_name))
        conn.commit()
        print(f"Successfully added alias '{alias_name}' for {result[0]} (ID: {player_id})")
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        conn.close()

#python -c "from player_utils import add_player_alias; add_player_alias(8479977, 'Alex Nylander')"
