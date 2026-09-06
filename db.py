"""
Shared database access for the Fantasy Streams web app.

Canonical SQLAlchemy Core engine + a few thin query helpers so route code
does not repeat connection/`text()`/row-unpacking boilerplate. Everything
here is Core, not ORM. Raw SQL strings are passed in and wrapped in
`text()` internally; bind parameters use the `:name` style, supplied as a
dict.

The offline `preseason_db_build/` pipeline keeps its own tiny engine in
`db_config.py` on purpose: those scripts run with the working directory
set to `preseason_db_build/` and import `from db_config import engine`,
so they cannot see this module. Both read the same `DATABASE_URL`.

Author - Jason Druckenmiller
Created - 9/3/2026
Updated - 9/3/2026
"""

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

# SQLAlchemy + psycopg2 wants the postgresql:// scheme; some hosts hand out postgres://.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

# pool_pre_ping replaces the hand-rolled liveness checks the old app carried:
# a dead/stale connection is detected and transparently replaced before use.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,   # recycle connections older than 30 min (managed PG drops idle ones)
    pool_size=5,         # fine for local + a single web process; tune per worker count in prod
    max_overflow=10,
)


def _as_text(sql):
    """Accept a raw SQL string or an already-built text()/TextClause."""
    return text(sql) if isinstance(sql, str) else sql


@contextmanager
def get_connection():
    """
    A plain connection for reads, streaming large result sets, or running
    several statements together. Does NOT commit — use transaction() for writes.
    """
    with engine.connect() as conn:
        yield conn


@contextmanager
def transaction():
    """
    A connection wrapped in a transaction (BEGIN/COMMIT, ROLLBACK on error).
    Use for writes, including INSERT ... RETURNING:

        with transaction() as conn:
            row = conn.execute(
                text("INSERT INTO t (a) VALUES (:a) RETURNING id"), {"a": 1}
            ).mappings().first()
    """
    with engine.begin() as conn:
        yield conn


def fetch_all(sql, params=None):
    """Run a SELECT and return every row as a plain dict (JSON-ready)."""
    with engine.connect() as conn:
        result = conn.execute(_as_text(sql), params or {})
        return [dict(row) for row in result.mappings()]


def fetch_one(sql, params=None):
    """Run a SELECT and return the first row as a dict, or None."""
    with engine.connect() as conn:
        row = conn.execute(_as_text(sql), params or {}).mappings().first()
        return dict(row) if row is not None else None


def fetch_scalar(sql, params=None):
    """Run a SELECT and return the first column of the first row, or None."""
    with engine.connect() as conn:
        return conn.execute(_as_text(sql), params or {}).scalar()


def execute(sql, params=None):
    """
    Run a write (INSERT/UPDATE/DELETE/DDL) inside a transaction and return the
    affected row count. For statements with RETURNING, use transaction() instead.
    `params` may be a dict, or a list of dicts for an executemany-style batch.
    """
    with engine.begin() as conn:
        result = conn.execute(_as_text(sql), params or {})
        return result.rowcount


__all__ = [
    "engine",
    "text",
    "Connection",
    "get_connection",
    "transaction",
    "fetch_all",
    "fetch_one",
    "fetch_scalar",
    "execute",
]
