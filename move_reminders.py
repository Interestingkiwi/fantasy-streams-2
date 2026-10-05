"""
Move reminders: a push notification on a phone when a planned move is due.

Yahoo's Fantasy API is gated, so Fantasy Streams cannot make an add/drop
itself, and doing it through a user's own Yahoo session would mean holding
their credentials or keeping their browser running. What it can do is say
when. A move planned on League Home (`fs_standaloneMoves`, `{week: [{add,
drop, date}]}`, NHL player ids) becomes a notification - "Time to add X, drop
Y" - at the time the device asked for, and tapping it opens Yahoo's player
search for X in the league, where Yahoo's own Add button asks whom to drop.

**Reminders need an account.** The point is planning on one device and being
told on another, so the server has to see the moves, and it does: an account
already carries every league's planned moves (`account_leagues.state`). Each
device that turns reminders on is a row in `push_subscriptions`, tied to the
account, with its own time zone and time - the day of the move or the night
before, at a time of its choosing. Every league in the account is watched.

**When.** A reminder is due from its time until the end of the move's day in
the device's zone; after that the move has happened or not. Nothing is sent
for a move already made - the added player on your team in the league's last
roster scrape. `push_sent` records each one sent, so none goes twice, and a
send that fails for a reason other than the device having gone is tried again
the next minute.

**Who sends.** A daemon thread in each web process (`start`, when
`PUSH_SENDER` is on - production), once a minute. Gunicorn runs two workers,
so each pass takes a Postgres advisory lock and the other skips; claims are
written before anything is sent. No cron service: a minute's precision from a
cron would be a new billable service running 1,440 times a day.

**Web push**, via `pywebpush`: the payload is encrypted for the device, so the
push service (Google's, Apple's, Mozilla's) carries it unread. A 404 or 410
from it means the device unsubscribed or the subscription expired, and the row
is dropped. On an iPhone, push only works for the site added to the Home
Screen (iOS 16.4+), and the page says so.

    python move_reminders.py keys     # a new VAPID key pair, for the env
    python move_reminders.py run      # send whatever is due, once

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import base64
import json
import logging
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import Config
from db import fetch_all, text, transaction
from yahoo_rosters import RosterPageError, league_id_from

log = logging.getLogger(__name__)

DEFAULT_ZONE = "America/New_York"
DEFAULT_TIME = "10:00"
DAY_CHOICES = (0, -1)             # the day of the move, or the night before
TICK_SECONDS = 60
# Consecutive failed sends before a device is given up on
MAX_FAILURES = 20
# Any constant both workers agree on; it names the lock, nothing else
LOCK_KEY = 48151623
SEARCH_URL = "https://hockey.fantasysports.yahoo.com/hockey/{league}/playersearch?search={name}"
FALLBACK_URL = "/standalone/#free-agents"
TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ------------------------------------------------------------------ settings

def zone(name):
    try:
        return ZoneInfo(str(name)) if name else ZoneInfo(DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def clean_settings(raw):
    """(remind_day, remind_at, time_zone) from what a page sent, each falling
    back to the default when it is not one of the allowed values."""
    raw = raw or {}
    try:
        day = int(raw.get("remindDay", 0))
    except (TypeError, ValueError):
        day = 0
    at = str(raw.get("remindAt") or DEFAULT_TIME)
    name = str(raw.get("timeZone") or DEFAULT_ZONE)
    return (day if day in DAY_CHOICES else 0,
            at if TIME_OF_DAY.match(at) else DEFAULT_TIME,
            zone(name).key)


def fire_time(move_date, remind_day, remind_at, tz):
    """When a move on `move_date` is to be reminded of, as an aware datetime."""
    hour, minute = (int(p) for p in remind_at.split(":"))
    day = date.fromisoformat(move_date) + timedelta(days=remind_day)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def day_end(move_date, tz):
    """The end of the move's day, when its reminder stops mattering."""
    day = date.fromisoformat(move_date) + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=tz)


