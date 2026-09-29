"""
Simple accounts, so a hand-scraped league follows its user between devices.

Temporary. While Yahoo's Fantasy API is gated every league is scraped by hand,
and until now it lived only in the browser's localStorage - one device, and
gone with a cleared cache. An account here is a username and a password and
nothing more: no email, so no password reset. Signed in, the pages keep
working from localStorage exactly as before, and `static/account-sync.js`
mirrors the keys that describe a league into `account_leagues`, and back onto
any other device the account signs in on. Signed out, nothing changes.

**Each account holds its own copy of each league, never a shared one.** The
obvious design - one row per Yahoo league, joined by anyone who scrapes it -
cannot be made safe for private leagues. A private league reaches the server
as page HTML the user's browser posts (the bookmarklet), and the server cannot
tell a real Yahoo page from a hand-made one carrying the right league ID, so
"scrape it to join it" proves nothing. Per account, nobody sees anything they
did not enter or scrape themselves, and nothing needs verifying. The cost is a
league stored once per member who uses this, tens of KB each.

**Saves replace the whole league, so each one says what it was built on.**
The page sends the `updatedAt` it last loaded or saved as `base`, and a save
built on anything older is refused (409) rather than applied. Without that, a
tab left open on one device would silently put back everything changed on
another the next time it saved. The page reloads onto the newer copy instead,
and checks for one whenever it comes back into view.

**Staying signed in** is Flask's signed session cookie, made permanent:
`PERMANENT_SESSION_LIFETIME` (30 days), renewed on every visit. The cookie
carries the account id only, and every request re-reads the account, so one
deleted with `manage_accounts.py` is signed out wherever it was signed in.

**Remove it all** once Yahoo sync lands: this module, `static/account-sync.js`,
`templates/partials/account.html`, `manage_accounts.py`, and the tables in
`schema.ACCOUNT_DDL`. Nothing here needs migrating - a real sync replaces it.

Author - Jason Druckenmiller
Created - 9/29/2026
Updated - 9/29/2026
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, fetch_all, fetch_one, text, transaction

log = logging.getLogger(__name__)

account_bp = Blueprint('account', __name__, url_prefix='/account')

SESSION_ACCOUNT = 'account_id'
SESSION_LEAGUE = 'account_league'

# The localStorage keys that describe one league, which an account mirrors.
# Must match LEAGUE_KEYS in static/account-sync.js - a test fails if they
# drift. Everything else a page keeps - the open tab, the selected week, draft
# prep's view settings, saved lists and tags - belongs to the device and stays
# local. The two legacy roster keys are here so a new league starts without
# them: the page seeds an empty league from them when they are present.
LEAGUE_KEYS = frozenset({
    'fs_leagueTeams', 'fs_yahooLeagueId', 'fs_numTeams',
    'fs_selectedStats', 'fs_statWeights', 'fs_leagueMode', 'fs_pimPolarity',
    'fs_rosterMode', 'fs_rosterSlots', 'fs_playoffWeeks', 'fs_lineupSlots',
    'fs_fantasyWeeks', 'fs_standaloneMoves', 'fs_standaloneBanked',
    'fs_standaloneGoalieStats', 'fs_lineupEdits', 'fs_leagueTransactions',
    'fs_standaloneRoster', 'fs_standaloneOpponent',
})

USERNAME = re.compile(r'^[A-Za-z0-9_.-]{3,30}$')
PASSWORD_MIN, PASSWORD_MAX = 8, 200
MAX_LEAGUES = 20
# A 12-team league with a season of moves, edits and scores is well under
# 100 KB, and a season of scraped transactions adds ~120 KB (1,100 of them)
# to ~250 KB for the busiest imported league. Room to spare, not a target.
MAX_STATE_BYTES = 1024 * 1024
LOCK_AFTER_FAILURES = 10
LOCK_MINUTES = 15
SIGNUPS_PER_HOUR = 5

WRONG_LOGIN = "Wrong username or password."

# Checked against when the username does not exist, so a wrong username costs
# the same hashing time as a wrong password.
_DUMMY_HASH = generate_password_hash('fantasy-streams-no-such-account')

# {ip: [monotonic times]} of recent sign-ups, per process. Crude, and enough:
# it only has to stop one address filling the table.
_signups = {}
_signups_lock = threading.Lock()


def _error(message, status):
    return jsonify({"status": "error", "message": message}), status


def _client_ip():
    # Render's proxy appends the address it saw. Anything before that came
    # from the client and could say anything.
    forwarded = request.headers.get('X-Forwarded-For', '')
    return forwarded.split(',')[-1].strip() or request.remote_addr or ''


def _recent_signups(ip):
    now = time.monotonic()
    with _signups_lock:
        recent = [t for t in _signups.get(ip, []) if now - t < 3600]
        _signups[ip] = recent
        return recent


def _record_signup(ip):
    with _signups_lock:
        _signups.setdefault(ip, []).append(time.monotonic())


def _json_body():
    """The posted JSON object. Refuses anything else: a cross-site form cannot
    send JSON without a preflight, which is half of the CSRF story here -
    SameSite=Lax on the session cookie is the other half."""
    if not request.is_json:
        raise ValueError("Send JSON.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


def _sign_in(account_id):
    session[SESSION_ACCOUNT] = int(account_id)
    session.pop(SESSION_LEAGUE, None)
    session.permanent = True
    g.pop('fs_account', None)


def _sign_out():
    session.pop(SESSION_ACCOUNT, None)
    session.pop(SESSION_LEAGUE, None)
    g.pop('fs_account', None)


def current_account():
    """
    The signed-in account as {id, username}, or None. A session whose account
    has since been deleted is signed out here. Read once per request.
    """
    if 'fs_account' in g:
        return g.fs_account
    account = None
    account_id = session.get(SESSION_ACCOUNT)
    if account_id:
        account = fetch_one('SELECT id, username FROM local_accounts WHERE id = :id',
                            {'id': account_id})
        if not account:
            _sign_out()
    g.fs_account = account
    return account


def clean_state(raw):
    """
    The posted league state as {localStorage key: raw string}. Keys outside
    LEAGUE_KEYS and non-string values are dropped rather than refused, so an
    older page and a newer server still agree. Raises ValueError when too big.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("League state must be an object.")
    state = {key: value for key, value in raw.items()
             if key in LEAGUE_KEYS and isinstance(value, str)}
    if len(json.dumps(state)) > MAX_STATE_BYTES:
        raise ValueError("That league is too large to save.")
    return state


