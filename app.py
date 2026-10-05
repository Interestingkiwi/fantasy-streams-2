"""
app.py for Fantasy Streams
Author - Jason Druckenmiller
Created - 7/3/2026
Updated - 10/5/2026
"""


import logging
import os

from flask import Flask

from config import Config, check_config
from schema import init_schema
from routes.main_routes import main_bp
from routes.auth_routes import auth_bp
from routes.draft_routes import draft_bp
from routes.league_routes import league_bp
from routes.schedule_routes import schedule_bp
from routes.standalone_routes import standalone_bp
from routes.account_routes import account_bp
from routes.notification_routes import notification_bp
import move_reminders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)
check_config()

# Ensure admin + per-league tables exist (idempotent; SKIP_SCHEMA_INIT=1 to bypass).
init_schema()

#Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(draft_bp)
app.register_blueprint(league_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(standalone_bp)
app.register_blueprint(account_bp)
app.register_blueprint(notification_bp)

# Move reminders go out from a thread in each web process (each gunicorn
# worker imports this module); an advisory lock lets one send per minute.
# Off outside production unless PUSH_SENDER=1. Under the dev server's
# reloader only the child that serves requests starts it.
if app.config["PUSH_SENDER"] and (not app.config["DEBUG"] or os.environ.get("WERKZEUG_RUN_MAIN")):
    move_reminders.start()

if __name__ == '__main__':
    # Bind IPv4 explicitly, and say so. Werkzeug listens on one address family
    # only, so `localhost` costs a flat ~2s per request on Windows: the client
    # resolves it to ::1 first, waits for that connection to fail, then retries
    # on IPv4. Every request, regardless of payload size - a 1 KB response is
    # as slow as a 1 MB one. Use the printed URL, not localhost.
    host = os.environ.get("DEV_HOST", "127.0.0.1")
    log.info("Serving on http://%s:5000 - use this address, not localhost.", host)
    app.run(debug=app.config["DEBUG"], host=host, port=5000)