# ------------------------------------------------------------- the moves

def _parse(raw, fallback):
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def player_id(value):
    """A player id as text - '8478483' whether it was stored as 8478483,
    8478483.0 or a string - or None for anything that is not one. Imported
    rookies with no NHL id have negative ones (-4), so those count too."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return str(int(number)) if number.is_integer() and number != 0 else None


def planned_moves(raw):
    """[(date, add, drop)] from `fs_standaloneMoves` as stored - every week's."""
    found = []
    for week in (_parse(raw, {}) or {}).values():
        for move in week if isinstance(week, list) else []:
            if not isinstance(move, dict) or not player_id(move.get("add")) or not move.get("date"):
                continue
            try:
                date.fromisoformat(str(move["date"]))
            except ValueError:
                continue
            found.append((str(move["date"]), player_id(move["add"]), player_id(move.get("drop"))))
    return found


def my_roster(raw):
    """The player ids on the team marked yours in `fs_leagueTeams`."""
    league = _parse(raw, {}) or {}
    mine = next((t for t in league.get("teams") or [] if t.get("id") == league.get("mine")), None)
    return {player_id(p.get("id")) for p in (mine or {}).get("players") or [] if player_id(p.get("id"))}


def yahoo_league(raw):
    """The Yahoo League ID a league names, or None."""
    text_value = str(raw or "").strip().strip('"')
    if not text_value:
        return None
    try:
        return league_id_from(text_value)
    except RosterPageError:
        return None


def reminders(subscription, leagues, now, upcoming=False):
    """
    The reminders a device is due now - or, with `upcoming`, those still to
    come - over every league of its account. `leagues` are {id, moves, teams,
    yahoo} with the raw stored values. Each is {key, league, yahoo, date, add,
    drop, fire}; the key is the league and the move, so a move planned again
    unchanged is not reminded twice, and one moved to another night is.
    """
    tz = zone(subscription.get("time_zone"))
    day = subscription.get("remind_day", 0)
    at = subscription.get("remind_at") or DEFAULT_TIME
    local_now = now.astimezone(tz)
    found = []
    for league in leagues:
        made = my_roster(league.get("teams"))
        seen = set()
        for move_date, add, drop in planned_moves(league.get("moves")):
            key = f"{league['id']}|{move_date}|{add}|{drop or ''}"
            if key in seen or add in made:
                continue
            seen.add(key)
            fire = fire_time(move_date, day, at, tz)
            end = day_end(move_date, tz)
            if (fire > local_now if upcoming else fire <= local_now) and local_now < end:
                found.append({"key": key, "league": league["id"], "yahoo": yahoo_league(league.get("yahoo")),
                              "date": move_date, "add": add, "drop": drop, "fire": fire})
    return sorted(found, key=lambda r: r["fire"])


def _when(iso):
    d = date.fromisoformat(iso)
    return f"{d:%a} {d:%b} {d.day}"


def message(reminder, names):
    """
    The notification: {title, body, url, tag}. `names` is {playerId: (name,
    team, position)}. Tapping it opens Yahoo's player search for the added
    player in the league, or League Home when no League ID is known.
    """
    name, team, position = names.get(reminder["add"]) or ("a planned pickup", "", "")
    detail = " · ".join(x for x in (team, position) if x)
    title = f"Time to add {name}" + (f" ({detail})" if detail else "")
    if reminder["drop"]:
        dropped = (names.get(reminder["drop"]) or ("the player you planned to drop",))[0]
        body = f"Drop {dropped} for him - planned for {_when(reminder['date'])}."
    else:
        body = f"Planned for {_when(reminder['date'])}, with nobody to drop."
    if reminder.get("yahoo"):
        url = SEARCH_URL.format(league=reminder["yahoo"], name=quote(name))
        body += " Tap to find him on Yahoo."
    else:
        url = FALLBACK_URL
        body += " Tap to open League Home."
    return {"title": title, "body": body, "url": url, "tag": reminder["key"]}


