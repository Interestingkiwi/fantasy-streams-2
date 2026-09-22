"""
Routes for Standalone mode.

Everything a synced league would supply - categories, starting slots, the
roster - is typed in on the page and kept in the browser's localStorage, then
posted with each request. So nothing here reads the session, a per-league
table or Yahoo: standalone mode works signed out, and keeps working while
Yahoo's Fantasy API is gated.

The maths lives in `week_planner`; this module only loads rows and validates
what the page sends.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/21/2026
"""

import logging
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, render_template, request

import daily_value as dv
import opponent_strength as ops
import player_form
import week_planner
import yahoo_matchup
import yahoo_rosters
from db import engine, fetch_all
from lineup_utils import GENERIC_SLOTS, NON_STARTING_SLOTS
from ranking_utils import YAHOO_POSITIONS, calculate_player_ranks
from routes.draft_routes import get_stat_mappings

log = logging.getLogger(__name__)

standalone_bp = Blueprint('standalone', __name__, url_prefix='/standalone')

# Slots the page may send. Anything else is dropped rather than rejected, so
# a stale localStorage key cannot break the page.
ALLOWED_SLOTS = set(YAHOO_POSITIONS) | set(GENERIC_SLOTS) | set(NON_STARTING_SLOTS)
MAX_SLOT_COUNT = 20
MAX_ROSTER = 60
# A combined week runs two weeks (the All-Star week is Feb 1-14), and a league
# can merge further in the weeks editor; this only stops a runaway request.
MAX_PLAN_DAYS = 35

# The home/road effect on hits, blocks and PIM is measured over every row of
# `player_game_stats` - ~50k rows and ~200ms, the one slow read in a request
# that is otherwise ~40ms. It only changes when the nightly job runs, so an
# hour in memory is plenty fresh.
PERIPHERAL_TTL_SECONDS = 3600
_peripheral_cache = {'at': 0.0, 'value': None}
_peripheral_lock = threading.Lock()


def _error(message, status):
    return jsonify({"status": "error", "message": message}), status


def _peripheral_venue():
    with _peripheral_lock:
        if (_peripheral_cache['value'] is not None
                and time.monotonic() - _peripheral_cache['at'] < PERIPHERAL_TTL_SECONDS):
            return _peripheral_cache['value']

    rows = fetch_all('SELECT "homeRoad", hits, "blockedShots", "penaltyMinutes"'
                     ' FROM player_game_stats')
    value = ops.peripheral_venue(rows)
    with _peripheral_lock:
        _peripheral_cache.update(at=time.monotonic(), value=value)
    return value


def _clean_slots(raw):
    """{slot: int} limited to known slots and sane counts."""
    slots = {}
    for slot, count in (raw or {}).items():
        if slot not in ALLOWED_SLOTS:
            continue
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= MAX_SLOT_COUNT:
            slots[slot] = n
    return slots


def _dates_between(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("start must not be after end.")
    if (last - first).days >= MAX_PLAN_DAYS:
        raise ValueError(f"A plan covers at most {MAX_PLAN_DAYS} days.")
    return [(first + timedelta(days=n)).isoformat()
            for n in range((last - first).days + 1)]


@standalone_bp.route('/')
def page():
    """The standalone lineup planner. No sign-in required."""
    test = current_app.config.get("ROSTER_SCRAPE_TEST", False)
    return render_template(
        'pages/standalone.html',
        roster_scrape_test=test,
        roster_test_league=yahoo_rosters.TEST_LEAGUE_ID,
        roster_test_url=yahoo_rosters.TEST_ROSTERS_URL,
        matchup_test_url=yahoo_matchup.TEST_MATCHUP_URL,
    )


# Yahoo's Starting Rosters page is ~1.2 MB for twelve teams; anything far
# larger posted to the parse route is not that page.
MAX_ROSTER_HTML_BYTES = 6 * 1024 * 1024

ROSTER_ERROR_STATUS = {
    'invalid_id': 400,
    'not_found': 404,
    'private': 403,
    'unrecognised': 422,
    'unreachable': 502,
}


def _roster_error(exc, yahoo_url=None):
    body = {"status": "error", "code": exc.code, "message": str(exc)}
    if yahoo_url:
        body["yahooUrl"] = yahoo_url
    return jsonify(body), ROSTER_ERROR_STATUS.get(exc.code, 400)


def _matched_rosters(html):
    """Parse a Starting Rosters page and match its players to projections."""
    parsed = yahoo_rosters.parse(html)
    pool = fetch_all('SELECT "playerId", "fullName", "teamAbbrevs", "positionCode"'
                     ' FROM final_projections')
    try:
        aliases = {r["alias_name"]: r["player_id"]
                   for r in fetch_all("SELECT player_id, alias_name FROM player_aliases")}
    except Exception:                             # noqa: BLE001 - the table is optional
        aliases = {}
    return yahoo_rosters.match(parsed, pool, aliases, lookup=yahoo_rosters.yahoo_details)


@standalone_bp.route('/api/rosters/scrape', methods=['POST'])
def scrape_rosters():
    """
    Every team's roster from a league's Starting Rosters page, fetched here.

    Body: league_id (the number, or a pasted league URL). Works for leagues
    whose rosters are public. A private league answers 403 with code
    `private` and the `yahooUrl` to open, so the page can switch to the
    bookmarklet, which reads the page in the user's own signed-in browser and
    posts it to `/api/rosters/parse`.

    With ROSTER_SCRAPE_TEST on (the default outside production) the League ID
    is ignored and a completed public 2025-26 league is read instead.
    """
    test = current_app.config.get("ROSTER_SCRAPE_TEST", False)
    url = None
    try:
        body = request.get_json(silent=True) or {}
        url = yahoo_rosters.rosters_url(body.get('league_id'), test=test)
        result = _matched_rosters(yahoo_rosters.fetch(url))
        return jsonify({"status": "success", "source": "server", "test": test,
                        "url": url, **result})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc, yahoo_url=url)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Roster scrape failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/rosters/parse', methods=['POST'])
