"""
Routes used by Draft Prep
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 9/6/2026
"""

from collections import Counter

from flask import Blueprint, render_template, jsonify, request
from db import engine, text
from ranking_utils import calculate_player_ranks

# Create the Blueprint with a URL prefix
draft_bp = Blueprint('draft', __name__, url_prefix='/draft-prep')

# Shared with the Schedules page so the two cannot drift apart.
from schedule_utils import LIGHT_NIGHT_MAX_GAMES  # noqa: E402

def get_stat_mappings(conn):
    """Helper function to dynamically classify stats as Skater or Goalie from the DB."""
    query = text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'final_projections'
          AND column_name NOT IN ('id', 'playerId', 'teamAbbrevs', 'positionCode', 'projectedGames', 'fullName', 'productionTrend', 'peripheralTrend', 'projectionSource', 'onNhlRoster', 'eligiblePositions');
    """)
    result = conn.execute(query)
    stats = [row[0] for row in result]

    if not stats:
        return [], []

    count_selects = ", ".join([f'COUNT("{stat}") AS "{stat}"' for stat in stats])

    # Query for Goalies
    g_query = text(f'SELECT {count_selects} FROM final_projections WHERE "positionCode" = \'G\'')
    g_result = dict(conn.execute(g_query).fetchone()._mapping)
    goalie_stats = [stat for stat in stats if g_result[stat] > 0]

    # Query for Skaters
    s_query = text(f'SELECT {count_selects} FROM final_projections WHERE "positionCode" != \'G\'')
    s_result = dict(conn.execute(s_query).fetchone()._mapping)
    skater_stats = [stat for stat in stats if s_result[stat] > 0]

    strict_goalie_stats = [stat for stat in goalie_stats if stat not in skater_stats]

    return skater_stats, strict_goalie_stats


@draft_bp.route('/')
def dashboard():
    """Serves the main draft prep page at /draft-prep/"""
    return render_template('pages/draft-prep.html')

@draft_bp.route('/api/available-stats')
def get_available_stats():
    """Dynamically fetches stat columns and separates them by Skater vs Goalie."""
    try:
        with engine.connect() as conn:
            skater_stats, goalie_stats = get_stat_mappings(conn)

        return jsonify({
            "status": "success",
            "skater_stats": skater_stats,
            "goalie_stats": goalie_stats
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@draft_bp.route('/api/projections')
def get_projections():
    """Fetches the actual player data and projections."""
    try:
        with engine.connect() as conn:
            query = text("SELECT * FROM final_projections")
            result = conn.execute(query)
            players = [dict(row._mapping) for row in result]

        return jsonify({"status": "success", "data": players})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@draft_bp.route('/api/playoff-schedule', methods=['POST'])
def playoff_schedule():
    """
    Counts each team's games across the selected fantasy playoff weeks.

    Returns games plus light-night games - those falling on a date when under
    a quarter of the league is playing, which is when a manager can actually
    get an extra starter into the lineup.
    """
    try:
        weeks = (request.json or {}).get('weeks', [])
        ranges = [(w.get('start'), w.get('end')) for w in weeks
                  if w.get('start') and w.get('end')]

        if not ranges:
            return jsonify({'status': 'success', 'teams': {}, 'average': 0})

        clauses = []
        params = {}
        for index, (start, end) in enumerate(ranges):
            clauses.append(f'("gameDate" BETWEEN :start{index} AND :end{index})')
            params[f'start{index}'] = start
            params[f'end{index}'] = end
        window = ' OR '.join(clauses)

        with engine.connect() as conn:
            rows = conn.execute(text(f'''
                SELECT "gameDate", "homeTeam", "awayTeam"
                FROM nhl_schedule
                WHERE {window}
            '''), params).fetchall()

        games_on_date = Counter(row[0] for row in rows)
        light_nights = {date for date, count in games_on_date.items()
                        if count < LIGHT_NIGHT_MAX_GAMES}

        teams = {}
        for game_date, home, away in rows:
            for team in (home, away):
                entry = teams.setdefault(team, {'games': 0, 'lightNights': 0})
                entry['games'] += 1
                if game_date in light_nights:
                    entry['lightNights'] += 1

        counts = [entry['games'] for entry in teams.values()]
        average = round(sum(counts) / len(counts), 1) if counts else 0

        return jsonify({
            'status': 'success',
            'teams': teams,
            'average': average,
            'max': max(counts) if counts else 0,
            'min': min(counts) if counts else 0,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@draft_bp.route('/api/rank-players', methods=['POST'])
def rank_players():
    try:
        user_settings = request.json
        active_stats = user_settings.get('active_stats', {})

        league_mode = user_settings.get('league_mode', 'categories')

        # Roster shape drives replacement level; absent, ranking falls back to raw value
        num_teams = user_settings.get('num_teams')
        roster_slots = user_settings.get('roster_slots') or {}
        roster_mode = user_settings.get('roster_mode', 'split')

        # roster | projection | balanced - how hard the board leans on scarcity
        rank_mode = user_settings.get('rank_mode', 'roster')

        with engine.connect() as conn:
            skater_stats, goalie_stats = get_stat_mappings(conn)

            query = text("SELECT * FROM final_projections")
            result = conn.execute(query)
            players_data = [dict(row._mapping) for row in result]

            ranked_players = calculate_player_ranks(
                players_data=players_data,
                active_stats=active_stats,
                league_mode=league_mode,
                goalie_stat_keywords=goalie_stats,
                num_teams=num_teams,
                roster_slots=roster_slots,
                roster_mode=roster_mode,
                rank_mode=rank_mode
            )

        return jsonify(ranked_players)

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
