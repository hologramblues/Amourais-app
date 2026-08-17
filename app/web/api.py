"""
Flask Blueprint for API routes (prefix: /api).

Returns HTML fragments for HTMX consumption or JSON for status endpoints.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import re

from flask import Blueprint, abort, jsonify, render_template, request
from loguru import logger
from markupsafe import escape
from sqlalchemy import func, desc

from app.config import DEBUG, PLATFORM_URLS, SESSIONS_DIR, BASE_DIR, DATA_DIR, DB_PATH, DOWNLOAD_DIR, SETTINGS_ENV
from app.db import MediaItem, Profile, ScrapeJob, SessionLocal
from app.scheduler import enqueue_manual_scrape

api_bp = Blueprint("api", __name__)

# Keys the Settings UI is allowed to write to the persistent .env.
# Anything else in the POST body is ignored (prevents arbitrary env injection).
ALLOWED_ENV_KEYS = frozenset({
    # Google Drive
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "GOOGLE_REFRESH_TOKEN", "GDRIVE_ROOT_FOLDER_NAME", "STORAGE_MODE",
    # Instagram Graph API
    "FB_APP_ID", "FB_APP_SECRET", "IG_ACCESS_TOKEN", "IG_USER_ID",
    # Apify (backend de scrape par API — lot A + multi-plateforme)
    "APIFY_TOKEN", "APIFY_ACTOR", "TIKTOK_ACTOR", "TWITTER_ACTOR",
    # Scraper tuning
    "DEFAULT_SCRAPE_INTERVAL_MINUTES", "BROWSER_POOL_SIZE", "SCROLL_PAUSE_MS",
    "MAX_SCROLLS", "DELAY_BETWEEN_PROFILES_MS", "BACKFILL_MAX_SCROLLS",
    "DAILY_MAX_SCROLLS", "DAILY_SCRAPE_INTERVAL_MINUTES", "MAX_CONCURRENT_SCRAPES",
    # Proxies
    "PROXY_URL", "PROXY_INSTAGRAM", "PROXY_TIKTOK", "PROXY_TWITTER", "PROXY_REDDIT",
    # App / server
    "LOG_LEVEL", "EDITOR_MAX_FILE_SIZE_MB",
    # NOTE: APP_USERNAME / APP_PASSWORD / FLASK_SECRET_KEY are deliberately
    # NOT writable from the Settings UI (risque #52, AUDIT.md §6.13): a single
    # request would lock the owner out of his own application, from a file that
    # survives redeploys.
    # NOTE: PORT is deliberately NOT writable either (lot 2.1). The persistent
    # `.env` of the volume is loaded with `override=True`, so it SHADOWS the
    # variables injected by Railway — PORT included. Saving the Settings form
    # would make the container listen on the wrong port and kill the
    # deployment. PORT belongs to the platform, not to the UI.
})

# Subset of ALLOWED_ENV_KEYS that `app/config.py` casts with `int()`.
# A non-numeric value here used to be written to the persistent volume and
# brick the next boot (risque #41, AUDIT.md §4.14).
# (PORT is kept here as defence in depth even though it left ALLOWED_ENV_KEYS:
# should anyone ever put it back, it must still be validated as an integer.)
NUMERIC_ENV_KEYS = frozenset({
    "PORT", "EDITOR_MAX_FILE_SIZE_MB", "BROWSER_POOL_SIZE", "SCROLL_PAUSE_MS",
    "MAX_SCROLLS", "BACKFILL_MAX_SCROLLS", "DAILY_MAX_SCROLLS",
    "DEFAULT_SCRAPE_INTERVAL_MINUTES", "DAILY_SCRAPE_INTERVAL_MINUTES",
    "DELAY_BETWEEN_PROFILES_MS", "MAX_CONCURRENT_SCRAPES",
})


def _is_int(value: str) -> bool:
    """True when `int()` will accept this value at config import time."""
    try:
        int(value.strip())
    except (TypeError, ValueError, AttributeError):
        return False
    return True


# Characters that would let a single form field forge EXTRA lines — hence extra
# variables — in the persistent `.env` (lot 2.1). `PROXY_URL=x\nAPP_PASSWORD=y`
# posted as ONE value used to write TWO variables, and the volume `.env` is
# loaded with `override=True`: the injected variable wins over everything.
# NUL is rejected too: it truncates the file for most C readers.
_ENV_FORBIDDEN_CHARS = ("\n", "\r", "\0")


def _env_value_is_safe(value: str) -> bool:
    """False when a value would break out of its `KEY=value` line."""
    return not any(ch in value for ch in _ENV_FORBIDDEN_CHARS)


# One writer at a time. `_write_env_file` is a read-modify-write, and two
# concurrent saves (two browser tabs, or the IG-API view which writes three
# times in a row) could otherwise lose updates or interleave.
_ENV_WRITE_LOCK = threading.Lock()


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
    """Write user settings to the persistent .env on the data volume.

    The write is ATOMIC (temporary file + `os.replace`) and serialised by
    `_ENV_WRITE_LOCK` (lot 2.1b). `config.get_proxy_for_platform` and
    `instagram_api` re-read this file at any moment, from other threads: with
    an in-place `write_text` a scrape starting mid-save read a truncated file
    and silently lost its proxy (fenêtre de troncature, AUDIT.md §4.15).
    `os.replace` is atomic on POSIX, so a reader sees either the old file or
    the new one — never half of one.
    """
    # Refuse to serialise a value that would forge extra lines, whatever the
    # caller: `save_env` filters too, but `setup_ig_api` also writes here.
    toxiques = sorted(k for k, v in updates.items() if not _env_value_is_safe(str(v)))
    if toxiques:
        raise ValueError(f"valeurs .env contenant un saut de ligne ou un NUL : {toxiques}")

    env_path = SETTINGS_ENV  # DATA_DIR/.env — persistent on Railway
    env_path.parent.mkdir(parents=True, exist_ok=True)

    with _ENV_WRITE_LOCK:
        _write_env_file_locked(env_path, updates)

    logger.info("Settings saved to {}: {}", env_path, list(updates.keys()))


def _write_env_file_locked(env_path: Path, updates: dict[str, str]) -> None:
    """Read-modify-write body of `_write_env_file`; call under the lock."""
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

    # Atomic publication: write a sibling temp file, flush it to disk, then
    # rename it over the target. A concurrent reader never sees a partial file.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(env_path.parent), prefix=env_path.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines))
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates the file 0600, and os.replace keeps that mode: the
        # .env holds proxy passwords and API tokens.
        os.replace(tmp_path, env_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Helper: cross-site request rejection (lot 2.3)
# ---------------------------------------------------------------------------
# There is NO CSRF token anywhere in the application. `save_env` reads
# `request.form` and `upload_session` reads `request.form` / `request.files`
# in multipart: both are "simple" content types in the CORS sense, so a form
# hosted on ANY other site can POST to them from the owner's browser without
# a preflight — and HTTP Basic credentials ride along.
#
# The cheap, token-free defence is to check the request's PROVENANCE, which
# the browser sets and JavaScript cannot forge: `Sec-Fetch-Site` (all current
# browsers) with `Origin` as a fallback.
#
# WIRED UP in `create_app()` (app/web/app.py) via
# `app.before_request(reject_cross_site_request)`, registered right after the
# authentication hook so a 401 keeps priority over a 403. Defining the
# function here without registering it there makes it dead code: the test
# `test_le_hook_inter_site_est_enregistre_sur_lapplication` guards that.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# `same-origin` is the normal case (our own pages), `same-site` covers a
# sub-domain deployment. `none` means the navigation was started by the user
# himself (bookmark, address bar) — never a cross-site form POST.
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "same-site", "none"})


def request_is_cross_site(req=None) -> bool:
    """True when a state-changing request visibly comes from another site.

    Verdict order:
      1. safe methods are never cross-site;
      2. `Sec-Fetch-Site` decides when the browser sent it;
      3. otherwise `Origin` is compared to the host actually requested;
      4. a request with NEITHER header is not a browser form post (curl,
         HTMX-less scripts, health probes): it is allowed, because a browser
         ALWAYS sends `Origin` on a cross-origin POST — the absence of the
         header cannot be forged from another site.
    """
    req = req if req is not None else request

    if req.method.upper() in _SAFE_METHODS:
        return False

    fetch_site = (req.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site:
        return fetch_site not in _ALLOWED_FETCH_SITES

    origin = (req.headers.get("Origin") or "").strip()
    if origin:
        if origin.lower() == "null":  # sandboxed iframe / data: document
            return True
        return urlsplit(origin).netloc.lower() != (req.host or "").lower()

    return False


def reject_cross_site_request(req=None):
    """`before_request` body: return a 403 response, or None to let it pass.

    Meant to be registered ONCE on the Flask app:

        from app.web.api import reject_cross_site_request
        app.before_request(reject_cross_site_request)
    """
    req = req if req is not None else request
    if not request_is_cross_site(req):
        return None

    logger.warning(
        "Cross-site {} on {} refused (Origin={!r}, Sec-Fetch-Site={!r})",
        req.method, req.path,
        req.headers.get("Origin"), req.headers.get("Sec-Fetch-Site"),
    )
    if req.path.startswith("/api/"):
        return jsonify(error="Requête inter-site refusée", code=403), 403
    return "Requête inter-site refusée", 403


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
            return '<small class="text-error">Plateforme et username requis</small>', 400

        # Validate username charset (defense-in-depth against stored XSS / Drive
        # query injection). Platform handles allow letters, digits, '.', '_', '-'.
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", username):
            return '<small class="text-error">Username invalide (lettres, chiffres, . _ - uniquement)</small>', 400

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
            return '<small class="text-error">Ce profil existe deja</small>', 409
        logger.error("Failed to add profile: {}", exc)
        return '<small class="text-error">Erreur serveur</small>', 500
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
        "empty": "orange",
        "queued": "gray",
    }.get(status, "gray")


# Libellé français + glyphe par statut de job.
#
# Cohérence inter-écrans : le Calendrier rend ses états « ✓ Publié /
# ! Échoué / ◷ Programmé » et le Viewer « ● Utilisé / ○ Inédit ».
# Les écrans Jobs et Dashboard affichaient jusqu'ici la valeur brute
# de la base ("failed", "completed"), en anglais et sans glyphe — même
# concept, deux rendus. On aligne sur le motif glyphe + mot, qui reste
# lisible en niveaux de gris (critère G11).
_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "completed": ("✓", "Terminé"),
    "running": ("◌", "En cours"),
    "failed": ("!", "Échoué"),
    "partial": ("◐", "Partiel"),
    "empty": ("○", "Vide"),
    "queued": ("◷", "En file"),
}


_TRIGGER_LABELS: dict[str, str] = {
    "scheduler": "Planificateur",
    "manual": "Manuel",
    "backfill": "Backfill",
    "api": "API",
}


def _status_badge(status: str) -> str:
    """Render a job status as the app-wide pill: glyph + French label."""
    glyph, label = _STATUS_LABELS.get(status, ("•", str(status or "Inconnu")))
    color = _status_color(status)
    return (
        f'<span class="s-badge s-badge-{color}">'
        f'<span class="s-badge__glyph" aria-hidden="true">{escape(glyph)}</span>'
        f"{escape(label)}</span>"
    )


def _format_ts(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except (OSError, ValueError):
        return ""


@api_bp.route("/system/status")
def system_status():
    """Feed the nav's system indicator, on every screen.

    `partials/nav.html` has always carried `data-system-status` with a
    hardcoded `data-state="idle"` — a dot that never changed colour on
    any of the 8 screens because nothing ever fed it. This endpoint is
    what it reads: a running job wins over a failed one (the live state
    is more useful than the past one), and the label is what the nav
    shows next to the dot, so the meaning never rests on colour alone.
    """
    db = SessionLocal()
    try:
        running = (
            db.query(func.count(ScrapeJob.id))
            .filter(ScrapeJob.status.in_(("running", "queued")))
            .scalar()
            or 0
        )
        # created_at est un entier Unix (app/db.py:184), pas un datetime :
        # comparer à un objet datetime ne filtrerait rien.
        since = int((datetime.now() - timedelta(hours=24)).timestamp())
        failed = (
            db.query(func.count(ScrapeJob.id))
            .filter(ScrapeJob.status == "failed", ScrapeJob.created_at >= since)
            .scalar()
            or 0
        )

        if running:
            state, label = "running", f"{running} job{'s' if running > 1 else ''} en cours"
        elif failed:
            state, label = "error", f"{failed} échec{'s' if failed > 1 else ''} (24 h)"
        else:
            state, label = "ok", "File au repos"

        return jsonify(state=state, label=label, running=running, failed=failed)
    finally:
        db.close()


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
            username = escape(profile.username) if profile else "N/A"
            date_str = _format_ts(job.created_at)
            html_rows += (
                f"<tr>"
                f"<td>{username}</td>"
                f"<td>{_status_badge(job.status)}</td>"
                f'<td class="num">{job.media_new}</td>'
                f'<td class="num">{job.media_uploaded}</td>'
                f"<td>{date_str}</td>"
                f"</tr>"
            )

        return (
            "<table>"
            "<thead><tr>"
            "<th>Profil</th><th>État</th><th class=\"num\">Nouveaux</th>"
            "<th class=\"num\">Envoyés</th><th>Date</th>"
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
            username = escape(profile.username) if profile else "N/A"
            date_str = _format_ts(job.created_at)
            retry_btn = ""
            if job.status == "failed":
                retry_btn = (
                    f'<button class="s-btn-sm" '
                    f'hx-post="/api/jobs/{job.id}/retry" hx-swap="none">'
                    f"Relancer</button>"
                )
            trigger = _TRIGGER_LABELS.get(job.triggered_by, job.triggered_by or "—")
            html += (
                f"<tr>"
                f'<td class="num">{job.id}</td>'
                f"<td>{username}</td>"
                f"<td>{_status_badge(job.status)}</td>"
                f"<td>{escape(str(trigger))}</td>"
                f'<td class="num">{job.media_found}</td>'
                f'<td class="num">{job.media_new}</td>'
                f'<td class="num">{job.media_downloaded}</td>'
                f'<td class="num">{job.media_uploaded}</td>'
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
    """Diagnostic endpoint to check persistent volume health.

    Development only: it maps DATA_DIR (paths, file names, free space, row
    counts).  With APP_PASSWORD unset the whole app is unauthenticated, so in
    production this route is hidden rather than merely unadvertised.
    """
    if not DEBUG:
        abort(404)

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
        rejected: list[str] = []
        invalides: list[str] = []
        injections: list[str] = []
        for key, value in request.form.items():
            if not isinstance(value, str):
                continue
            if key in ALLOWED_ENV_KEYS:
                # A value carrying \n / \r / \0 would forge EXTRA variables in
                # the persistent .env (lot 2.1) — refuse before anything else.
                if not _env_value_is_safe(value):
                    injections.append(key)
                    continue
                if key in NUMERIC_ENV_KEYS and not _is_int(value):
                    invalides.append(key)
                    continue
                updates[key] = value
            else:
                rejected.append(key)

        if rejected:
            logger.warning("save_env ignored non-whitelisted keys: {}", rejected)

        # Refuse the whole write: an injected value must never reach the volume.
        if injections:
            logger.warning("save_env rejected line-breaking values for: {}", injections)
            return (
                '<small class="text-error">Valeur invalide (saut de ligne interdit) : '
                f'{escape(", ".join(sorted(injections)))}</small>',
                400,
            )

        # Refuse the whole write: a toxic value must never reach the volume.
        if invalides:
            logger.warning("save_env rejected non-numeric values for: {}", invalides)
            return (
                '<small class="text-error">Valeur numerique invalide : '
                f'{", ".join(sorted(invalides))}</small>',
                400,
            )

        if not updates:
            return '<small class="text-error">Aucun champ valide a sauvegarder</small>', 400

        _write_env_file(updates)
        return '<small class="text-success">Sauvegarde OK</small>'
    except Exception as exc:
        logger.error("Failed to save settings: {}", exc)
        return '<small class="text-error">Erreur de sauvegarde</small>', 500


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
                # The exception text carries the Graph API URL and the access
                # token query string — log it, never return it (lot 2.4b).
                logger.warning("IG user id discovery failed: {}", e)
                return jsonify({
                    "ok": False,
                    "error": "Token sauvegarde mais impossible de detecter le compte IG "
                             "(voir les logs serveur).",
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
            logger.warning("IG profile verification failed: {}", e)
            return jsonify({
                "ok": True,
                "ig_user_id": ig_user_id,
                "message": "Credentials sauvegardees. La verification du compte a echoue "
                           "(voir les logs serveur).",
            })

    except Exception as exc:
        # `str(exc)` leaks the full SQL statement on a SQLAlchemy error and the
        # absolute volume path on an OSError (lot 2.4b): log, return generic.
        logger.exception("Failed to setup IG API: {}", exc)
        return jsonify({"ok": False, "error": "Erreur serveur"}), 500


# Cookie that MAKES the session for each platform. Without it the export is
# an anonymous browsing profile: the scrape sees a logged-out page.
_CRITICAL_SESSION_COOKIES = {
    "instagram": ("sessionid",),
    "tiktok": ("sessionid",),
    "twitter": ("auth_token",),
    "reddit": ("reddit_session",),
}

# How many timestamped copies of a session file we keep.
_SESSION_BACKUPS_KEPT = 5


def _missing_critical_cookies(platform: str, cookies: list[dict]) -> list[str]:
    """Names of the platform's critical cookies absent (or empty) in the upload."""
    presents = {
        str(c.get("name", "")).strip().lower()
        for c in cookies
        if str(c.get("value", "") or "").strip()
    }
    return [
        name
        for name in _CRITICAL_SESSION_COOKIES.get(platform, ())
        if name.lower() not in presents
    ]


