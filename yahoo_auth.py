"""
Yahoo Fantasy OAuth2 client for Fantasy Streams.

Everything that talks to Yahoo's login and Fantasy APIs lives here so the
route layer stays thin: build the consent URL, swap the authorization code
for tokens, keep those tokens fresh, and make authenticated calls.

Tokens live in the `users` table keyed by Yahoo's `guid`; the browser only
ever holds that guid in the signed session cookie, never a token. Access
tokens last an hour, so `get_valid_access_token()` refreshes on demand
(with a safety margin) and writes the new pair back.

Refresh is hand-rolled rather than delegated to `yahoo_oauth`: that library
kept credentials in a file and was flagged not thread-safe in the old repo.
The authorization-code exchange is a single POST, so `requests_oauthlib`
earns no keep either - one mechanism, one dependency.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import base64
import json
import logging
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import current_app, jsonify, session, url_for

from db import fetch_one, text, transaction

log = logging.getLogger(__name__)

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

# Refresh this many seconds before Yahoo's stated expiry, so a token cannot
# lapse mid-flight on a slow request.
EXPIRY_SKEW = 300
HTTP_TIMEOUT = 20

# Session keys. Grouped here because the auth routes and the page routes
# both read them.
SESSION_GUID = "guid"
SESSION_LEAGUE = "league_id"
SESSION_LEAGUES = "leagues"
SESSION_STATE = "oauth_state"
SESSION_PENDING_LEAGUE = "pending_league_id"
SESSION_ERROR = "auth_error"

DEV_GUID = "DEV_ADMIN_GUID"


class YahooAuthError(Exception):
    """Anything that stops us getting or using a Yahoo token."""


# --- configuration -----------------------------------------------------------

def is_configured():
    """True when the app has Yahoo credentials and can start a real login."""
    return bool(
        current_app.config.get("YAHOO_CONSUMER_KEY")
        and current_app.config.get("YAHOO_CONSUMER_SECRET")
    )


def redirect_uri():
    """
    The callback Yahoo sends the user back to. Must match the Redirect URI
    registered in the Yahoo developer console *exactly*, which is why it is
    overridable by env var - behind a tunnel or a proxy the URL Flask builds
    is not the one the browser saw.
    """
    configured = current_app.config.get("YAHOO_REDIRECT_URI")
    if configured:
        return configured
    return url_for("auth.callback", _external=True)


# --- the authorization-code flow ---------------------------------------------

def build_authorization_url():
    """
    Build the Yahoo consent URL and the CSRF `state` that goes with it.
    Returns (url, state); the caller stores state in the session and checks
    it when Yahoo redirects back.

    `scope` is required, and registering the app for Fantasy read/write does
    not substitute for it. Registration caps what the app *may* ask for; the
    token only carries what this request actually asks for. Omitting it mints
    a token with no Fantasy access, which 403s on every Fantasy endpoint with
    "This application is not authorized to perform this action" - misleading,
    because the app is authorized and the token is not.
    """
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": current_app.config["YAHOO_CONSUMER_KEY"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "state": state,
        "language": "en-us",
    }

    scope = (current_app.config.get("YAHOO_SCOPE") or "").strip()
    if scope:
        params["scope"] = scope

    return f"{AUTH_URL}?{urlencode(params)}", state


def _post_token(payload):
    """POST to Yahoo's token endpoint with HTTP Basic client auth."""
    auth = (
        current_app.config["YAHOO_CONSUMER_KEY"],
        current_app.config["YAHOO_CONSUMER_SECRET"],
    )
    payload = dict(payload, redirect_uri=redirect_uri())

    try:
        response = requests.post(
            current_app.config.get("YAHOO_TOKEN_URL") or TOKEN_URL,
            data=payload,
            auth=auth,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise YahooAuthError(f"Could not reach Yahoo: {exc}") from exc

    if response.status_code != 200:
        # Yahoo puts the useful part in the body, not the status line.
        raise YahooAuthError(
            f"Yahoo rejected the token request ({response.status_code}): "
            f"{response.text[:300]}"
        )

    token = response.json()
    if not token.get("access_token"):
        raise YahooAuthError("Yahoo returned no access_token.")
    return token


def exchange_code_for_token(code):
    """Swap the one-time authorization code for an access/refresh token pair."""
    return _post_token({"grant_type": "authorization_code", "code": code})


def refresh_token(refresh):
    """Trade a refresh token for a fresh access token."""
    if not refresh:
        raise YahooAuthError("No refresh token stored - the user must log in again.")
    return _post_token({"grant_type": "refresh_token", "refresh_token": refresh})


def _forbidden_hint(body):
    """
    Yahoo's 403 body says "This application is not authorized", which points
    at the app registration and is usually the wrong place to look: far more
    often the app is fine and the *token* was minted without Fantasy scope.
    Lead with that, since it is both the likelier cause and the easy one to
    overlook.
    """
    scope = (current_app.config.get("YAHOO_SCOPE") or "").strip() or "(none)"
    return (
        f"Yahoo refused the Fantasy API with 403, using scope {scope!r}. This "
        "usually means the token lacks Fantasy scope rather than the app "
        "lacking permission: YAHOO_SCOPE must include 'fspt-w' (read/write) "
        "or 'fspt-r' (read), and a token minted before the scope changed keeps "
        "the old one - sign in again to mint a fresh token. Only if that is "
        "already right is it worth checking Fantasy Sports permission on the "
        f"app at developer.yahoo.com/apps. Yahoo said: {body[:200]}"
    )


def _guid_from_id_token(id_token):
    """
    Read the guid out of Yahoo's OpenID Connect `id_token`, whose `sub` claim
    is the same value as `xoauth_yahoo_guid`.

    The signature is not verified, and does not need to be: this JWT came
    straight back from Yahoo's token endpoint over TLS on a server-to-server
    call, so it is not attacker-supplied the way a client-presented token is.
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)          # restore base64 padding
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except Exception:                                 # noqa: BLE001 - best effort
        log.warning("Could not read a guid out of Yahoo's id_token.")
        return None


def resolve_guid(token):
    """
    The guid for a freshly issued token, cheapest source first: the token
    response, then its OIDC `id_token`, then an API call.

    Not every Yahoo app returns `xoauth_yahoo_guid`, and the API fallback
    needs Fantasy permission the app may not have - so the id_token is the
    one that works regardless.
    """
    guid = token.get("xoauth_yahoo_guid")
    if guid:
        return guid

    if token.get("id_token"):
        guid = _guid_from_id_token(token["id_token"])
        if guid:
            return guid

    return fetch_guid(token["access_token"])


def fetch_guid(access_token):
    """
    Last-resort guid lookup for a token we hold but have not stored yet.
    Prefer resolve_guid(), which only lands here when the token response
    carried neither the guid nor a readable id_token.
    """
    base = current_app.config.get("YAHOO_API_BASE") or API_BASE
    try:
        response = requests.get(
            # The documented collection form; a bare `users` resource is not
            # a valid Fantasy endpoint.
            f"{base}/users;use_login=1/games",
            params={"format": "json"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise YahooAuthError(f"Could not reach Yahoo: {exc}") from exc

    if response.status_code == 403:
        raise YahooAuthError(_forbidden_hint(response.text))
    if response.status_code != 200:
        # Carry Yahoo's own words - the status alone is not diagnosable.
        raise YahooAuthError(
            f"Could not identify the Yahoo user ({response.status_code}): "
            f"{response.text[:200]}"
        )

    guid = _find_first(response.json(), "guid")
    if not guid:
        raise YahooAuthError("Yahoo did not return a user guid.")
    return guid


def _find_first(node, key):
    """First value for `key` anywhere in a nested Yahoo response, or None."""
    if isinstance(node, dict):
        if key in node and isinstance(node[key], str):
            return node[key]
        for value in node.values():
            found = _find_first(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_first(value, key)
            if found:
                return found
    return None


# --- token storage -----------------------------------------------------------

def save_user_credentials(guid, token):
    """
    Upsert a token pair against a guid. Yahoo omits `refresh_token` from some
    refresh responses, so COALESCE keeps the one already on file rather than
    nulling it and forcing a re-login an hour later.
    """
    with transaction() as conn:
        conn.execute(
            text("""
                INSERT INTO users (guid, access_token, refresh_token, token_type,
                                   expires_in, token_time, last_seen_at)
                VALUES (:guid, :access_token, :refresh_token, :token_type,
                        :expires_in, :token_time, NOW())
                ON CONFLICT (guid) DO UPDATE SET
                    access_token  = EXCLUDED.access_token,
                    refresh_token = COALESCE(EXCLUDED.refresh_token, users.refresh_token),
                    token_type    = EXCLUDED.token_type,
                    expires_in    = EXCLUDED.expires_in,
                    token_time    = EXCLUDED.token_time,
                    last_seen_at  = NOW()
            """),
            {
                "guid": guid,
                "access_token": token.get("access_token"),
                "refresh_token": token.get("refresh_token"),
                "token_type": token.get("token_type"),
                "expires_in": int(token.get("expires_in") or 3600),
                "token_time": time.time(),
            },
        )


def record_terms_acceptance(guid, version):
    """Stamp the Terms version the user ticked at login."""
    with transaction() as conn:
        conn.execute(
            text("""
                UPDATE users
                   SET tos_accepted_version = :version,
                       tos_accepted_at = NOW()
                 WHERE guid = :guid
            """),
            {"guid": guid, "version": int(version)},
        )


def get_valid_access_token(guid, force_refresh=False):
    """
    Return an access token that is good right now, refreshing and persisting
    if it is inside EXPIRY_SKEW of expiry.

    Two concurrent requests for the same expired token will both refresh; the
    second write simply wins. Yahoo tolerates that, and a real lock belongs
    with the Phase 2 worker rather than here.
    """
    row = fetch_one(
        "SELECT access_token, refresh_token, expires_in, token_time"
        " FROM users WHERE guid = :guid",
        {"guid": guid},
    )
    if not row:
        raise YahooAuthError("Unknown user - log in again.")
    if not row["access_token"] and not row["refresh_token"]:
        raise YahooAuthError("No Yahoo tokens stored for this user.")

    expires_at = (row["token_time"] or 0) + (row["expires_in"] or 0)
    if force_refresh or time.time() >= expires_at - EXPIRY_SKEW:
        log.info("Refreshing Yahoo token for %s.", guid)
        token = refresh_token(row["refresh_token"])
        save_user_credentials(guid, token)
        return token["access_token"]

    return row["access_token"]


# --- authenticated API calls -------------------------------------------------

def api_get(guid, path, params=None):
    """
    GET a Fantasy API path (e.g. "/users;use_login=1/games") as `guid` and
    return parsed JSON. Retries once on a 401, since a token can be revoked
    server-side before its stated expiry.
    """
    params = dict(params or {}, format="json")
    base = current_app.config.get("YAHOO_API_BASE") or API_BASE

    for attempt in (0, 1):
        access_token = get_valid_access_token(guid, force_refresh=bool(attempt))
        try:
            response = requests.get(
                f"{base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise YahooAuthError(f"Could not reach Yahoo: {exc}") from exc

        if response.status_code == 401 and attempt == 0:
            continue
        if response.status_code == 403:
            raise YahooAuthError(_forbidden_hint(response.text))
        if response.status_code != 200:
            raise YahooAuthError(
                f"Yahoo API {path} failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
        return response.json()

    raise YahooAuthError(f"Yahoo API {path} kept returning 401.")


def _merge_entity(value):
    """
    Flatten one Yahoo entity. Yahoo hands back entities as a dict, or as a
    list whose elements are dicts (and sometimes lists of dicts) that each
    carry a slice of the attributes. Merging beats indexing by position.
    """
    merged = {}
    if isinstance(value, dict):
        merged.update(value)
    elif isinstance(value, list):
        for part in value:
            if isinstance(part, dict):
                merged.update(part)
            elif isinstance(part, list):
                merged.update(_merge_entity(part))
    return merged


def _iter_leagues(node):
    """
    Walk the response and yield every merged `league` entity. Yahoo nests
    leagues under numeric string keys inside `games` inside `users`; walking
    for the key we want is far less brittle than indexing that structure.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "league":
                merged = _merge_entity(value)
                if merged.get("league_key"):
                    yield merged
            else:
                yield from _iter_leagues(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_leagues(value)


def fetch_nhl_leagues(guid):
    """
    Every NHL league the logged-in user is in this season. `game_keys=nhl`
    resolves to the current season's game on Yahoo's side, so this needs no
    season constant.

    Deliberately narrow, because the result is cached in the session cookie.
    Measured, this leaves plenty of headroom: 30 leagues serialises to ~450
    bytes against the browser's ~4KB limit (Flask compresses the session), so
    the cap is not a live concern - but there is no reason to carry Yahoo's
    `url` field, which nothing uses and can be rebuilt from `league_key`.
    """
    data = api_get(guid, "/users;use_login=1/games;game_keys=nhl/leagues")

    leagues, seen = [], set()
    for league in _iter_leagues(data):
        league_key = league.get("league_key")
        if league_key in seen:
            continue
        seen.add(league_key)
        leagues.append({
            "league_key": league_key,
            "league_id": str(league.get("league_id") or league_key.split(".")[-1]),
            "name": league.get("name"),
            "season": league.get("season"),
            "num_teams": league.get("num_teams"),
            "scoring_type": league.get("scoring_type"),
        })
    return leagues


# --- session helpers ---------------------------------------------------------

def current_guid():
    """The signed-in user's Yahoo guid, or None."""
    return session.get(SESSION_GUID)


def is_dev_session():
    """True for a DEV_BACKDOOR_PASS login, which has no Yahoo tokens behind it."""
    return session.get(SESSION_GUID) == DEV_GUID


def login_required(view):
    """
    Gate a JSON API on a signed-in session. Matches the repo's API shape:
    {"status": "error", "message": ...} with a 401.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_guid():
            return jsonify({"status": "error", "message": "Not signed in."}), 401
        return view(*args, **kwargs)

    return wrapper
