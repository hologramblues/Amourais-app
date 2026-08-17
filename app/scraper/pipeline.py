"""
Scraping pipeline orchestration.

Coordinates the full lifecycle of a scrape job:
    extract -> insert -> download -> upload -> cleanup -> update stats
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.config import (
    BACKFILL_MAX_SCROLLS,
    DAILY_MAX_SCROLLS,
    DAILY_SCRAPE_INTERVAL_MINUTES,
    STORAGE_MODE,
    get_apify_settings,
    get_proxy_for_platform,
)
from app.db import MediaItem, Profile, ProfileSnapshot, ScrapeJob, SessionLocal
from app.scraper.base import ExtractOptions, PlatformExtractor
from app.scraper.downloaders import download_media

# ---------------------------------------------------------------------------
# Platform extractor registry
# ---------------------------------------------------------------------------
_EXTRACTORS: dict[str, type[PlatformExtractor]] = {}


def _get_extractor(platform: str) -> PlatformExtractor:
    """Lazy-load and cache platform extractor instances."""
    if not _EXTRACTORS:
        # Import here to avoid circular imports at module level
        from app.scraper.instagram import InstagramExtractor
        from app.scraper.reddit import RedditExtractor
        from app.scraper.tiktok import TikTokExtractor
        from app.scraper.twitter import TwitterExtractor

        _EXTRACTORS["instagram"] = InstagramExtractor
        _EXTRACTORS["reddit"] = RedditExtractor
        _EXTRACTORS["tiktok"] = TikTokExtractor
        _EXTRACTORS["twitter"] = TwitterExtractor

    cls = _EXTRACTORS.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return cls()


def _choisir_extracteur(platform: str) -> tuple[PlatformExtractor, str]:
    """Aiguillage du backend d'extraction (lot A) : (extracteur, nom du backend).

    Instagram passe par l'API Apify quand `APIFY_TOKEN` est posé — le jeton
    est relu À CHAUD (`get_apify_settings` recharge le .env persistant, comme
    les proxys), donc poser ou retirer le jeton dans Réglages change de
    backend au scrape suivant, sans redémarrage. Toutes les autres plateformes
    — et Instagram sans jeton — restent sur le backend navigateur.

    `_EXTRACTORS` n'est PAS modifié : le registre reste celui des extracteurs
    navigateur sous contrat (tests/test_extracteurs_contrat.py).
    """
    if platform == "instagram":
        token, _acteur = get_apify_settings()
        if token:
            from app.scraper.instagram_apify import InstagramApifyExtractor

            return InstagramApifyExtractor(), "apify"
    return _get_extractor(platform), "navigateur"


# ---------------------------------------------------------------------------
# Empreintes de déduplication (lot A)
# ---------------------------------------------------------------------------
# Deux empreintes, deux questions différentes :
#   md5   — « est-ce EXACTEMENT le même fichier ? » Un seul octet de différence
#           change tout le condensat. C'est la seule réponse fiable au doublon
#           strict, et elle ne coûte qu'une lecture séquentielle.
#   phash — « est-ce visuellement la MÊME image ? » Un dhash 64 bits : on
#           ramène l'image à 9x8 pixels en niveaux de gris, puis on compare
#           chaque pixel à son voisin de droite. 8 comparaisons par ligne,
#           8 lignes : 64 bits. Un recadrage léger, un ré-encodage, un
#           filigrane déplacent peu de bits ; deux photos différentes en
#           déplacent une trentaine.
#
# Pillow n'est PAS installé dans ce venv (vérifié) et n'est tiré par aucune
# dépendance. On n'en ajoute pas : ffmpeg est déjà là — il fabrique déjà les
# vignettes de l'application — et il sait sortir une image redimensionnée en
# niveaux de gris en un seul appel, pour une image comme pour une vidéo.
_TAILLE_BLOC_MD5 = 1 << 20  # 1 Mio

_EXTENSIONS_VIDEO = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")

#: Une image UNIFORME (noire, blanche, unie) donne un dhash dégénéré : tous les
#: bits identiques. Elle « ressemble » alors à toutes les autres images unies,
#: ce qui produit un groupe géant et faux. On calcule quand même l'empreinte
#: (la colonne cesse d'être nulle, donc on ne resonde pas le fichier à chaque
#: passage), mais l'écran des quasi-doublons l'écarte explicitement.
PHASH_DEGENERE = ("0000000000000000", "ffffffffffffffff")

#: Délai maximum accordé à ffmpeg pour une seule empreinte perceptuelle.
_TIMEOUT_FFMPEG_S = 20


def est_une_video(chemin: str | Path) -> bool:
    return Path(chemin).suffix.lower() in _EXTENSIONS_VIDEO


def md5_fichier(chemin: str | Path) -> str | None:
    """MD5 hexadécimal du fichier, ou None s'il est illisible."""
    h = hashlib.md5()
    try:
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(_TAILLE_BLOC_MD5), b""):
                h.update(bloc)
    except OSError as exc:
        logger.debug("MD5 impossible pour {} : {}", chemin, exc)
        return None
    return h.hexdigest()