def parse_rosters():
    """
    The same, for a Starting Rosters page the bookmarklet read in the user's
    browser. Body: html, url. Nothing in the HTML is run or stored - it is
    parsed for team names, slots and player names, and discarded.
    """
    try:
        if (request.content_length or 0) > MAX_ROSTER_HTML_BYTES:
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "That page is far too large to be a Yahoo rosters page.")
        body = request.get_json(silent=True) or {}
        host = urlparse(str(body.get('url') or '')).hostname or ''
        if not (host == 'yahoo.com' or host.endswith('.yahoo.com')):
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "Rosters can only be read from a Yahoo Fantasy page.")
        result = _matched_rosters(str(body.get('html') or ''))
        return jsonify({"status": "success", "source": "browser",
                        "url": body.get('url'), **result})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Roster parse failed.")
        return _error(str(exc), 500)


def _is_yahoo_url(url):
    host = urlparse(str(url or '')).hostname or ''
    return host == 'yahoo.com' or host.endswith('.yahoo.com')


def _matchup_columns(parsed):
    """
    The parsed matchup with each team's stats also keyed by projection column,
    which is how the page keys the score so far. Only categories the lineup
    engine knows get a column; rates are marked so the page can show them
    without banking them, since a ratio cannot be added to.
    """
    for team in parsed["teams"]:
        team["columns"] = {}
        for code, value in team["stats"].items():
            column = dv.COUNTING_COLUMNS.get(code) or dv.RATE_COLUMNS.get(code)
            if column:
                team["columns"][column] = value
    parsed["rateColumns"] = sorted(dv.RATE_COLUMNS.values())
    return parsed


@standalone_bp.route('/api/matchup/scrape', methods=['POST'])
def scrape_matchup():
    """
    The score so far from a league's Matchup page, fetched here.

    Body: league_id, week (Yahoo's week number) and team (the Yahoo team
    number the roster scrape recorded). Without a team Yahoo answers with team
    1's matchup, so the page checks the names it gets back. Public leagues
    only; a private one answers 403 `private` with the `yahooUrl` to open for
    the bookmarklet, as the roster scrape does.
    """
    test = current_app.config.get("ROSTER_SCRAPE_TEST", False)
    url = None
    try:
        body = request.get_json(silent=True) or {}
        week = body.get('week')
        team = body.get('team')
        if week not in (None, '') and not str(week).isdigit():
            return _error("week must be a number.", 400)
        if team not in (None, '') and not str(team).isdigit():
            return _error("team must be a Yahoo team number.", 400)
        url = yahoo_matchup.matchup_url(body.get('league_id'), week=week or None,
                                        team=team or None, test=test)
        result = _matchup_columns(yahoo_matchup.parse(yahoo_matchup.fetch(url)))
        return jsonify({"status": "success", "source": "server", "test": test,
                        "url": url, **result})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc, yahoo_url=url)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Matchup scrape failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/matchup/parse', methods=['POST'])
def parse_matchup():
    """
    The same, for a Matchup page the bookmarklet read in the user's browser.
    Body: html, url. Parsed for team names and category totals, and discarded.
    """
    try:
        if (request.content_length or 0) > MAX_ROSTER_HTML_BYTES:
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "That page is far too large to be a Yahoo matchup page.")
        body = request.get_json(silent=True) or {}
        if not _is_yahoo_url(body.get('url')):
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "A matchup can only be read from a Yahoo Fantasy page.")
        result = _matchup_columns(yahoo_matchup.parse(str(body.get('html') or '')))
        return jsonify({"status": "success", "source": "browser",
                        "url": body.get('url'), **result})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Matchup parse failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/setup')
