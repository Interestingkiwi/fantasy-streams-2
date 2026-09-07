"""
Copy the old app's per-league tables into this app's database.

Transitional dev tool. Phase 2 (the league ETL) is blocked on Yahoo API
access, so the per-league tables here are empty and every page that reads
them has nothing to show. The old deployment's database still holds a real
synced league, and its per-league schema is the same one `schema.py` ports,
so it drops straight in as fixture data.

Reads RENDER_DATABASE_URL (the old deployment, **read only**) and writes to
DATABASE_URL (local). Only the per-league tables are touched - projection
tables like `final_projections` are never read or written.

    python import_legacy_league_data.py --dry-run     # counts only, no writes
    python import_legacy_league_data.py               # copy
    python import_legacy_league_data.py --tables teams,weeks

Delete this once Phase 2 can populate the tables for real.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from schema import LEAGUE_DDL

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import")

CHUNK = 1000

# ~9M rows between them, pulled over the network from the old deployment, and
# only Season History (last in Phase 3) reads them. Skipped unless asked for.
SLOW_TABLES = ["daily_player_stats", "daily_bench_stats"]


def league_table_names():
    """The per-league table names, taken from schema.py so the two cannot drift."""
    names = []
    for ddl in LEAGUE_DDL:
        marker = "CREATE TABLE IF NOT EXISTS "
        start = ddl.index(marker) + len(marker)
        names.append(ddl[start:].split("(", 1)[0].strip())
    return names


def normalise(url):
    if url and url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url and "sslmode" not in url and "render.com" in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def table_exists(conn, name):
    return bool(conn.execute(
        text("SELECT 1 FROM information_schema.tables"
             " WHERE table_schema='public' AND table_name=:n"),
        {"n": name},
    ).first())


def shared_columns(src, dst, name):
    """Only copy columns both sides have, so a schema drift is skipped, not fatal."""
    def cols(conn):
        return [r[0] for r in conn.execute(
            text("SELECT column_name FROM information_schema.columns"
                 " WHERE table_schema='public' AND table_name=:n"
                 " ORDER BY ordinal_position"),
            {"n": name},
        )]

    source, dest = cols(src), set(cols(dst))
    return [c for c in source if c in dest]


def copy_table(src, dst, name, dry_run):
    if not table_exists(src, name):
        log.info("%-22s source has no such table - skipped", name)
        return 0
    if not table_exists(dst, name):
        log.warning("%-22s destination has no such table - skipped", name)
        return 0

    columns = shared_columns(src, dst, name)
    if not columns:
        log.warning("%-22s no columns in common - skipped", name)
        return 0

    total = src.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()
    if dry_run:
        log.info("%-22s %7d rows (dry run)", name, total)
        return total
    if not total:
        log.info("%-22s %7d rows", name, 0)
        return 0

    quoted = ", ".join(f'"{c}"' for c in columns)
    binds = ", ".join(f":{c}" for c in columns)

    # Replace wholesale: this is fixture data, not a merge.
    dst.execute(text(f'DELETE FROM "{name}"'))

    rows = src.execute(text(f'SELECT {quoted} FROM "{name}"'))
    copied = 0
    while True:
        batch = [dict(r) for r in rows.mappings().fetchmany(CHUNK)]
        if not batch:
            break
        dst.execute(text(f'INSERT INTO "{name}" ({quoted}) VALUES ({binds})'), batch)
        copied += len(batch)

    log.info("%-22s %7d rows", name, copied)
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report source row counts without writing")
    parser.add_argument("--tables", help="comma-separated subset to copy")
    parser.add_argument("--skip", default=",".join(SLOW_TABLES),
                        help="comma-separated tables to skip; defaults to the "
                             "daily stats tables, which are ~9M rows and only "
                             "Season History needs them. Pass --skip '' for all.")
    args = parser.parse_args()

    source_url = normalise(os.getenv("RENDER_DATABASE_URL"))
    dest_url = normalise(os.getenv("DATABASE_URL"))
    if not source_url:
        sys.exit("RENDER_DATABASE_URL is not set - it points at the old deployment.")
    if not dest_url:
        sys.exit("DATABASE_URL is not set.")

    names = league_table_names()
    skipped = {t.strip() for t in (args.skip or "").split(",") if t.strip()}
    if skipped:
        names = [n for n in names if n not in skipped]
        log.info("Skipping: %s", ", ".join(sorted(skipped)))
    if args.tables:
        wanted = {t.strip() for t in args.tables.split(",")}
        unknown = wanted - set(names)
        if unknown:
            sys.exit(f"Not per-league tables: {', '.join(sorted(unknown))}")
        names = [n for n in names if n in wanted]

    src_engine = create_engine(source_url, pool_pre_ping=True)
    dst_engine = create_engine(dest_url, pool_pre_ping=True)

    log.info("Copying %d tables%s", len(names), " (dry run)" if args.dry_run else "")
    total = 0
    with src_engine.connect() as src, dst_engine.begin() as dst:
        for name in names:
            total += copy_table(src, dst, name, args.dry_run)

    log.info("Done - %d rows%s.", total, " would be copied" if args.dry_run else " copied")

    if not args.dry_run:
        with dst_engine.connect() as dst:
            leagues = dst.execute(
                text("SELECT DISTINCT league_id FROM league_info ORDER BY league_id")
            ).scalars().all()
            log.info("league_id(s) now present: %s", leagues or "none")


if __name__ == "__main__":
    main()
