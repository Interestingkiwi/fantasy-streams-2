"""
app.py for Fantasy Streams
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 9/6/2026
"""


import logging

from flask import Flask

from schema import init_schema
from routes.main_routes import main_bp
from routes.auth_routes import auth_bp
from routes.draft_routes import draft_bp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)

# Ensure admin + per-league tables exist (idempotent; SKIP_SCHEMA_INIT=1 to bypass).
init_schema()

#Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(draft_bp)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
