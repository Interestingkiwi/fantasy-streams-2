"""
Routes used by Draft Prep
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/5/2026
"""

from flask import Blueprint, render_template, jsonify, request
from sqlalchemy import text
from preseason_db_build.db_config import engine
from ranking_utils import calculate_player_ranks

# Create the Blueprint with a URL prefix
draft_bp = Blueprint('draft', __name__, url_prefix='/draft-prep')

def get_stat_mappings(conn):
    """Helper function to dynamically classify stats as Skater or Goalie from the DB."""
    query = text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'final_projections'
          AND column_name NOT IN ('id', 'playerId', 'teamAbbrevs', 'positionCode', 'projectedGames', 'fullName', 'productionTrend', 'peripheralTrend');
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


@draft_bp.route('/api/rank-players', methods=['POST'])
def rank_players():
    try:
        user_settings = request.json
        active_stats = user_settings.get('active_stats', {})
        
        league_mode = user_settings.get('league_mode', 'categories')

        with engine.connect() as conn:
            skater_stats, goalie_stats = get_stat_mappings(conn)

            query = text("SELECT * FROM final_projections")
            result = conn.execute(query)
            players_data = [dict(row._mapping) for row in result]

            ranked_players = calculate_player_ranks(
                players_data=players_data,
                active_stats=active_stats,
                league_mode=league_mode,
                goalie_stat_keywords=goalie_stats
            )

        return jsonify(ranked_players)

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
