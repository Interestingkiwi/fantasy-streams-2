"""
Creates extra db tables
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""


from sqlalchemy import text
from db_config import engine

def setup_infrastructure():
    with engine.begin() as conn:

        #Audit Log Table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS match_audit_log (
                timestamp TIMESTAMP,
                source_name TEXT,
                target_name TEXT,
                player_id INTEGER,
                match_type TEXT,
                confidence REAL
            )
        '''))

        #Aliases Table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS player_aliases (
                player_id INTEGER,
                alias_name TEXT
            )
        '''))

    print("Audit log and Alias tables safely created in PostgreSQL.")

if __name__ == "__main__":
    setup_infrastructure()
