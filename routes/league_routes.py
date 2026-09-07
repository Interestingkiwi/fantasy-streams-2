"""
Routes for the League Database viewer.

Read-only windows onto what the league sync has ingested: settings, teams
and their rosters, the weekly schedule and matchups, transactions, and the
free-agent/waiver pool. First page in MIGRATION Phase 3, and the simplest
end-to-end proof that the per-league tables hold what they should.

Note this is *not* the old repo's `league-database` page - that one is the
sync control panel (trigger an update, watch the progress log), which
belongs with the Phase 2 ETL and has nothing to display until it exists.

Every endpoint is scoped to the league in the session, so a signed-in user
can only read the league they selected.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import logging

from flask import Blueprint, jsonify, redirect, render_template, session, url_for

from db import fetch_all, fetch_one
from yahoo_auth import SESSION_LEAGUE, login_required

log = logging.getLogger(__name__)

league_bp = Blueprint('league', __name__, url_prefix='/league')

# Yahoo player ids are TEXT in `yahoo_players` but INTEGER in every per-league
# table, inherited from the old schema. Casting at the join keeps that in one
# place; reconcile the column types in Phase 2 if it starts to cost anything.
PLAYER_JOIN = 'LEFT JOIN yahoo_players yp ON yp.player_id = {alias}.player_id::text'

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _error(message, status):
    return jsonify({"status": "error", "message": message}), status


def current_league():
    """The league id on the session, or None. Everything here is scoped to it."""
    league_id = session.get(SESSION_LEAGUE)
    return int(league_id) if league_id and str(league_id).isdigit() else None


def _limit(request_args, default=DEFAULT_LIMIT):
    """A sane row cap, so a huge league cannot blow up a page."""
    try:
        value = int(request_args.get('limit', default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


def league_scoped(view):
    """Gate an API on a signed-in session that has picked a league."""
    @login_required
    def wrapper(*args, **kwargs):
        if current_league() is None:
            return _error("No league selected.", 400)
        return view(*args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


@league_bp.route('/')
def database():
    """The viewer page. Needs a league, so bounce home to pick one."""
    if current_league() is None:
        return redirect(url_for('main.home'))
    return render_template('pages/league-database.html')


@league_bp.route('/api/overview')
@league_scoped
def overview():
    """League settings, scoring categories, roster slots and row counts."""
    league_id = current_league()
    try:
        info = {
            row["key"]: row["value"]
            for row in fetch_all(
                "SELECT key, value FROM league_info WHERE league_id = :lid",
                {"lid": league_id},
            )
        }

        counts = fetch_one(
            """
            SELECT (SELECT count(*) FROM teams            WHERE league_id = :lid) AS teams,
                   (SELECT count(*) FROM weeks            WHERE league_id = :lid) AS weeks,
                   (SELECT count(*) FROM matchups         WHERE league_id = :lid) AS matchups,
                   (SELECT count(*) FROM transactions     WHERE league_id = :lid) AS transactions,
                   (SELECT count(*) FROM rosters_tall     WHERE league_id = :lid) AS rostered,
                   (SELECT count(*) FROM free_agents      WHERE league_id = :lid) AS free_agents,
                   (SELECT count(*) FROM waiver_players   WHERE league_id = :lid) AS waivers
            """,
            {"lid": league_id},
        )

        return jsonify({
            "status": "success",
            "league_id": league_id,
            "info": info,
            "counts": counts,
            "scoring": fetch_all(
                "SELECT stat_id, category, scoring_group FROM scoring"
                " WHERE league_id = :lid ORDER BY stat_id",
                {"lid": league_id},
            ),
            "roster_slots": fetch_all(
                "SELECT position, position_count FROM lineup_settings"
                " WHERE league_id = :lid ORDER BY position_id",
                {"lid": league_id},
            ),
        })
    except Exception as exc:                      # noqa: BLE001 - repo convention
        log.exception("Overview failed for league %s.", league_id)
        return _error(str(exc), 500)


@league_bp.route('/api/teams')
@league_scoped
def teams():
    """Every team with how many players it currently has rostered."""
    league_id = current_league()
    try:
        return jsonify({"status": "success", "teams": fetch_all(
            """
            SELECT t.team_id,
                   t.name,
                   t.manager_nickname,
                   (SELECT count(*) FROM rosters_tall rt
                     WHERE rt.league_id = t.league_id
                       AND rt.team_id::text = t.team_id) AS roster_size
            FROM teams t
            WHERE t.league_id = :lid
            ORDER BY t.team_id::int
            """,
            {"lid": league_id},
        )})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Teams failed for league %s.", league_id)
        return _error(str(exc), 500)


@league_bp.route('/api/roster/<team_id>')
@league_scoped
def roster(team_id):
    """One team's players, with names resolved from the Yahoo directory."""
    league_id = current_league()
    if not str(team_id).isdigit():
        return _error("Invalid team id.", 400)

    try:
        return jsonify({"status": "success", "team_id": team_id, "players": fetch_all(
            f"""
            SELECT rt.player_id,
                   yp.player_name,
                   yp.player_team,
                   yp.status AS injury_status,
                   COALESCE(rp.eligible_positions, yp.positions) AS positions
            FROM rosters_tall rt
            {PLAYER_JOIN.format(alias='rt')}
            LEFT JOIN rostered_players rp
                   ON rp.league_id = rt.league_id AND rp.player_id = rt.player_id
            WHERE rt.league_id = :lid AND rt.team_id = :tid
            ORDER BY yp.player_name NULLS LAST
            """,
            {"lid": league_id, "tid": int(team_id)},
        )})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Roster failed for league %s team %s.", league_id, team_id)
        return _error(str(exc), 500)


