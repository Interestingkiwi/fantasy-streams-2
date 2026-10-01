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
Updated - 10/1/2026
"""

import logging
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, render_template, request

import bench_lineups
import bench_points
import daily_value as dv
import game_lines
import opponent_strength as ops
import goalie_planning
import player_card
import player_form
import week_planner
import yahoo_matchup
import yahoo_rosters
import yahoo_league_api
import yahoo_transactions
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

    # A deployment whose nightly job has not run yet has no table at all. The
    # hits/blocks/PIM venue multipliers then fall back to their long-run size,
    # which is what a first week of games would barely move anyway - it must
    # not cost the whole plan.
    try:
        rows = fetch_all('SELECT "homeRoad", hits, "blockedShots", "penaltyMinutes"'
                         ' FROM player_game_stats')
    except Exception:                             # noqa: BLE001
        log.warning("No player_game_stats yet - long-run venue peripherals only.")
        rows = []
    value = ops.peripheral_venue(rows)
    with _peripheral_lock:
        _peripheral_cache.update(at=time.monotonic(), value=value)
    return value


def _team_stats():
    """
    Every `team_stats` window, or [] before the nightly job has created it.

    Same case as `_peripheral_venue`: the table only exists once
    `nightly_update` has run, and without it the week is planned with no
    opponent adjustment and home ice at its long-run size - which `Week`
    reports - rather than not at all.
    """
    try:
        return fetch_all('SELECT * FROM team_stats')
    except Exception:                             # noqa: BLE001
        log.warning("No team_stats yet - no opponent adjustment, long-run home ice.")
        return []


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
        transactions_test_url=yahoo_transactions.TEST_PAGE_URL,
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


@standalone_bp.route('/api/transactions/scrape', methods=['POST'])
def scrape_transactions():
    """
    Every add, drop and trade in a league this season, from Yahoo's public
    read-only API (see `yahoo_transactions`). Body: league_id.

    Public leagues only. A private one answers 403 `private` with the
    `yahooUrl` of its Transactions page, where the bookmarklet reads every
    page in the user's own browser and posts them to `/api/transactions/parse`.
    With ROSTER_SCRAPE_TEST on, the completed 2025-26 test league is read.
    """
    test = current_app.config.get("ROSTER_SCRAPE_TEST", False)
    page_url = None
    try:
        body = request.get_json(silent=True) or {}
        page_url = yahoo_transactions.page_url(body.get('league_id'), test=test)
        league, raw = yahoo_transactions.fetch_api(body.get('league_id'), test=test)
        return jsonify({"status": "success", "source": "api", "test": test,
                        **yahoo_transactions.from_api(league, raw)})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc, yahoo_url=page_url)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Transactions scrape failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/bench', methods=['POST'])
def bench():
    """
    Bench points for the season so far: every benched player who played, and
    the swaps that would have won or tied a category (see `bench_points`).
    Body: league_id.

    Public leagues only, from Yahoo's public API through `yahoo_league_api`,
    whose cache makes every finished day a one-time read. A private league
    answers 403 `private`; its day-by-day rosters are not read yet.
    """
    test = current_app.config.get("ROSTER_SCRAPE_TEST", False)
    try:
        body = request.get_json(silent=True) or {}
        info, days, weeks = yahoo_league_api.season(body.get('league_id'), test=test)
        return jsonify({"status": "success", "source": "api", "test": test,
                        "league": {"name": info.get("name"), "season": info.get("season"),
                                   "currentWeek": info.get("currentWeek")},
                        "asOf": days[-1]["date"] if days else None,
                        **bench_points.summarise(info, days, weeks)})
    except yahoo_rosters.RosterPageError as exc:
        if exc.code == 'private':
            exc = yahoo_rosters.RosterPageError(
                'private', "This league is private, so its lineups are read through the bookmarklet: "
                           "use Update from Yahoo at the top of the page.")
        return _roster_error(exc)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Bench points failed.")
        return _error(str(exc), 500)


MAX_BENCH_DATES = 300
MAX_BENCH_WEEKS = 40


def _game_pool():
    """
    Everyone a Yahoo lineup could name: the projected pool, plus anyone who has
    played a game this data covers but is not projected (a call-up, a rookie).
    """
    pool = fetch_all('SELECT "playerId", "fullName", "teamAbbrevs", "positionCode" FROM final_projections')
    seen = {row["playerId"] for row in pool}
    try:
        extra = fetch_all(
            'SELECT DISTINCT ON ("playerId") "playerId", "fullName", "teamAbbrev" AS "teamAbbrevs",'
            ' COALESCE("positionCode", \'G\') AS "positionCode"'
            ' FROM player_game_stats ORDER BY "playerId", "gameDate" DESC')
    except Exception:                             # noqa: BLE001 - no games scraped yet
        extra = []
    return pool + [row for row in extra if row["playerId"] not in seen]


@standalone_bp.route('/api/bench/lineups', methods=['POST'])
def bench_from_lineups():
    """
    Bench points for a private league, from the lineups the bookmarklet read
    off its Starting Rosters pages and our own game stats (see `bench_lineups`).

    Body: lineups ({dates: {date: {team: [[yahooId, slot]]}}, players: {yahooId:
    [name, 'TEAM - POS']}, pairs: {week: [[team, team]]}}), weeks ([{week,
    start, end}]), categories and league_mode as the week plan takes them, and
    names ({team number: name}). The answer is the shape `/api/bench` gives.
    """
    try:
        body = request.get_json(silent=True) or {}
        lineups = body.get('lineups') or {}
        dates = lineups.get('dates')
        if not isinstance(dates, dict) or not dates:
            raise BadRequest("No lineups yet - Update from Yahoo reads them.")
        if len(dates) > MAX_BENCH_DATES:
            raise BadRequest("That is more days than a season has.")
        for when, teams in dates.items():
            date.fromisoformat(str(when))
            if not isinstance(teams, dict):
                raise BadRequest("Lineups are {team: [[yahooId, slot]]} per date.")
        weeks = []
        for week in (body.get('weeks') or [])[:MAX_BENCH_WEEKS]:
            weeks.append({"week": int(week["week"]), "start": date.fromisoformat(week["start"]).isoformat(),
                          "end": date.fromisoformat(week["end"]).isoformat()})

        codes, _unmapped = week_planner.categories_from_columns(body.get('categories'))
        points = body.get('league_mode') == 'points'
        info, unsupported = bench_lineups.league_info(codes, points=points)
        players = {str(k): v for k, v in (lineups.get('players') or {}).items()
                   if isinstance(v, list) and len(v) == 2}
        try:
            aliases = {r["alias_name"]: r["player_id"]
                       for r in fetch_all("SELECT player_id, alias_name FROM player_aliases")}
        except Exception:                         # noqa: BLE001 - the table is optional
            aliases = {}
        ids = bench_lineups.match_players(players, _game_pool(), aliases)
        first, last = min(dates), max(dates)
        try:
            rows = fetch_all('SELECT * FROM player_game_stats WHERE "gameDate" BETWEEN :a AND :b'
                             ' AND "playerId" = ANY(:ids)',
                             {"a": first, "b": last, "ids": list(set(ids.values()))})
        except Exception:                         # noqa: BLE001 - no games scraped yet
            rows = []
        lines = {(r["playerId"], r["gameDate"]): bench_lineups.game_line(r) for r in rows}

        days = bench_lineups.build_days({**lineups, "players": players}, ids, lines)
        built = bench_lineups.build_weeks(weeks, lineups, ids, lines,
                                          yahoo_league_api.today().isoformat(), bench_points.NOT_STARTING)
        result = bench_points.summarise(info, days, built)
        names = {str(k): str(v) for k, v in (body.get('names') or {}).items()}
        result["teams"] = {**result["teams"], **names}
        # [name, NHL team, positions], as the public path gives them
        result["players"] = {}
        for yahoo_id, (name, info_text) in players.items():
            team, positions = bench_lineups.split_info(info_text)
            result["players"][yahoo_id] = [name, team, ",".join(positions)]
        return jsonify({"status": "success", "source": "browser", "asOf": last,
                        "unsupported": unsupported,
                        "unmatched": sorted(players[k][0] for k in players if k not in ids),
                        **result})
    except BadRequest as exc:
        return _error(str(exc), 400)
    except (KeyError, TypeError, ValueError) as exc:
        return _error(f"Those lineups could not be read: {exc}", 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Bench points from lineups failed.")
        return _error(str(exc), 500)


@standalone_bp.route('/api/transactions/parse', methods=['POST'])
def parse_transactions():
    """
    The same, from the Transactions page tables the bookmarklet read in the
    user's browser. Body: html (every page's table, newest first), url, and
    timeZone - the browser's, which the page's times are taken to be in.
    Parsed and discarded; nothing in the HTML is run or stored.
    """
    try:
        if (request.content_length or 0) > MAX_ROSTER_HTML_BYTES:
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "That is far too much to be a league's transactions.")
        body = request.get_json(silent=True) or {}
        if not _is_yahoo_url(body.get('url')):
            raise yahoo_rosters.RosterPageError(
                "unrecognised", "Transactions can only be read from a Yahoo Fantasy page.")
        result = yahoo_transactions.parse_pages(str(body.get('html') or ''),
                                                body.get('timeZone'), url=body.get('url'))
        return jsonify({"status": "success", "source": "browser", **result})
    except yahoo_rosters.RosterPageError as exc:
        return _roster_error(exc)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Transactions parse failed.")
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
        team_stats=_team_stats(), peripheral=_peripheral_venue(),
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
    the free-agent drops use), `form` (`player_form`: PP share, trends,
    home/road) and `line` (`game_lines.latest`: his line and PP unit) - all for
    the roster view on League Home's Lineups tab.
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
        lines = game_lines.latest(ids, form_season)
        for side in sides:
            for player in side.get('players', []):
                key = str(player['playerId'])
                player['seasonRank'] = (season.get(key) or {}).get('rank')
                player['form'] = forms.get(key)
                player['line'] = lines.get(key)
        result['formSeason'] = _season_label(form_season)
        return jsonify({"status": "success", **result})
    except BadRequest as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone week plan failed.")
        return _error(str(exc), 500)


def _injuries():
    """
    {playerId: {status, detail, returnDate}} from the preseason injury scrape.

    Shown as a badge and offered as a filter, never applied to a projection -
    `apply_injury_adjustments` has already done that. The feed goes stale (see
    *The projection pipeline*), so the page says how old it is rather than
    presenting it as today's news.
    """
    try:
        rows = fetch_all('SELECT "playerId", "injuryStatus", "injuryDetails", "injuryDate"'
                         ' FROM current_injuries')
    except Exception:                             # noqa: BLE001 - the table is optional
        return {}, None
    found, newest = {}, None
    for row in rows:
        player_id = row.get("playerId")
        if player_id is None:
            continue
        details = str(row.get("injuryDetails") or "")
        found[str(int(player_id))] = {
            "status": row.get("injuryStatus"),
            "type": _between(details, "'type': '", "'"),
            "returnDate": _between(details, "'returnDate': '", "'"),
        }
        seen = str(row.get("injuryDate") or "")[:10]
        newest = max(newest, seen) if newest else seen
    return found, newest


def _between(text_value, prefix, suffix):
    start = text_value.find(prefix)
    if start < 0:
        return None
    start += len(prefix)
    end = text_value.find(suffix, start)
    return text_value[start:end] if end > start else None


# The counting stats a week of goaltending is judged on. GAA and SVpct are
# worked out from them, never summed.
GOALIE_STATS = ['W', 'GA', 'SA', 'SV', 'SHO']


def _goalie_totals(days, per_start):
    """
    (totals, starts) still to come from a side's goalie seats.

    Each seat carries the odds he starts, so a projected total is the
    per-start line times those odds - the same arithmetic the lineups use.
    """
    totals, starts = {}, []
    for day in days or []:
        for seat in day.get('slots', []):
            player = seat.get('player')
            if seat.get('slot') != 'G' or not player:
                continue
            line = per_start(player['playerId'], day['date'])
            if line is None:
                continue
            odds = line['startProbability']
            starts.append({**line, 'playerId': player['playerId'],
                           'fullName': player['fullName'],
                           'teamAbbrevs': player.get('teamAbbrevs')})
            totals = goalie_planning.add(totals, {c: _num(line['perStart'].get(c)) * odds
                                                  for c in GOALIE_STATS})
            # Minutes a start really lasts on average, not a flat hour
            totals['minutes'] = totals.get('minutes', 0.0) + goalie_planning.projected_minutes(odds)
    return totals, starts


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _side_week(so_far, days, per_start):
    """One side's goaltending: the week so far, what is left, and the rates."""
    entered = {c: _num((so_far or {}).get(c)) for c in GOALIE_STATS}
    minutes, source = goalie_planning.minutes_played(
        entered.get('GA'), (so_far or {}).get('GAA'), (so_far or {}).get('starts'))
    remaining, starts = _goalie_totals(days, per_start)
    remaining_minutes = remaining.pop('minutes', 0.0)

    projected = goalie_planning.add(entered, remaining)
    return {
        'soFar': {**entered, **goalie_planning.rates(entered, minutes)},
        'remaining': {**remaining, **goalie_planning.rates(remaining, remaining_minutes)},
        'projected': {**projected,
                      **goalie_planning.rates(projected, minutes + remaining_minutes)},
        'minutes': round(minutes, 1),
        'minutesFrom': source,
        'projectedMinutes': round(minutes + remaining_minutes, 1),
        'starts': starts,
    }