def _clean_name(raw):
    return str(raw or '').strip()[:80]


def _league_list(account_id):
    return fetch_all(
        'SELECT id, name, updated_at FROM account_leagues'
        ' WHERE account_id = :a ORDER BY updated_at DESC, id DESC', {'a': account_id})


def _create_league(account_id, state, name=''):
    """A new league for the account, returned as a row. Raises ValueError past
    MAX_LEAGUES."""
    with transaction() as conn:
        count = conn.execute(text('SELECT count(*) FROM account_leagues WHERE account_id = :a'),
                             {'a': account_id}).scalar()
        if count >= MAX_LEAGUES:
            raise ValueError(f"An account holds at most {MAX_LEAGUES} leagues. Delete one first.")
        return dict(conn.execute(text(
            'INSERT INTO account_leagues (account_id, name, state)'
            ' VALUES (:a, :name, CAST(:state AS jsonb))'
            ' RETURNING id, name, state, updated_at'),
            {'a': account_id, 'name': name, 'state': json.dumps(state)}).mappings().first())


def active_league(account_id):
    """
    The league this session is working on: the one it last opened, else the
    most recently saved, else a new empty one - so a signed-in page always has
    a league to save into.
    """
    row = None
    wanted = session.get(SESSION_LEAGUE)
    if wanted:
        row = fetch_one('SELECT id, name, state, updated_at FROM account_leagues'
                        ' WHERE id = :id AND account_id = :a', {'id': wanted, 'a': account_id})
    if not row:
        row = fetch_one('SELECT id, name, state, updated_at FROM account_leagues'
                        ' WHERE account_id = :a ORDER BY updated_at DESC, id DESC LIMIT 1',
                        {'a': account_id})
    if not row:
        row = _create_league(account_id, {})
    session[SESSION_LEAGUE] = row['id']
    return row