def _backup_session_file(dest: Path) -> None:
    """Copy an existing session file aside, timestamped, before it is replaced.

    Best effort: a backup failure must never block the upload itself.
    """
    if not dest.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = dest.with_name(f"{dest.name}.{stamp}.bak")
    try:
        backup.write_bytes(dest.read_bytes())
        logger.info("Previous session backed up to {}", backup.name)
    except OSError as exc:
        logger.warning("Could not back up {}: {}", dest.name, exc)
        return

    # Keep the directory bounded: only the N most recent copies survive.
    try:
        anciens = sorted(dest.parent.glob(f"{dest.name}.*.bak"))
        for vieux in anciens[:-_SESSION_BACKUPS_KEPT]:
            vieux.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not prune session backups: {}", exc)


@api_bp.route("/settings/session", methods=["POST"])
def upload_session():
    try:
        platform = (request.form.get("platform") or "").strip()
        if platform not in ("instagram", "reddit", "tiktok", "twitter"):
            return '<small class="text-error">Plateforme invalide</small>', 400

        file = request.files.get("cookies")
        if not file or not file.filename:
            return '<small class="text-error">Aucun fichier selectionne</small>', 400

        raw = file.read()
        # Cap size (cookie files are small; reject anything absurd)
        if len(raw) > 1 * 1024 * 1024:  # 1 MB
            return '<small class="text-error">Fichier trop volumineux</small>', 400
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return '<small class="text-error">Encodage invalide (UTF-8 attendu)</small>', 400

        # Validate JSON shape: must be a list of cookie objects.
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return '<small class="text-error">Fichier JSON invalide</small>', 400
        if not isinstance(parsed, list) or not all(isinstance(c, dict) for c in parsed):
            return '<small class="text-error">Format attendu : tableau de cookies</small>', 400

        # An export missing the authentication cookie is INERT: it would replace
        # a working session with a logged-out one, and the Settings light —
        # which only reads the file's mtime — would turn GREEN at the exact
        # moment the session is destroyed (risque #61, AUDIT.md §6.14).
        manquants = _missing_critical_cookies(platform, parsed)
        if manquants:
            logger.warning(
                "Session upload for {} refused: missing cookie(s) {}", platform, manquants
            )
            return (
                '<small class="text-error">Cookies incomplets : '
                f'{escape(", ".join(manquants))} manquant(s). Session inchangee.</small>',
                400,
            )

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        dest = SESSIONS_DIR / f"{platform}.json"

        # Keep the previous session: a fresh export can still turn out to be
        # expired, and there is no other copy of it anywhere.
        _backup_session_file(dest)

        dest.write_text(content, encoding="utf-8")

        logger.info("Session cookies uploaded for {}", platform)

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        return f'<small class="text-success">Cookies OK ({now_str})</small>'
    except Exception as exc:
        logger.error("Failed to upload session cookies: {}", exc)
        return '<small class="text-error">Erreur upload</small>', 500


