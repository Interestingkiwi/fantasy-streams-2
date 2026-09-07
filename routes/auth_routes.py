"""
Routes used for authorization

The Yahoo OAuth2 authorization-code flow, front to back:

    POST /login      -> returns the Yahoo consent URL for the browser to follow
    GET  /callback   -> Yahoo redirects here with ?code; exchanged for tokens
    POST /logout     -> clears the session (tokens stay on file for next time)
    GET  /api/session, /api/my_leagues, POST /api/switch_league

The token mechanics live in `yahoo_auth`; this module is the HTTP shape
around them. The browser never sees a Yahoo token - the signed session
cookie carries only the user's guid and their selected league.

Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 9/6/2026
"""

import logging

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

import yahoo_auth
from yahoo_auth import (
    SESSION_ERROR,
    SESSION_GUID,
    SESSION_LEAGUE,
    SESSION_LEAGUES,
    SESSION_PENDING_LEAGUE,
    SESSION_STATE,
    YahooAuthError,
    login_required,
)
from db import text, transaction

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def _error(message, status):
    """The repo's API error shape."""
    return jsonify({"status": "error", "message": message}), status


def _split_dev_login(league_id):
    """
    The dev backdoor is entered in the League ID box as "<league_id>-<pass>".
    Returns the bare league id when the password matches, else None.
    """
    backdoor = current_app.config.get("DEV_BACKDOOR_PASS")
    if not backdoor or "-" not in (league_id or ""):
        return None

    prefix, _, supplied = league_id.rpartition("-")
    return prefix if prefix and supplied == backdoor else None


def _claim_league(guid, league_id):
    """
    Record the active league and, if nobody owns it yet, make this user the
    one whose token keeps it synced. First claimant keeps the role - handing
    it over when they go stale is Phase 2's problem.
    """
    session[SESSION_LEAGUE] = str(league_id)
    with transaction() as conn:
        conn.execute(
            text("""
                INSERT INTO league_updaters (league_id, user_guid)
                VALUES (:league_id, :guid)
                ON CONFLICT (league_id) DO NOTHING
            """),
            {"league_id": str(league_id), "guid": guid},
        )


@auth_bp.route('/login', methods=['POST'])
def login():
    """
    Start a login. Returns the Yahoo consent URL for the browser to follow,
    or - when DEV_BACKDOOR_PASS is set and supplied - signs straight in
    without Yahoo so the rest of the app can be worked on offline.
    """
    data = request.get_json(silent=True) or {}
    league_id = (data.get('league_id') or '').strip()
    terms_accepted = bool(data.get('terms_accepted'))

    if not terms_accepted:
        return _error("You must accept the Terms of Service to continue.", 400)
    if not league_id:
        return _error("Enter your Yahoo League ID.", 400)

    dev_league = _split_dev_login(league_id)
    if dev_league:
        session.clear()
        session.permanent = True
        session[SESSION_GUID] = yahoo_auth.DEV_GUID
        _claim_league(yahoo_auth.DEV_GUID, dev_league)
        yahoo_auth.record_terms_acceptance(
            yahoo_auth.DEV_GUID, current_app.config["TOS_VERSION"]
        )
        log.warning("Dev backdoor login for league %s.", dev_league)
        return jsonify({
            "status": "success",
            "dev_login": True,
            "redirect_url": url_for('main.home'),
        })

    if not yahoo_auth.is_configured():
        return _error(
            "Yahoo login is not configured on this server "
            "(YAHOO_CONSUMER_KEY / YAHOO_CONSUMER_SECRET are unset).",
            503,
        )

    try:
        auth_url, state = yahoo_auth.build_authorization_url()
    except Exception as exc:                      # noqa: BLE001 - reported as 500 JSON
        log.exception("Could not build the Yahoo authorization URL.")
        return _error(str(exc), 500)

    # Survive the round trip to Yahoo: state proves the callback is ours, and
    # the league id is what the user typed before being sent away.
    session.permanent = True
    session[SESSION_STATE] = state
    session[SESSION_PENDING_LEAGUE] = league_id

    return jsonify({"status": "success", "auth_url": auth_url})