def _vignette_9x8(chemin: Path, video: bool) -> bytes | None:
    """72 octets : une image 9x8 en niveaux de gris, extraite par ffmpeg.

    Pour une VIDÉO on prend une image de référence à 0,1 s — pas l'image
    zéro, souvent noire — et on retombe sur l'image zéro si le fichier est
    plus court que ça. Une vidéo n'a pas de « hash perceptuel » propre : ce
    qui est comparé est cette image, et l'écran le dit.
    """
    def _lancer(args_entree: list[str]) -> bytes | None:
        cmd = (
            ["ffmpeg", "-v", "error", "-nostdin", "-threads", "1"]
            + args_entree
            + [
                "-i", str(chemin),
                "-frames:v", "1",
                # `format=gray` AVANT `scale` : la moyenne se fait alors sur la
                # luminance, pas sur un canal choisi au hasard.
                "-vf", "format=gray,scale=9:8:flags=bilinear",
                "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ]
        )
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_FFMPEG_S)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("ffmpeg indisponible pour {} : {}", chemin, exc)
            return None
        if res.returncode != 0 or len(res.stdout) < 72:
            return None
        return res.stdout[:72]

    if video:
        octets = _lancer(["-ss", "0.1"])
        if octets is not None:
            return octets
    return _lancer([])


def phash_fichier(chemin: str | Path, media_type: str | None = None) -> str | None:
    """dhash 64 bits en hexadécimal (16 caractères), ou None si non calculable."""
    chemin = Path(chemin)
    video = media_type == "video" or est_une_video(chemin)
    pixels = _vignette_9x8(chemin, video)
    if pixels is None:
        return None

    bits = 0
    for ligne in range(8):
        base = ligne * 9
        for colonne in range(8):
            bits = (bits << 1) | (1 if pixels[base + colonne] > pixels[base + colonne + 1] else 0)
    return format(bits, "016x")