@standalone_bp.route('/api/goalies', methods=['POST'])
def goalie_planning_view():
    """
    Goalie planning: where both sides' goaltending stands, and what one more
    start would risk.

    Body as `_request_plan`, plus `goalie_stats` ({mine, theirs} each
    {W, GA, SA, SV, SHO, GAA, starts} for the week so far - what the Matchup
    scrape read off Yahoo, or what was typed in) and optionally `extra`
    ({playerId, date}), the start being considered.

    GA, SA and SV are asked for whether or not the league scores them: GAA and
    save percentage are built from them, so the page needs them even in a
    league that scores neither.
    """
    try:
        body = request.get_json(silent=True) or {}
        week, rosters, _unmapped = _request_plan(body)
        # Every goalie the week could start: the roster, plus anyone a planned
        # move adds to it
        roster_ids = list(rosters['roster']) + [m['add'] for m in rosters['moves']]
        plan = week_planner.plan_week(None, rosters.pop('roster'), None, None, None, None,
                                      week=week, lineups=_lineups(body.get('lineups')),
                                      **rosters)

        # A second Week over the goalie counting stats, because the league's
        # own categories may not include GA or SA and this cannot be read off
        # a plan that never projected them. Same pool, same adjustments.
        goalie_week = week_planner.Week(
            fetch_all('SELECT * FROM final_projections'), GOALIE_STATS, {'G': 2},
            week.dates, week.schedule, team_stats=_team_stats(),
            peripheral=_peripheral_venue())

        def per_start(player_id, day):
            player = goalie_week.by_id.get(str(player_id))
            return goalie_week.per_start(player, day) if player else None

        entered = body.get('goalie_stats') or {}
        mine = _side_week(entered.get('mine'), plan.get('days'), per_start)
        theirs = _side_week(entered.get('theirs'), (plan.get('opponent') or {}).get('days'),
                            per_start)

        result = {"status": "success", "mine": mine, "theirs": theirs,
                  "adjusted": week.adjusted, "homeIce": bool(goalie_week.venue),
                  "dates": week.dates,
                  "goalies": _roster_goalies(goalie_week, roster_ids, plan)}

        extra = body.get('extra') or {}
        if extra.get('playerId') and extra.get('date'):
            result['extra'] = _extra_start(goalie_week, mine, theirs, extra)
        return jsonify(result)
    except BadRequest as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone goalie planning failed.")
        return _error(str(exc), 500)


