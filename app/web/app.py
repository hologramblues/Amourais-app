"""
Flask application factory for SAMOURAIS SCRAPPER.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from flask import Flask, Response, jsonify, request
from loguru import logger
from werkzeug.exceptions import HTTPException


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
    """Jinja2 filter: monochrome two-letter mark for a platform.

    Cohérence inter-écrans : le Calendrier rend déjà les plateformes
    par une marque monochrome (`PLATFORMS[*].mono` dans calendar.js —
    IG / TT / X / RD) et le Viewer par un libellé texte. L'écran
    Profils était le seul à afficher un emoji en couleur pleine
    (📷 🐦 👽 🎵), ce que la grille interdit explicitement (critère G8 :
    « sans logo de plateforme en couleur pleine surface »).

    Le rendu suit maintenant la même convention que le Calendrier, et
    reste lisible en niveaux de gris.
    """
    marks = {
        "instagram": "IG",
        "reddit": "RD",
        "tiktok": "TT",
        "twitter": "X",
    }
    mark = marks.get(platform, (platform or "?")[:2].upper())
    return f'<span class="s-plat" aria-hidden="true">{mark}</span>'


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
        # Compare BYTES, not str: hmac.compare_digest raises TypeError on any
        # non-ASCII str, so an accented APP_PASSWORD used to blow up on every
        # request carrying an Authorization header (risque #59, AUDIT.md §6.3).
        user_ok = hmac.compare_digest(
            (auth.username or "").encode("utf-8"), (APP_USERNAME or "").encode("utf-8")
        )
        pass_ok = hmac.compare_digest(
            (auth.password or "").encode("utf-8"), (APP_PASSWORD or "").encode("utf-8")
        )
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
    # Protection inter-site (CSRF) — risque #62, AUDIT.md §9/2.3
    # La logique vit dans app/web/api.py ; elle n'a d'effet qu'une fois
    # enregistrée ici. Elle est posée APRÈS l'authentification pour que le
    # 401 reste prioritaire sur le 403, et elle laisse passer toutes les
    # méthodes sûres — /health compris.
    # ------------------------------------------------------------------
    from app.web.api import reject_cross_site_request

    app.before_request(reject_cross_site_request)

    # ------------------------------------------------------------------
    # Error handling
    # No view may ever leak a traceback — let alone the interactive Werkzeug
    # console — to the caller (risque #8, AUDIT.md §6.3).
    # ------------------------------------------------------------------
    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    def _sober_error_page(code: int, message: str) -> Response:
        return Response(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>Erreur {code}</title>"
            "<body style='font-family:system-ui;margin:3rem;'>"
            f"<h1>Erreur {code}</h1><p>{message}</p>"
            "</body>",
            code,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):  # noqa: ANN202
        """404 / 405 / 413… stay what they are — only the rendering changes."""
        code = exc.code or 500
        if _wants_json():
            return jsonify(error=exc.description, code=code), code
        return exc.get_response()

    @app.errorhandler(413)
    def _handle_too_large(exc):  # noqa: ANN202
        """Editor upload above MAX_CONTENT_LENGTH."""
        message = f"Fichier trop volumineux (max {EDITOR_MAX_FILE_SIZE_MB} Mo)"
        if _wants_json():
            return jsonify(error=message, code=413), 413
        return _sober_error_page(413, message)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):  # noqa: ANN202
        """Last resort: turn any uncaught exception into a structured 500."""
        if isinstance(exc, HTTPException):
            return _handle_http_exception(exc)
        logger.exception("Unhandled exception on {} {}", request.method, request.path)
        if _wants_json():
            return jsonify(error="Erreur serveur"), 500
        return _sober_error_page(500, "Une erreur interne est survenue.")

    # ------------------------------------------------------------------
    # Jinja2 custom filters
    # ------------------------------------------------------------------
    app.jinja_env.filters["formatdate"] = _filter_formatdate
    app.jinja_env.filters["timestamptodate"] = _filter_timestamptodate
    app.jinja_env.filters["platformicon"] = _filter_platformicon

    # ------------------------------------------------------------------
    # Cache-busting des assets
    # `layout.html` appelle `asset_version(chemin)` dès que la globale
    # existe, et retombe sinon sur une empreinte de build écrite à la main.
    # Calculer l'empreinte sur le mtime évite d'avoir à penser à bumper
    # cette constante : un CSS corrigé purge le cache tout seul.
    # ------------------------------------------------------------------
    def _asset_version(path: str) -> str:
        try:
            return str(int(os.stat(os.path.join(app.static_folder, path)).st_mtime))
        except OSError:
            # Fichier absent ou illisible : une valeur stable vaut mieux
            # qu'une erreur de rendu sur toute la page.
            return "0"

    app.jinja_env.globals["asset_version"] = _asset_version

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
