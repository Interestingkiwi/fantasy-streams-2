"""
Utility Functions for Player DB
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""

import difflib
import pandas as pd
from datetime import datetime
from sqlalchemy import text
from db_config import engine

def log_match(source_name, target_name, player_id, match_type):
    """Logs only Fuzzy Matches and Failures."""
    if match_type in ["Fuzzy", "Failed"]:
        safe_pid = int(player_id) if player_id is not None else None

        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO match_audit_log (timestamp, source_name, target_name, player_id, match_type, confidence)
                VALUES (:ts, :src, :tgt, :pid, :mtype, :conf)
            """), {
                "ts": datetime.now(),
                "src": source_name,
                "tgt": target_name,
                "pid": safe_pid,
                "mtype": match_type,
                "conf": 1.0
            })

def find_player_id(name, team=None, position=None):
    df = pd.read_sql("SELECT * FROM player_directory", con=engine)
    aliases = pd.read_sql("SELECT * FROM player_aliases", con=engine)

    #Filter by Name (Exact or Alias)
    match = df[df['fullName'].str.lower() == name.lower()]

    if match.empty:
        alias_match = aliases[aliases['alias_name'].str.lower() == name.lower()]
        if not alias_match.empty:
            pid = alias_match.iloc[0]['player_id']
            match = df[df['playerId'] == pid]

    #Context Disambiguation
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
    with engine.begin() as conn:
        result = conn.execute(text('SELECT "fullName" FROM player_directory WHERE "playerId" = :pid'), {"pid": player_id}).fetchone()

        if not result:
            print(f"Error: Player ID {player_id} not found in player_directory. Cannot add alias.")
            return

        try:
            exists = conn.execute(text("SELECT 1 FROM player_aliases WHERE player_id = :pid AND alias_name = :alias"),
                                  {"pid": player_id, "alias": alias_name}).fetchone()

            if not exists:
                conn.execute(text("INSERT INTO player_aliases (player_id, alias_name) VALUES (:pid, :alias)"),
                             {"pid": player_id, "alias": alias_name})
                print(f"Successfully added alias '{alias_name}' for {result[0]} (ID: {player_id})")
            else:
                print(f"Alias '{alias_name}' already exists for ID: {player_id}")
        except Exception as e:
            print(f"Database error: {e}")

# python -c "from player_utils import add_player_alias; add_player_alias(8479977, 'Alex Nylander')"
