"""
Routes used for main nav
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 9/6/2026
"""


from datetime import date

from flask import Blueprint, current_app, render_template, session

import yahoo_auth
from yahoo_auth import SESSION_ERROR, SESSION_LEAGUE, SESSION_LEAGUES

# Create the Blueprint
main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def home():
    """
    Serves the main welcome/login page. Renders signed-in or signed-out from
    the session, so a returning user does not flash the login card before JS
    catches up. `auth_error` is one-shot: set by /callback, popped here.
    """
    leagues = session.get(SESSION_LEAGUES, [])
    active_id = session.get(SESSION_LEAGUE)

    return render_template(
        'index.html',
        authenticated=bool(yahoo_auth.current_guid()),
        dev_login=yahoo_auth.is_dev_session(),
        yahoo_configured=yahoo_auth.is_configured(),
        leagues=leagues,
        active_league_id=active_id,
        active_league=next(
            (lg for lg in leagues if lg["league_id"] == active_id), None
        ),
        auth_error=session.pop(SESSION_ERROR, None),
    )

@main_bp.route('/standalone')
def standalone():
    """Placeholder for your standalone mode."""
    return "Standalone Mode Dashboard coming soon!"

@main_bp.route('/terms')
def terms():
    """
    Terms of Service / privacy policy. The login modal links here and users
    must accept before signing in, so the version shown has to be the one
    recorded against them in users.tos_accepted_version.
    """
    return render_template(
        'terms.html',
        terms_updated=current_app.config["TOS_UPDATED"],
        copyright_year=date.today().year,
    )