def empreintes(chemin: str | Path, media_type: str | None = None) -> tuple[str | None, str | None]:
    """(md5, phash) pour un fichier local. Aucune exception ne sort d'ici."""
    if not chemin or not os.path.isfile(chemin):
        return None, None
    return md5_fichier(chemin), phash_fichier(chemin, media_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_ts() -> int:
    """Current unix timestamp as integer."""
    return int(datetime.now().timestamp())


def _plateforme_non_atteinte(result) -> str | None:
    """Return an error message when *result* proves the platform was never reached.

    Two signals (risque #53, §4.20):

    * ``fetch_error`` — posé par les 4 extracteurs quand ``StealthyFetcher.fetch``
      lève. C'est le signal explicite ;
    * un résultat TOTALEMENT vide — aucun média, aucun post vu, et pas la moindre
      bribe de profil. Un extracteur qui a réellement atteint la page en rapporte
      toujours quelque chose (nom, avatar, ou au moins un post vu) ; du vide
      intégral signifie navigateur mort, proxy mort, session détruite ou blocage.

    Renvoie ``None`` quand la plateforme a bien répondu (y compris « rien de neuf »).
    """
    fetch_error = getattr(result, "fetch_error", None)
    if fetch_error:
        return str(fetch_error)

    profile_info = getattr(result, "profile_info", None)
    profil_vide = profile_info is None or not any(
        valeur is not None for valeur in vars(profile_info).values()
    )
    if not result.media and not getattr(result, "total_seen", 0) and profil_vide:
        return (
            "Aucune donnée reçue de la plateforme (navigateur, proxy, session ou "
            "blocage) — le profil n'a pas été atteint"
        )
    return None


#: Statuts TERMINAUX d'un job : plus rien ne s'exécutera après eux, ils
#: s'horodatent donc tous. `empty` en fait partie (risque #3, §4.1, lot 3.3) —
#: son absence de `completed_at` laissait l'interface sans date de fin sur les
#: jobs quotidiens sans nouveauté, c'est-à-dire sur la moitié d'entre eux.
_STATUTS_TERMINAUX = ("completed", "failed", "partial", "empty")


def _mark_job(db, job: ScrapeJob, status: str, error: str | None = None) -> None:
    """Update job status and optional error message."""
    job.status = status
    if status == "running" and job.started_at is None:
        job.started_at = _now_ts()
    if status in _STATUTS_TERMINAUX:
        job.completed_at = _now_ts()
    if error:
        job.error_message = error
    db.commit()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_scrape_job(job_id: int) -> None:
    """
    Execute a complete scrape job identified by *job_id*.

    Steps:
        1. Load job and profile from DB
        2. Extract media from the platform
        3. Insert new media items (deduplicated)
        4. Download pending media
        5. Upload to Google Drive
        6. Clean up local files
        7. Update job stats and profile state
    """
    db = SessionLocal()
    try:
        _run_scrape_job_inner(db, job_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_scrape_job_inner(db, job_id: int) -> None:  # noqa: C901 (complexity accepted)
    # ------------------------------------------------------------------
    # 1. Load job and profile
    # ------------------------------------------------------------------
    job: ScrapeJob | None = db.query(ScrapeJob).filter_by(id=job_id).first()
    if job is None:
        logger.error("ScrapeJob {} not found, aborting", job_id)
        return

    profile: Profile | None = db.query(Profile).filter_by(id=job.profile_id).first()
    if profile is None:
        logger.error("Profile {} not found for job {}", job.profile_id, job_id)
        _mark_job(db, job, "failed", "Profile not found")
        return

    logger.info(
        "Starting job {} for @{} on {} (mode={})",
        job_id,
        profile.username,
        profile.platform,
        profile.scrape_mode,
    )

    # ------------------------------------------------------------------
    # 2. Determine scrape mode and max scrolls
    # ------------------------------------------------------------------
    scrape_mode = profile.scrape_mode or "daily"
    max_scrolls = (
        BACKFILL_MAX_SCROLLS if scrape_mode == "backfill" else DAILY_MAX_SCROLLS
    )
    proxy = get_proxy_for_platform(profile.platform)
    if proxy:
        logger.info("Using proxy for {}: {}...{}", profile.platform, proxy[:20], proxy[-10:])

    options = ExtractOptions(
        scrape_mode=scrape_mode,
        max_scrolls=max_scrolls,
        backfill_from=float(profile.backfill_from) if profile.backfill_from else None,
        backfill_to=float(profile.backfill_to) if profile.backfill_to else None,
        proxy=proxy or None,
    )

    # ------------------------------------------------------------------
    # 3. Mark job as running
    # ------------------------------------------------------------------
    _mark_job(db, job, "running")

    # ------------------------------------------------------------------
    # 4. Get known post IDs for deduplication
    # ------------------------------------------------------------------
    known_rows = (
        db.query(MediaItem.post_id)
        .filter(MediaItem.profile_id == profile.id)
        .all()
    )
    known_post_ids: set[str] = {row[0] for row in known_rows}
    logger.debug("Known post IDs for profile {}: {}", profile.id, len(known_post_ids))

    # ------------------------------------------------------------------
    # 5. Extract media from platform
    # ------------------------------------------------------------------
    try:
        extractor, backend = _choisir_extracteur(profile.platform)
        # Le backend AYANT SERVI est noté dans les logs du job (lot A) : sans
        # cette ligne, impossible de savoir si un média vient d'Apify (payant)
        # ou du navigateur.
        logger.info(
            "Job {}: extraction de @{} ({}) via le backend « {} »",
            job_id, profile.username, profile.platform, backend,
        )
        result = extractor.extract(profile.profile_url, known_post_ids, options)
    except Exception as exc:
        logger.exception("Extraction failed for job {}: {}", job_id, exc)
        # risque #10 / §4.5 — horodater malgré l'échec : sans cela le profil reste
        # éternellement « dû » et consomme un slot de scrape à chaque cycle.
        profile.last_scraped_at = _now_ts()
        profile.updated_at = _now_ts()
        _mark_job(db, job, "failed", f"Extraction error: {exc}")
        return

    # ------------------------------------------------------------------
    # 5b. Échec TOTAL de fetch : `failed`, jamais `empty` (risque #53, §4.20)
    #     `last_scraped_at` reste intact : la plateforme n'a pas été atteinte,
    #     le profil doit redevenir dû au cycle suivant.
    # ------------------------------------------------------------------
    erreur_de_fetch = _plateforme_non_atteinte(result)
    if erreur_de_fetch:
        logger.error("Fetch failed for job {} (@{}): {}", job_id, profile.username, erreur_de_fetch)
        _mark_job(db, job, "failed", f"Fetch error: {erreur_de_fetch}")
        return

    media_found = len(result.media)
    total_seen = getattr(result, "total_seen", media_found)
    logger.info(
        "Extracted {} new media items for job {} (total seen on page: {})",
        media_found, job_id, total_seen,
    )

    # ------------------------------------------------------------------
    # 6. Update profile display_name / avatar_url / account stats
    # ------------------------------------------------------------------
    if result.profile_info:
        if result.profile_info.display_name:
            profile.display_name = result.profile_info.display_name
        if result.profile_info.avatar_url:
            profile.avatar_url = result.profile_info.avatar_url
        if result.profile_info.biography is not None:
            profile.biography = result.profile_info.biography
        if result.profile_info.is_verified is not None:
            profile.is_verified = result.profile_info.is_verified
        if result.profile_info.followers_count is not None:
            profile.followers_count = result.profile_info.followers_count
        if result.profile_info.following_count is not None:
            profile.following_count = result.profile_info.following_count
        if result.profile_info.media_count is not None:
            profile.media_count = result.profile_info.media_count
        profile.updated_at = _now_ts()
        db.commit()

        # Create a daily snapshot (max 1 per day per profile)
        if result.profile_info.followers_count is not None:
            today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            existing_snapshot = (
                db.query(ProfileSnapshot)
                .filter(
                    ProfileSnapshot.profile_id == profile.id,
                    ProfileSnapshot.snapshot_at >= today_start,
                )
                .first()
            )
            if existing_snapshot:
                # Update today's snapshot
                existing_snapshot.followers_count = result.profile_info.followers_count
                existing_snapshot.following_count = result.profile_info.following_count
                existing_snapshot.media_count = result.profile_info.media_count
            else:
                # Create new snapshot
                snapshot = ProfileSnapshot(
                    profile_id=profile.id,
                    followers_count=result.profile_info.followers_count,
                    following_count=result.profile_info.following_count,
                    media_count=result.profile_info.media_count,
                )
                db.add(snapshot)
            db.commit()
            logger.info(
                "Profile snapshot saved for @{}: {} followers, {} following, {} posts",
                profile.username,
                result.profile_info.followers_count,
                result.profile_info.following_count,
                result.profile_info.media_count,
            )

    # ------------------------------------------------------------------
    # 7. Insert new media items (ON CONFLICT DO NOTHING via IntegrityError)
    # ------------------------------------------------------------------
    media_new = 0
    for item in result.media:
        media_item = MediaItem(
            profile_id=profile.id,
            platform=profile.platform,
            post_id=item.post_id,
            post_url=item.post_url,
            media_type=item.media_type,
            media_url=item.media_url,
            caption=item.caption,
            width=item.width,
            height=item.height,
            duration=item.duration,
            ig_like_count=getattr(item, "like_count", None),
            ig_comment_count=getattr(item, "comment_count", None),
            ig_view_count=getattr(item, "view_count", None),
            posted_at=(
                int(item.posted_at.timestamp()) if item.posted_at else None
            ),
            status="pending",
        )
        try:
            # risque #9 / §4.4 — SAVEPOINT : sans lui, `db.rollback()` annulait la
            # transaction ENTIÈRE depuis le dernier commit, donc tous les items
            # sains déjà flushés du même job. Ici le doublon n'annule que lui-même,
            # et `media_new` n'est incrémenté qu'après un flush réussi.
            with db.begin_nested():
                db.add(media_item)
                db.flush()
            media_new += 1
        except IntegrityError:
            # Item already exists -- skip silently
            logger.debug(
                "Duplicate media item skipped: post_id={}, url={}",
                item.post_id,
                item.media_url,
            )

    db.commit()
    logger.info("Inserted {} new media items (of {} found)", media_new, media_found)

    # ------------------------------------------------------------------
    # 8. Download pending media for this profile
    # ------------------------------------------------------------------
    pending_items = (
        db.query(MediaItem)
        .filter(
            MediaItem.profile_id == profile.id,
            MediaItem.status == "pending",
        )
        .all()
    )

    media_downloaded = 0
    for mi in pending_items:
        try:
            dl = download_media(mi.media_url)
            mi.local_path = dl.local_path
            mi.file_size = dl.file_size
            mi.content_hash = dl.content_hash
            # Empreintes calculées À L'INGESTION : c'est le seul moment où le
            # fichier est certainement là (en mode gdrive il est effacé du
            # disque à l'étape 10). Un échec n'interrompt rien — la colonne
            # reste nulle et le calcul différé la rattrapera.
            try:
                mi.md5, mi.phash = empreintes(dl.local_path, mi.media_type)
            except Exception as exc:  # noqa: BLE001 — jamais fatal
                logger.warning("Empreintes non calculées pour {} : {}", mi.id, exc)
            mi.downloaded_at = _now_ts()
            mi.status = "downloaded"
            mi.error_message = None
            media_downloaded += 1
            db.commit()
        except Exception as exc:
            logger.warning(
                "Download failed for media {} (post {}): {}",
                mi.id,
                mi.post_id,
                exc,
            )
            mi.status = "download_failed"
            mi.error_message = str(exc)[:500]
            mi.retry_count += 1
            db.commit()

    logger.info("Downloaded {} media items", media_downloaded)

    # ------------------------------------------------------------------
    # 9. Upload to Google Drive (or mark as stored locally)
    # ------------------------------------------------------------------
    downloaded_items = (
        db.query(MediaItem)
        .filter(
            MediaItem.profile_id == profile.id,
            MediaItem.status == "downloaded",
        )
        .all()
    )

    media_uploaded = 0

    if STORAGE_MODE == "gdrive":
        from app.storage import upload_to_gdrive

        for mi in downloaded_items:
            if not mi.local_path or not os.path.isfile(mi.local_path):
                logger.warning(
                    "Local file missing for media {}, marking for re-download",
                    mi.id,
                )
                mi.status = "pending"
                mi.local_path = None
                db.commit()
                continue

            try:
                import mimetypes
                guessed_mime = mimetypes.guess_type(mi.local_path)[0] or "application/octet-stream"
                gdrive = upload_to_gdrive(
                    local_path=mi.local_path,
                    platform=profile.platform,
                    username=profile.username,
                    post_id=mi.post_id,
                    mime_type=guessed_mime,
                )
                mi.gdrive_file_id = gdrive["file_id"]
                mi.gdrive_url = gdrive["web_view_link"]
                mi.uploaded_at = _now_ts()
                mi.status = "uploaded"
                mi.error_message = None
                media_uploaded += 1
                db.commit()

            except Exception as exc:
                logger.warning(
                    "Upload failed for media {} (post {}): {}. "
                    "File kept locally at {}",
                    mi.id,
                    mi.post_id,
                    exc,
                    mi.local_path,
                )
                mi.status = "upload_failed"
                mi.error_message = str(exc)[:500]
                mi.retry_count += 1
                db.commit()

        logger.info("Uploaded {} media items to Google Drive", media_uploaded)

    else:
        # Local-only mode: mark downloaded items as stored
        for mi in downloaded_items:
            if mi.local_path and os.path.isfile(mi.local_path):
                mi.status = "uploaded"
                mi.uploaded_at = _now_ts()
                media_uploaded += 1
        db.commit()
        logger.info(
            "Local mode: {} media items stored in {}",
            media_uploaded,
            mi.local_path if downloaded_items else "N/A",
        )

    # ------------------------------------------------------------------
    # 10. Clean up local files after successful upload (gdrive only)
    # ------------------------------------------------------------------
    if STORAGE_MODE == "gdrive":
        uploaded_items = (
            db.query(MediaItem)
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.status == "uploaded",
                MediaItem.local_path.isnot(None),
            )
            .all()
        )

        for mi in uploaded_items:
            if mi.local_path and os.path.isfile(mi.local_path):
                try:
                    os.unlink(mi.local_path)
                    logger.debug("Cleaned up local file: {}", mi.local_path)
                except OSError as exc:
                    logger.warning("Failed to delete local file {}: {}", mi.local_path, exc)
                mi.local_path = None
        db.commit()

    # ------------------------------------------------------------------
    # 11. Update job stats
    # ------------------------------------------------------------------
    job.media_found = media_found
    job.media_new = media_new
    job.media_downloaded = media_downloaded
    job.media_uploaded = media_uploaded

    # risque #3 / §4.1 — le statut se décide sur `total_seen`, PAS sur
    # `media_found`. `media_found = len(result.media)` ne compte que les médias
    # NOUVEAUX : un profil sain dont aucun post n'a bougé depuis le dernier
    # cycle en rapporte zéro, et tombait donc dans `empty` — le même badge
    # orange qu'une session morte. `total_seen` compte les posts VUS sur la
    # page ; les 4 extracteurs l'incrémentent désormais (lot 1.5).
    #
    # `empty` garde un sens précis, et un seul : la page n'a rien montré du
    # tout. L'échec TOTAL de fetch, lui, est déjà parti en `failed` à l'étape 5b
    # (risque #53) — les deux situations ne partagent plus de statut.
    #
    # `media_found` reste dans la condition en garde : un extracteur qui
    # rapporterait des médias sans alimenter `total_seen` ne doit pas produire
    # un `empty` absurde.
    if total_seen == 0 and media_found == 0:
        # Nothing seen on the page at all — expired/missing cookies, a block or
        # a genuinely empty profile. NOT a success: flagged distinctly.
        final_status = "empty"
    elif media_downloaded < len(pending_items) or media_uploaded < len(downloaded_items):
        final_status = "partial"
    else:
        final_status = "completed"

    _mark_job(db, job, final_status)

    logger.info(
        "Job {} finished ({}): found={}, new={}, downloaded={}, uploaded={}",
        job_id,
        final_status,
        media_found,
        media_new,
        media_downloaded,
        media_uploaded,
    )

    # ------------------------------------------------------------------
    # 12. Auto-transition: backfill -> daily when truly exhausted
    # ------------------------------------------------------------------
    if scrape_mode == "backfill":
        if media_new == 0 and total_seen == 0:
            # Page returned zero content at all — likely blocked or empty profile
            logger.info(
                "Backfill for @{}: page returned 0 content. "
                "Keeping backfill mode (may be a temporary block).",
                profile.username,
            )
        elif media_new == 0 and total_seen > 0:
            # Found posts but they're all already in DB — true backfill complete
            logger.info(
                "Backfill complete for @{} ({} posts seen, all already known). "
                "Switching to daily mode (interval={}min)",
                profile.username,
                total_seen,
                DAILY_SCRAPE_INTERVAL_MINUTES,
            )
            profile.scrape_mode = "daily"
            profile.scrape_interval_minutes = DAILY_SCRAPE_INTERVAL_MINUTES
            profile.updated_at = _now_ts()
            db.commit()

    # ------------------------------------------------------------------
    # 13. Update profile.last_scraped_at
    # ------------------------------------------------------------------
    profile.last_scraped_at = _now_ts()
    profile.updated_at = _now_ts()
    db.commit()
