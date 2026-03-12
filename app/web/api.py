"""
Flask Blueprint for API routes (prefix: /api).

Returns HTML fragments for HTMX consumption or JSON for status endpoints.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request
from loguru import logger
from sqlalchemy import func, desc

from app.config import PLATFORM_URLS, SESSIONS_DIR, BASE_DIR, DATA_DIR, DB_PATH, DOWNLOAD_DIR, SETTINGS_ENV
from app.db import MediaItem, Profile, ScrapeJob, SessionLocal
from app.scheduler import enqueue_manual_scrape

api_bp = Blueprint("api", __name__)


# ---------------------------------------------------------------------------
# Helper: read / write .env
# ---------------------------------------------------------------------------
def _read_env_file() -> dict[str, str]:
    """Read user settings from the persistent .env (survives Railway redeploy)."""
    values: dict[str, str] = {}
    # Merge: project-root .env (defaults) then persistent volume .env (overrides)
    for env_path in [BASE_DIR / ".env", SETTINGS_ENV]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            eq_idx = trimmed.find("=")
            if eq_idx == -1:
                continue
            values[trimmed[:eq_idx].strip()] = trimmed[eq_idx + 1 :].strip()
    return values


def _write_env_file(updates: dict[str, str]) -> None:
    """Write user settings to the persistent .env on the data volume."""
    env_path = SETTINGS_ENV  # DATA_DIR/.env — persistent on Railway
    env_path.parent.mkdir(parents=True, exist_ok=True)

    content = ""
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")

    lines = content.split("\n")
    updated_keys: set[str] = set()

    new_lines: list[str] = []
    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            new_lines.append(line)
            continue
        eq_idx = trimmed.find("=")
        if eq_idx == -1:
            new_lines.append(line)
            continue
        key = trimmed[:eq_idx].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append keys that were not already in the file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines), encoding="utf-8")
    logger.info("Settings saved to {}: {}", env_path, list(updates.keys()))


# ---------------------------------------------------------------------------
# Helper: render the profile list partial (reused by multiple endpoints)
# ---------------------------------------------------------------------------
def _render_profile_list() -> str:
    """Query profiles with media counts and return the HTML fragment."""
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
            "partials/profile_list.html",
            profiles=profiles_with_counts,
        )
    finally:
        db.close()


# ===========================================================================
# Profiles
# ===========================================================================
@api_bp.route("/profiles", methods=["POST"])
def add_profile():
    db = SessionLocal()
    try:
        platform = (request.form.get("platform") or "").strip()
        username = (request.form.get("username") or "").strip().lstrip("@")

        if not username or platform not in ("instagram", "reddit", "tiktok", "twitter"):
            return '<small style="color:red;">Plateforme et username requis</small>', 400

        url_builder = PLATFORM_URLS.get(platform)
        profile_url = url_builder(username) if url_builder else ""

        profile = Profile(
            platform=platform,
            username=username,
            profile_url=profile_url,
        )
        db.add(profile)
        db.commit()

        logger.info("Profile added: {} @{}", platform, username)
        return _render_profile_list()

    except Exception as exc:
        db.rollback()
        if "UNIQUE constraint" in str(exc):
            return '<small style="color:red;">Ce profil existe deja</small>', 409
        logger.error("Failed to add profile: {}", exc)
        return '<small style="color:red;">Erreur serveur</small>', 500
    finally:
        db.close()


@api_bp.route("/profiles/<int:profile_id>", methods=["PATCH"])
def update_profile(profile_id: int):
    db = SessionLocal()
    try:
        profile = db.query(Profile).get(profile_id)
        if not profile:
            return jsonify(error="Profil non trouve"), 404

        data = request.get_json(silent=True) or request.form.to_dict()

        if "isActive" in data:
            val = data["isActive"]
            profile.is_active = val in (True, "true", "True", "1")
        if "scrapeIntervalMinutes" in data:
            profile.scrape_interval_minutes = int(data["scrapeIntervalMinutes"])
        if "scrapeMode" in data and data["scrapeMode"] in ("backfill", "daily"):
            profile.scrape_mode = data["scrapeMode"]
        if "backfillFrom" in data:
            val = data["backfillFrom"]
            if val:
                # Accept ISO date string (YYYY-MM-DD) or unix timestamp
                try:
                    profile.backfill_from = int(datetime.fromisoformat(val).timestamp()) if isinstance(val, str) and "-" in val else int(val)
                except (ValueError, TypeError):
                    pass
            else:
                profile.backfill_from = None
        if "backfillTo" in data:
            val = data["backfillTo"]
            if val:
                try:
                    profile.backfill_to = int(datetime.fromisoformat(val).timestamp()) if isinstance(val, str) and "-" in val else int(val)
                except (ValueError, TypeError):
                    pass
            else:
                profile.backfill_to = None

        profile.updated_at = int(datetime.now().timestamp())
        db.commit()

        logger.info("Profile updated: id={}", profile_id)
        return _render_profile_list()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to update profile: {}", exc)
        return jsonify(error="Erreur serveur"), 500
    finally:
        db.close()


@api_bp.route("/profiles/<int:profile_id>", methods=["DELETE"])
def delete_profile(profile_id: int):
    db = SessionLocal()
    try:
        profile = db.query(Profile).get(profile_id)
        if not profile:
            return jsonify(error="Profil non trouve"), 404

        db.delete(profile)
        db.commit()
        logger.info("Profile deleted: id={}", profile_id)
        return _render_profile_list()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to delete profile: {}", exc)
        return jsonify(error="Erreur serveur"), 500
    finally:
        db.close()


@api_bp.route("/profiles/<int:profile_id>/scrape", methods=["POST"])
def trigger_scrape(profile_id: int):
    db = SessionLocal()
    try:
        profile = db.query(Profile).get(profile_id)
        if not profile:
            return jsonify(error="Profil non trouve"), 404

        job = ScrapeJob(profile_id=profile_id, triggered_by="manual")
        db.add(job)
        db.commit()
        db.refresh(job)

        logger.info("Manual scrape triggered: profile_id={} job_id={}", profile_id, job.id)
        enqueue_manual_scrape(profile_id, job.id)

        return jsonify(success=True, jobId=job.id)
    except Exception as exc:
        db.rollback()
        logger.error("Failed to trigger scrape: {}", exc)
        return jsonify(error="Erreur serveur"), 500
    finally:
        db.close()


# ===========================================================================
# Jobs
# ===========================================================================
def _status_color(status: str) -> str:
    """Return CSS class suffix for a job status."""
    return {
        "completed": "green",
        "running": "blue",
        "failed": "red",
        "partial": "orange",
        "queued": "gray",
    }.get(status, "gray")


def _format_ts(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except (OSError, ValueError):
        return ""


@api_bp.route("/jobs/recent")
def jobs_recent():
    db = SessionLocal()
    try:
        rows = (
            db.query(ScrapeJob, Profile)
            .outerjoin(Profile, ScrapeJob.profile_id == Profile.id)
            .order_by(desc(ScrapeJob.created_at))
            .limit(10)
            .all()
        )

        if not rows:
            return '<p class="text-muted">Aucun job pour le moment.</p>'

        html_rows = ""
        for job, profile in rows:
            username = profile.username if profile else "N/A"
            color = _status_color(job.status)
            date_str = _format_ts(job.created_at)
            html_rows += (
                f"<tr>"
                f"<td>{username}</td>"
                f'<td><span class="s-badge s-badge-{color}">{job.status}</span></td>'
                f"<td>{job.media_new}</td>"
                f"<td>{job.media_uploaded}</td>"
                f"<td>{date_str}</td>"
                f"</tr>"
            )

        return (
            "<table>"
            "<thead><tr>"
            "<th>Profil</th><th>Status</th><th>Nouveau</th><th>Upload</th><th>Date</th>"
            "</tr></thead>"
            f"<tbody>{html_rows}</tbody>"
            "</table>"
        )
    finally:
        db.close()


@api_bp.route("/jobs/list")
def jobs_list():
    db = SessionLocal()
    try:
        rows = (
            db.query(ScrapeJob, Profile)
            .outerjoin(Profile, ScrapeJob.profile_id == Profile.id)
            .order_by(desc(ScrapeJob.created_at))
            .limit(50)
            .all()
        )

        if not rows:
            return '<tr><td colspan="10">Aucun job.</td></tr>'

        html = ""
        for job, profile in rows:
            username = profile.username if profile else "N/A"
            color = _status_color(job.status)
            date_str = _format_ts(job.created_at)
            retry_btn = ""
            if job.status == "failed":
                retry_btn = (
                    f'<button class="s-btn-sm" '
                    f'hx-post="/api/jobs/{job.id}/retry" hx-swap="none">'
                    f"Retry</button>"
                )
            html += (
                f"<tr>"
                f"<td>{job.id}</td>"
                f"<td>{username}</td>"
                f'<td><span class="s-badge s-badge-{color}">{job.status}</span></td>'
                f"<td>{job.triggered_by}</td>"
                f"<td>{job.media_found}</td>"
                f"<td>{job.media_new}</td>"
                f"<td>{job.media_downloaded}</td>"
                f"<td>{job.media_uploaded}</td>"
                f"<td>{date_str}</td>"
                f"<td>{retry_btn}</td>"
                f"</tr>"
            )

        return html
    finally:
        db.close()


@api_bp.route("/jobs/<int:job_id>/retry", methods=["POST"])
def retry_job(job_id: int):
    db = SessionLocal()
    try:
        job = db.query(ScrapeJob).get(job_id)
        if not job:
            return jsonify(error="Job non trouve"), 404

        new_job = ScrapeJob(profile_id=job.profile_id, triggered_by="manual")
        db.add(new_job)
        db.commit()
        db.refresh(new_job)

        logger.info("Job retry triggered: old={} new={}", job_id, new_job.id)
        enqueue_manual_scrape(job.profile_id, new_job.id)

        return jsonify(success=True, jobId=new_job.id)
    except Exception as exc:
        db.rollback()
        logger.error("Failed to retry job: {}", exc)
        return jsonify(error="Erreur serveur"), 500
    finally:
        db.close()


# ===========================================================================
# Status (JSON)
# ===========================================================================
@api_bp.route("/status")
def status():
    db = SessionLocal()
    try:
        profile_count = db.query(func.count(Profile.id)).scalar() or 0
        media_count = db.query(func.count(MediaItem.id)).scalar() or 0
        pending_count = (
            db.query(func.count(MediaItem.id))
            .filter(MediaItem.status == "pending")
            .scalar()
            or 0
        )
        return jsonify(profiles=profile_count, media=media_count, pending=pending_count)
    finally:
        db.close()


@api_bp.route("/debug/volume")
def debug_volume():
    """Diagnostic endpoint to check persistent volume health."""
    import time

    data_dir = str(DATA_DIR)
    is_mount = os.path.ismount(data_dir)
    data_dir_exists = os.path.exists(data_dir)

    # Disk stats
    disk = {}
    try:
        stat = os.statvfs(data_dir)
        disk = {
            "total_gb": round((stat.f_frsize * stat.f_blocks) / (1024 ** 3), 2),
            "free_gb": round((stat.f_frsize * stat.f_bavail) / (1024 ** 3), 2),
            "used_gb": round((stat.f_frsize * (stat.f_blocks - stat.f_bavail)) / (1024 ** 3), 2),
        }
    except Exception as e:
        disk = {"error": str(e)}

    # Marker file
    marker_path = Path(data_dir) / ".samourais_volume_marker"
    marker_value = None
    if marker_path.exists():
        marker_value = marker_path.read_text().strip()

    # DB info
    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0

    # Downloads count
    dl_count = len(list(DOWNLOAD_DIR.glob("**/*"))) if DOWNLOAD_DIR.exists() else 0

    # Top-level contents
    contents = []
    if Path(data_dir).exists():
        contents = sorted([c.name for c in Path(data_dir).iterdir()])

    # Profile / media counts from DB
    db = SessionLocal()
    try:
        profile_count = db.query(func.count(Profile.id)).scalar() or 0
        media_count = db.query(func.count(MediaItem.id)).scalar() or 0
    except Exception:
        profile_count = -1
        media_count = -1
    finally:
        db.close()

    return jsonify({
        "data_dir": data_dir,
        "data_dir_env": os.getenv("DATA_DIR", "<not set>"),
        "data_dir_exists": data_dir_exists,
        "is_mount_point": is_mount,
        "disk": disk,
        "marker_from_previous_boot": marker_value,
        "volume_persists": marker_value is not None,
        "db_exists": db_exists,
        "db_size_kb": round(db_size / 1024, 1),
        "download_files": dl_count,
        "profiles_in_db": profile_count,
        "media_in_db": media_count,
        "data_dir_contents": contents,
        "current_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    })


# ===========================================================================
# Settings
# ===========================================================================
@api_bp.route("/settings/env", methods=["POST"])
def save_env():
    try:
        updates: dict[str, str] = {}
        for key, value in request.form.items():
            if isinstance(value, str):
                updates[key] = value

        if not updates:
            return '<small style="color:red;">Aucun champ a sauvegarder</small>', 400

        _write_env_file(updates)
        return '<small style="color:green;">Sauvegarde OK</small>'
    except Exception as exc:
        logger.error("Failed to save settings: {}", exc)
        return '<small style="color:red;">Erreur de sauvegarde</small>', 500


@api_bp.route("/settings/ig-api", methods=["POST"])
def setup_ig_api():
    """Configure Instagram Graph API: save credentials, auto-discover IG User ID,
    optionally exchange short-lived token for long-lived one."""
    try:
        data = request.get_json(force=True)
        fb_app_id = data.get("FB_APP_ID", "").strip()
        fb_app_secret = data.get("FB_APP_SECRET", "").strip()
        access_token = data.get("IG_ACCESS_TOKEN", "").strip()
        ig_user_id = data.get("IG_USER_ID", "").strip()

        if not access_token:
            return jsonify({"ok": False, "error": "Access Token requis"}), 400

        # Save credentials to persistent .env first
        env_updates = {"IG_ACCESS_TOKEN": access_token}
        if fb_app_id:
            env_updates["FB_APP_ID"] = fb_app_id
        if fb_app_secret:
            env_updates["FB_APP_SECRET"] = fb_app_secret

        # Try to exchange for long-lived token if we have app credentials
        if fb_app_id and fb_app_secret:
            try:
                from app.instagram_api import exchange_for_long_lived_token
                result = exchange_for_long_lived_token(access_token)
                long_token = result.get("access_token", "")
                expires_in = result.get("expires_in", 0)
                if long_token:
                    env_updates["IG_ACCESS_TOKEN"] = long_token
                    logger.info("Exchanged for long-lived token (expires in {}s)", expires_in)
            except Exception as e:
                logger.warning("Could not exchange token (may already be long-lived): {}", e)

        _write_env_file(env_updates)

        # Auto-discover IG User ID if not provided
        if not ig_user_id:
            try:
                from app.instagram_api import discover_ig_user_id
                discovery = discover_ig_user_id()
                ig_user_id = discovery.get("ig_user_id", "")
                page_name = discovery.get("page_name", "")
                if ig_user_id:
                    _write_env_file({"IG_USER_ID": ig_user_id})
                    logger.info("Auto-discovered IG User ID: {} (page: {})", ig_user_id, page_name)
            except Exception as e:
                return jsonify({
                    "ok": False,
                    "error": f"Token sauvegarde mais impossible de detecter le compte IG: {e}",
                }), 400
        else:
            _write_env_file({"IG_USER_ID": ig_user_id})

        # Verify by fetching profile
        try:
            from app.instagram_api import fetch_profile
            profile = fetch_profile()
            username = profile.get("username", "?")
            followers = profile.get("followers_count", 0)
            return jsonify({
                "ok": True,
                "ig_user_id": ig_user_id,
                "message": f"Connecte a @{username} ({followers} followers). Collection des stats en cours...",
            })
        except Exception as e:
            return jsonify({
                "ok": True,
                "ig_user_id": ig_user_id,
                "message": f"Credentials sauvegardees. Verification: {e}",
            })

    except Exception as exc:
        logger.exception("Failed to setup IG API: {}", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@api_bp.route("/settings/session", methods=["POST"])
def upload_session():
    try:
        platform = (request.form.get("platform") or "").strip()
        if platform not in ("instagram", "reddit", "tiktok", "twitter"):
            return '<small style="color:red;">Plateforme invalide</small>', 400

        file = request.files.get("cookies")
        if not file or not file.filename:
            return '<small style="color:red;">Aucun fichier selectionne</small>', 400

        content = file.read().decode("utf-8")

        # Validate JSON
        try:
            json.loads(content)
        except json.JSONDecodeError:
            return '<small style="color:red;">Fichier JSON invalide</small>', 400

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        dest = SESSIONS_DIR / f"{platform}.json"
        dest.write_text(content, encoding="utf-8")

        # Backup cookies in DB so they survive accidental file deletion
        from app.db import SessionCookie, SessionLocal as _SL
        _db = _SL()
        try:
            existing = _db.query(SessionCookie).filter_by(platform=platform).first()
            if existing:
                existing.cookies_json = content
                existing.updated_at = int(datetime.now().timestamp())
            else:
                _db.add(SessionCookie(platform=platform, cookies_json=content))
            _db.commit()
        except Exception as db_exc:
            logger.warning("Failed to backup cookies in DB: {}", db_exc)
            _db.rollback()
        finally:
            _db.close()

        logger.info("Session cookies uploaded for {} (file + DB backup)", platform)

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        return f'<small style="color:green;">Cookies OK ({now_str})</small>'
    except Exception as exc:
        logger.error("Failed to upload session cookies: {}", exc)
        return '<small style="color:red;">Erreur upload</small>', 500


# ===========================================================================
# Quick Download (single URL)
# ===========================================================================
@api_bp.route("/quick-download", methods=["POST"])
def quick_download_url():
    """Download media from a single post URL."""
    import threading

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL requise"}), 400

    # Detect platform first (fast check)
    from app.scraper.quick_download import detect_platform
    detection = detect_platform(url)
    if detection is None:
        return jsonify({
            "error": "URL non reconnue. Plateformes supportées: Instagram, TikTok, Twitter/X, Reddit"
        }), 400

    platform, post_id = detection

    # Run download in background thread
    def _do_download():
        from app.scraper.quick_download import quick_download
        result = quick_download(url)

        if result.error:
            logger.warning("Quick download failed for {}: {}", url, result.error)
            return

        # Save successful downloads to DB
        db = SessionLocal()
        try:
            saved = 0
            for item in result.media_items:
                if "error" in item and item["error"]:
                    continue
                # Create a "quick download" profile-less media item
                # Use profile_id=0 or find/create a special quick-download profile
                _ensure_quick_profile(db, platform)
                qp = db.query(Profile).filter_by(
                    platform=platform, username=f"__quick_download_{platform}"
                ).first()

                media_item = MediaItem(
                    profile_id=qp.id if qp else 0,
                    platform=platform,
                    post_id=item["post_id"],
                    post_url=item["post_url"],
                    media_type=item["media_type"],
                    media_url=item["media_url"],
                    caption=item.get("caption"),
                    width=item.get("width"),
                    height=item.get("height"),
                    duration=item.get("duration"),
                    local_path=item.get("local_path"),
                    file_size=item.get("file_size"),
                    content_hash=item.get("content_hash"),
                    status="downloaded",
                    downloaded_at=int(datetime.now().timestamp()),
                    discovered_at=int(datetime.now().timestamp()),
                )
                try:
                    db.add(media_item)
                    db.flush()
                    saved += 1
                except Exception:
                    db.rollback()
            db.commit()
            logger.info("Quick download saved {} media items for {}", saved, url)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to save quick download results: {}", exc)
        finally:
            db.close()

    t = threading.Thread(target=_do_download, name=f"quick-dl-{post_id}", daemon=True)
    t.start()

    return jsonify({
        "status": "downloading",
        "platform": platform,
        "post_id": post_id,
        "message": f"Téléchargement lancé pour {platform} (post {post_id})",
    })


def _ensure_quick_profile(db, platform: str):
    """Create a hidden quick-download profile if it doesn't exist."""
    username = f"__quick_download_{platform}"
    existing = db.query(Profile).filter_by(platform=platform, username=username).first()
    if not existing:
        profile = Profile(
            platform=platform,
            username=username,
            profile_url=f"quick-download://{platform}",
            display_name=f"Quick Downloads ({platform})",
            is_active=False,  # Never auto-scraped
        )
        db.add(profile)
        db.commit()
        logger.info("Created quick-download profile for {}", platform)
