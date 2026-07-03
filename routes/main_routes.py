"""
Routes used for main nav
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 7/3/2026
"""


from flask import Blueprint, render_template

# Create the Blueprint
main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def home():
    """Serves the main welcome/login page."""
    return render_template('index.html')

@main_bp.route('/standalone')
def standalone():
    """Placeholder for your standalone mode."""
    return "Standalone Mode Dashboard coming soon!"

@main_bp.route('/terms')
def terms():
    """Placeholder for Terms of Service."""
    return "Terms of Service coming soon!"