def _iso(value):
    return value.isoformat() if value else None


def boot():
    """
    What `account-sync.js` needs before any page script reads localStorage:
    who is signed in, their leagues, and the open league's state. Rendered into
    every page by `partials/account.html`. Never lets a database problem cost
    the page - it degrades to signed out.
    """
    try:
        account = current_account()
        if not account:
            return {'signedIn': False}
        league = active_league(account['id'])
        return {
            'signedIn': True,
            'accountId': account['id'],
            'username': account['username'],
            'league': league['id'],
            'state': league['state'] or {},
            'updatedAt': _iso(league['updated_at']),
            'leagues': [{'id': row['id'], 'name': row['name'], 'updatedAt': _iso(row['updated_at'])}
                        for row in _league_list(account['id'])],
            'maxLeagues': MAX_LEAGUES,
        }
    except Exception:                             # noqa: BLE001
        log.exception("Account boot failed - rendering signed out.")
        return {'signedIn': False, 'unavailable': True}


@account_bp.app_context_processor
def _inject_boot():
    # A callable, so only a template that asks pays for the queries
    return {'account_boot': boot}


def _credentials(body):
    return str(body.get('username') or '').strip(), str(body.get('password') or '')


@account_bp.route('/api/signup', methods=['POST'])
def signup():
    """Body: {username, password}. Creates the account and signs it in."""
    try:
        username, password = _credentials(_json_body())
        if not USERNAME.match(username):
            return _error("Usernames are 3-30 letters, numbers, dots, dashes or underscores.", 400)
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            return _error(f"Passwords need at least {PASSWORD_MIN} characters.", 400)
        ip = _client_ip()
        if len(_recent_signups(ip)) >= SIGNUPS_PER_HOUR:
            return _error("Too many new accounts from here. Try again in an hour.", 429)

        with transaction() as conn:
            row = conn.execute(text(
                'INSERT INTO local_accounts (username, username_key, password_hash, last_seen_at)'
                ' VALUES (:u, :k, :h, now()) ON CONFLICT (username_key) DO NOTHING RETURNING id'),
                {'u': username, 'k': username.lower(),
                 'h': generate_password_hash(password)}).mappings().first()
        if not row:
            return _error("That username is taken.", 409)

        _record_signup(ip)
        _sign_in(row['id'])
        return jsonify({"status": "success", "accountId": row['id'], "username": username,
                        "leagues": []})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Sign-up failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/signin', methods=['POST'])
def signin():
    """
    Body: {username, password}. Ten wrong passwords lock the account for
    fifteen minutes - a lock, not a delay, so it holds across processes.
    """
    try:
        username, password = _credentials(_json_body())
        row = fetch_one('SELECT id, username, password_hash, failed_logins, locked_until'
                        ' FROM local_accounts WHERE username_key = :k', {'k': username.lower()})
        now = datetime.now(timezone.utc)
        if row and row['locked_until'] and row['locked_until'] > now:
            minutes = max(1, round((row['locked_until'] - now).total_seconds() / 60))
            return _error(f"Too many wrong passwords. Try again in {minutes} minute"
                          f"{'' if minutes == 1 else 's'}.", 429)
        if not row:
            check_password_hash(_DUMMY_HASH, password)
            return _error(WRONG_LOGIN, 401)
        if not check_password_hash(row['password_hash'], password):
            failures = row['failed_logins'] + 1
            locked = failures >= LOCK_AFTER_FAILURES
            execute('UPDATE local_accounts SET failed_logins = :f, locked_until = :until'
                    ' WHERE id = :id',
                    {'f': 0 if locked else failures, 'id': row['id'],
                     'until': now + timedelta(minutes=LOCK_MINUTES) if locked else None})
            return _error(WRONG_LOGIN, 401)

        execute('UPDATE local_accounts SET failed_logins = 0, locked_until = NULL,'
                ' last_seen_at = now() WHERE id = :id', {'id': row['id']})
        _sign_in(row['id'])
        return jsonify({"status": "success", "accountId": row['id'], "username": row['username'],
                        "leagues": [{'id': r['id'], 'name': r['name']}
                                    for r in _league_list(row['id'])]})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Sign-in failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/signout', methods=['POST'])
