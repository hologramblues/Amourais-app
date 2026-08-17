"""
Flask Blueprint for page routes (HTML pages).
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, make_response, redirect, render_template, request, send_from_directory
from werkzeug.utils import safe_join
from loguru import logger
from sqlalchemy import func

from app.config import (
    DATA_DIR,
    DOWNLOAD_DIR,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REFRESH_TOKEN,
    STORAGE_MODE,
)
from app.db import MediaItem, Profile, ScrapeJob, SessionLocal
from app.scraper import session_health as sante
from app.scraper.downloaders import MEDIA_EXTENSIONS
from app.storage import get_gdrive_auth_url, exchange_code

pages_bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------------------
# Favicon
# ---------------------------------------------------------------------------
# `GET /favicon.ico 404` était la SEULE ligne d'erreur de la console,
# sur les 8 écrans, une occurrence par chargement de page. Les critics
# du viewer et du calendrier l'ont tous deux relevée sans pouvoir la
# corriger : elle est au niveau application, hors de tout lot d'écran.
#
# Le glyphe est le même que la marque de la nav (⚔), dessiné en SVG
# inline : aucun fichier binaire à servir, aucune couleur brute hors
# de l'aplat d'accent, et le fond transparent laisse l'onglet du
# navigateur suivre son propre thème.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#2f6fe0"/>'
    '<text x="16" y="23" font-size="19" text-anchor="middle" fill="#ffffff">'
    "⚔</text></svg>"
)


@pages_bp.route("/favicon.ico")
def favicon():
    resp = make_response(_FAVICON_SVG)
    resp.headers["Content-Type"] = "image/svg+xml"
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


# ---------------------------------------------------------------------------
# Helper: read current .env values for settings form
# ---------------------------------------------------------------------------
def _read_env_file() -> dict[str, str]:
    """Valeurs affichées par l'écran Réglages — les valeurs RÉELLES.

    Délègue au lecteur de web/api.py, qui fusionne le .env du projet (défauts)
    PUIS le .env du volume (DATA_DIR/.env, où `save_env` ÉCRIT). L'ancienne
    version ne lisait que le .env du projet : chaque sauvegarde réussie
    devenait invisible au rechargement — champ vidé, badge « Non configuré »
    malgré un jeton actif (audit §4.6 : « le formulaire n'affiche jamais les
    valeurs réellement enregistrées »).
    """
    from app.web.api import _read_env_file as _lire_env_fusionne

    return _lire_env_fusionne()


# ---------------------------------------------------------------------------
# SANTÉ DE SESSION — mise en mots pour l'écran
# ---------------------------------------------------------------------------
# L'ancien calcul tenait en deux lignes : le fichier de cookies existe, et il a
# telle date de modification → « OK » en vert. Mesuré le 15/08/2026 sur la vraie
# session Instagram (fichier du 10 mars, `ds_user_id` expiré depuis 68 jours,
# scrape rapportant 0 média sur un mur de connexion), ce voyant restait VERT.
#
# Il est remplacé ici par le moteur `app.scraper.session_health`, qui rend un
# état parmi quatre. Ce module ne DÉCIDE plus rien : il met en français, en date
# absolue et en geste correctif ce que le moteur a déjà tranché.
_NOMS_PLATEFORMES = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "twitter": "Twitter / X",
    "reddit": "Reddit",
}

#: Identifiant court utilisé par les cibles HTMX de l'écran Réglages.
_SLUGS_PLATEFORMES = {
    "instagram": "ig",
    "tiktok": "tt",
    "twitter": "tw",
    "reddit": "rd",
}

#: État du moteur → ton visuel. Trois couleurs sémantiques, plus le GRIS de
#: l'indéterminé. « inconnu » n'emprunte jamais le vert : c'est exactement la
#: confusion que ce chantier corrige.
_TONS = {
    "connecté": "ok",
    "bloqué": "warn",
    "déconnecté": "danger",
    "inconnu": "neutre",
}

#: Libellé affiché À CÔTÉ de la pastille — le sens ne repose jamais sur la
#: seule couleur. Accordé au féminin : on parle de « la session ».
_LIBELLES = {
    "connecté": "Connectée",
    "bloqué": "Bloquée",
    "déconnecté": "Déconnectée",
    "inconnu": "Indéterminée",
}

_MOIS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def _date_fr(ts: int | float | None) -> str | None:
    """Horodatage unix → date ABSOLUE en français (« 10 mars à 20:47 »).

    Absolue et non relative : « il y a 5 mois » se lit vite mais ne permet pas
    de recouper avec un souvenir (« je les ai réimportés après les vacances »).
    L'année n'apparaît que si elle n'est pas l'année courante — la porter tout
    le temps allonge la ligne sans rien apprendre.
    """
    if not ts:
        return None
    try:
        d = datetime.fromtimestamp(float(ts))
    except (OverflowError, OSError, ValueError):
        return None
    annee = "" if d.year == datetime.now().year else f" {d.year}"
    return f"{d.day} {_MOIS_FR[d.month - 1]}{annee} à {d:%H:%M}"


def _mise_en_mots(etat: dict) -> dict:
    """Enrichit un verdict du moteur de tout ce que le gabarit doit afficher."""
    plateforme = etat.get("plateforme", "")
    brut = etat.get("etat") or "inconnu"
    jamais_vu = not etat.get("dernier_succes") and not etat.get("derniere_sonde")

    libelle = _LIBELLES.get(brut, "Indéterminée")
    if brut == "inconnu" and jamais_vu:
        # « Jamais vérifiée » ne doit surtout pas ressembler à « tout va bien ».
        libelle = "Jamais vérifiée"

    succes = _date_fr(etat.get("dernier_succes"))
    if succes:
        succes_texte = f"Dernière session valide le {succes}"
    elif jamais_vu:
        succes_texte = "Jamais vérifiée — aucune session valide connue"
    else:
        succes_texte = "Aucune session valide connue"

    cookies = _date_fr(etat.get("cookies_le"))
    cookies_texte = f"Cookies importés le {cookies}" if cookies else "Aucun cookie importé"

    expire = etat.get("expire_le")
    expire_texte = None
    if expire:
        quand = _date_fr(expire)
        if quand:
            passe = expire < datetime.now().timestamp()
            expire_texte = f"Cookies expirés le {quand}" if passe else f"Cookies valables jusqu'au {quand}"

    sonde = _date_fr(etat.get("derniere_sonde"))
    sonde_texte = f"Dernière vérification en direct le {sonde}" if sonde else "Jamais vérifiée en direct"

    return {
        **etat,
        "nom": _NOMS_PLATEFORMES.get(plateforme, plateforme.capitalize()),
        "slug": _SLUGS_PLATEFORMES.get(plateforme, plateforme),
        "ton": _TONS.get(brut, "neutre"),
        "libelle": libelle,
        "succes_texte": succes_texte,
        "cookies_texte": cookies_texte,
        "expire_texte": expire_texte,
        "sonde_texte": sonde_texte,
    }


def _sante_des_sessions() -> list[dict]:
    """Les 4 plateformes, LES PLUS URGENTES D'ABORD, prêtes à afficher.

    Le tri vient du moteur (`GRAVITE`) : une plateforme en panne remonte en tête
    de liste toute seule. Si le moteur casse, l'écran doit rester debout : on
    rend une liste vide plutôt qu'une 500, et le gabarit le dit.
    """
    try:
        return [_mise_en_mots(e) for e in sante.etats_de_toutes_les_plateformes()]
    except Exception as exc:  # pragma: no cover - filet
        logger.error("Sante des sessions illisible: {}", exc)
        return []


def _resume_sessions(etats: list[dict]) -> dict:
    """Compteur pour le tableau de bord — un chiffre ET une phrase.

    Un chiffre seul (« 1 ») n'apprend rien : le résumé porte la CAUSE et le
    lien vers le GESTE. Une session morte arrête tout le produit, elle ne peut
    pas rester invisible tant qu'on n'ouvre pas les Réglages.
    """
    en_panne = [e for e in etats if e.get("urgent")]
    connectees = [e for e in etats if e.get("etat") == "connecté"]
    indeterminees = [e for e in etats if e.get("etat") == "inconnu"]

    if en_panne:
        pire = en_panne[0]
        ton = _TONS.get(pire.get("etat"), "danger")
        valeur = str(len(en_panne))
        titre = (
            f"Session {pire['nom']} {pire['libelle'].lower()}"
            if len(en_panne) == 1
            else f"{len(en_panne)} sessions à reconnecter"
        )
        detail = pire.get("geste") or ""
    elif connectees:
        ton, valeur = "ok", str(len(connectees))
        titre = "session valide" if len(connectees) == 1 else "sessions valides"
        detail = "Aucune plateforme ne demande d'action."
    else:
        ton, valeur = "neutre", str(len(indeterminees) or len(etats))
        titre = "Aucune session vérifiée"
        detail = "Importe tes cookies, puis lance « Vérifier maintenant »."

    return {
        "ton": ton,
        "valeur": valeur,
        "titre": titre,
        "detail": detail,
        "en_panne": en_panne,
        "total": len(etats),
    }


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

        # Santé de session : gratuite (fichiers de cookies + historique des
        # jobs déjà en base), aucun appel réseau, donc elle peut vivre sur le
        # tableau de bord sans le ralentir.
        etats_sessions = _sante_des_sessions()

        return render_template(
            "dashboard.html",
            page="dashboard",
            title="Dashboard",
            profiles=profiles_list,
            sessions_sante=etats_sessions,
            sessions_resume=_resume_sessions(etats_sessions),
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
            # Sans `title`, layout.html rend « SAMOURAIS » tout court :
            # quatre onglets du navigateur portaient le même nom.
            title="Profils",
            profiles=profiles_with_counts,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@pages_bp.route("/jobs")
def jobs_page():
    return render_template("jobs.html", page="jobs", title="Jobs")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@pages_bp.route("/settings")
def settings_page():
    gdrive_connected = bool(GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN)

    # ANCIEN CALCUL, RETIRÉ : `SESSIONS_DIR/<p>.json` existe → vert, avec sa
    # mtime pour toute preuve. Un fichier lisible dont la plateforme a invalidé
    # le `sessionid` côté serveur affichait « OK » en vert. Le voyant vient
    # désormais du moteur, qui lit AUSSI l'expiration des cookies critiques,
    # l'historique des jobs et le dernier verdict de sonde.
    etats_sessions = _sante_des_sessions()

    env_values = _read_env_file()

    return render_template(
        "settings.html",
        page="settings",
        title="Réglages",
        gdrive_connected=gdrive_connected,
        sessions_sante=etats_sessions,
        sessions_resume=_resume_sessions(etats_sessions),
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
        # Persist the token directly to the persistent .env instead of echoing
        # it back in the HTTP response (avoids leaking the long-lived secret).
        from app.web.api import _write_env_file
        _write_env_file({"GOOGLE_REFRESH_TOKEN": refresh_token})
        return (
            "<h1>Google Drive connecte !</h1>"
            "<p>Le refresh token a ete enregistre automatiquement.</p>"
            '<a href="/settings">Retour aux settings</a>'
        )
    except Exception as exc:
        logger.error("OAuth callback error: {}", exc)
        return "<h1>Erreur</h1><p>Echec de la connexion Google Drive.</p><a href=\"/settings\">Retour</a>"


# ---------------------------------------------------------------------------
# Media Viewer
# ---------------------------------------------------------------------------
@pages_bp.route("/viewer")
def viewer_page():
    return render_template("viewer.html", page="viewer")


@pages_bp.route("/media/file/<path:filename>")
def serve_media_file(filename):
    """Serve downloaded media files with caching and Range request support."""
    # ?dl=1 forces a download (Content-Disposition: attachment)
    # conditional_response=True enables HTTP 304 (ETag/Last-Modified) + Range (206)
    #
    # Lot 2.5 (§6.6 / T10) — tout ce qui n'est pas une extension média connue
    # est servi en PIÈCE JOINTE et en `application/octet-stream`. Le
    # téléchargeur applique désormais une liste blanche à l'écriture, mais les
    # fichiers déjà sur le volume (`.html`, `.svg`… écrits avant ce lot)
    # seraient toujours rendus INLINE sur l'origine de l'application : XSS
    # stockée. `nosniff` complète la mesure en interdisant au navigateur de
    # deviner un type plus « exécutable » que celui annoncé.
    est_media = Path(filename).suffix.lower() in MEDIA_EXTENSIONS
    force_download = request.args.get("dl") == "1"

    resp = send_from_directory(
        str(DOWNLOAD_DIR), filename, conditional=True,
        as_attachment=force_download or not est_media,
        mimetype=None if est_media else "application/octet-stream",
    )
    # Cache for 7 days — files don't change once downloaded
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
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
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # safe_join rejects path traversal (../) and absolute paths -> returns None
    safe_source = safe_join(str(DOWNLOAD_DIR), filename)
    if safe_source is None:
        return "Invalid path", 400
    source_path = Path(safe_source)
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
    resp.headers["X-Content-Type-Options"] = "nosniff"
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
