"""
A public Yahoo league's settings, weekly scoreboards and day-by-day rosters,
from Yahoo's public read-only API, cached in Postgres.

What bench points (and later pages) are built from. Checked on league 5848 on
9/30/2026: `pub-api-ro` - anonymous, what Yahoo's own signed-out pages call -
answers all of it for a public league, and a private one with 401, which the
callers turn into the bookmarklet steps.

- **Settings** (`/league/<key>/settings`): the scored categories with Yahoo's
  stat ids, which way each counts (`sort_order`: GA and GAA are lower-better),
  which are display-only (GA, SV, SA beside GAA and SV% in many leagues), the
  roster slots, the season's dates and weeks.
- **A week's scoreboard** (`/scoreboard;week=N`): its dates, who played whom,
  and each team's totals.
- **A day's rosters with that day's stats**
  (`/teams/roster;date=D/players/stats;type=date;date=D`): every team's slot for
  each player on day D, his eligible positions and his stats - `-` for no game.
  One request for the whole league, ~0.7 s.

**Cached for good once it cannot change** - a day before today (US Eastern,
Yahoo's clock), a week whose status is `postevent` - so a season is read from
Yahoo once, whoever asks. Settings are kept for six hours. Public data only:
nothing a private league sends is ever stored here. The cache is optional; with
the table missing everything still works, just slower.

Shapes are kept compact, since a day holds ~200 players and a season ~180 days:
a day is `{date, teams: {number: [[yahooId, slot, [eligible], {statId: value}]]},
names: {number: name}, players: {yahooId: [name, nhlTeam, positions]}}`, with a
value None where Yahoo shows `-`.

Author - Jason Druckenmiller
Created - 9/30/2026
Updated - 9/30/2026
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from db import execute, fetch_one
from yahoo_rosters import HEADERS, TIMEOUT_SECONDS, RosterPageError
from yahoo_transactions import API_BASE, api_error, league_key

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
SETTINGS_TTL = timedelta(hours=6)
# Concurrent reads of a season's days. Polite, and ~4x faster than one at a time.
WORKERS = 4


def today():
    """Yahoo's date - its fantasy day follows US Eastern."""
    return datetime.now(EASTERN).date()


def get_json(path, session=None):
    """`fantasy_content` for an API path. Raises RosterPageError: `private`,
    `not_found`, `unreachable`, `unrecognised`."""
    session = session or requests.Session()
    url = f"{API_BASE}{path}{'&' if '?' in path else '?'}format=json_f"
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RosterPageError("unreachable", f"Could not reach Yahoo: {exc}") from exc
    if response.status_code != 200:
        raise api_error(response)
    try:
        return response.json()["fantasy_content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RosterPageError("unrecognised", "Yahoo sent something other than league data.") from exc


# ------------------------------------------------------------------- the cache

def _cached(key, max_age=None):
    try:
        row = fetch_one("SELECT payload, fetched_at FROM yahoo_public_cache WHERE cache_key = :k",
                        {"k": key})
    except Exception:                             # noqa: BLE001 - the cache is optional
        return None
    if not row:
        return None
    if max_age and datetime.now(timezone.utc) - row["fetched_at"] > max_age:
        return None
    return row["payload"]


def _store(key, payload):
    try:
        execute("INSERT INTO yahoo_public_cache (cache_key, payload) VALUES (:k, CAST(:p AS jsonb))"
                " ON CONFLICT (cache_key) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now()",
                {"k": key, "p": json.dumps(payload)})
    except Exception:                             # noqa: BLE001
        log.warning("Could not cache %s.", key)


def number(value):
    """
    A stat as Yahoo sends it, as a float - None for its dash or nothing. Time
    on ice comes as "MM:SS" and is read as minutes.
    """
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, str) and ":" in value:
        minutes, _, seconds = value.partition(":")
        try:
            return int(minutes) + int(seconds) / 60.0
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _team_number(team_key):
    return str(team_key or "").rsplit(".t.", 1)[-1] or None


# ---------------------------------------------------------------- the readers

