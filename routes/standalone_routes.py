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
Updated - 9/16/2026
"""

import logging
import threading
import time
from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request

import daily_value as dv
import opponent_strength as ops
import week_planner
from db import fetch_all
from lineup_utils import GENERIC_SLOTS, NON_STARTING_SLOTS
from ranking_utils import YAHOO_POSITIONS

log = logging.getLogger(__name__)

standalone_bp = Blueprint('standalone', __name__, url_prefix='/standalone')

# Slots the page may send. Anything else is dropped rather than rejected, so
# a stale localStorage key cannot break the page.
ALLOWED_SLOTS = set(YAHOO_POSITIONS) | set(GENERIC_SLOTS) | set(NON_STARTING_SLOTS)
MAX_SLOT_COUNT = 20
MAX_ROSTER = 60
MAX_PLAN_DAYS = 14

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
    return render_template('pages/standalone.html')


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


@standalone_bp.route('/api/week', methods=['POST'])
def plan_week():
    """
    The best lineup for each night of a window, for a roster sent by the page.

    Body: roster (playerIds), out (playerIds), league_mode ('categories' |
    'points'), categories (draft-prep column names or Yahoo codes), points
    ({column or code: points}), pim_positive, slots ({slot: count}), start,
    end.
    """
    try:
        body = request.get_json(silent=True) or {}

        roster = list(body.get('roster') or [])[:MAX_ROSTER]
        if not roster:
            return _error("Add at least one player to the roster.", 400)

        slots = _clean_slots(body.get('slots'))
        if not any(n for s, n in slots.items() if s not in NON_STARTING_SLOTS):
            return _error("Set at least one starting slot.", 400)

        try:
            dates = _dates_between(str(body.get('start')), str(body.get('end')))
        except ValueError as exc:
            return _error(str(exc), 400)

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
            categories = list(points)
            unmapped = []
            if not categories:
                return _error("Give at least one stat a points value.", 400)
        else:
            categories, unmapped = week_planner.categories_from_columns(
                body.get('categories'))
            if not categories:
                return _error("Choose at least one scoring category.", 400)

        pool = fetch_all('SELECT * FROM final_projections')
        schedule = [(r["gameDate"], r["homeTeam"], r["awayTeam"]) for r in fetch_all(
            'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule')]
        if not schedule:
            return _error("No NHL schedule loaded. Run the preseason pipeline.", 404)
        team_stats = fetch_all('SELECT * FROM team_stats')

        result = week_planner.plan_week(
            pool, roster, categories, slots, dates, schedule,
            team_stats=team_stats, peripheral=_peripheral_venue(),
            points=points, pim_positive=bool(body.get('pim_positive')),
            out=body.get('out') or [])
        result['unmappedCategories'] = unmapped

        return jsonify({"status": "success", **result})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone week plan failed.")
        return _error(str(exc), 500)