def setup():
    """
    What the page needs to build a league and a roster: the player pool
    (slim, for search) and which projection columns the lineup engine can
    score, keyed the way draft prep stores them.
    """
    try:
        players = fetch_all(
            'SELECT "playerId", "fullName", "teamAbbrevs", "positionCode",'
            ' "eligiblePositions" FROM final_projections ORDER BY "fullName"')
        categories = [
            {"column": column, "code": code,
             "goalie": code in dv.GOALIE_CATEGORIES,
             "rate": code in dv.RATE_COLUMNS}
            for column, code in week_planner.COLUMN_TO_CATEGORY.items()
        ]
        return jsonify({"status": "success", "players": players,
                        "categories": categories})
    except Exception as exc:                      # noqa: BLE001 - repo convention
        log.exception("Standalone setup failed.")
        return _error(str(exc), 500)


def _season_values(week, body):
    """
    {playerId: {value, rank}} from the draft board's own ranking, for judging
    which rostered players are cheap to drop.

    The same call `/draft-prep/api/rank-players` makes, with the league's
    draft-prep roster shape (`draft_slots`, `roster_mode`) and team count
    (`num_teams`) when the page sends them, so a drop suggestion and the draft
    board agree about who is worth what. Without a roster shape the ranking
    falls back to raw value, as it does on the board.
    """
    columns = {code: column for column, code in week_planner.COLUMN_TO_CATEGORY.items()}
    if week.points:
        active = {columns[c]: w for c, w in week.points.items() if c in columns}
        mode, raw_key = 'points', 'fantasy_points_per_game'
    else:
        active = {columns[c]: week.polarity.get(c, 1.0) for c in week.categories if c in columns}
        mode, raw_key = 'categories', 'total_value'
    if not active:
        return {}

    with engine.connect() as conn:
        _skater_stats, goalie_stats = get_stat_mappings(conn)

    try:
        num_teams = int(body.get('num_teams') or 0) or None
    except (TypeError, ValueError):
        num_teams = None
    ranked = calculate_player_ranks(
        [dict(p) for p in week.valued], active, league_mode=mode,
        goalie_stat_keywords=goalie_stats, num_teams=num_teams,
        roster_slots=body.get('draft_slots') or None,
        roster_mode='group' if body.get('roster_mode') == 'group' else 'split',
        rank_mode='roster')

    def value(player):
        found = player.get('value_over_replacement', player.get(raw_key))
        return float(found) if found is not None else 0.0

    ranked = sorted(ranked, key=value, reverse=True)
    return {str(p['playerId']): {'value': value(p), 'rank': rank}
            for rank, p in enumerate(ranked, start=1)}


class BadRequest(ValueError):
    """What the page sent cannot be planned; the message is shown as is."""


def _moves(raw):
    """[{add, drop, date}] with a real date and an add; anything else dropped."""
    moves = []
    for move in raw or []:
        if not isinstance(move, dict) or move.get('add') in (None, ''):
            continue
        try:
            day = date.fromisoformat(str(move.get('date'))).isoformat()
        except ValueError:
            continue
        drop = move.get('drop')
        moves.append({'add': move['add'], 'drop': None if drop in (None, '') else drop,
                      'date': day})
    return moves


def _lineups(raw):
    """
    {date: [playerId or None per seat]} - manual nights, as the page stores
    them. Anything malformed is dropped rather than refused: a stale browser
    entry should cost that night its manual lineup, not the whole plan.
    """
    lineups = {}
    for day, seats in (raw or {}).items() if isinstance(raw, dict) else []:
        try:
            day = date.fromisoformat(str(day)).isoformat()
        except ValueError:
            continue
        if not isinstance(seats, list) or len(seats) > MAX_SLOT_COUNT * 8:
            continue
        lineups[day] = [None if s in (None, '') else str(s) for s in seats]
    return lineups