def _roster_goalies(goalie_week, roster_ids, plan):
    """Your goalies and the nights each could start, for the picker."""
    seated = {(str(s['player']['playerId']), day['date'])
              for day in plan.get('days', []) for s in day['slots']
              if s.get('slot') == 'G' and s.get('player')}
    goalies = []
    for player_id in roster_ids:
        player = goalie_week.by_id.get(str(player_id))
        if player is None or 'G' not in str(player.get('positionCode') or ''):
            continue
        nights = []
        for day in goalie_week.dates:
            line = goalie_week.per_start(player, day)
            if line:
                nights.append({'date': day, 'opponent': line['opponent'],
                               'home': line['home'],
                               'startProbability': round(line['startProbability'], 3),
                               'alreadyStarting': (str(player_id), day) in seated})
        if nights:
            goalies.append({'playerId': player['playerId'], 'fullName': player['fullName'],
                            'teamAbbrevs': player.get('teamAbbrevs'), 'nights': nights})
    return goalies


def _extra_start(goalie_week, mine, theirs, extra):
    """
    One more start, outcome by outcome: what it does to your ratios, how
    likely each outcome is, and the line that would cost you each category.
    """
    player = goalie_week.by_id.get(str(extra['playerId']))
    if player is None:
        raise BadRequest("That goalie is not in the projections.")
    line = goalie_week.per_start(player, str(extra['date']))
    if line is None:
        raise BadRequest("That goalie's team does not play that night.")

    per_start = line['perStart']
    shots = _num(per_start.get('SA'))
    goals = _num(per_start.get('GA'))
    save_pct = (shots - goals) / shots if shots > 0 else 0.9

    # The start is judged on top of everything else already projected, so the
    # question is the honest one: one more start than the plan already has.
    base = {c: _num(mine['projected'].get(c)) for c in GOALIE_STATS}
    minutes = mine['projectedMinutes']
    target = {'GAA': theirs['projected'].get('GAA'), 'SVpct': theirs['projected'].get('SVpct')}

    rows = []
    for outcome in goalie_planning.outcomes(shots, save_pct):
        after, after_minutes = goalie_planning.apply_start(
            base, minutes, outcome, win=_num(per_start.get('W')),
            shutout=_num(per_start.get('SHO')) if outcome['goals'] else 1.0)
        after_rates = goalie_planning.rates(after, after_minutes)
        rows.append({
            **outcome,
            'after': {**{c: round(_num(after.get(c)), 2) for c in GOALIE_STATS},
                      'GAA': _round(after_rates['GAA']), 'SVpct': _round(after_rates['SVpct'], 4)},
            'keepsGAA': _beats(after_rates['GAA'], target['GAA'], lower_is_better=True),
            'keepsSVpct': _beats(after_rates['SVpct'], target['SVpct'], lower_is_better=False),
        })

    limits = goalie_planning.worst_start(base, minutes, shots, target)
    for category, limit in limits.items():
        limit['chance'] = round(goalie_planning.chance_of_at_most(
            shots, save_pct, limit.get('maxGoals')), 4)

    return {
        'playerId': player['playerId'],
        'fullName': player['fullName'],
        'date': line['date'],
        'opponent': line['opponent'],
        'home': line['home'],
        'startProbability': round(line['startProbability'], 3),
        'expected': {'shots': round(shots, 1), 'goals': round(goals, 2),
                     'savePct': round(save_pct, 4),
                     'wins': _round(_num(per_start.get('W')), 3)},
        'without': {'GAA': _round(mine['projected'].get('GAA')),
                    'SVpct': _round(mine['projected'].get('SVpct'), 4)},
        'target': {'GAA': _round(target['GAA']), 'SVpct': _round(target['SVpct'], 4)},
        'outcomes': rows,
        'limits': limits,
    }


