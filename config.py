"""
App configuration for Fantasy Streams.

One Config class read from the environment (.env in dev, real env vars in
prod). Load it with app.config.from_object("config.Config"); read values
via current_app.config[...] rather than os.environ scattered through the
code.

DATABASE_URL is intentionally not here - db.py owns the database
connection and reads it directly.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"

_DEV_SECRET = "dev-insecure-secret-change-me"


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    APP_ENV = APP_ENV
    DEBUG = _bool("FLASK_DEBUG", not IS_PRODUCTION)
    TESTING = False

    # Session signing. A missing key is fatal in production; dev gets a fixed
    # fallback so sessions survive restarts.
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or (None if IS_PRODUCTION else _DEV_SECRET)

    # Cookie hardening. SameSite=Lax (not Strict) so the session cookie rides
    # along on the top-level redirect back from Yahoo OAuth.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PRODUCTION

    # url_for(..., _external=True) scheme - https in prod, http for localhost.
    PREFERRED_URL_SCHEME = "https" if IS_PRODUCTION else "http"

    # Sessions last a month, so a returning user is not asked to reauthorize
    # Yahoo every browser restart. The refresh token behind it lives longer.
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # Yahoo Fantasy OAuth (Phase 1). Auth simply won't work until these are set.
    YAHOO_CONSUMER_KEY = os.getenv("YAHOO_CONSUMER_KEY")
    YAHOO_CONSUMER_SECRET = os.getenv("YAHOO_CONSUMER_SECRET")

    # Must match the Redirect URI registered in the Yahoo developer console
    # character for character. Unset -> built from the incoming request, which
    # is right for a plain deploy but wrong behind a tunnel or proxy.
    YAHOO_REDIRECT_URI = os.getenv("YAHOO_REDIRECT_URI") or None

    # OAuth scope requested at the consent screen. Empty by default, and that
    # is deliberate: the old repo's working implementation sends no scope and
    # lets the app registration decide, so this matches it. Adding "fspt-w"
    # here did NOT fix the 403 it was meant to, so do not reintroduce it as a
    # guess. Kept configurable only as a diagnostic lever.
    YAHOO_SCOPE = os.getenv("YAHOO_SCOPE", "")

    # Endpoint overrides. Only for pointing the OAuth flow at a local stub in
    # tests; leave unset everywhere else.
    YAHOO_TOKEN_URL = os.getenv("YAHOO_TOKEN_URL") or None
    YAHOO_API_BASE = os.getenv("YAHOO_API_BASE") or None

    # Terms of Service version recorded against a user at login
    # (users.tos_accepted_version). Bump when the terms change, along with
    # TOS_UPDATED below and CURRENT_TERMS_VERSION in templates/index.html.
    TOS_VERSION = int(os.getenv("TOS_VERSION", "1"))

    # When the terms were last revised - shown on /terms. Not today's date:
    # "Last Updated" has to stay put until the text actually changes.
    TOS_UPDATED = "December 12, 2025"

    # Background jobs (Phase 2+). Unset -> league sync runs inline.
    REDIS_URL = os.getenv("REDIS_URL") or None

    # Optional: "<league_id>-<pass>" in the login box bypasses Yahoo in dev.
    DEV_BACKDOOR_PASS = os.getenv("DEV_BACKDOOR_PASS") or None


def check_config(cfg=Config):
    """Fail fast on anything that must be set; warn on the rest. Call at startup."""
    if not cfg.SECRET_KEY:
        raise RuntimeError(
            "FLASK_SECRET_KEY must be set when APP_ENV=production."
        )
    if cfg.SECRET_KEY == _DEV_SECRET:
        log.warning("Using the insecure dev SECRET_KEY - set FLASK_SECRET_KEY.")

    if not (cfg.YAHOO_CONSUMER_KEY and cfg.YAHOO_CONSUMER_SECRET):
        log.warning("YAHOO_CONSUMER_KEY / _SECRET not set - Yahoo login is disabled.")
    if not cfg.REDIS_URL:
        log.info("REDIS_URL not set - background jobs will run inline.")

    log.info("Config loaded (APP_ENV=%s, debug=%s).", cfg.APP_ENV, cfg.DEBUG)
