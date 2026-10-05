"""
Tests for move reminders (move_reminders.py, routes/notification_routes.py).

The timing needs no database; sending runs against the real Postgres under
`zztestpush*` accounts with the push service stubbed - except one check, which
sends a real encrypted notification to a push service stood up on a local port
and decrypts it with the device's own key, so the VAPID signing and the
payload encryption are proven, not assumed.

What is pinned: a reminder is due from its time until the end of the move's
day in the device's own zone, never before and never after; the night-before
setting; a move already made (the pickup on your team) is never reminded; a
move listed twice is reminded once, and one moved to another night again; the
notification names both players and links Yahoo's search for the pickup;
every reminder goes once, through two passes, a failure is tried again, a
device that has gone is dropped, and a held lock means another worker is
sending; and the routes - sign-in required, one account never touching
another's device, settings clamped, the service worker served for the whole
site.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import base64
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("FLASK_SECRET_KEY", "move-reminders-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import move_reminders as mr                                  # noqa: E402
from config import Config                                    # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


ET = ZoneInfo("America/New_York")


def et(day, hour, minute=0):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}").replace(tzinfo=ET).astimezone(timezone.utc)


MOVES = json.dumps({
    "2026-10-05": [{"add": 101, "drop": 201, "date": "2026-10-08"},
                   {"add": 102, "drop": None, "date": "2026-10-09"},
                   {"add": 103, "drop": 203, "date": "2026-10-08"}],      # 103 is on the team already
    "2026-10-12": [{"add": 101, "drop": 201, "date": "2026-10-08"},       # the same move, listed twice
                   {"add": "bad"}, {"add": 104, "date": "not a date"}],
})
TEAMS = json.dumps({"mine": "t1", "teams": [{"id": "t1", "players": [{"id": 103}, {"id": 1}]},
                                            {"id": "t2", "players": [{"id": 101}]}]})
LEAGUE = {"id": 7, "moves": MOVES, "teams": TEAMS, "yahoo": "https://hockey.fantasysports.yahoo.com/hockey/5848"}
DEVICE = {"time_zone": "America/New_York", "remind_day": 0, "remind_at": "10:00"}

# --------------------------------------------------------------------------
print("\n=== 1. when a reminder is due ===")

check("settings are clamped to what is allowed",
      mr.clean_settings({"remindDay": 3, "remindAt": "25:00", "timeZone": "Mars/Base"})
      == (0, "10:00", "America/New_York")
      and mr.clean_settings({"remindDay": "-1", "remindAt": "21:30", "timeZone": "America/Vancouver"})
      == (-1, "21:30", "America/Vancouver"))
check("the moves are read from every week, bad ones skipped",
      sorted(mr.planned_moves(MOVES)) == sorted([("2026-10-08", "101", "201"), ("2026-10-09", "102", None),
                                                 ("2026-10-08", "103", "203"), ("2026-10-08", "101", "201")]),
      mr.planned_moves(MOVES))
check("your roster is the team marked yours", mr.my_roster(TEAMS) == {"103", "1"})
check("a player id reads the same stored as a number, a float or text, and nothing else is one",
      [mr.player_id(v) for v in (8478483, 8478483.0, "8478483", "8478483.0", "bad", None, 0, 1.5)]
      == ["8478483"] * 4 + [None] * 4)
check("an imported rookie's negative id is an id", mr.player_id(-4) == "-4" and mr.player_id("-4") == "-4")
check("a pasted league URL is a League ID", mr.yahoo_league(LEAGUE["yahoo"]) == "5848")

due = lambda when, device=DEVICE: mr.reminders(device, [LEAGUE], when)       # noqa: E731
check("not before its time", due(et("2026-10-08", 9, 59)) == [])
first = due(et("2026-10-08", 10, 0))
check("due at its time, once though planned twice, and never for a move already made",
      [r["add"] for r in first] == ["101"], first)
check("the key is the league and the move",
      first and first[0]["key"] == "7|2026-10-08|101|201" and first[0]["yahoo"] == "5848", first)
check("still due late that night, if the server was down at ten",
      [r["add"] for r in due(et("2026-10-08", 23, 50))] == ["101"])
check("but not the next day - the move has happened or it has not",
      [r["add"] for r in due(et("2026-10-09", 9, 0))] == [])
night = dict(DEVICE, remind_day=-1, remind_at="21:00")
check("the night before: from 9pm the evening before",
      due(et("2026-10-07", 20, 59), night) == [] and [r["add"] for r in due(et("2026-10-07", 21, 0), night)] == ["101"])
west = dict(DEVICE, time_zone="America/Vancouver")
check("ten o'clock is the device's own ten o'clock",
      due(et("2026-10-08", 12, 59), west) == [] and [r["add"] for r in due(et("2026-10-08", 13, 0), west)] == ["101"])
coming = mr.reminders(DEVICE, [LEAGUE], et("2026-10-07", 12, 0), upcoming=True)
check("upcoming lists what is still to come, soonest first",
      [(r["add"], r["date"]) for r in coming] == [("101", "2026-10-08"), ("102", "2026-10-09")], coming)
moved = dict(LEAGUE, moves=json.dumps({"w": [{"add": 101, "drop": 201, "date": "2026-10-09"}]}))
check("a move put on another night is a new reminder",
      mr.reminders(DEVICE, [moved], et("2026-10-09", 10, 0))[0]["key"] == "7|2026-10-09|101|201")

names = {"101": ("Alexis Lafrenière", "NYR", "R"), "201": ("Elias Pettersson", "VAN", "D")}
note = mr.message(first[0], names)
check("the notification names both players, the pickup's team and position, and the night",
      note["title"] == "Time to add Alexis Lafrenière (NYR · R)"
      and note["body"].startswith("Drop Elias Pettersson for him - planned for Thu Oct 8."), note)
check("and opens Yahoo's player search for him in the league",
      note["url"] == "https://hockey.fantasysports.yahoo.com/hockey/5848/playersearch?search=Alexis%20Lafreni%C3%A8re"
      and note["tag"] == first[0]["key"], note["url"])
alone = mr.message({**first[0], "drop": None, "yahoo": None}, names)
check("with nobody to drop and no League ID, it says so and opens League Home",
      "nobody to drop" in alone["body"] and alone["url"] == mr.FALLBACK_URL, alone)


# --------------------------------------------------------------------------
print("\n=== 2. a real push, encrypted and decrypted ===")

captured = {}


class PushService(BaseHTTPRequestHandler):
    def do_POST(self):                                       # noqa: N802 - http.server's name
        # Header names are case-insensitive; pywebpush sends them lower case
        captured["headers"] = {k.lower(): v for k, v in self.headers.items()}
        captured["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(captured.get("answer", 201))
        self.end_headers()

    def log_message(self, *_args):
        pass


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


saved_keys = (Config.VAPID_PUBLIC_KEY, Config.VAPID_PRIVATE_KEY)
server = HTTPServer(("127.0.0.1", 0), PushService)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    Config.VAPID_PUBLIC_KEY, Config.VAPID_PRIVATE_KEY = mr.generate_keys()
    device_key = ec.generate_private_key(ec.SECP256R1())
    secret = os.urandom(16)
    device = {"id": 0, "endpoint": f"http://127.0.0.1:{server.server_port}/push/abc",
              "p256dh": b64(device_key.public_key().public_bytes(serialization.Encoding.X962,
                                                                 serialization.PublicFormat.UncompressedPoint)),
              "auth": b64(secret)}
    outcome = mr.deliver(device, note, ttl=600)
    auth_header = captured.get("headers", {}).get("authorization", "")
    check("the push service takes it", outcome == "ok", outcome)
    check("signed with this server's VAPID key",
          auth_header.startswith("vapid t=") and f"k={Config.VAPID_PUBLIC_KEY}" in auth_header, auth_header[:60])
    plain = http_ece.decrypt(captured.get("body", b""), private_key=device_key, auth_secret=secret,
                             version="aes128gcm")
    check("and only the device can read it: decrypted with its key, it is the reminder",
          json.loads(plain) == note and note["title"].encode() not in captured.get("body", b""))
    check("it lasts until the move's day is over, and goes urgently",
          captured["headers"].get("ttl") == "600" and captured["headers"].get("urgency") == "high",
          captured["headers"])
    captured["answer"] = 410
    check("a 410 means the device has gone", mr.deliver(device, note) == "gone")
    captured["answer"] = 500
    check("anything else is a failure to try again", mr.deliver(device, note) == "failed")
except Exception as exc:                                    # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("the real push ran", False, f"{type(exc).__name__}: {exc}")
finally:
    server.shutdown()


# --------------------------------------------------------------------------
print("\n=== 3. sending, once ===")

PREFIX = "zztestpush"
try:
    import app as app_module
    from db import engine, execute, fetch_all, fetch_one, text

    def cleanup():
        execute("DELETE FROM local_accounts WHERE username_key LIKE :p", {"p": PREFIX + "%"})

    cleanup()
    with engine.begin() as conn:
        account_id = conn.execute(text("INSERT INTO local_accounts (username, username_key, password_hash)"
                                       " VALUES (:u, :u, 'x') RETURNING id"), {"u": PREFIX + "one"}).scalar()
        conn.execute(text("INSERT INTO account_leagues (account_id, state) VALUES (:a, CAST(:s AS jsonb))"),
                     {"a": account_id, "s": json.dumps({"fs_standaloneMoves": MOVES, "fs_leagueTeams": TEAMS,
                                                        "fs_yahooLeagueId": "5848"})})
        sub_id = conn.execute(text("INSERT INTO push_subscriptions (account_id, endpoint, p256dh, auth)"
                                   " VALUES (:a, 'https://push.example/zztest-1', 'p', 'k') RETURNING id"),
                              {"a": account_id}).scalar()

    sent = []

    def stub(outcomes):
        def send(subscription, payload, ttl):
            outcome = outcomes.pop(0) if outcomes else "ok"
            if subscription["id"] == sub_id:
                sent.append((payload["tag"], outcome, round(ttl)))
            return outcome
        return send

    at_ten = et("2026-10-08", 10, 0)
    mr.send_due(at_ten, send=stub(["failed"]))
    row = fetch_one("SELECT failures FROM push_subscriptions WHERE id = :id", {"id": sub_id})
    check("a failed send is counted and its claim released", [s[1] for s in sent] == ["failed"]
          and row["failures"] == 1
          and not fetch_all("SELECT 1 FROM push_sent WHERE subscription_id = :id", {"id": sub_id}), (sent, row))
    mr.send_due(et("2026-10-08", 10, 1), send=stub([]))
    league_key = fetch_one("SELECT id FROM account_leagues WHERE account_id = :a", {"a": account_id})["id"]
    check("and tried again the next minute, lasting until the day is over (13h59m)",
          sent[-1][0] == f"{league_key}|2026-10-08|101|201" and sent[-1][1] == "ok" and sent[-1][2] == 50340, sent)
    check("which clears the count", fetch_one("SELECT failures FROM push_subscriptions WHERE id = :id",
                                              {"id": sub_id})["failures"] == 0)
    before = len(sent)
    mr.send_due(et("2026-10-08", 10, 2), send=stub([]))
    mr.send_due(et("2026-10-08", 18, 0), send=stub([]))
    check("never twice", len(sent) == before, sent)

    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": mr.LOCK_KEY})
        try:
            check("a held lock means another worker is sending: this pass does nothing",
                  mr.send_due(et("2026-10-09", 10, 0), send=stub([])) == 0 and len(sent) == before)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": mr.LOCK_KEY})
            holder.commit()

    mr.send_due(et("2026-10-09", 10, 0), send=stub(["gone"]))
    check("a device that has gone is dropped",
          not fetch_one("SELECT 1 FROM push_subscriptions WHERE id = :id", {"id": sub_id}), sent[-1:])

    # ----------------------------------------------------------------------
    print("\n=== 4. the routes ===")

    flask_app = app_module.app
    client = flask_app.test_client()
    client.environ_base["HTTP_X_FORWARDED_FOR"] = "10.7.7.1"
    other = flask_app.test_client()
    other.environ_base["HTTP_X_FORWARDED_FOR"] = "10.7.7.2"

    worker = client.get("/sw.js")
    check("the service worker is served for the whole site, never cached stale",
          worker.status_code == 200 and worker.headers.get("Service-Worker-Allowed") == "/"
          and worker.mimetype == "application/javascript" and b"showNotification" in worker.data)
    manifest = client.get("/manifest.webmanifest").get_json(force=True)
    check("the manifest opens League Home as an app, with icons that exist",
          manifest["start_url"] == "/standalone/" and manifest["display"] == "standalone"
          and all((ROOT / icon["src"].lstrip("/")).exists() for icon in manifest["icons"]), manifest)
    check("the page links it, and the iPhone icon", all(s in client.get("/standalone/").data for s in
                                                       (b'rel="manifest"', b"apple-touch-icon.png")))

    status = client.post("/notifications/api/status", json={}).get_json()
    check("signed out: available, but not without an account",
          status["available"] is True and status["signedIn"] is False and status["publicKey"] == Config.VAPID_PUBLIC_KEY)
    good_keys = {"p256dh": b64(b"\x04" + os.urandom(64)), "auth": b64(os.urandom(16))}
    body = {"subscription": {"endpoint": "https://push.example/zztest-2", "keys": good_keys},
            "remindDay": -1, "remindAt": "21:00", "timeZone": "America/Chicago"}
    check("turning reminders on needs an account", client.post("/notifications/api/subscribe", json=body).status_code == 401)

    client.post("/account/api/signup", json={"username": PREFIX + "two", "password": "correct horse"})
    league = client.get("/standalone/")
    league_id = json.loads(league.data.split(b"window.FS_ACCOUNT = ")[1].split(b";</script>")[0])["league"]
    projected = fetch_one('SELECT "playerId", "fullName" FROM final_projections ORDER BY "playerId" LIMIT 1')
    client.put(f"/account/api/leagues/{league_id}", json={"state": {
        "fs_standaloneMoves": json.dumps({"w": [{"add": projected["playerId"], "drop": None, "date": "2099-01-01"}]})}})
    check("an endpoint that is not https is refused", client.post("/notifications/api/subscribe", json={
        **body, "subscription": {**body["subscription"], "endpoint": "http://evil.example"}}).status_code == 400)
    check("and one without keys", client.post("/notifications/api/subscribe", json={
        **body, "subscription": {"endpoint": "https://push.example/zztest-2"}}).status_code == 400)
    check("and keys no browser would make - the wrong size, or not base64",
          all(client.post("/notifications/api/subscribe", json={**body, "subscription": {
              "endpoint": "https://push.example/zztest-2", "keys": keys}}).status_code == 400
              for keys in ({"p256dh": "p", "auth": "a"}, {**good_keys, "auth": b64(os.urandom(15))},
                           {**good_keys, "p256dh": "not*base64!"})))
    on = client.post("/notifications/api/subscribe", json=body)
    data = on.get_json()
    check("signed in, it turns on with this device's settings",
          on.status_code == 200 and data["settings"] == {"remindDay": -1, "remindAt": "21:00", "timeZone": "America/Chicago"},
          data)
    check("and lists what is coming, by name, the night before in the device's zone",
          data["upcoming"] and data["upcoming"][0]["date"] == "2099-01-01"
          and data["upcoming"][0]["at"].startswith("2098-12-31T21:00:00-06:00")
          and data["upcoming"][0]["add"] == projected["fullName"], data.get("upcoming"))
    status = client.post("/notifications/api/status", json={"endpoint": "https://push.example/zztest-2"}).get_json()
    check("its status says so, with the account's device count", status["subscribed"] and status["devices"] == 1, status)
    clamped = client.post("/notifications/api/settings", json={"endpoint": "https://push.example/zztest-2",
                                                               "remindDay": 5, "remindAt": "nope"}).get_json()
    check("settings out of range fall back to the day of, at ten",
          clamped["settings"]["remindDay"] == 0 and clamped["settings"]["remindAt"] == "10:00", clamped)

    other.post("/account/api/signup", json={"username": PREFIX + "three", "password": "correct horse"})
    endpoint = {"endpoint": "https://push.example/zztest-2"}
    by_endpoint = {"e": endpoint["endpoint"]}
    check("another account cannot see this device's reminders, test it or change them",
          other.post("/notifications/api/status", json=endpoint).get_json()["subscribed"] is False
          and other.post("/notifications/api/test", json=endpoint).status_code == 404
          and other.post("/notifications/api/settings", json=endpoint).status_code == 404)

    real_deliver = mr.deliver
    try:
        mr.deliver = lambda subscription, payload, ttl=3600: "ok"
        check("a test notification goes to this device", client.post("/notifications/api/test", json=endpoint).status_code == 200)
        mr.deliver = lambda subscription, payload, ttl=3600: "gone"
        check("and one the browser has dropped says to turn reminders on again, and is forgotten",
              client.post("/notifications/api/test", json=endpoint).status_code == 410
              and not fetch_one("SELECT 1 FROM push_subscriptions WHERE endpoint = :e", by_endpoint))
    finally:
        mr.deliver = real_deliver
    client.post("/notifications/api/subscribe", json=body)
    moved = other.post("/notifications/api/subscribe", json=body)
    check("one device signed in to another account now reminds that account",
          moved.status_code == 200 and fetch_one(
              "SELECT a.username FROM push_subscriptions s JOIN local_accounts a ON a.id = s.account_id"
              " WHERE s.endpoint = :e", by_endpoint)["username"] == PREFIX + "three")
    check("turning off forgets the device", other.post("/notifications/api/unsubscribe", json=endpoint).status_code == 200
          and not fetch_one("SELECT 1 FROM push_subscriptions WHERE endpoint = :e", by_endpoint))
    check("a page that is not JSON is refused", client.post("/notifications/api/status", data="x").status_code == 400)
except Exception as exc:                                    # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("database-backed checks ran", False, f"{type(exc).__name__}: {exc}")
finally:
    Config.VAPID_PUBLIC_KEY, Config.VAPID_PRIVATE_KEY = saved_keys
    try:
        cleanup()
    except Exception:                                       # noqa: BLE001
        pass


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