def _beats(value, target, lower_is_better):
    if value is None or target is None:
        return None
    return value <= target if lower_is_better else value >= target


def _round(value, places=3):
    return None if value is None else round(value, places)


@standalone_bp.route('/api/free-agents/pool', methods=['POST'])
def free_agent_pool():
    """
    Every free agent in the league, as player lines for the Free Agents table.

    Body as `_request_plan`, plus rostered (every playerId on any team) and
    optionally next_start / next_end. Each player comes back with his games
    this week and next, his per-game line and its shading, the draft board's
    rank, his form, his line and PP unit and any injury on file - the same
    line the Lineups roster view shows, so one table renders both.
    """
    try:
        body = request.get_json(silent=True) or {}
        week, rosters, _unmapped = _request_plan(body)
        rostered = set(map(str, body.get('rostered') or []))
        rostered |= set(map(str, rosters['roster'])) | set(map(str, rosters['opponent']))

        next_games = {}
        if body.get('next_start') and body.get('next_end'):
            try:
                next_games = week_planner.games_by_team(
                    week.schedule, _dates_between(str(body['next_start']), str(body['next_end'])))
            except ValueError:
                next_games = {}

        players = week_planner.available(week, rostered, next_games)
        season = _season_values(week, body)
        forms, form_season = player_form.forms([p['playerId'] for p in players],
                                               week.points or week.flat)
        lines = game_lines.latest([p['playerId'] for p in players], form_season)
        injuries, injury_date = _injuries()
        for player in players:
            key = str(player['playerId'])
            player['seasonRank'] = (season.get(key) or {}).get('rank')
            player['form'] = forms.get(key)
            player['line'] = lines.get(key)
            if key in injuries:
                player['injury'] = injuries[key]

        return jsonify({"status": "success", "players": players,
                        "formSeason": _season_label(form_season),
                        "injuryDate": injury_date,
                        "categories": week.categories,
                        "dates": week.dates})
    except BadRequest as exc:
        return _error(str(exc), 400)
    except Exception as exc:                      # noqa: BLE001
        log.exception("Standalone free agent pool failed.")
        return _error(str(exc), 500)


