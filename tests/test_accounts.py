"""
Tests for the simple accounts (routes/account_routes.py).

Runs the real routes against the real Postgres, under usernames starting
`zztest`, and deletes them before and after. What it leans on hardest: that
one account can never read, save over, open or delete another's league; that
a signed-in page carries its league for account-sync.js to put in place before
any page script reads it, escaped so a team name cannot close the script tag;
that ten wrong passwords lock an account even against the right one; and that
an account deleted by `manage_accounts.py` is signed out wherever it was
signed in.

Author - Jason Druckenmiller
Created - 9/29/2026
Updated - 9/29/2026
"""

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "accounts-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


PREFIX = "zztest"
PASSWORD = "correct horse"


def boot_from(response):
    """window.FS_ACCOUNT as rendered into a page."""
    found = re.search(rb"window\.FS_ACCOUNT = (.*?);</script>", response.data, re.S)
    return json.loads(found.group(1)) if found else None


try:
    import app as app_module
    import manage_accounts
    from db import engine, execute, fetch_all, text
    from routes import account_routes as accounts

    def cleanup():
        execute("DELETE FROM local_accounts WHERE username_key LIKE :p", {"p": PREFIX + "%"})

    cleanup()
    accounts._signups.clear()
    flask_app = app_module.app
    counter = iter(range(1, 1000))

    def client_for():
        """A test client from an address of its own, so sign-up limits stay apart."""
        client = flask_app.test_client()
        client.environ_base["HTTP_X_FORWARDED_FOR"] = f"10.9.9.{next(counter)}"
        return client

    # ----------------------------------------------------------------------
    print("\n=== 1. the two lists of league keys ===")

    script = (ROOT / "static" / "account-sync.js").read_text(encoding="utf-8")
    block = re.search(r"const LEAGUE_KEYS = \[(.*?)\];", script, re.S).group(1)
    js_keys = set(re.findall(r"'([^']+)'", block))
    check("account-sync.js and the server mirror exactly the same keys",
          js_keys == set(accounts.LEAGUE_KEYS), sorted(js_keys ^ set(accounts.LEAGUE_KEYS)))
    check("the per-device keys are not among them",
          not {"fs_standaloneTab", "fs_standaloneWeek", "fs_lists", "fs_rankMode"} & js_keys)

    # ----------------------------------------------------------------------
    print("\n=== 2. signing up ===")

    anon = client_for()
    signed_out = anon.get("/standalone/")
    check("a signed-out page says so, and offers to sign in",
          signed_out.status_code == 200 and boot_from(signed_out) == {"signedIn": False}
          and b">Sign in</span>" in signed_out.data)

    for body, label in [({"username": "ab", "password": PASSWORD}, "a two-letter username"),
                        ({"username": "has space", "password": PASSWORD}, "a username with a space"),
                        ({"username": PREFIX + "short", "password": "1234567"}, "a seven-character password")]:
        check(f"{label} is refused", anon.post("/account/api/signup", json=body).status_code == 400)
    check("a form post rather than JSON is refused",
          anon.post("/account/api/signup", data={"username": PREFIX + "form", "password": PASSWORD})
          .status_code == 400)

    alice = client_for()
    made = alice.post("/account/api/signup", json={"username": PREFIX + "Alice", "password": PASSWORD})
    check("a new account signs in at once", made.status_code == 200
          and made.get_json()["leagues"] == [], made.get_json())
    cookie = made.headers.get("Set-Cookie", "")
    check("and the cookie outlives the browser session, so it stays signed in",
          "Expires=" in cookie and "HttpOnly" in cookie, cookie)
    stored = fetch_all("SELECT password_hash FROM local_accounts WHERE username_key = :k",
                       {"k": PREFIX + "alice"})
    check("the password is stored hashed, never as typed",
          stored and PASSWORD not in stored[0]["password_hash"]
          and stored[0]["password_hash"].startswith(("scrypt:", "pbkdf2:")))
    check("a username differing only in case is taken",
          client_for().post("/account/api/signup", json={"username": PREFIX + "ALICE",
                                                         "password": PASSWORD}).status_code == 409)

    page = alice.get("/standalone/")
    boot = boot_from(page)
    check("a signed-in page carries the account and an empty league to save into",
          boot and boot["signedIn"] and boot["username"] == PREFIX + "Alice"
          and boot["league"] and boot["state"] == {} and len(boot["leagues"]) == 1, boot)
    check("and the button shows who is signed in",
          f">{PREFIX}Alice</span>".encode() in page.data)
    # /league/ is left out: it needs a Yahoo session and redirects without one
    for path in ("/draft-prep/", "/schedules/", "/"):
        response = alice.get(path)
        check(f"{path} carries the same league",
              response.status_code == 200 and (boot_from(response) or {}).get("league") == boot["league"],
              response.status_code)
    alice_league = boot["league"]

    # ----------------------------------------------------------------------
    print("\n=== 3. saving a league ===")

    teams = {"teams": [{"id": "t1", "name": "</script><script>alert(1)</script>", "players": [{"id": 1}]}],
             "mine": "t1"}
    state = {"fs_leagueTeams": json.dumps(teams), "fs_selectedStats": '["proj_goals"]',
             "fs_standaloneTab": '"league"', "fs_leagueMode": 7}
    saved = alice.put(f"/account/api/leagues/{alice_league}", json={"state": state, "name": "Mine"})
    check("a league saves", saved.status_code == 200 and saved.get_json().get("updatedAt"),
          saved.get_json())
    boot = boot_from(alice.get("/standalone/"))
    check("and comes back on the next page",
          boot["state"].get("fs_selectedStats") == '["proj_goals"]'
          and json.loads(boot["state"]["fs_leagueTeams"]) == teams, boot["state"])
    check("keys outside the league's, and values that are not strings, are dropped",
          "fs_standaloneTab" not in boot["state"] and "fs_leagueMode" not in boot["state"],
          sorted(boot["state"]))
    raw = alice.get("/standalone/").data
    check("a team name cannot close the script tag it is rendered into",
          b"</script><script>alert(1)" not in raw)
    # A stale copy must never overwrite a newer one
    loaded = boot_from(alice.get("/standalone/"))["updatedAt"]
    first = alice.put(f"/account/api/leagues/{alice_league}",
                      json={"state": {**state, "fs_numTeams": "12"}, "base": loaded})
    check("a save built on the latest version lands, and says what it is now",
          first.status_code == 200 and first.get_json()["updatedAt"] > loaded, first.get_json())
    stale = alice.put(f"/account/api/leagues/{alice_league}",
                      json={"state": {**state, "fs_numTeams": "8"}, "base": loaded})
    check("one built on an older version is refused as a conflict",
          stale.status_code == 409 and stale.get_json().get("conflict") is True, stale.get_json())
    check("and leaves the newer save in place",
          boot_from(alice.get("/standalone/"))["state"].get("fs_numTeams") == "12")
    version = alice.get(f"/account/api/leagues/{alice_league}/version").get_json()
    check("a tab can ask which version is current without loading the league",
          version.get("updatedAt") == first.get_json()["updatedAt"], version)
    check("a base that is not a timestamp is refused",
          alice.put(f"/account/api/leagues/{alice_league}",
                    json={"state": state, "base": "yesterday"}).status_code == 400)

    check("a league too big to be real is refused",
          alice.put(f"/account/api/leagues/{alice_league}",
                    json={"state": {"fs_leagueTeams": "x" * (accounts.MAX_STATE_BYTES + 1)}})
          .status_code == 400)

    second = alice.post("/account/api/leagues", json={"state": {"fs_numTeams": "10"}, "name": "Other"})
    other_league = second.get_json().get("league")
    boot = boot_from(alice.get("/standalone/"))
    check("a new league opens at once", second.status_code == 200 and boot["league"] == other_league
          and boot["state"] == {"fs_numTeams": "10"} and len(boot["leagues"]) == 2, boot)
    reopened = alice.post(f"/account/api/leagues/{alice_league}/open")
    check("and the first can be opened again",
          reopened.status_code == 200 and boot_from(alice.get("/standalone/"))["league"] == alice_league)

    # ----------------------------------------------------------------------
    print("\n=== 4. one account never touches another's ===")

    bob = client_for()
    bob.post("/account/api/signup", json={"username": PREFIX + "bob", "password": PASSWORD})
    bob_boot = boot_from(bob.get("/standalone/"))
    check("another account sees only its own leagues",
          [league["id"] for league in bob_boot["leagues"]] == [bob_boot["league"]]
          and bob_boot["league"] not in (alice_league, other_league), bob_boot["leagues"])
    check("it cannot save over one of them",
          bob.put(f"/account/api/leagues/{alice_league}", json={"state": {}}).status_code == 404)
    check("or open one",
          bob.post(f"/account/api/leagues/{alice_league}/open").status_code == 404)
    check("or delete one",
          bob.delete(f"/account/api/leagues/{alice_league}").status_code == 404)
    check("or even learn when it was saved",
          bob.get(f"/account/api/leagues/{alice_league}/version").status_code == 404)
    check("and the league is exactly as it was",
          boot_from(alice.get("/standalone/"))["state"].get("fs_selectedStats") == '["proj_goals"]')
    check("signed out, nothing answers",
          all(status == 401 for status in (
              anon.put(f"/account/api/leagues/{alice_league}", json={"state": {}}).status_code,
              anon.post("/account/api/leagues", json={}).status_code,
              anon.post(f"/account/api/leagues/{alice_league}/open").status_code,
              anon.delete(f"/account/api/leagues/{alice_league}").status_code,
              anon.get(f"/account/api/leagues/{alice_league}/version").status_code)))

    # ----------------------------------------------------------------------
    print("\n=== 5. signing in and out ===")

    check("signing out ends it", bob.post("/account/api/signout").status_code == 200
          and boot_from(bob.get("/standalone/")) == {"signedIn": False})
    wrong = bob.post("/account/api/signin", json={"username": PREFIX + "bob", "password": "nope nope"})
    unknown = bob.post("/account/api/signin", json={"username": PREFIX + "nobody", "password": PASSWORD})
    check("a wrong password and an unknown name get the same answer",
          wrong.status_code == unknown.status_code == 401
          and wrong.get_json()["message"] == unknown.get_json()["message"])
    back = bob.post("/account/api/signin", json={"username": PREFIX + "BOB", "password": PASSWORD})
    check("signing in ignores the case of the username, and lists the leagues",
          back.status_code == 200 and len(back.get_json()["leagues"]) == 1, back.get_json())

    for _ in range(accounts.LOCK_AFTER_FAILURES):
        bob.post("/account/api/signin", json={"username": PREFIX + "bob", "password": "nope nope"})
    locked = bob.post("/account/api/signin", json={"username": PREFIX + "bob", "password": PASSWORD})
    check("ten wrong passwords lock the account, even against the right one",
          locked.status_code == 429 and "minute" in locked.get_json()["message"], locked.get_json())
    execute("UPDATE local_accounts SET locked_until = NULL WHERE username_key = :k",
            {"k": PREFIX + "bob"})
    check("and it opens again once the lock runs out",
          bob.post("/account/api/signin", json={"username": PREFIX + "bob", "password": PASSWORD})
          .status_code == 200)

    # ----------------------------------------------------------------------
    print("\n=== 6. deleting ===")

    check("deleting a league takes it out of the list",
          alice.delete(f"/account/api/leagues/{other_league}").status_code == 200
          and [league["id"] for league in boot_from(alice.get("/standalone/"))["leagues"]]
          == [alice_league])
    check("deleting the account needs its password",
          bob.post("/account/api/delete", json={"password": "nope nope"}).status_code == 401)
    bob_id = fetch_all("SELECT id FROM local_accounts WHERE username_key = :k",
                       {"k": PREFIX + "bob"})[0]["id"]
    gone = bob.post("/account/api/delete", json={"password": PASSWORD})
    check("with it, the account and its leagues go, and it is signed out",
          gone.status_code == 200
          and not fetch_all("SELECT 1 FROM account_leagues WHERE account_id = :a", {"a": bob_id})
          and boot_from(bob.get("/standalone/")) == {"signedIn": False})

    # The developer's delete, as for someone who lost their password
    with engine.begin() as conn:
        manage_accounts.delete_account(conn, PREFIX + "ALICE", assume_yes=True)
    check("an account deleted by manage_accounts.py is signed out where it was signed in",
          boot_from(alice.get("/standalone/")) == {"signedIn": False})
    check("and its leagues went with it",
          not fetch_all("SELECT 1 FROM account_leagues WHERE id = :id", {"id": alice_league}))

    # ----------------------------------------------------------------------
    print("\n=== 7. limits and failures ===")

    crowd = flask_app.test_client()
    crowd.environ_base["HTTP_X_FORWARDED_FOR"] = "10.8.8.8"
    statuses = [crowd.post("/account/api/signup",
                           json={"username": f"{PREFIX}crowd{i}", "password": PASSWORD}).status_code
                for i in range(accounts.SIGNUPS_PER_HOUR + 1)]
    check("one address can only make a few accounts an hour",
          statuses[:-1] == [200] * accounts.SIGNUPS_PER_HOUR and statuses[-1] == 429, statuses)
    spoofed = flask_app.test_client()
    spoofed.environ_base["HTTP_X_FORWARDED_FOR"] = "1.2.3.4, 10.8.8.8"
    check("and a made-up forwarding header does not reset the count",
          spoofed.post("/account/api/signup", json={"username": PREFIX + "spoof", "password": PASSWORD})
          .status_code == 429)

    full = client_for()
    full.post("/account/api/signup", json={"username": PREFIX + "full", "password": PASSWORD})
    full.get("/standalone/")
    made = [full.post("/account/api/leagues", json={}).status_code
            for _ in range(accounts.MAX_LEAGUES)]
    check("an account holds at most twenty leagues",
          made.count(200) == accounts.MAX_LEAGUES - 1 and made[-1] == 400, made[-3:])

    real_fetch = accounts.fetch_one

    def broken(*_args, **_kwargs):
        raise RuntimeError("database down")

    accounts.fetch_one = broken
    try:
        down = full.get("/standalone/")
        check("with the database down the page still renders, signed out",
              down.status_code == 200 and (boot_from(down) or {}).get("signedIn") is False)
    finally:
        accounts.fetch_one = real_fetch

except Exception as exc:                                    # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("database-backed checks ran", False, f"{type(exc).__name__}: {exc}")
finally:
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
