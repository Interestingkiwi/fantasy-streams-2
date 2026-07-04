"""
Routes used by Draft Prep
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/3/2026
"""


from flask import Blueprint, render_template, jsonify
from sqlalchemy import text
from preseason_db_build.db_config import engine

# Create the Blueprint with a URL prefix
draft_bp = Blueprint('draft', __name__, url_prefix='/draft-prep')

@draft_bp.route('/')
def dashboard():
    """Serves the main draft prep page at /draft-prep/"""
    return render_template('pages/draft-prep.html')

@draft_bp.route('/api/available-stats')
def get_available_stats():
    """Dynamically fetches all stat columns available in the database."""
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'final_projections'
                  AND column_name NOT IN ('id', 'playerId', 'player_name', 'team', 'position');
            """)
            result = conn.execute(query)

            # Extract just the column names into a clean list
            stats = [row[0] for row in result]

        return jsonify({"status": "success", "stats": stats})
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
