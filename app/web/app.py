"""
Flask application factory for SAMOURAIS SCRAPPER.
"""
from __future__ import annotations

from datetime import datetime

from flask import Flask


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
    app.secret_key = "samourais-scrapper-secret-key"

    # Max upload size for the meme editor (100 MB default)
    from app.config import EDITOR_MAX_FILE_SIZE_MB
    app.config["MAX_CONTENT_LENGTH"] = EDITOR_MAX_FILE_SIZE_MB * 1024 * 1024

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
