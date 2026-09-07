"""
Database schema for the Fantasy Streams web app.

Idempotent DDL run once at startup: the admin tables (users, league
assignments, job logs, scheduled transactions) plus the per-league tables
that the league-sync ETL fills from Yahoo. One Postgres database holds
every league's rows, keyed by league_id — not a database per league.

Ported from the old repo: admin tables came from app.py's init_admin_db(),
the per-league set from db_builder._create_tables(). This is plain
CREATE TABLE IF NOT EXISTS text; a real migration tool (Alembic) is the
eventual path once the schema stabilises.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import logging
import os

from db import text, transaction

log = logging.getLogger(__name__)


# --- Admin tables --------------------------------------------------------------
# Global app state: who has logged in, which user keeps each league fresh,
# background-job log lines, and pending automated add/drops.

ADMIN_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        guid TEXT PRIMARY KEY,
        access_token TEXT,
        refresh_token TEXT,
        token_type TEXT,
        expires_in INTEGER,
        token_time DOUBLE PRECISION,
        consumer_key TEXT,
        consumer_secret TEXT,
        is_premium BOOLEAN DEFAULT FALSE,
        premium_expiration_date DATE,
        tos_accepted_version INTEGER DEFAULT 0,
        tos_accepted_at TIMESTAMP,
        last_seen_at TIMESTAMP
    )
    """,
    # NOTE: league_id is TEXT here but INTEGER in the per-league tables below.
    # Kept as-is from the old schema; reconcile in Phase 2 if it causes casts.
    """
    CREATE TABLE IF NOT EXISTS league_updaters (
        league_id TEXT PRIMARY KEY,
        user_guid TEXT,
        last_updated_ts DOUBLE PRECISION,
        FOREIGN KEY (user_guid) REFERENCES users(guid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_logs (
        log_id SERIAL PRIMARY KEY,
        job_id TEXT NOT NULL,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_transactions (
        id SERIAL PRIMARY KEY,
        user_guid TEXT NOT NULL,
        league_id TEXT NOT NULL,
        team_key TEXT,
        add_player_id INTEGER,
        add_player_name TEXT,
        drop_player_id INTEGER,
        drop_player_name TEXT,
        scheduled_time TIMESTAMP,
        status TEXT DEFAULT 'pending',      -- pending, success, failed
        result_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_guid) REFERENCES users(guid)
    )
    """,
]


# --- Per-league tables -------------------------------------------------------
# Filled by the league-sync ETL (Phase 2, ported from db_builder.py). Every
# row carries league_id; primary keys are composite on it.

