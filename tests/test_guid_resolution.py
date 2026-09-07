"""
Guid resolution, including the shape that broke in production.

resolve_guid() tries xoauth_yahoo_guid, then the OIDC id_token, then an API
call. These cover each path plus the failure that cost a day: a token
response carrying no guid at all, against a Fantasy API returning 403.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import base64
import json
import os
import sys
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STUB_PORT = 8733
os.environ["YAHOO_CONSUMER_KEY"] = "k"
os.environ["YAHOO_CONSUMER_SECRET"] = "s"
os.environ["FLASK_SECRET_KEY"] = "guid-resolution-test"
os.environ["YAHOO_TOKEN_URL"] = f"http://127.0.0.1:{STUB_PORT}/oauth2/get_token"
os.environ["YAHOO_API_BASE"] = f"http://127.0.0.1:{STUB_PORT}/fantasy/v2"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GUID = "IDTOKENGUID0000000000000001"
MODE = {"token": "guid", "api": 200}
API_HITS = []


def make_id_token(sub):
    """A JWT shaped like Yahoo's: header.payload.signature, base64url, unpadded."""
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{seg({'alg':'RS256'})}.{seg({'sub':sub,'iss':'https://api.login.yahoo.com'})}.sig"


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload, raw=False):
        body = payload.encode() if raw else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        token = {"access_token": "at", "refresh_token": "rt",
                 "token_type": "bearer", "expires_in": 3600}
        if MODE["token"] == "guid":
            token["xoauth_yahoo_guid"] = GUID
        elif MODE["token"] == "id_token":
            token["id_token"] = make_id_token(GUID)
        elif MODE["token"] == "bad_id_token":
            token["id_token"] = "not.a.jwt"
        self._send(200, token)

    def do_GET(self):
        API_HITS.append(self.path)
        if MODE["api"] == 403:
            return self._send(403, "<yahoo:description>Please provide valid "
                                   "credentials OAuth oauth_problem</yahoo:description>", raw=True)
        if "use_login=1" in self.path:
            return self._send(200, {"fantasy_content": {"users": {"0": {"user": [
                {"guid": GUID}, {"games": {"count": 0}}]}, "count": 1}}})
        self._send(404, {"error": "nope"})


FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub).serve_forever,
                 daemon=True).start()

import app as app_module            # noqa: E402
import yahoo_auth                   # noqa: E402
from db import execute, fetch_one   # noqa: E402

flask_app = app_module.app
flask_app.config["TESTING"] = True


def cleanup():
    execute("DELETE FROM league_updaters WHERE user_guid = :g", {"g": GUID})
    execute("DELETE FROM users WHERE guid = :g", {"g": GUID})


LAST_CLIENT = {}


def run_login():
    """Drive /login -> /callback and return the resulting session error, if any."""
    cleanup()
    client = flask_app.test_client()
    LAST_CLIENT["c"] = client
    r = client.post("/login", json={"league_id": "1", "terms_accepted": True})
    state = parse_qs(urlparse(r.get_json()["auth_url"]).query)["state"][0]
    client.get(f"/callback?code=C&state={state}")
    with client.session_transaction() as sess:
        return sess.get("guid"), sess.get("auth_error")


print("\n=== A. token carries xoauth_yahoo_guid (no API call needed) ===")
MODE.update(token="guid", api=200)
API_HITS.clear()
guid, err = run_login()
check("signed in", guid == GUID, err)
check("no guid-lookup API call", not any(h.startswith("/fantasy/v2/users;use_login=1?") for h in API_HITS),
      API_HITS)

print("\n=== B. no xoauth_yahoo_guid, id_token present (the production case) ===")
MODE.update(token="id_token", api=403)
API_HITS.clear()
guid, err = run_login()
check("guid read from id_token despite a 403 API", guid == GUID, err)
check("tokens stored", fetch_one("SELECT guid FROM users WHERE guid=:g", {"g": GUID}) is not None)

# Signed in, but the leagues call failed - the page must not claim a sync.
home = LAST_CLIENT["c"].get("/").get_data(as_text=True)
check("home does not claim League Synced", "League Synced" not in home)
check("home says signed in instead", "Signed in to Yahoo" in home)

print("\n=== C. 403 on the leagues call reports the real cause ===")
check("leagues 403 points at app permission",
      err and "developer.yahoo.com" in err, err)
check("points at the full body in the log", err and "server log" in err, err)

print("\n=== D. no guid anywhere -> falls back to the API ===")
MODE.update(token="none", api=200)
API_HITS.clear()
guid, err = run_login()
check("guid recovered from the API", guid == GUID, err)
check("used the bare users resource, as the working app does",
      any(h.startswith("/fantasy/v2/users;use_login=1?") for h in API_HITS), API_HITS)

print("\n=== E. unreadable id_token -> still falls back, no crash ===")
MODE.update(token="bad_id_token", api=200)
guid, err = run_login()
check("recovered via the API", guid == GUID, err)

print("\n=== F. no guid and a 403 -> clean error, not a 500 ===")
MODE.update(token="none", api=403)
guid, err = run_login()
check("not signed in", guid is None)
check("explains it is a registration problem", err and "no API access" in err, err)

cleanup()
print("\n" + "=" * 46)
if FAILURES:
    print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    sys.exit(1)
print("All checks passed.")