@auth_bp.route('/callback')
def callback():
    """
    Where Yahoo sends the user back. Verifies `state`, exchanges the code for
    tokens, stores them against the Yahoo guid, and picks up the league the
    user asked for. Always lands back on the home page - errors are shown
    there rather than as a bare 500.
    """
    if request.args.get('error'):
        description = request.args.get('error_description') or request.args['error']
        session[SESSION_ERROR] = f"Yahoo declined the login: {description}"
        return redirect(url_for('main.home'))

    expected_state = session.pop(SESSION_STATE, None)
    if not expected_state or request.args.get('state') != expected_state:
        # Either a forged callback or a session that expired mid-login.
        session[SESSION_ERROR] = "Login session expired or was invalid. Please try again."
        return redirect(url_for('main.home'))

    code = request.args.get('code')
    if not code:
        session[SESSION_ERROR] = "Yahoo did not return an authorization code."
        return redirect(url_for('main.home'))

    pending_league = session.pop(SESSION_PENDING_LEAGUE, None)

    try:
        token = yahoo_auth.exchange_code_for_token(code)
        guid = token.get('xoauth_yahoo_guid') or yahoo_auth.fetch_guid(token['access_token'])
        yahoo_auth.save_user_credentials(guid, token)
        yahoo_auth.record_terms_acceptance(guid, current_app.config["TOS_VERSION"])

        session[SESSION_GUID] = guid
        session.permanent = True

        leagues = yahoo_auth.fetch_nhl_leagues(guid)
        session[SESSION_LEAGUES] = leagues
    except YahooAuthError as exc:
        log.warning("Yahoo login failed: %s", exc)
        session[SESSION_ERROR] = str(exc)
        return redirect(url_for('main.home'))
    except Exception as exc:                      # noqa: BLE001 - surfaced on the page
        log.exception("Unexpected failure completing the Yahoo login.")
        session[SESSION_ERROR] = f"Login failed: {exc}"
        return redirect(url_for('main.home'))

    # Prefer the league the user typed; otherwise, if they are in exactly one
    # NHL league, there is nothing to choose between.
    known = {league["league_id"] for league in leagues}
    if pending_league and pending_league in known:
        _claim_league(guid, pending_league)
    elif len(leagues) == 1:
        _claim_league(guid, leagues[0]["league_id"])
    elif pending_league:
        session[SESSION_ERROR] = (
            f"Signed in, but league {pending_league} is not one of your NHL "
            "leagues this season. Pick one below."
        )

    return redirect(url_for('main.home'))


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Clear the session. Stored tokens stay put so the next login is quiet."""
    session.clear()
    return jsonify({"status": "success", "redirect_url": url_for('main.home')})


@auth_bp.route('/api/session')
def session_state():
    """What the front end needs to render signed-in vs signed-out."""
    return jsonify({
        "status": "success",
        "authenticated": bool(yahoo_auth.current_guid()),
        "dev_login": yahoo_auth.is_dev_session(),
        "league_id": session.get(SESSION_LEAGUE),
        "leagues": session.get(SESSION_LEAGUES, []),
        "yahoo_configured": yahoo_auth.is_configured(),
    })


@auth_bp.route('/api/my_leagues')
@login_required
def my_leagues():
    """
    The user's NHL leagues. Served from the session copy taken at login;
    ?refresh=1 goes back to Yahoo (a second or so) and re-caches.
    """
    cached = session.get(SESSION_LEAGUES)
    if cached is not None and request.args.get('refresh') != '1':
        return jsonify({"status": "success", "leagues": cached})

    if yahoo_auth.is_dev_session():
        return jsonify({"status": "success", "leagues": []})

    try:
        leagues = yahoo_auth.fetch_nhl_leagues(yahoo_auth.current_guid())
    except YahooAuthError as exc:
        return _error(str(exc), 502)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Failed to fetch leagues from Yahoo.")
        return _error(str(exc), 500)

    session[SESSION_LEAGUES] = leagues
    return jsonify({"status": "success", "leagues": leagues})


@auth_bp.route('/api/switch_league', methods=['POST'])
@login_required
def switch_league():
    """Make one of the user's leagues the active one for this session."""
    data = request.get_json(silent=True) or {}
    league_id = str(data.get('league_id') or '').strip()
    if not league_id:
        return _error("No league_id given.", 400)

    known = {league["league_id"] for league in session.get(SESSION_LEAGUES, [])}
    if known and league_id not in known:
        return _error(f"League {league_id} is not one of your NHL leagues.", 403)

    try:
        _claim_league(yahoo_auth.current_guid(), league_id)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Failed to switch league.")
        return _error(str(exc), 500)

    return jsonify({"status": "success", "league_id": league_id})