# ===========================================================================
# Santé de session (lot « moteur de santé de session »)
# ===========================================================================
# Deux endpoints, deux coûts très différents :
#   * GET  /api/sessions/health          — gratuit, instantané, sans réseau.
#     Il rend le SIGNAL PASSIF (cookies + historique des jobs) fusionné avec le
#     dernier verdict de sonde stocké. C'est ce que l'UI interroge.
#   * POST /api/sessions/<plateforme>/probe — lance la SONDE ACTIVE.
#     Elle démarre un navigateur : la requête HTTP ne l'attend PAS. Elle part
#     dans un thread démon et répond 202 immédiatement ; le résultat se lit
#     ensuite par le GET. Une requête web ne doit jamais pendre des minutes.


@api_bp.route("/sessions/health")
def sessions_health():
    """État de session de toutes les plateformes, les plus urgents d'abord."""
    from app.scraper import session_health as sante

    try:
        etats = sante.etats_de_toutes_les_plateformes()
    except Exception as exc:
        logger.exception("Lecture de la sante des sessions impossible: {}", exc)
        return jsonify({"ok": False, "error": "Erreur serveur"}), 500

    return jsonify({
        "ok": True,
        "sonde_en_cours": sante.sonde_en_cours(),
        "etats": etats,
        "alertes": [e for e in etats if e["urgent"] or e["alerte"]],
    })


