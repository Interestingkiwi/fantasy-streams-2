"""
Routes used for authorization
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/3/2026
"""


from flask import Blueprint, request, jsonify

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    """Placeholder for your Yahoo OAuth logic."""
    data = request.get_json()
    league_id = data.get('league_id')
    terms_accepted = data.get('terms_accepted')

    # Right now, we will just return a fake auth_url to test the JS loading state
    # Later, this is where your Yahoo API logic will live!
    return jsonify({
        "status": "success",
        "message": f"League {league_id} received.",
        "auth_url": "#yahoo-oauth-flow-placeholder"
    })