@league_bp.route('/api/schedule')
@league_scoped
def schedule():
    """Fantasy weeks and the matchups played in each."""
    league_id = current_league()
    try:
        matchups = fetch_all(
            "SELECT week, team1, team2 FROM matchups"
            " WHERE league_id = :lid ORDER BY week, team1",
            {"lid": league_id},
        )
        by_week = {}
        for row in matchups:
            by_week.setdefault(row["week"], []).append(row)

        weeks = fetch_all(
            "SELECT week_num, start_date, end_date FROM weeks"
            " WHERE league_id = :lid ORDER BY week_num",
            {"lid": league_id},
        )
        for week in weeks:
            week["matchups"] = by_week.get(week["week_num"], [])

        return jsonify({"status": "success", "weeks": weeks})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Schedule failed for league %s.", league_id)
        return _error(str(exc), 500)


@league_bp.route('/api/transactions')
@league_scoped
def transactions():
    """Most recent adds/drops first."""
    from flask import request

    league_id = current_league()
    try:
        return jsonify({"status": "success", "transactions": fetch_all(
            "SELECT transaction_date, player_id, player_name, fantasy_team, move_type"
            " FROM transactions WHERE league_id = :lid"
            " ORDER BY transaction_date DESC, player_name"
            " LIMIT :lim",
            {"lid": league_id, "lim": _limit(request.args)},
        )})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Transactions failed for league %s.", league_id)
        return _error(str(exc), 500)


@league_bp.route('/api/player-pool')
@league_scoped
def player_pool():
    """Unrostered players - free agents by default, or the waiver wire."""
    from flask import request

    league_id = current_league()
    table = "waiver_players" if request.args.get('pool') == 'waivers' else "free_agents"

    try:
        return jsonify({"status": "success", "pool": table, "players": fetch_all(
            f"""
            SELECT p.player_id,
                   p.status,
                   yp.player_name,
                   yp.player_team,
                   yp.positions
            FROM {table} p
            {PLAYER_JOIN.format(alias='p')}
            WHERE p.league_id = :lid
            ORDER BY yp.player_name NULLS LAST
            LIMIT :lim
            """,
            {"lid": league_id, "lim": _limit(request.args)},
        )})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Player pool failed for league %s.", league_id)
        return _error(str(exc), 500)