def settings(league_id, test=False, session=None):
    """The league's categories, slots, dates and weeks. See the module docstring."""
    key = league_key(league_id, test)
    cached = _cached(f"{key}|settings", SETTINGS_TTL)
    if cached:
        return cached
    content = get_json(f"/league/{key}/settings", session)["league"]
    raw = content.get("settings") or {}
    categories = []
    for wrapper in (raw.get("stat_categories") or {}).get("stats", []):
        stat = wrapper.get("stat") or {}
        if str(stat.get("enabled", "1")) != "1":
            continue
        categories.append({
            "id": str(stat.get("stat_id")),
            "name": stat.get("display_name") or stat.get("abbr") or str(stat.get("stat_id")),
            "full": stat.get("name"),
            "higherBetter": str(stat.get("sort_order", "1")) == "1",
            "displayOnly": str(stat.get("is_only_display_stat", "0")) == "1",
            "goalie": stat.get("position_type") == "G",
        })
    payload = {
        "key": content.get("league_key") or key,
        "name": content.get("name"),
        "season": content.get("season"),
        "scoring": content.get("scoring_type"),
        "startDate": content.get("start_date"),
        "endDate": content.get("end_date"),
        "startWeek": int(content.get("start_week") or 1),
        "currentWeek": int(content.get("current_week") or 1),
        "endWeek": int(content.get("end_week") or 1),
        "categories": categories,
        "slots": {p["roster_position"]["position"]: int(p["roster_position"].get("count") or 0)
                  for p in raw.get("roster_positions") or [] if p.get("roster_position")},
    }
    _store(f"{key}|settings", payload)
    return payload


def scoreboard(key, week, session=None):
    """{week, start, end, status, playoffs, matchups: [{teams, names, totals}]}."""
    cache_key = f"{key}|week|{week}"
    cached = _cached(cache_key)
    if cached:
        return cached
    content = get_json(f"/league/{key}/scoreboard;week={week}", session)["league"]["scoreboard"]
    matchups, start, end, status, playoffs = [], None, None, None, False
    for wrapper in content.get("matchups") or []:
        matchup = wrapper.get("matchup") or {}
        start = start or matchup.get("week_start")
        end = end or matchup.get("week_end")
        status = status or matchup.get("status")
        playoffs = playoffs or str(matchup.get("is_playoffs")) == "1"
        teams, names, totals = [], {}, {}
        for side in matchup.get("teams") or []:
            team = side.get("team") or {}
            num = _team_number(team.get("team_key"))
            teams.append(num)
            names[num] = team.get("name")
            totals[num] = {str(s["stat"]["stat_id"]): number(s["stat"].get("value"))
                           for s in (team.get("team_stats") or {}).get("stats", []) if s.get("stat")}
        matchups.append({"teams": teams, "names": names, "totals": totals})
    payload = {"week": int(content.get("week") or week), "start": start, "end": end,
               "status": status, "playoffs": playoffs, "matchups": matchups}
    if status == "postevent":
        _store(cache_key, payload)
    return payload


def day(key, when, session=None):
    """Every team's roster on a date, with each player's stats that day."""
    when = str(when)
    cache_key = f"{key}|day|{when}"
    cached = _cached(cache_key)
    if cached:
        return cached
    content = get_json(f"/league/{key}/teams/roster;date={when}/players/stats;type=date;date={when}",
                       session)["league"]
    teams, names, players = {}, {}, {}
    for wrapper in content.get("teams") or []:
        team = wrapper.get("team") or {}
        num = str(team.get("team_id") or _team_number(team.get("team_key")))
        names[num] = team.get("name")
        rows = []
        for entry in (team.get("roster") or {}).get("players") or []:
            player = entry.get("player") or {}
            yahoo_id = str(player.get("player_id") or "")
            if not yahoo_id:
                continue
            players[yahoo_id] = [(player.get("name") or {}).get("full") or "",
                                 player.get("editorial_team_abbr") or "",
                                 player.get("display_position") or ""]
            stats = {str(s["stat"]["stat_id"]): number(s["stat"].get("value"))
                     for s in (player.get("player_stats") or {}).get("stats", []) if s.get("stat")}
            rows.append([yahoo_id,
                         (player.get("selected_position") or {}).get("position"),
                         [p.get("position") for p in player.get("eligible_positions") or [] if p.get("position")],
                         stats])
        teams[num] = rows
    payload = {"date": when, "teams": teams, "names": names, "players": players}
    if date.fromisoformat(when) < today():
        _store(cache_key, payload)
    return payload


def season(league_id, test=False):
    """
    ({settings}, [day], [week]) for everything finished so far: each day from
    opening night to yesterday (or the season's end), and each week that has
    begun. Days are read a few at a time; cached ones cost nothing.
    """
    info = settings(league_id, test)
    key = info["key"]
    first = date.fromisoformat(info["startDate"])
    last = min(date.fromisoformat(info["endDate"]), today() - timedelta(days=1))
    dates = [first + timedelta(days=n) for n in range(max(0, (last - first).days + 1))]
    last_week = min(info["currentWeek"], info["endWeek"])
    weeks_wanted = list(range(info["startWeek"], last_week + 1))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        days = list(pool.map(lambda d: day(key, d.isoformat()), dates))
        weeks = list(pool.map(lambda w: scoreboard(key, w), weeks_wanted))
    return info, days, weeks
