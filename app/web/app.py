"""
Flask application factory for SAMOURAIS SCRAPPER.
"""
from __future__ import annotations

import hmac
from datetime import datetime

from flask import Flask, Response, request


def _filter_formatdate(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Jinja2 filter: format a unix timestamp (int) or datetime to a readable French date string."""
    if value is None:
        return "Jamais"
    if isinstance(value, (int, float)):
        try:
            value = datetime.fromtimestamp(value)
        except (OSError, ValueError):
            return "Invalide"
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return str(value)


def _filter_timestamptodate(value) -> str:
    """Jinja2 filter: convert a unix timestamp to YYYY-MM-DD for date inputs."""
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d")
    except (OSError, ValueError, TypeError):
        return ""


def _filter_platformicon(platform: str) -> str:
    """Jinja2 filter: return a small emoji/icon for the given platform name."""
    icons = {
        "instagram": "&#x1F4F7;",  # camera
        "reddit": "&#x1F47D;",     # alien (Snoo)
        "tiktok": "&#x1F3B5;",     # musical note
        "twitter": "&#x1F426;",    # bird
    }
    return icons.get(platform, "&#x1F310;")  # globe fallback


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    from app.config import (
        EDITOR_MAX_FILE_SIZE_MB,
        FLASK_SECRET_KEY,
        APP_USERNAME,
        APP_PASSWORD,
    )

    app.secret_key = FLASK_SECRET_KEY

    # Max upload size for the meme editor (100 MB default)
    app.config["MAX_CONTENT_LENGTH"] = EDITOR_MAX_FILE_SIZE_MB * 1024 * 1024

    # ------------------------------------------------------------------
    # HTTP Basic authentication
    # Enabled ONLY when APP_PASSWORD is set. The /health endpoint is always
    # public so the Railway healthcheck keeps passing.
    # ------------------------------------------------------------------
    @app.route("/health")
    def _health():  # noqa: ANN202 — simple liveness probe, no auth
        return "ok", 200

    def _check_auth(auth) -> bool:
        if auth is None:
            return False
        user_ok = hmac.compare_digest(auth.username or "", APP_USERNAME)
        pass_ok = hmac.compare_digest(auth.password or "", APP_PASSWORD)
        return user_ok and pass_ok

    @app.before_request
    def _require_auth():  # noqa: ANN202
        if not APP_PASSWORD:
            return None  # auth disabled (no password configured)
        # Always allow the liveness probe.
        if request.path == "/health":
            return None
        if _check_auth(request.authorization):
            return None
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="SAMOURAIS SCRAPPER"'},
        )

    # ------------------------------------------------------------------
    # Jinja2 custom filters
    # ------------------------------------------------------------------
    app.jinja_env.filters["formatdate"] = _filter_formatdate
    app.jinja_env.filters["timestamptodate"] = _filter_timestamptodate
    app.jinja_env.filters["platformicon"] = _filter_platformicon

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------
    from app.web.routes import pages_bp
    from app.web.api import api_bp
    from app.web.viewer_api import viewer_api_bp
    from app.editor.api import editor_api_bp
    from app.calendar.api import calendar_api_bp
    from app.analytics.api import analytics_api_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(viewer_api_bp, url_prefix="/api")
    app.register_blueprint(editor_api_bp, url_prefix="/api")
    app.register_blueprint(calendar_api_bp, url_prefix="/api")
    app.register_blueprint(analytics_api_bp, url_prefix="/api")

    return app