def _names(player_ids):
    """{playerId: name} for players in `player_game_stats` - linemates, mostly."""
    ids = sorted({int(i) for i in player_ids})
    if not ids:
        return {}
    rows = fetch_all('SELECT DISTINCT ON ("playerId") "playerId", "fullName" FROM player_game_stats '
                     'WHERE "playerId" = ANY(:ids) ORDER BY "playerId", "gameDate" DESC', {'ids': ids})
    return {r['playerId']: r['fullName'] for r in rows}


def _scraped_through():
    """The last night in `player_game_stats`, or None before the first scrape."""
    try:
        return fetch_all('SELECT max("gameDate") AS through FROM player_game_stats')[0]['through']
    except Exception:                             # noqa: BLE001 - no table yet
        return None


@standalone_bp.route('/api/player/<int:player_id>')
def player_detail(player_id):
    """
    One player's card for League Home's player modal (`player_card`): his
    season so far and his last 20, 10 and 5 games, the game log, his line and
    power-play unit or a goalie's starts, and his team's games in a week.

    Query: start and end (YYYY-MM-DD), the week whose games to show; without
    them the card has no schedule. Needs no league - it is the same card in
    every league, and the page orders the stats by the league's categories.
    """
    try:
        start, end = request.args.get('start'), request.args.get('end')
        if start or end:
            try:
                _dates_between(str(start), str(end))
            except ValueError as exc:
                return _error(f"Bad week: {exc}", 400)

        projection = fetch_all('SELECT * FROM final_projections WHERE "playerId" = :id',
                               {'id': player_id})
        games, season = player_form.load([player_id], columns=None)
        games = games.get(str(player_id), [])
        if not projection and not games:
            return _error("No player with that id.", 404)

        line_rows = game_lines.recent([player_id], season, games=player_card.LINE_HISTORY)
        line_rows = line_rows.get(str(player_id), [])
        mates = {p for r in line_rows for p in [*(r.get('lineMates') or []), *(r.get('ppMates') or []),
                                                 *(m[0] for m in r.get('mates') or [])]}
        schedule = [(r["gameDate"], r["homeTeam"], r["awayTeam"]) for r in fetch_all(
            'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule')]
        injuries, injury_date = _injuries()

        card = player_card.card(
            player_id, projection[0] if projection else None, games, line_rows, _names(mates),
            schedule, _team_stats(), date.today(), start=start, end=end,
            injury=injuries.get(str(player_id)), through=_scraped_through())
        return jsonify({"status": "success", "season": _season_label(season),
                        "injuryDate": injury_date, **card})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Player card failed.")
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