def signout():
    _sign_out()
    return jsonify({"status": "success"})


@account_bp.route('/api/leagues', methods=['POST'])
def create_league():
    """Body: {state?, name?}. A new league for the account, opened at once."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        body = _json_body()
        row = _create_league(account['id'], clean_state(body.get('state')),
                             _clean_name(body.get('name')))
        session[SESSION_LEAGUE] = row['id']
        return jsonify({"status": "success", "league": row['id']})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Creating a league failed.")
        return _error(str(exc), 500)


def _own_league(league_id, account_id):
    return fetch_one('SELECT id, updated_at FROM account_leagues WHERE id = :id AND account_id = :a',
                     {'id': league_id, 'a': account_id})


@account_bp.route('/api/leagues/<int:league_id>', methods=['PUT'])
def save_league(league_id):
    """
    Body: {state, name, base}. Replaces the league's saved state, unless it
    has been saved since `base` (the updatedAt the page's copy came from) - a
    409 then, so a stale copy never overwrites a newer one. Without `base` the
    save is unconditional.
    """
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        body = _json_body()
        state = clean_state(body.get('state'))
        base = body.get('base') or None
        if base is not None:
            try:
                base = datetime.fromisoformat(str(base))
            except ValueError as exc:
                raise ValueError("base must be an ISO timestamp.") from exc
        with transaction() as conn:
            row = conn.execute(text(
                'UPDATE account_leagues SET state = CAST(:state AS jsonb), name = :name,'
                ' updated_at = now() WHERE id = :id AND account_id = :a'
                ' AND (CAST(:base AS timestamptz) IS NULL OR updated_at <= CAST(:base AS timestamptz))'
                ' RETURNING updated_at'),
                {'state': json.dumps(state), 'name': _clean_name(body.get('name')),
                 'id': league_id, 'a': account['id'], 'base': base}).mappings().first()
        if not row:
            current = _own_league(league_id, account['id'])
            if not current:
                return _error("That league is not in your account.", 404)
            return jsonify({"status": "error", "conflict": True,
                            "updatedAt": _iso(current['updated_at']),
                            "message": "This league was changed on another device."}), 409
        return jsonify({"status": "success", "updatedAt": _iso(row['updated_at'])})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Saving a league failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/leagues/<int:league_id>/version')
def league_version(league_id):
    """When the league was last saved - how a tab coming back into view tells
    whether another device has saved since it loaded."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        current = _own_league(league_id, account['id'])
        if not current:
            return _error("That league is not in your account.", 404)
        return jsonify({"status": "success", "updatedAt": _iso(current['updated_at'])})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Reading a league's version failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/leagues/<int:league_id>/open', methods=['POST'])
def open_league(league_id):
    """Makes the league the one this session's pages work on."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        if not _own_league(league_id, account['id']):
            return _error("That league is not in your account.", 404)
        session[SESSION_LEAGUE] = league_id
        return jsonify({"status": "success", "league": league_id})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Opening a league failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/leagues/<int:league_id>', methods=['DELETE'])
def delete_league(league_id):
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        removed = execute('DELETE FROM account_leagues WHERE id = :id AND account_id = :a',
                          {'id': league_id, 'a': account['id']})
        if not removed:
            return _error("That league is not in your account.", 404)
        if session.get(SESSION_LEAGUE) == league_id:
            session.pop(SESSION_LEAGUE, None)
        return jsonify({"status": "success"})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Deleting a league failed.")
        return _error(str(exc), 500)


@account_bp.route('/api/delete', methods=['POST'])
def delete_account():
    """Body: {password}. Deletes the account and every league in it."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        _username, password = _credentials(_json_body())
        row = fetch_one('SELECT password_hash FROM local_accounts WHERE id = :id',
                        {'id': account['id']})
        if not row or not check_password_hash(row['password_hash'], password):
            return _error("That password is not right.", 401)
        execute('DELETE FROM local_accounts WHERE id = :id', {'id': account['id']})
        _sign_out()
        return jsonify({"status": "success"})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Deleting an account failed.")
        return _error(str(exc), 500)
