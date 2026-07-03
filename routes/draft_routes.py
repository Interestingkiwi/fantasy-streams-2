"""
Routes used by Draft Prep
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/3/2026
"""


from flask import Blueprint, render_template

# Create the Blueprint with a URL prefix
draft_bp = Blueprint('draft', __name__, url_prefix='/draft-prep')

@draft_bp.route('/')
def dashboard():
    """Serves the main draft prep page at /draft-prep/"""
    return render_template('pages/draft-prep.html')

@draft_bp.route('/api/players')
def get_players():
    """Placeholder endpoint to fetch player data for the table later."""
    return {"status": "success", "data": []}