def player_names(ids):
    """{playerId: (name, team, position)} for the players reminders name."""
    wanted = sorted({int(player_id(i)) for i in ids if player_id(i)})
    if not wanted:
        return {}
    rows = fetch_all('SELECT "playerId", "fullName", "teamAbbrevs", "positionCode" FROM final_projections'
                     ' WHERE "playerId" = ANY(:ids)', {"ids": wanted})
    names = {player_id(r["playerId"]): (r["fullName"], r["teamAbbrevs"] or "", r["positionCode"] or "")
             for r in rows}
    missing = [i for i in wanted if str(i) not in names]
    if missing:
        try:
            for r in fetch_all('SELECT DISTINCT ON ("playerId") "playerId", "fullName", "teamAbbrev",'
                               ' "positionCode" FROM player_game_stats WHERE "playerId" = ANY(:ids)'
                               ' ORDER BY "playerId", "gameDate" DESC', {"ids": missing}):
                names[player_id(r["playerId"])] = (r["fullName"], r["teamAbbrev"] or "", r["positionCode"] or "")
        except Exception:                         # noqa: BLE001 - no games scraped yet
            pass
    return names


# ------------------------------------------------------------- the leagues

LEAGUES_SQL = ("SELECT id, account_id, state->>'fs_standaloneMoves' AS moves,"
               " state->>'fs_leagueTeams' AS teams, state->>'fs_yahooLeagueId' AS yahoo"
               " FROM account_leagues WHERE account_id = ANY(:a)")


def account_leagues(account_ids, conn=None):
    """{account_id: [league]} - only what reminders read, never the
    transactions a league also carries."""
    params = {"a": sorted(set(account_ids))}
    rows = (conn.execute(text(LEAGUES_SQL), params).mappings().all() if conn
            else fetch_all(LEAGUES_SQL, params)) if params["a"] else []
    found = {}
    for row in rows:
        found.setdefault(row["account_id"], []).append(dict(row))
    return found


# ----------------------------------------------------------------- sending

def enabled():
    return bool(Config.VAPID_PUBLIC_KEY and Config.VAPID_PRIVATE_KEY)


def public_key():
    """What a page subscribes with - None until the keys are set."""
    return Config.VAPID_PUBLIC_KEY if enabled() else None


