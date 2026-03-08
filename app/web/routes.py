"""
Flask Blueprint for page routes (HTML pages).
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, make_response, redirect, render_template, request, send_from_directory
from loguru import logger
from sqlalchemy import func

from app.config import (
    DATA_DIR,
    DOWNLOAD_DIR,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REFRESH_TOKEN,
    SESSIONS_DIR,
    STORAGE_MODE,
)
from app.db import MediaItem, Profile, ScrapeJob, SessionLocal
from app.storage import get_gdrive_auth_url, exchange_code

pages_bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------------------
# Helper: read current .env values for settings form
# ---------------------------------------------------------------------------
def _read_env_file() -> dict[str, str]:
    """Read the .env file and return key-value pairs."""
    from app.config import BASE_DIR

    env_path = BASE_DIR / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        eq_idx = trimmed.find("=")
        if eq_idx == -1:
            continue
        key = trimmed[:eq_idx].strip()
        val = trimmed[eq_idx + 1 :].strip()
        values[key] = val
    return values


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@pages_bp.route("/")
def dashboard():
    db = SessionLocal()
    try:
        total_profiles = db.query(func.count(Profile.id)).scalar() or 0
        active_profiles = (
            db.query(func.count(Profile.id)).filter(Profile.is_active == True).scalar() or 0
        )
        total_media = db.query(func.count(MediaItem.id)).scalar() or 0
        uploaded_media = (
            db.query(func.count(MediaItem.id))
            .filter(MediaItem.status == "uploaded")
            .scalar()
            or 0
        )
        pending_media = (
            db.query(func.count(MediaItem.id))
            .filter(MediaItem.status == "pending")
            .scalar()
            or 0
        )

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_ts = int(today_start.timestamp())
        today_jobs = (
            db.query(func.count(ScrapeJob.id))
            .filter(ScrapeJob.created_at >= today_ts)
            .scalar()
            or 0
        )
        running_jobs = (
            db.query(func.count(ScrapeJob.id))
            .filter(ScrapeJob.status == "running")
            .scalar()
            or 0
        )

        profiles_list = db.query(Profile).all()

        return render_template(
            "dashboard.html",
            page="dashboard",
            profiles=profiles_list,
            storage_mode=STORAGE_MODE,
            data_dir=str(DATA_DIR),
            stats={
                "totalProfiles": total_profiles,
                "activeProfiles": active_profiles,
                "totalMedia": total_media,
                "uploadedMedia": uploaded_media,
                "pendingMedia": pending_media,
                "todayJobs": today_jobs,
                "runningJobs": running_jobs,
            },
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
@pages_bp.route("/profiles")
def profiles_page():
    db = SessionLocal()
    try:
        all_profiles = db.query(Profile).all()
        profiles_with_counts = []
        for p in all_profiles:
            media_count = (
                db.query(func.count(MediaItem.id))
                .filter(MediaItem.profile_id == p.id)
                .scalar()
                or 0
            )
            profiles_with_counts.append({"profile": p, "media_count": media_count})

        return render_template(
            "profiles.html",
            page="profiles",
            profiles=profiles_with_counts,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@pages_bp.route("/jobs")
def jobs_page():
    return render_template("jobs.html", page="jobs")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@pages_bp.route("/settings")
def settings_page():
    gdrive_connected = bool(GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN)

    # Check session cookie files
    sessions: dict[str, datetime | None] = {
        "instagram": None,
        "reddit": None,
        "tiktok": None,
        "twitter": None,
    }
    for platform in ("instagram", "reddit", "tiktok", "twitter"):
        cookie_path = SESSIONS_DIR / f"{platform}.json"
        if cookie_path.exists():
            mtime = cookie_path.stat().st_mtime
            sessions[platform] = datetime.fromtimestamp(mtime)

    env_values = _read_env_file()

    return render_template(
        "settings.html",
        page="settings",
        gdrive_connected=gdrive_connected,
        sessions=sessions,
        env=env_values,
    )


# ---------------------------------------------------------------------------
# Google Drive OAuth
# ---------------------------------------------------------------------------
@pages_bp.route("/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return (
            "<h1>Erreur</h1>"
            "<p>GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET doivent etre configures dans .env</p>"
            '<a href="/settings">Retour</a>'
        )
    auth_url = get_gdrive_auth_url()
    return redirect(auth_url)


@pages_bp.route("/auth/google/callback")
def auth_google_callback():
    code = request.args.get("code", "")
    if not code:
        return (
            "<h1>Erreur</h1>"
            "<p>Pas de code d'autorisation recu.</p>"
            '<a href="/settings">Retour</a>'
        )
    try:
        refresh_token = exchange_code(code)
        return (
            "<h1>Google Drive connecte !</h1>"
            "<p>Ajoutez ce refresh token dans votre fichier <code>.env</code> :</p>"
            f"<pre>GOOGLE_REFRESH_TOKEN={refresh_token}</pre>"
            "<p>Puis relancez l'application.</p>"
            '<a href="/settings">Retour aux settings</a>'
        )
    except Exception as exc:
        logger.error("OAuth callback error: {}", exc)
        return f'<h1>Erreur</h1><p>{exc}</p><a href="/settings">Retour</a>'


# ---------------------------------------------------------------------------
# Media Viewer
# ---------------------------------------------------------------------------
@pages_bp.route("/viewer")
def viewer_page():
    return render_template("viewer.html", page="viewer")


@pages_bp.route("/media/file/<path:filename>")
def serve_media_file(filename):
    """Serve downloaded media files with caching and Range request support."""
    # conditional_response=True enables HTTP 304 (ETag/Last-Modified) + Range (206)
    resp = send_from_directory(
        str(DOWNLOAD_DIR), filename, conditional=True,
    )
    # Cache for 7 days — files don't change once downloaded
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp


@pages_bp.route("/media/thumb/<path:filename>")
def serve_media_thumbnail(filename):
    """Serve a JPEG thumbnail for any media file (video or image), cached on disk.

    For videos: extracts first frame via ffmpeg.
    For images: resizes to 480px wide via ffmpeg (faster than Pillow for large files).
    """
    import subprocess

    thumb_dir = DOWNLOAD_DIR / ".thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    base = Path(filename).stem
    thumb_name = f"{base}.jpg"
    thumb_path = thumb_dir / thumb_name

    # Return cached thumbnail
    if thumb_path.exists():
        resp = send_from_directory(str(thumb_dir), thumb_name, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return resp

    source_path = DOWNLOAD_DIR / filename
    if not source_path.exists():
        return "File not found", 404

    # Detect if video or image by extension
    ext = source_path.suffix.lower()
    is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")

    try:
        if is_video:
            # -ss before -i = fast input seeking (no decode from start)
            cmd = [
                "ffmpeg", "-y",
                "-ss", "0.1",
                "-i", str(source_path),
                "-vframes", "1",
                "-vf", "scale='min(480,iw)':-1",
                "-q:v", "5",
                str(thumb_path),
            ]
        else:
            # Image: resize to 480px max width
            cmd = [
                "ffmpeg", "-y",
                "-i", str(source_path),
                "-vf", "scale='min(480,iw)':-1",
                "-q:v", "5",
                str(thumb_path),
            ]

        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0 or not thumb_path.exists():
            logger.warning("Thumbnail generation failed for {}: {}", filename,
                           result.stderr[:300] if result.stderr else "unknown error")
            return "Thumbnail generation failed", 500
    except Exception as exc:
        logger.error("Thumbnail error for {}: {}", filename, exc)
        return "Thumbnail generation failed", 500

    resp = send_from_directory(str(thumb_dir), thumb_name, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return resp


# ---------------------------------------------------------------------------
# Meme Editor
# ---------------------------------------------------------------------------
@pages_bp.route("/editor")
def editor_page():
    return render_template("editor.html", page="editor")


# ---------------------------------------------------------------------------
# Calendar (placeholder — Phase 2)
# ---------------------------------------------------------------------------
@pages_bp.route("/calendar")
def calendar_page():
    return render_template("calendar.html", page="calendar")


# ---------------------------------------------------------------------------
# Analytics (placeholder — Phase 3)
# ---------------------------------------------------------------------------
@pages_bp.route("/analytics")
def analytics_page():
    return render_template("analytics.html", page="analytics")
