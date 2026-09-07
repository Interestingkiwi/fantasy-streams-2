"""
End-to-end exercise of the Yahoo OAuth flow.

Runs the real Flask app against the real Postgres, but points the Yahoo
endpoints at a local stdlib HTTP server that speaks Yahoo's shapes, so the
whole login journey is exercised without Yahoo credentials. Covers CSRF
state, token storage, refresh-on-expiry, the 401 retry, league switching,
logout and the dev backdoor.

Writes rows for a test guid and deletes them again.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import json
import os
import sys
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Must be set before config.py is imported.
os.environ["YAHOO_CONSUMER_KEY"] = "test-consumer-key"
os.environ["YAHOO_CONSUMER_SECRET"] = "test-consumer-secret"
os.environ["FLASK_SECRET_KEY"] = "test-secret-for-oauth-flow"
os.environ["DEV_BACKDOOR_PASS"] = "letmein"

TEST_GUID = "TESTGUID000000000000000001"
STUB_PORT = 8731
os.environ["YAHOO_TOKEN_URL"] = f"http://127.0.0.1:{STUB_PORT}/oauth2/get_token"
os.environ["YAHOO_API_BASE"] = f"http://127.0.0.1:{STUB_PORT}/fantasy/v2"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CALLS = []          # every request the stub saw
FAIL_NEXT_API = []  # push a status code to make the next API call fail with it

LEAGUES_JSON = {
    "fantasy_content": {
        "users": {
            "0": {"user": [
                {"guid": TEST_GUID},
                {"games": {
                    "0": {"game": [
                        {"game_key": "465", "game_id": "465", "code": "nhl", "season": "2026"},
                        {"leagues": {
                            "0": {"league": [{
                                "league_key": "465.l.11111", "league_id": "11111",
                                "name": "Puck Luck", "season": "2026",
                                "num_teams": 12, "scoring_type": "head",
                            }]},
                            "1": {"league": [{
                                "league_key": "465.l.22222", "league_id": "22222",
                                "name": "Slapshot Squad", "season": "2026",
                                "num_teams": 10, "scoring_type": "head",
                            }]},
                            "count": 2,
                        }},
                    ]},
                    "count": 1,
                }},
            ]},
            "count": 1,
        }
    }
}


class StubYahoo(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        grant = form.get("grant_type", [""])[0]
        CALLS.append({
            "path": self.path,
            "grant_type": grant,
            "client_id": form.get("client_id", [""])[0],
            "client_secret_sent": bool(form.get("client_secret", [""])[0]),
            "redirect_uri": form.get("redirect_uri", [""])[0],
            "code": form.get("code", [""])[0],
            "refresh_token": form.get("refresh_token", [""])[0],
        })
        if grant == "authorization_code" and form.get("code", [""])[0] != "GOOD_CODE":
            return self._send(400, {"error": "invalid_grant"})

        self._send(200, {
            "access_token": f"access-{grant}-{len(CALLS)}",
            "refresh_token": f"refresh-{len(CALLS)}",
            "token_type": "bearer",
            "expires_in": 3600,
            "xoauth_yahoo_guid": TEST_GUID,
        })

    def do_GET(self):
        CALLS.append({
            "path": self.path,
            "bearer": self.headers.get("Authorization", "").replace("Bearer ", ""),
        })
        if FAIL_NEXT_API:
            return self._send(FAIL_NEXT_API.pop(0), {"error": "forced"})
        if "/leagues" in self.path:
            return self._send(200, LEAGUES_JSON)
        self._send(404, {"error": "not found"})


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


FAILURES = []

server = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), StubYahoo)
threading.Thread(target=server.serve_forever, daemon=True).start()

import app as app_module                     # noqa: E402
from db import execute, fetch_one            # noqa: E402

flask_app = app_module.app
flask_app.config["TESTING"] = True


def cleanup():
    execute("DELETE FROM league_updaters WHERE user_guid = :g", {"g": TEST_GUID})
    execute("DELETE FROM league_updaters WHERE league_id IN ('11111','22222','99999')")
    execute("DELETE FROM users WHERE guid = :g", {"g": TEST_GUID})


cleanup()
client = flask_app.test_client()

print("\n=== 1. /login validation ===")
r = client.post("/login", json={"league_id": "11111", "terms_accepted": False})
check("terms not accepted -> 400", r.status_code == 400, r.get_data(as_text=True))
check("error uses repo shape", r.get_json().get("status") == "error")

r = client.post("/login", json={"league_id": "", "terms_accepted": True})
check("blank league id -> 400", r.status_code == 400)

print("\n=== 2. /login builds the Yahoo consent URL ===")
r = client.post("/login", json={"league_id": "11111", "terms_accepted": True})
body = r.get_json()
check("200 with auth_url", r.status_code == 200 and "auth_url" in body, body)
parsed = urlparse(body["auth_url"])
qs = parse_qs(parsed.query)
check("points at Yahoo", parsed.netloc == "api.login.yahoo.com", parsed.netloc)
check("response_type=code", qs.get("response_type") == ["code"])
check("client_id sent", qs.get("client_id") == ["test-consumer-key"])
check("redirect_uri is our callback",
      qs.get("redirect_uri") == ["http://localhost/callback"], qs.get("redirect_uri"))
check("state present", len(qs.get("state", [""])[0]) > 20)
state = qs["state"][0]

print("\n=== 3. /callback rejects a bad state (CSRF) ===")
r = client.get(f"/callback?code=GOOD_CODE&state=forged-{state}")
check("redirects home", r.status_code == 302 and r.headers["Location"].endswith("/"))
with client.session_transaction() as sess:
    check("no session established", "guid" not in sess)
    check("error recorded", "expired or was invalid" in (sess.get("auth_error") or ""),
          sess.get("auth_error"))

print("\n=== 4. full successful callback ===")
r = client.post("/login", json={"league_id": "11111", "terms_accepted": True})
state = parse_qs(urlparse(r.get_json()["auth_url"]).query)["state"][0]
CALLS.clear()
r = client.get(f"/callback?code=GOOD_CODE&state={state}")
check("redirects home", r.status_code == 302, r.status_code)

token_call = next((c for c in CALLS if c.get("grant_type") == "authorization_code"), None)
check("exchanged the code", token_call is not None)
check("sent client_id in the body", token_call and token_call["client_id"] == "test-consumer-key")
check("sent client_secret in the body", token_call and token_call["client_secret_sent"])
check("sent redirect_uri on exchange",
      token_call and token_call["redirect_uri"] == "http://localhost/callback")
check("fetched leagues", any("/leagues" in c["path"] for c in CALLS))

row = fetch_one("SELECT * FROM users WHERE guid = :g", {"g": TEST_GUID})
check("tokens stored against the guid", row is not None)
check("access token saved", row and row["access_token"].startswith("access-authorization_code"))
check("refresh token saved", row and row["refresh_token"].startswith("refresh-"))
check("token_time stamped", row and row["token_time"] > time.time() - 60)
check("terms version recorded", row and row["tos_accepted_version"] == 1,
      row and row["tos_accepted_version"])

with client.session_transaction() as sess:
    check("session carries the guid", sess.get("guid") == TEST_GUID)
    check("session carries NO token", "access_token" not in json.dumps(dict(sess)))
    check("typed league became active", sess.get("league_id") == "11111", sess.get("league_id"))
    check("both leagues cached", len(sess.get("leagues", [])) == 2)
    check("league names parsed",
          [lg["name"] for lg in sess["leagues"]] == ["Puck Luck", "Slapshot Squad"],
          sess.get("leagues"))

claim = fetch_one("SELECT * FROM league_updaters WHERE league_id = '11111'")
check("league_updaters claimed", claim and claim["user_guid"] == TEST_GUID)

print("\n=== 5. home page renders signed-in ===")
html = client.get("/").get_data(as_text=True)
check("shows League Synced", "League Synced" in html)
check("shows active league name", "Puck Luck" in html)
check("offers the other league", "Slapshot Squad" in html)
check("has a sign-out button", 'id="logout-btn"' in html)
check("login card is gone", 'id="open-sync-modal-btn"' not in html)

print("\n=== 6. /api/session and /api/my_leagues ===")
body = client.get("/api/session").get_json()
check("authenticated", body["authenticated"] is True)
check("league_id reported", body["league_id"] == "11111")
check("yahoo_configured", body["yahoo_configured"] is True)

body = client.get("/api/my_leagues").get_json()
check("leagues served from cache", len(body["leagues"]) == 2)

print("\n=== 7. switching league ===")
r = client.post("/api/switch_league", json={"league_id": "99999"})
check("unknown league rejected", r.status_code == 403, r.status_code)
r = client.post("/api/switch_league", json={"league_id": "22222"})
check("known league accepted", r.status_code == 200, r.get_data(as_text=True))
with client.session_transaction() as sess:
    check("active league switched", sess.get("league_id") == "22222")

print("\n=== 8. token refresh when expired ===")
execute("UPDATE users SET token_time = :t WHERE guid = :g",
        {"t": time.time() - 4000, "g": TEST_GUID})
CALLS.clear()
r = client.get("/api/my_leagues?refresh=1")
refresh_call = next((c for c in CALLS if c.get("grant_type") == "refresh_token"), None)
check("expired token triggered a refresh", refresh_call is not None)
check("sent the stored refresh token",
      refresh_call and refresh_call["refresh_token"].startswith("refresh-"))
check("call succeeded", r.status_code == 200, r.get_data(as_text=True))
row = fetch_one("SELECT * FROM users WHERE guid = :g", {"g": TEST_GUID})
check("new access token persisted", row["access_token"].startswith("access-refresh_token"))

print("\n=== 9. 401 mid-session forces one retry ===")
execute("UPDATE users SET token_time = :t WHERE guid = :g",
        {"t": time.time(), "g": TEST_GUID})
CALLS.clear()
FAIL_NEXT_API.append(401)
r = client.get("/api/my_leagues?refresh=1")
check("recovered from a 401", r.status_code == 200, r.get_data(as_text=True))
check("refreshed and retried",
      any(c.get("grant_type") == "refresh_token" for c in CALLS)
      and len([c for c in CALLS if "/leagues" in c["path"]]) == 2,
      [c.get("path") or c.get("grant_type") for c in CALLS])

print("\n=== 10. logout ===")
r = client.post("/logout")
check("200", r.status_code == 200)
with client.session_transaction() as sess:
    check("session cleared", "guid" not in sess)
check("APIs now 401", client.get("/api/my_leagues").status_code == 401)
check("home shows the login card", 'id="open-sync-modal-btn"' in client.get("/").get_data(as_text=True))
check("tokens survive logout",
      fetch_one("SELECT guid FROM users WHERE guid = :g", {"g": TEST_GUID}) is not None)

print("\n=== 11. dev backdoor ===")
dev = flask_app.test_client()
r = dev.post("/login", json={"league_id": "99999-wrongpass", "terms_accepted": True})
check("wrong backdoor pass falls through to Yahoo",
      r.get_json().get("auth_url", "").startswith("https://api.login.yahoo.com"),
      r.get_data(as_text=True))
r = dev.post("/login", json={"league_id": "99999-letmein", "terms_accepted": True})
body = r.get_json()
check("dev login succeeds", body.get("dev_login") is True, body)
check("redirects home", body.get("redirect_url") == "/")
with dev.session_transaction() as sess:
    check("dev guid in session", sess.get("guid") == "DEV_ADMIN_GUID")
    check("league id stripped of the pass", sess.get("league_id") == "99999", sess.get("league_id"))
html = dev.get("/").get_data(as_text=True)
check("home marks it as DEV", "DEV" in html and "League Synced" in html)

cleanup()
server.shutdown()

print("\n" + "=" * 46)
if FAILURES:
    print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    sys.exit(1)
print("All checks passed.")