@api_bp.route("/sessions/<platform>/health")
def session_health_detail(platform: str):
    """Verdict retenu POUR UNE plateforme, avec les deux signaux séparés."""
    from app.scraper import session_health as sante

    if platform not in sante.PLATEFORMES:
        return jsonify({"ok": False, "error": "Plateforme inconnue"}), 404
    try:
        return jsonify({"ok": True, **sante.etats_detailles(platform)})
    except Exception as exc:
        logger.exception("Sante de session illisible pour {}: {}", platform, exc)
        return jsonify({"ok": False, "error": "Erreur serveur"}), 500


@api_bp.route("/sessions/<platform>/probe", methods=["POST"])
def session_probe(platform: str):
    """Déclenche la sonde active en arrière-plan. Répond tout de suite (202).

    La réponse porte l'état CONNU au moment de l'appel : l'interface peut
    l'afficher sans attendre, puis rafraîchir via `GET /api/sessions/health`.
    """
    from app.scraper import session_health as sante

    if platform not in sante.PLATEFORMES:
        return jsonify({"ok": False, "error": "Plateforme inconnue"}), 404

    if sante.sonde_en_cours():
        return jsonify({
            "ok": False,
            "lancee": False,
            "error": "Une sonde est déjà en cours, réessayez dans un instant.",
        }), 409

    def _sonder():
        try:
            sante.sonder_si_libre(platform)
        except sante.SondeOccupee:
            logger.info("Sonde {} ignoree: une sonde tourne deja", platform)
        except Exception as exc:  # la sonde ne doit jamais tuer son thread
            logger.error("Sonde {} en echec: {}", platform, exc)

    threading.Thread(target=_sonder, daemon=True, name=f"sonde-{platform}").start()

    try:
        etat_connu = sante.etat_courant(platform).en_dict()
    except Exception:
        etat_connu = None

    return jsonify({
        "ok": True,
        "lancee": True,
        "plateforme": platform,
        "message": "Sonde lancée — le résultat apparaîtra dans quelques instants.",
        "etat_connu": etat_connu,
    }), 202


