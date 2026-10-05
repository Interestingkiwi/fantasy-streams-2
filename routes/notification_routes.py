"""
Routes for move reminders: the service worker, the web app manifest, and the
API a device turns reminders on and off with (see move_reminders.py).

`/sw.js` and `/manifest.webmanifest` sit at the site root. A service worker
controls only the paths under its own, and an iPhone needs the manifest to
add League Home to its Home Screen - the only way iOS lets a site push.

Every API call is JSON only (as the account routes are, which is half the
CSRF story) and needs a signed-in account, since reminders are of the
account's planned moves. A device is known by its push endpoint, sent in the
body, never the URL: it is a capability, and anyone holding it can push to
that device.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import base64
import binascii
import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, send_from_directory

import move_reminders
from db import execute, fetch_one, text, transaction
from routes.account_routes import current_account

log = logging.getLogger(__name__)

notification_bp = Blueprint('notifications', __name__)

MAX_ENDPOINT = 2048
MAX_KEY = 256
UPCOMING_SHOWN = 5


def _error(message, status):
    return jsonify({"status": "error", "message": message}), status


@notification_bp.route('/sw.js')
def service_worker():
    response = send_from_directory(current_app.static_folder, 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@notification_bp.route('/manifest.webmanifest')
def manifest():
    """What a phone needs to add League Home to its Home Screen as an app."""
    icons = [{"src": f"/static/icons/icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png",
              "purpose": "any maskable"} for size in (192, 512)]
    response = jsonify({
        "name": "Fantasy Streams", "short_name": "Streams", "id": "/standalone/",
        "start_url": "/standalone/", "scope": "/", "display": "standalone",
        "background_color": "#111827", "theme_color": "#1f2937", "icons": icons,
    })
    response.headers['Content-Type'] = 'application/manifest+json'
    return response


def _body():
    if not request.is_json:
        raise ValueError("Send JSON.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


def _endpoint(body):
    endpoint = str(body.get('endpoint') or '')
    if not endpoint.startswith('https://') or len(endpoint) > MAX_ENDPOINT:
        raise ValueError("That is not a push subscription.")
    return endpoint


def _key(value, size):
    """A subscription key as the browser sent it, if it decodes (base64url)
    to the size Web Push expects - a 65-byte P-256 point, a 16-byte secret."""
    text_value = str(value or '')
    if not text_value or len(text_value) > MAX_KEY:
        return None
    try:
        raw = base64.urlsafe_b64decode(text_value + '=' * (-len(text_value) % 4))
    except (binascii.Error, ValueError):
        return None
    return text_value if len(raw) == size else None


def _own(endpoint, account_id):
    return fetch_one('SELECT * FROM push_subscriptions WHERE endpoint = :e AND account_id = :a',
                     {'e': endpoint, 'a': account_id})


def _upcoming(subscription, account_id):
    leagues = move_reminders.account_leagues([account_id]).get(account_id, [])
    coming = move_reminders.reminders(subscription, leagues, datetime.now(timezone.utc), upcoming=True)
    names = move_reminders.player_names([r['add'] for r in coming[:UPCOMING_SHOWN]]
                                        + [r['drop'] for r in coming[:UPCOMING_SHOWN] if r['drop']])
    return [{'at': r['fire'].isoformat(), 'date': r['date'],
             'add': (names.get(r['add']) or ('Unknown player',))[0],
             'drop': (names.get(r['drop']) or ('Unknown player',))[0] if r['drop'] else None}
            for r in coming[:UPCOMING_SHOWN]]


def _settings(row):
    return {'remindDay': row['remind_day'], 'remindAt': row['remind_at'], 'timeZone': row['time_zone']}


@notification_bp.route('/notifications/api/status', methods=['POST'])
def status():
    """
    Body: {endpoint?}. Whether reminders can be had here (the server's keys,
    the session's account), the public key to subscribe with, and - for a
    device already on - its settings and the next reminders it will get.
    """
    try:
        body = _body()
        account = current_account()
        result = {"status": "success", "available": move_reminders.enabled(),
                  "publicKey": move_reminders.public_key(),
                  "signedIn": bool(account), "subscribed": False}
        if account and body.get('endpoint'):
            row = _own(_endpoint(body), account['id'])
            if row:
                result.update(subscribed=True, settings=_settings(row),
                              upcoming=_upcoming(row, account['id']))
        if account:
            devices = fetch_one('SELECT count(*) AS n FROM push_subscriptions WHERE account_id = :a',
                                {'a': account['id']})
            result['devices'] = devices['n'] if devices else 0
        return jsonify(result)
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Reminder status failed.")
        return _error(str(exc), 500)


@notification_bp.route('/notifications/api/subscribe', methods=['POST'])
def subscribe():
    """
    Body: {subscription: {endpoint, keys: {p256dh, auth}}, remindDay,
    remindAt, timeZone}. Turns reminders on for this device, or refreshes it
    (a browser can rotate a subscription). An endpoint signed up under
    another account moves to this one: it is one device, and whoever is
    signed in on it now is who it reminds.
    """
    try:
        account = current_account()
        if not account:
            return _error("Sign in first - reminders are of your account's planned moves.", 401)
        if not move_reminders.enabled():
            return _error("Reminders are not set up on this server yet.", 503)
        body = _body()
        subscription = body.get('subscription') or {}
        endpoint = _endpoint(subscription)
        keys = subscription.get('keys') or {}
        p256dh, auth = _key(keys.get('p256dh'), 65), _key(keys.get('auth'), 16)
        if not (p256dh and auth):
            raise ValueError("That push subscription's keys are not ones a browser makes.")
        day, at, zone = move_reminders.clean_settings(body)
        with transaction() as conn:
            row = conn.execute(text(
                'INSERT INTO push_subscriptions (account_id, endpoint, p256dh, auth, time_zone, remind_day, remind_at)'
                ' VALUES (:a, :e, :p, :k, :z, :d, :t)'
                ' ON CONFLICT (endpoint) DO UPDATE SET account_id = EXCLUDED.account_id, p256dh = EXCLUDED.p256dh,'
                ' auth = EXCLUDED.auth, time_zone = EXCLUDED.time_zone, remind_day = EXCLUDED.remind_day,'
                ' remind_at = EXCLUDED.remind_at, failures = 0 RETURNING *'),
                {'a': account['id'], 'e': endpoint, 'p': p256dh, 'k': auth, 'z': zone, 'd': day, 't': at}
            ).mappings().first()
        return jsonify({"status": "success", "settings": _settings(row),
                        "upcoming": _upcoming(dict(row), account['id'])})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Turning reminders on failed.")
        return _error(str(exc), 500)


@notification_bp.route('/notifications/api/settings', methods=['POST'])
def settings():
    """Body: {endpoint, remindDay, remindAt, timeZone}. When this device is reminded."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        body = _body()
        day, at, zone = move_reminders.clean_settings(body)
        with transaction() as conn:
            row = conn.execute(text(
                'UPDATE push_subscriptions SET remind_day = :d, remind_at = :t, time_zone = :z'
                ' WHERE endpoint = :e AND account_id = :a RETURNING *'),
                {'d': day, 't': at, 'z': zone, 'e': _endpoint(body), 'a': account['id']}).mappings().first()
        if not row:
            return _error("Reminders are not on for this device.", 404)
        return jsonify({"status": "success", "settings": _settings(row),
                        "upcoming": _upcoming(dict(row), account['id'])})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Saving reminder settings failed.")
        return _error(str(exc), 500)


@notification_bp.route('/notifications/api/unsubscribe', methods=['POST'])
def unsubscribe():
    """
    Body: {endpoint}. Turns reminders off for this device. Needs no account:
    holding the endpoint is proof enough, and signing out turns them off first.
    """
    try:
        execute('DELETE FROM push_subscriptions WHERE endpoint = :e', {'e': _endpoint(_body())})
        return jsonify({"status": "success"})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Turning reminders off failed.")
        return _error(str(exc), 500)


@notification_bp.route('/notifications/api/test', methods=['POST'])
def test_notification():
    """Body: {endpoint}. One notification now, to this device."""
    try:
        account = current_account()
        if not account:
            return _error("Sign in first.", 401)
        row = _own(_endpoint(_body()), account['id'])
        if not row:
            return _error("Reminders are not on for this device.", 404)
        outcome = move_reminders.send_test(row)
        if outcome == 'gone':
            return _error("The browser no longer has this subscription - turn reminders on again.", 410)
        if outcome != 'ok':
            return _error("The push service did not take it. Try again in a minute.", 502)
        return jsonify({"status": "success"})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Test notification failed.")
        return _error(str(exc), 500)
