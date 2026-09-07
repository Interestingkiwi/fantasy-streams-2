"""
Copies the tables the draft-prep page reads from a local database to Render.

`build_database.py` re-scrapes every external source, so running it against
Render produces fresh numbers rather than the ones you just reviewed locally.
This lifts the finished tables across instead: same rows, no re-scrape, and the
live site ends up with exactly what was verified.

Source is DATABASE_URL, target is RENDER_DATABASE_URL - both from .env, so no
connection string is ever typed on the command line or pasted into a chat. Use
Render's *External* Database URL, with ?sslmode=require.

Dry run by default; pass --apply to actually write. Tables are replaced whole,
the same way the pipeline writes them.

    python transfer_to_render.py           # report what would happen
    python transfer_to_render.py --apply   # do it

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

# Everything /draft-prep/ reads. The rest of the projection tables are working
# state for the pipeline and are not queried by the web app.
DEFAULT_TABLES = ["final_projections", "nhl_schedule"]

# Recreated after the copy, since replacing a table drops its indexes
INDEXES = {
    "nhl_schedule": [
        ('idx_schedule_date', '"gameDate"'),
    ],
}

CHUNK_SIZE = 500


def normalise(url):
    """SQLAlchemy + psycopg2 wants postgresql://; some hosts hand out postgres://."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def describe(url):
    """host/dbname for printing - never the credentials."""
    try:
        tail = url.split("@", 1)[1]
        host, _, database = tail.partition("/")
        return f"{host.split(':')[0]} / {database.split('?')[0]}"
    except (IndexError, AttributeError):
        return "<unparseable>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write to the target (default is a dry run)")
    parser.add_argument("--tables", nargs="+", default=DEFAULT_TABLES,
                        help=f"tables to copy (default: {' '.join(DEFAULT_TABLES)})")
    args = parser.parse_args()

    source_url = normalise(os.getenv("DATABASE_URL"))
    target_url = normalise(os.getenv("RENDER_DATABASE_URL"))

    if not source_url:
        sys.exit("DATABASE_URL is not set. It is the local source database.")
    if not target_url:
        sys.exit(
            "RENDER_DATABASE_URL is not set.\n"
            "Add Render's External Database URL to .env:\n"
            "  RENDER_DATABASE_URL=postgresql://...@dpg-....render.com/fantasy_streams?sslmode=require"
        )

    if source_url == target_url:
        sys.exit("Source and target are the same database; refusing to run.")

    if "render.com" in target_url and "sslmode" not in target_url:
        print(" -> [WARN] Target has no ?sslmode=require; Render usually needs it.")

    print(f"  source: {describe(source_url)}")
    print(f"  target: {describe(target_url)}")
    print(f"  tables: {', '.join(args.tables)}")
    print(f"  mode:   {'APPLY - the target will be overwritten' if args.apply else 'dry run'}")
    print()

    source = create_engine(source_url)
    target = create_engine(target_url, pool_pre_ping=True)

    target_tables = set(inspect(target).get_table_names())
    frames = {}

    for table in args.tables:
        frame = pd.read_sql(f'SELECT * FROM {table}', con=source)
        frames[table] = frame

        if table in target_tables:
            with target.connect() as conn:
                before = conn.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
            current = f"{before} rows"
        else:
            current = "does not exist yet"

        print(f"  {table}: {len(frame)} rows locally, target {current}")

    if not args.apply:
        print("\nDry run - nothing written. Re-run with --apply to copy.")
        return

    print()
    for table, frame in frames.items():
        print(f"  Writing {table} ({len(frame)} rows)...")
        frame.to_sql(table, con=target, if_exists="replace", index=False,
                     chunksize=CHUNK_SIZE, method="multi")

        for name, columns in INDEXES.get(table, []):
            with target.begin() as conn:
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})'))

    print("\n  Verifying:")
    ok = True
    for table, frame in frames.items():
        with target.connect() as conn:
            after = conn.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
        match = "OK" if after == len(frame) else "MISMATCH"
        if after != len(frame):
            ok = False
        print(f"    {table}: {after} rows on target, {len(frame)} local - {match}")

    # The column that makes the position filters work is the easiest thing to lose
    if "final_projections" in frames:
        with target.connect() as conn:
            has_eligibility = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'final_projections'
                  AND column_name = 'eligiblePositions'
            """)).scalar()
        print(f"    eligiblePositions column present: {'yes' if has_eligibility else 'NO'}")
        ok = ok and bool(has_eligibility)

    print("\nDone." if ok else "\nFinished with mismatches - check the output above.")


if __name__ == "__main__":
    main()