def deliver(subscription, payload, ttl=3600):
    """
    Sends one notification. 'ok', 'gone' (the device unsubscribed or its
    subscription expired - drop it) or 'failed' (try again later).
    """
    from pywebpush import WebPushException, webpush
    try:
        webpush(
            subscription_info={"endpoint": subscription["endpoint"],
                               "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=Config.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": Config.VAPID_SUBJECT},
            ttl=max(60, int(ttl)),
            headers={"Urgency": "high"},
            timeout=15,
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            return "gone"
        log.warning("Push to subscription %s failed: %s", subscription.get("id"), exc)
        return "failed"
    except Exception as exc:                      # noqa: BLE001 - a network error, say
        log.warning("Push to subscription %s failed: %s", subscription.get("id"), exc)
        return "failed"


def _record(subscription_id, outcome, reminder_key=None):
    """What a send did: a device gone is dropped, a failure counted (and its
    claim released, to be tried again), a success clears the count."""
    with transaction() as conn:
        if outcome == "gone":
            conn.execute(text("DELETE FROM push_subscriptions WHERE id = :id"), {"id": subscription_id})
        elif outcome == "failed":
            if reminder_key:
                conn.execute(text("DELETE FROM push_sent WHERE subscription_id = :id AND reminder_key = :k"),
                             {"id": subscription_id, "k": reminder_key})
            conn.execute(text("UPDATE push_subscriptions SET failures = failures + 1 WHERE id = :id"),
                         {"id": subscription_id})
            conn.execute(text("DELETE FROM push_subscriptions WHERE id = :id AND failures >= :n"),
                         {"id": subscription_id, "n": MAX_FAILURES})
        else:
            conn.execute(text("UPDATE push_subscriptions SET failures = 0, last_sent_at = now()"
                              " WHERE id = :id"), {"id": subscription_id})


def send_due(now=None, send=None):
    """
    Sends every reminder now due, to every subscribed device. Returns how many
    were sent. Claims are written, under an advisory lock, before anything is
    sent, so two workers - or a slow pass and the next - never send one twice.
    """
    if not (enabled() or send):
        return 0
    send = send or deliver
    now = now or datetime.now(timezone.utc)
    claimed = []
    with transaction() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return 0
        subscriptions = [dict(r) for r in conn.execute(text("SELECT * FROM push_subscriptions")).mappings()]
        if not subscriptions:
            return 0
        leagues = account_leagues([s["account_id"] for s in subscriptions], conn)
        for subscription in subscriptions:
            for reminder in reminders(subscription, leagues.get(subscription["account_id"], []), now):
                if conn.execute(text(
                        "INSERT INTO push_sent (subscription_id, reminder_key) VALUES (:s, :k)"
                        " ON CONFLICT DO NOTHING RETURNING reminder_key"),
                        {"s": subscription["id"], "k": reminder["key"]}).first():
                    claimed.append((subscription, reminder))
    if not claimed:
        return 0
    names = player_names([r["add"] for _s, r in claimed] + [r["drop"] for _s, r in claimed if r["drop"]])
    sent, gone = 0, set()
    for subscription, reminder in claimed:
        if subscription["id"] in gone:
            continue
        ttl = (day_end(reminder["date"], reminder["fire"].tzinfo) - now).total_seconds()
        outcome = send(subscription, message(reminder, names), ttl)
        _record(subscription["id"], outcome, reminder["key"])
        if outcome == "gone":
            gone.add(subscription["id"])
        sent += outcome == "ok"
    log.info("Move reminders: %d sent, %d claimed.", sent, len(claimed))
    return sent


def send_test(subscription, send=None):
    """One notification now, so a device can see reminders work."""
    payload = {"title": "Fantasy Streams reminders are on",
               "body": "This is how a planned move will look: tap it to find the player on Yahoo.",
               "url": FALLBACK_URL, "tag": "fs-test"}
    outcome = (send or deliver)(subscription, payload, 600)
    _record(subscription["id"], outcome)
    return outcome


# ------------------------------------------------------------- the thread

_started = False
_start_lock = threading.Lock()


def _loop():
    while True:
        try:
            send_due()
        except Exception:                         # noqa: BLE001 - one bad pass must not stop the next
            log.exception("Move reminders pass failed.")
        time.sleep(TICK_SECONDS)


def start():
    """Starts the sender in this process, once. Called by app.py when
    PUSH_SENDER is on and the keys are set."""
    global _started
    with _start_lock:
        if _started or not enabled():
            return False
        _started = True
    threading.Thread(target=_loop, name="move-reminders", daemon=True).start()
    log.info("Move reminders: sending every %ds.", TICK_SECONDS)
    return True


# ------------------------------------------------------------------- keys

def generate_keys():
    """(public, private) VAPID keys, base64url: the public one is what a page
    subscribes with, the private one signs every push."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return b64(public), b64(private)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "keys":
        public_key, private_key = generate_keys()
        print("Add these to the environment (.env locally, the dashboard on Render).")
        print("Keep the private key secret; a new pair makes every device turn reminders on again.\n")
        print(f"VAPID_PUBLIC_KEY={public_key}")
        print(f"VAPID_PRIVATE_KEY={private_key}")
    elif command == "run":
        if not enabled():
            sys.exit("VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are not set - see `keys`.")
        print(f"{send_due()} reminder(s) sent.")
    else:
        sys.exit("Usage: python move_reminders.py keys | run")
