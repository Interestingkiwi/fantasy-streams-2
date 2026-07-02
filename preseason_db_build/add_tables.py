"""
Creates extra db tables
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/1/2026
"""


import sqlite3

def setup_infrastructure():
    conn = sqlite3.connect("projections.db")
    cursor = conn.cursor()

    #Audit Log Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS match_audit_log (
            timestamp DATETIME,
            source_name TEXT,
            target_name TEXT,
            player_id INTEGER,
            match_type TEXT,
            confidence REAL
        )
    ''')

    #Aliases Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS player_aliases (
            player_id INTEGER,
            alias_name TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print("Audit log and Alias tables created.")

if __name__ == "__main__":
    setup_infrastructure()
