"""
Who is hurt today: ESPN's NHL injury report, refreshed every night into
`current_injuries`.

Until 10/5/2026 the table was written once, by the preseason pipeline's
`scrape_injuries.py`, and never again - so in October League Home was still
showing September's report: offseason surgeries for players who had played
both opening nights (Matthews "Out" since March, Tanev, Forsling, Markstrom).
ESPN's feed itself is current - 113 players on 10/5, every note dated within
the last three weeks - so the fix is to read it again each night. The nightly
job does, after the games.

**The same table, the same columns, plus four.** `apply_injury_adjustments`
reads `playerId`, `injuryStatus`, `injuryDetails` (ESPN's details dict as a
string) and `injuryDate`, so those are written as the pipeline writes them.
`injuryType`, `returnDate`, `team` and `fetchedAt` are added beside them, so
the page needs no string parsing and can say how old the report is. A preseason
re-run replaces the table without them, and the page copes.

**Matched by name, settled by team and position** - ESPN gives both - through
`yahoo_rosters.match`, against the projected pool plus anyone with games
(`player_pool`). A name that matches nobody is kept with no id, as the
pipeline does.

**A failed read keeps yesterday's report** rather than wiping it: the page says
how old the report is, and stale is better than nothing. So does an empty one -
there is never a day in the season with nobody hurt. The swap is one
transaction, so the page never sees the table half written.

**On the page (`by_player`), a note older than the player's last game is
stale.** ESPN dates each note by its last update, so a game after it means he
played through whatever it says: no badge, and the card shows it muted. The
report is dated by when it was read (`fetchedAt`), not by its newest note.

    python injury_report.py          # refresh now

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import player_pool
from db import text, transaction
from yahoo_rosters import TEAM_FIXES, match

log = logging.getLogger(__name__)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
TABLE = "current_injuries"
# DO NOT "fix" this User-Agent into a full browser string - see the same note
# in preseason_db_build/scrape_injuries.py. ESPN's WAF answers 403 to anything
# claiming to be Chrome; this truncated one gets 200.
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
TIMEOUT_SECONDS = 30
# Game dates are the NHL's local ones, so the report is dated the same way
EASTERN = ZoneInfo("America/New_York")
# ESPN's own abbreviations where they differ from the NHL's, beyond Yahoo's
ESPN_TEAM_FIXES = {**TEAM_FIXES, "UTAH": "UTA"}


class InjuryFeedError(Exception):
    """ESPN could not be read, or sent something that is not its report."""


def fetch(session=None):
    """ESPN's report as JSON. Raises InjuryFeedError."""
    session = session or requests.Session()
    try:
        response = session.get(ESPN_URL, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise InjuryFeedError(f"Could not reach ESPN: {exc}") from exc
    if response.status_code != 200:
        raise InjuryFeedError(f"ESPN answered {response.status_code} - a 403 usually means "
                              "the User-Agent was changed (see the note on HEADERS).")
    try:
        return response.json()
    except ValueError as exc:
        raise InjuryFeedError("ESPN sent something other than JSON.") from exc


def parse(raw):
    """
    [{name, team, position, status, type, returnDate, date, details}] from
    ESPN's JSON: one entry per player, team as the NHL's tricode, `date` the
    day ESPN last updated that player's note (an ISO timestamp, UTC).
    """
    entries = []
    for team_block in (raw or {}).get('injuries') or []:
        for item in team_block.get('injuries') or []:
            athlete = item.get('athlete') or {}
            name = athlete.get('displayName')
            if not name:
                continue
            details = item.get('details') if isinstance(item.get('details'), dict) else {}
            team = ((athlete.get('team') or {}).get('abbreviation') or '').upper()
            entries.append({
                'name': name,
                'team': ESPN_TEAM_FIXES.get(team, team) or None,
                'position': ((athlete.get('position') or {}).get('abbreviation') or '').upper() or None,
                'status': item.get('status') or 'Unknown',
                'type': details.get('type') or None,
                'returnDate': details.get('returnDate') or None,
                'date': item.get('date') or None,
                'details': details,
            })
    return entries


def match_ids(entries, pool, aliases=None):
    """Sets each entry's `playerId` (None when nobody matches) and returns
    the names left unmatched."""
    players = [{'name': e['name'], 'yahooId': str(i)} for i, e in enumerate(entries)]
    details = {str(i): {'team': e['team'], 'positions': [e['position']] if e['position'] else []}
               for i, e in enumerate(entries)}
    result = match({'teams': [{'players': players}]}, pool, aliases,
                   lookup=lambda wanted: {k: details[k] for k in wanted if k in details})
    for entry, player in zip(entries, result['teams'][0]['players']):
        entry['playerId'] = player.get('playerId')
    return result['unmatched']


def store(entries, fetched_at, table=TABLE):
    """Replaces `current_injuries` (or the test's `table`) with `entries`, in
    one transaction."""
    rows = [{
        'id': e.get('playerId'), 'name': e['name'], 'status': e['status'],
        # As the pipeline writes it: apply_injury_adjustments reads it back
        'details': str(e['details'] or 'Unknown'), 'date': e['date'] or 'Unknown',
        'type': e['type'], 'back': e['returnDate'], 'team': e['team'], 'at': fetched_at,
    } for e in entries]
    with transaction() as conn:
        conn.execute(text(
            f'CREATE TABLE IF NOT EXISTS {table} ("playerId" BIGINT, "playerName" TEXT,'
            ' "injuryStatus" TEXT, "injuryDetails" TEXT, "injuryDate" TEXT)'))
        conn.execute(text(
            f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "injuryType" TEXT,'
            ' ADD COLUMN IF NOT EXISTS "returnDate" TEXT, ADD COLUMN IF NOT EXISTS "team" TEXT,'
            ' ADD COLUMN IF NOT EXISTS "fetchedAt" TIMESTAMPTZ'))
        conn.execute(text(f'DELETE FROM {table}'))
        if rows:
            conn.execute(text(
                f'INSERT INTO {table} ("playerId", "playerName", "injuryStatus", "injuryDetails",'
                ' "injuryDate", "injuryType", "returnDate", "team", "fetchedAt")'
                ' VALUES (:id, :name, :status, :details, :date, :type, :back, :team, :at)'), rows)
    return len(rows)


def _between(text_value, prefix, suffix):
    start = text_value.find(prefix)
    if start < 0:
        return None
    start += len(prefix)
    end = text_value.find(suffix, start)
    return text_value[start:end] if end > start else None


def by_player(rows, last_games=None):
    """
    ({playerId: {status, type, returnDate, date, stale}}, as of) from
    `current_injuries` rows, for the page.

    `date` is the day ESPN last updated the player's note, and `stale` says
    he has played a game since - `last_games` is {playerId: last game date}.
    A note older than his last game describes something he has played
    through, so the page shows it but not as a badge. `as of` is the day the
    report was read; a table the preseason pipeline wrote has no `fetchedAt`,
    so its newest note stands in, as it always did.
    """
    found, newest, fetched = {}, None, None
    for row in rows:
        when = str(row.get("injuryDate") or "")[:10] or None
        if when and when[:1].isdigit():
            newest = max(newest, when) if newest else when
        if isinstance(row.get("fetchedAt"), datetime):
            stamp = row["fetchedAt"].astimezone(EASTERN).date().isoformat()
            fetched = max(fetched, stamp) if fetched else stamp
        player_id = row.get("playerId")
        if player_id is None:
            continue
        details = str(row.get("injuryDetails") or "")
        key = str(int(player_id))
        last = (last_games or {}).get(key)
        found[key] = {
            "status": row.get("injuryStatus"),
            # The pipeline's table has no columns for these, only the dict's text
            "type": row.get("injuryType") or _between(details, "'type': '", "'"),
            "returnDate": row.get("returnDate") or _between(details, "'returnDate': '", "'"),
            "date": when,
            "stale": bool(last and when and str(last) > when),
        }
    return found, fetched or newest


def refresh(session=None, table=TABLE):
    """
    Reads ESPN and replaces `current_injuries`. Returns (players stored,
    names unmatched). Raises InjuryFeedError, having changed nothing, when
    ESPN cannot be read or reports nobody.
    """
    entries = parse(fetch(session))
    if not entries:
        raise InjuryFeedError("ESPN reported no injuries at all - keeping the last report.")
    unmatched = match_ids(entries, player_pool.pool(), player_pool.aliases())
    stored = store(entries, datetime.now(timezone.utc), table=table)
    return stored, unmatched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        count, missed = refresh()
    except InjuryFeedError as error:
        log.error("%s", error)
        sys.exit(1)
    log.info("Injuries: %d players, %d not matched%s", count, len(missed),
             f" ({', '.join(missed)})" if missed else "")