LEAGUE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS league_info (
        league_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (league_id, key)
    )
    """,
    # manager_guid folded in from the old runtime ALTER in _create_tables.
    """
    CREATE TABLE IF NOT EXISTS teams (
        league_id INTEGER NOT NULL,
        team_id TEXT NOT NULL,
        name TEXT,
        manager_nickname TEXT,
        manager_guid TEXT,
        PRIMARY KEY (league_id, team_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_lineups_dump (
        league_id INTEGER NOT NULL,
        date_ TEXT NOT NULL,
        team_id INTEGER NOT NULL,
        c1 TEXT, c2 TEXT, l1 TEXT, l2 TEXT, r1 TEXT, r2 TEXT,
        d1 TEXT, d2 TEXT, d3 TEXT, d4 TEXT, g1 TEXT, g2 TEXT,
        b1 TEXT, b2 TEXT, b3 TEXT, b4 TEXT, b5 TEXT, b6 TEXT,
        b7 TEXT, b8 TEXT, b9 TEXT, b10 TEXT, b11 TEXT, b12 TEXT,
        b13 TEXT, b14 TEXT, b15 TEXT, b16 TEXT, b17 TEXT, b18 TEXT, b19 TEXT,
        i1 TEXT, i2 TEXT, i3 TEXT, i4 TEXT, i5 TEXT,
        PRIMARY KEY (league_id, date_, team_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scoring (
        league_id INTEGER NOT NULL,
        stat_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        scoring_group TEXT NOT NULL,
        PRIMARY KEY (league_id, stat_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lineup_settings (
        league_id INTEGER NOT NULL,
        position_id SERIAL PRIMARY KEY,
        position TEXT NOT NULL,
        position_count INTEGER NOT NULL,
        UNIQUE (league_id, position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weeks (
        league_id INTEGER NOT NULL,
        week_num INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        PRIMARY KEY (league_id, week_num)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matchups (
        league_id INTEGER NOT NULL,
        week INTEGER NOT NULL,
        team1 TEXT NOT NULL,
        team2 TEXT NOT NULL,
        PRIMARY KEY (league_id, week, team1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rosters (
        league_id INTEGER NOT NULL,
        team_id INTEGER NOT NULL,
        p1 INTEGER, p2 INTEGER, p3 INTEGER, p4 INTEGER, p5 INTEGER,
        p6 INTEGER, p7 INTEGER, p8 INTEGER, p9 INTEGER, p10 INTEGER,
        p11 INTEGER, p12 INTEGER, p13 INTEGER, p14 INTEGER, p15 INTEGER,
        p16 INTEGER, p17 INTEGER, p18 INTEGER, p19 INTEGER, p20 INTEGER,
        p21 INTEGER, p22 INTEGER, p23 INTEGER, p24 INTEGER, p25 INTEGER,
        p26 INTEGER, p27 INTEGER, p28 INTEGER, p29 INTEGER,
        PRIMARY KEY (league_id, team_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS free_agents (
        league_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        status TEXT,
        PRIMARY KEY (league_id, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS waiver_players (
        league_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        status TEXT,
        PRIMARY KEY (league_id, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rostered_players (
        league_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        status TEXT,
        eligible_positions TEXT,
        PRIMARY KEY (league_id, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS db_metadata (
        league_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (league_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        league_id INTEGER NOT NULL,
        transaction_date TEXT NOT NULL,
        player_id INTEGER NOT NULL,
        player_name TEXT NOT NULL,
        fantasy_team TEXT,
        move_type TEXT,
        PRIMARY KEY (league_id, transaction_date, player_id, move_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_player_stats (
        league_id INTEGER NOT NULL,
        date_ TEXT NOT NULL,
        team_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        player_name_normalized TEXT,
        lineup_pos TEXT,
        stat_id INTEGER NOT NULL,
        category TEXT,
        stat_value REAL,
        PRIMARY KEY (league_id, date_, player_id, stat_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_bench_stats (
        league_id INTEGER NOT NULL,
        date_ TEXT NOT NULL,
        team_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        player_name_normalized TEXT,
        lineup_pos TEXT,
        stat_id INTEGER NOT NULL,
        category TEXT,
        stat_value REAL,
        PRIMARY KEY (league_id, date_, player_id, stat_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rosters_tall (
        league_id INTEGER NOT NULL,
        team_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        PRIMARY KEY (league_id, team_id, player_id)
    )
    """,
]


# --- Seed rows ---------------------------------------------------------------
# The dev-backdoor login (/login with DEV_BACKDOOR_PASS) authenticates as this
# guid; premium checks then look it up.

SEEDS = [
    (
        """
        INSERT INTO users (guid, is_premium, premium_expiration_date)
        VALUES ('DEV_ADMIN_GUID', TRUE, '9999-12-31')
        ON CONFLICT (guid) DO NOTHING
        """,
        {},
    ),
]

# --- Shared reference tables ---------------------------------------------------
# Yahoo-derived data that is not per-league. Filled by the Phase 2 ETL (the old
# repo builds it in jobs/fetch_player_ids.py).

SHARED_DDL = [
    # Yahoo's player directory, keyed by Yahoo's own player_id - a different id
    # space from the NHL `playerId` in `player_directory` / `final_projections`,
    # which is why this is not called `players`. Every per-league table that
    # references a player (rosters_tall, rostered_players, free_agents,
    # waiver_players) carries a Yahoo player_id and nothing else, so this table
    # is the only way to show a name.
    """
    CREATE TABLE IF NOT EXISTS yahoo_players (
        player_id TEXT PRIMARY KEY,
        player_name TEXT,
        player_team TEXT,
        positions TEXT,
        status TEXT,
        player_name_normalized TEXT
    )
    """,
]


# --- Migrations --------------------------------------------------------------
# CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a column
# change has to be stated separately. Every entry must be safe to re-run.

MIGRATIONS = [
    # SQLite's REAL is 8 bytes; Postgres's REAL is 4 (~7 significant digits)
    # and cannot hold a Unix epoch - 1.79e9 rounds to the nearest ~128s, so
    # stored token_time was up to two minutes out and token expiry maths with
    # it. Both columns came across from the old repo's SQLite DDL unchanged.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users'
                      AND column_name = 'token_time'
                      AND data_type = 'real') THEN
            ALTER TABLE users
                ALTER COLUMN token_time TYPE DOUBLE PRECISION;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'league_updaters'
                      AND column_name = 'last_updated_ts'
                      AND data_type = 'real') THEN
            ALTER TABLE league_updaters
                ALTER COLUMN last_updated_ts TYPE DOUBLE PRECISION;
        END IF;
    END $$;
    """,
]

ALL_TABLES = ADMIN_DDL + LEAGUE_DDL + SHARED_DDL


def init_schema(strict=False):
    """
    Create every table if missing and apply seed rows. Safe to call on every
    startup. Set SKIP_SCHEMA_INIT=1 to bypass (tests, CI against a fixed DB).
    With strict=True, a failure re-raises instead of just logging.
    """
    if os.getenv("SKIP_SCHEMA_INIT"):
        log.info("SKIP_SCHEMA_INIT set - skipping schema init.")
        return

    try:
        with transaction() as conn:
            for ddl in ALL_TABLES:
                conn.execute(text(ddl))
            for migration in MIGRATIONS:
                conn.execute(text(migration))
            for sql, params in SEEDS:
                conn.execute(text(sql), params)
        log.info(
            "Schema ready (%d tables, %d migrations).",
            len(ALL_TABLES), len(MIGRATIONS),
        )
    except Exception:
        log.exception("Schema init failed.")
        if strict:
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_schema(strict=True)
    print(f"OK - {len(ALL_TABLES)} tables ensured.")
