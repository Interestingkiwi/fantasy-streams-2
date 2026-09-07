"""
Routes for NHL Schedule Insights.

How the NHL schedule falls across fantasy weeks: which teams play most,
which of those games land on nights when the league is largely idle (so a
bench player can actually be started), and how many games each night
carries.

Deliberately self-contained. It reads `nhl_schedule`, which the preseason
pipeline fills, so it works with no synced league and no Yahoo access. If a
league *is* selected and its weeks cover the schedule, those Yahoo weeks
are used instead of derived ones, since a league's matchups are scored
against its own weeks.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import logging

from flask import Blueprint, jsonify, render_template, request, session

from db import fetch_all, fetch_one
from schedule_utils import (
    LIGHT_NIGHT_MAX_GAMES,
    derive_weeks,
    games_per_date,
    light_nights,
    summarise,
    team_game_counts,
)
from yahoo_auth import SESSION_LEAGUE

log = logging.getLogger(__name__)

schedule_bp = Blueprint('schedules', __name__, url_prefix='/schedules')


def _error(message, status):
    return jsonify({"status": "error", "message": message}), status


def _season_span():
    """First and last game date in the schedule, or (None, None) if empty."""
    row = fetch_one('SELECT min("gameDate") AS lo, max("gameDate") AS hi FROM nhl_schedule')
    return (row["lo"], row["hi"]) if row and row["lo"] else (None, None)


def _league_weeks(first_date, last_date):
    """
    The selected league's Yahoo weeks, but only if they actually cover the
    schedule. The imported fixture data is a season behind the current
    schedule, and weeks that do not overlap would produce empty tables
    rather than an obvious error.
    """
    league_id = session.get(SESSION_LEAGUE)
    if not (league_id and str(league_id).isdigit()):
        return None

    weeks = fetch_all(
        "SELECT week_num AS week, start_date AS start, end_date AS \"end\""
        " FROM weeks WHERE league_id = :lid ORDER BY week_num",
        {"lid": int(league_id)},
    )
    if not weeks:
        return None

    overlaps = any(w["start"] <= last_date and w["end"] >= first_date for w in weeks)
    return weeks if overlaps else None


def _rows_between(start, end):
    return [
        (r["gameDate"], r["homeTeam"], r["awayTeam"])
        for r in fetch_all(
            'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule'
            ' WHERE "gameDate" BETWEEN :start AND :end',
            {"start": start, "end": end},
        )
    ]


@schedule_bp.route('/')
def page():
    """The Schedules page. No league required."""
    return render_template('pages/schedules.html')


@schedule_bp.route('/api/weeks')
def weeks():
    """
    Selectable windows: the season as a whole, then each fantasy week.
    Says which source the weeks came from so the page can be honest about it.
    """
    try:
        first_date, last_date = _season_span()
        if not first_date:
            return _error("No NHL schedule loaded. Run the preseason pipeline.", 404)

        league = _league_weeks(first_date, last_date)
        return jsonify({
            "status": "success",
            "source": "league" if league else "derived",
            "season": {"start": first_date, "end": last_date},
            "weeks": league or derive_weeks(first_date, last_date),
        })
    except Exception as exc:                      # noqa: BLE001 - repo convention
        log.exception("Weeks failed.")
        return _error(str(exc), 500)


@schedule_bp.route('/api/team-games')
def team_games():
    """
    Games per team in a window, with the light-night share, plus the nightly
    game counts for a calendar. Defaults to the whole season.
    """
    try:
        first_date, last_date = _season_span()
        if not first_date:
            return _error("No NHL schedule loaded. Run the preseason pipeline.", 404)

        start = request.args.get('start') or first_date
        end = request.args.get('end') or last_date
        if start > end:
            return _error("start must not be after end.", 400)

        rows = _rows_between(start, end)
        teams = team_game_counts(rows)
        per_date = games_per_date(rows)
        light = light_nights(rows)

        return jsonify({
            "status": "success",
            "window": {"start": start, "end": end},
            "lightNightThreshold": LIGHT_NIGHT_MAX_GAMES,
            "teams": teams,
            "summary": summarise(teams),
            "totalGames": len(rows),
            "calendar": [
                {"date": d, "games": per_date[d], "light": d in light}
                for d in sorted(per_date)
            ],
        })
    except Exception as exc:                      # noqa: BLE001
        log.exception("Team games failed.")
        return _error(str(exc), 500)


@schedule_bp.route('/api/team/<team>')
def team_detail(team):
    """Every game for one team in the window, for the expanded row."""
    try:
        first_date, last_date = _season_span()
        if not first_date:
            return _error("No NHL schedule loaded.", 404)

        start = request.args.get('start') or first_date
        end = request.args.get('end') or last_date
        code = team.upper()

        rows = _rows_between(start, end)
        light = light_nights(rows)

        games = [
            {
                "date": game_date,
                "opponent": away if home == code else home,
                "home": home == code,
                "light": game_date in light,
            }
            for game_date, home, away in rows
            if code in (home, away)
        ]
        games.sort(key=lambda g: g["date"])

        return jsonify({"status": "success", "team": code, "games": games})
    except Exception as exc:                      # noqa: BLE001
        log.exception("Team detail failed for %s.", team)
        return _error(str(exc), 500)
