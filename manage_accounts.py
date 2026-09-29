"""
Lists and deletes the simple accounts (routes/account_routes.py), for the
developer.

    python manage_accounts.py list
    python manage_accounts.py delete <username>
    python manage_accounts.py --render list             # Render's database
    python manage_accounts.py --render delete <username>

For someone who lost their password and made a new account, or who asks for
their data to be removed. There is deliberately no admin page: a command run by
whoever holds the database URL has no login of its own to protect.

`--render` reads RENDER_DATABASE_URL from .env, as transfer_to_render.py does;
without it, DATABASE_URL. Deleting takes the account's leagues with it (ON
DELETE CASCADE) and signs it out everywhere, since every request re-reads the
account. It asks you to type the username back first, unless given --yes.

Temporary, like the accounts themselves: remove it with them.

Author - Jason Druckenmiller
Created - 9/29/2026
Updated - 9/29/2026
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def normalise(url):
    """SQLAlchemy + psycopg2 wants postgresql://; some hosts hand out postgres://."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def describe(url):
    """host/dbname for printing - never the credentials."""
    try:
        host, _, database = url.split("@", 1)[1].partition("/")
        return f"{host.split(':')[0]} / {database.split('?')[0]}"
    except (IndexError, AttributeError):
        return "<unparseable>"


def list_accounts(conn):
    rows = conn.execute(text(
        'SELECT a.username, a.created_at, a.last_seen_at,'
        '       count(l.id) AS leagues, max(l.updated_at) AS saved'
        '  FROM local_accounts a LEFT JOIN account_leagues l ON l.account_id = a.id'
        ' GROUP BY a.id ORDER BY a.username_key')).mappings().all()
    if not rows:
        print("No accounts.")
        return

    def day(value):
        return value.strftime('%Y-%m-%d %H:%M') if value else '-'

    print(f"{'username':30s} {'created':16s} {'last sign-in':16s} {'leagues':>7s}  last save")
    for row in rows:
        print(f"{row['username']:30s} {day(row['created_at']):16s} {day(row['last_seen_at']):16s}"
              f" {row['leagues']:7d}  {day(row['saved'])}")
    print(f"\n{len(rows)} account(s).")


def delete_account(conn, username, assume_yes):
    row = conn.execute(text(
        'SELECT a.id, a.username, count(l.id) AS leagues'
        '  FROM local_accounts a LEFT JOIN account_leagues l ON l.account_id = a.id'
        ' WHERE a.username_key = :k GROUP BY a.id'), {'k': username.lower()}).mappings().first()
    if not row:
        sys.exit(f"No account called {username}.")

    print(f"Account {row['username']}, with {row['leagues']} league(s).")
    if not assume_yes:
        typed = input("Type the username again to delete it: ").strip()
        if typed.lower() != row['username'].lower():
            sys.exit("Did not match - nothing deleted.")
    conn.execute(text('DELETE FROM local_accounts WHERE id = :id'), {'id': row['id']})
    print(f"Deleted {row['username']} and its leagues.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--render', action='store_true',
                        help="use RENDER_DATABASE_URL rather than DATABASE_URL")
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='every account, with its leagues')
    delete = commands.add_parser('delete', help='delete an account and its leagues')
    delete.add_argument('username')
    delete.add_argument('--yes', action='store_true', help='skip typing the username back')
    args = parser.parse_args()

    variable = 'RENDER_DATABASE_URL' if args.render else 'DATABASE_URL'
    url = normalise(os.getenv(variable))
    if not url:
        sys.exit(f"{variable} is not set in .env.")
    print(f"Database: {describe(url)}\n")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        if args.command == 'list':
            list_accounts(conn)
        else:
            delete_account(conn, args.username, args.yes)


if __name__ == '__main__':
    main()