# ===========================================================================
# Quick Download (single URL)
# ===========================================================================
# Each quick download spawns a headless chromium. Without a cap, holding the
# button down spawns one browser per click until the container is OOM-killed
# (lot 2.4). The semaphore is released by the worker thread itself.
_QUICK_DOWNLOAD_MAX = 3
_QUICK_DOWNLOAD_SLOTS = threading.BoundedSemaphore(_QUICK_DOWNLOAD_MAX)


@api_bp.route("/quick-download", methods=["POST"])
def quick_download_url():
    """Download media from a single post URL."""
    # Validate the SHAPE of the body before touching it: get_json returns any
    # JSON type, so `{"url": 1}`, `[1,2]` or `"abc"` used to raise an
    # uncaught AttributeError (risque #8, AUDIT.md §6.3).
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({"error": "Corps JSON invalide : objet attendu"}), 400

    raw_url = data.get("url")
    if raw_url is not None and not isinstance(raw_url, str):
        return jsonify({"error": "URL invalide : chaine attendue"}), 400

    url = (raw_url or "").strip()

    if not url:
        return jsonify({"error": "URL requise"}), 400

    # Detect platform first (fast check)
    from app.scraper.quick_download import detect_platform, validate_public_url
    detection = detect_platform(url)
    if detection is None:
        return jsonify({
            "error": "URL non reconnue. Plateformes supportées: Instagram, TikTok, Twitter/X, Reddit"
        }), 400

    # SSRF gate (lot 2.4). `detect_platform` only `search`es the string: an URL
    # such as `http://169.254.169.254/x#instagram.com/p/aaa` is "recognised"
    # while aiming at the cloud metadata endpoint — and it is a headless
    # browser INSIDE the server's network that would open it.
    faute = validate_public_url(url)
    if faute:
        logger.warning("Quick download refused ({}): {}", faute, url[:120])
        return jsonify({"error": faute}), 400

    platform, post_id = detection

    # Bound the number of headless browsers a burst of clicks can spawn: each
    # quick download is a chromium (~300 MB). Beyond the cap the caller is told
    # to retry rather than the container being OOM-killed.
    if not _QUICK_DOWNLOAD_SLOTS.acquire(blocking=False):
        logger.warning("Quick download refused: {} slots already busy", _QUICK_DOWNLOAD_MAX)
        return jsonify({
            "error": f"Trop de telechargements simultanes (max {_QUICK_DOWNLOAD_MAX}). Reessaie dans un instant.",
        }), 429

    # Run download in background thread
    def _do_download():
        try:
            _run_quick_download(url, platform)
        finally:
            # Always give the slot back, including on an unexpected crash.
            _QUICK_DOWNLOAD_SLOTS.release()

    def _run_quick_download(url: str, platform: str) -> None:
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

    demarre = False
    try:
        t = threading.Thread(target=_do_download, name=f"quick-dl-{post_id}", daemon=True)
        t.start()
        demarre = True
    except RuntimeError as exc:
        logger.error("Quick download thread could not start: {}", exc)
        return jsonify({"error": "Serveur sature, reessaie dans un instant"}), 503
    finally:
        # No carrier thread means no `_do_download`, hence nobody to release the
        # slot: give it back here, or the cap leaks one unit per failure.
        if not demarre:
            _QUICK_DOWNLOAD_SLOTS.release()

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