def _request_plan(body):
    """
    Everything the week plan and the free-agent search share: the prepared
    `Week` and the rosters. Raises BadRequest with a message for the page.

    Body: roster (playerIds), out (playerIds), league_mode ('categories' |
    'points'), categories (draft-prep column names or Yahoo codes), points
    ({column or code: points}), pim_positive, slots ({slot: count}), start,
    end. Optionally opponent and opponent_out (playerIds), which add the
    matchup; banked ({category: {mine, theirs}}), the score so far - keyed by
    column name or Yahoo code, like the categories; and moves
    ([{add, drop, date}]), planned add/drops on your roster.
    """
    roster = list(body.get('roster') or [])[:MAX_ROSTER]
    if not roster:
        raise BadRequest("Add at least one player to the roster.")

    slots = _clean_slots(body.get('slots'))
    if not any(n for s, n in slots.items() if s not in NON_STARTING_SLOTS):
        raise BadRequest("Set at least one starting slot.")

    try:
        dates = _dates_between(str(body.get('start')), str(body.get('end')))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    points = None
    if body.get('league_mode') == 'points':
        points = {}
        for name, value in (body.get('points') or {}).items():
            mapped, _ = week_planner.categories_from_columns([name])
            try:
                weight = float(value)
            except (TypeError, ValueError):
                continue
            if mapped and weight:
                points[mapped[0]] = weight
        categories, unmapped = list(points), []
        if not categories:
            raise BadRequest("Give at least one stat a points value.")
    else:
        categories, unmapped = week_planner.categories_from_columns(body.get('categories'))
        if not categories:
            raise BadRequest("Choose at least one scoring category.")

    schedule = [(r["gameDate"], r["homeTeam"], r["awayTeam"]) for r in fetch_all(
        'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule')]
    if not schedule:
        raise BadRequest("No NHL schedule loaded. Run the preseason pipeline.")

    banked = {}
    for name, entry in (body.get('banked') or {}).items():
        mapped, _ = week_planner.categories_from_columns([name])
        if mapped and isinstance(entry, dict):
            banked[mapped[0]] = entry

    week = week_planner.Week(
        fetch_all('SELECT * FROM final_projections'), categories, slots, dates, schedule,
        team_stats=fetch_all('SELECT * FROM team_stats'), peripheral=_peripheral_venue(),
        points=points, pim_positive=bool(body.get('pim_positive')))

    return week, {
        'roster': roster,
        'out': body.get('out') or [],
        'opponent': list(body.get('opponent') or [])[:MAX_ROSTER],
        'opponent_out': body.get('opponent_out') or [],
        'banked': banked,
        'moves': _moves(body.get('moves')),
    }, unmapped


def _season_label(season):
    return f"{season}-{str(season + 1)[-2:]}" if season else None


@standalone_bp.route('/api/week', methods=['POST'])
def plan_week():
    """
    The best lineup for each night of a window. Body as `_request_plan`, plus
    optionally lineups ({date: [playerId or None per seat]}, nights set by
    hand) and next_start / next_end (the following week, for each player's
    games in it).

    Every player on either side also gets `seasonRank` (the draft board's, as
    the free-agent drops use) and `form` (`player_form`: PP share, trends,
    home/road) - both for the roster view on League Home's Lineups tab.
    """
    try:
        body = request.get_json(silent=True) or {}
        week, rosters, unmapped = _request_plan(body)
        next_dates = []
        if body.get('next_start') and body.get('next_end'):
            try:
                next_dates = _dates_between(str(body['next_start']), str(body['next_end']))
            except ValueError:
                next_dates = []
        result = week_planner.plan_week(None, rosters.pop('roster'), None, None, None, None,
                                        week=week, lineups=_lineups(body.get('lineups')),
                                        next_dates=next_dates, **rosters)
        result['unmappedCategories'] = unmapped

        sides = [result] + ([result['opponent']] if result.get('opponent') else [])
        ids = [p['playerId'] for side in sides for p in side.get('players', [])]
        season = _season_values(week, body)
        forms, form_season = player_form.forms(ids, week.points or week.flat)
        for side in sides:
            for player in side.get('players', []):
                key = str(player['playerId'])
                player['seasonRank'] = (season.get(key) or {}).get('rank')
                player['form'] = forms.get(key)
        result['formSeason'] = _season_label(form_season)
        return jsonify({"status": "success", **result})
    except BadRequest as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone week plan failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/free-agents', methods=['POST'])
def free_agents():
    """
    The adds that help this week's matchup most, each with a suggested drop and
    date, and the gain on every date. Body as `_request_plan`, plus rostered
    (every playerId on any team in the league) and optionally evaluate
    ({add, drop}) to score one pair the user chose instead of searching.
    """
    try:
        body = request.get_json(silent=True) or {}
        week, rosters, _unmapped = _request_plan(body)
        if not week.dates:
            raise BadRequest("No nights left to plan.")
        season = _season_values(week, body)
        rostered = set(map(str, body.get('rostered') or []))
        rostered |= set(map(str, rosters['roster'])) | set(map(str, rosters['opponent']))
        try:
            result = week_planner.free_agents(
                week, rosters['roster'], rostered, out=rosters['out'],
                opponent=rosters['opponent'], opponent_out=rosters['opponent_out'],
                banked=rosters['banked'], moves=rosters['moves'],
                evaluate=body.get('evaluate'), season=season,
                lineups=_lineups(body.get('lineups')))
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return jsonify({"status": "success", **result})
    except BadRequest as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone free agents failed.")
        return _error(str(exc), 500)
