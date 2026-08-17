"""
APScheduler-based job scheduling for the SAMOURAIS SCRAPPER.

Recurring tasks (see `start_scheduler` for the authoritative list):
    1. Check due profiles every 30 minutes and enqueue scrape jobs.
    2. Retry failed media downloads/uploads every 2 hours.
    3. Clean up stale temp files daily at 03:00.
    4. Check for due scheduled posts every 5 minutes.
    5/6. Collect Instagram stats and media insights via the Graph API.
    7. Probe each platform's session health every 6 hours — WITHOUT ever
       taking a scrape slot (see `sonder_les_sessions`).

Also provides an API for triggering immediate (manual) scrape jobs.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.config import (
    DELAY_BETWEEN_PROFILES_MS,
    DOWNLOAD_DIR,
    MAX_CONCURRENT_SCRAPES,
)
from app.db import (
    MediaItem,
    Profile,
    SavedMeme,
    ScheduledPost,
    ScrapeJob,
    SessionLocal,
)

# ---------------------------------------------------------------------------
# Scheduler singleton
# ---------------------------------------------------------------------------
scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce": True,          # collapse missed runs into one
        "max_instances": 1,        # prevent overlapping executions
        "misfire_grace_time": 300,  # 5 min grace for late triggers
    },
    timezone="UTC",
)

# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------
# Tracks profile IDs that currently have a running job to prevent duplicates.
_running_profiles: set[int] = set()
_running_lock = threading.Lock()

# Global cap on simultaneously-running scrape jobs (each spawns a headless
# browser). A job thread blocks on this semaphore before invoking the pipeline,
# so at most MAX_CONCURRENT_SCRAPES browsers run at once regardless of how many
# profiles fall due together. Guards against OOM kills on small containers.
_scrape_semaphore = threading.Semaphore(max(1, MAX_CONCURRENT_SCRAPES))


def _acquire_profile(profile_id: int) -> bool:
    """Try to mark a profile as running. Returns True if acquired."""
    with _running_lock:
        if profile_id in _running_profiles:
            return False
        _running_profiles.add(profile_id)
        return True


def _release_profile(profile_id: int) -> None:
    """Mark a profile as no longer running."""
    with _running_lock:
        _running_profiles.discard(profile_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_ts() -> int:
    return int(datetime.now().timestamp())


def _fail_job(job_id: int, message: str) -> None:
    """Mark a still-open job as ``failed`` from outside the pipeline.

    A job left in ``queued``/``running`` with no thread behind it freezes its
    profile: ``check_due_profiles`` skips any profile that already has an
    active job (risque #54, AUDIT.md §4.2). Opens its own session because the
    callers run outside any request/pipeline session.
    """
    db = SessionLocal()
    try:
        job = db.query(ScrapeJob).filter_by(id=job_id).first()
        if job and job.status in ("queued", "running"):
            job.status = "failed"
            job.error_message = message[:500]
            job.completed_at = _now_ts()
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Could not mark job {} as failed: {}", job_id, exc)
    finally:
        db.close()


def _run_job_safe(job_id: int, profile_id: int) -> None:
    """
    Execute a scrape job in the current thread with proper concurrency
    guarding and error handling.
    """
    if not _acquire_profile(profile_id):
        logger.info(
            "Profile {} already has a running job, skipping job {}",
            profile_id,
            job_id,
        )
        # Do not leave this job `queued` for ever: it would block every future
        # scheduled run of the profile (risque #54, §4.2 chemin A).
        _fail_job(
            job_id,
            "Skipped: profile already has a running job",
        )
        return

    sem_acquired = False
    try:
        # Block until a global scrape slot is free (caps concurrent browsers).
        _scrape_semaphore.acquire()
        sem_acquired = True

        # Import here to avoid circular imports at module level
        from app.scraper.pipeline import run_scrape_job

        run_scrape_job(job_id)
    except Exception as exc:
        logger.exception("Job {} failed with unhandled error: {}", job_id, exc)
        # Mark the job as failed so it does not block future runs
        db = SessionLocal()
        try:
            job = db.query(ScrapeJob).filter_by(id=job_id).first()
            # `queued` too: any exception raised before the pipeline reaches
            # `_mark_job(..., "running")` would otherwise leave the job
            # `queued` for ever (risque #54, §4.2 chemin B).
            if job and job.status in ("queued", "running"):
                job.status = "failed"
                job.error_message = f"Unhandled: {str(exc)[:500]}"
                job.completed_at = _now_ts()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    finally:
        if sem_acquired:
            _scrape_semaphore.release()
        _release_profile(profile_id)


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------
def check_due_profiles() -> None:
    """
    Query active profiles whose scrape interval has elapsed and create
    scrape jobs for each.
    """
    logger.debug("Checking for due profiles...")
    db = SessionLocal()
    try:
        now = _now_ts()
        profiles = (
            db.query(Profile)
            .filter(Profile.is_active == True)  # noqa: E712
            .all()
        )

        due: list[Profile] = []
        for p in profiles:
            if p.last_scraped_at is None:
                # Never scraped -- always due
                due.append(p)
            else:
                elapsed_minutes = (now - p.last_scraped_at) / 60
                if elapsed_minutes >= p.scrape_interval_minutes:
                    due.append(p)

        if not due:
            logger.debug("No profiles are due for scraping")
            return

        logger.info("{} profile(s) due for scraping", len(due))

        for profile in due:
            # Per-profile error handling: an exception raised for one profile
            # must not skip the remaining due profiles of the cycle
            # (risque #54, §4.2 chemin C).
            try:
                # Skip if already running
                with _running_lock:
                    if profile.id in _running_profiles:
                        logger.debug(
                            "Skipping @{} -- already running", profile.username
                        )
                        continue

                # Check for an existing queued job to avoid duplicates
                existing_queued = (
                    db.query(ScrapeJob)
                    .filter(
                        ScrapeJob.profile_id == profile.id,
                        ScrapeJob.status.in_(["queued", "running"]),
                    )
                    .first()
                )
                if existing_queued:
                    logger.debug(
                        "Skipping @{} -- job {} already {}",
                        profile.username,
                        existing_queued.id,
                        existing_queued.status,
                    )
                    continue

                # Create a new scrape job
                job = ScrapeJob(
                    profile_id=profile.id,
                    status="queued",
                    triggered_by="scheduler",
                )
                db.add(job)
                db.commit()

                logger.info(
                    "Created scheduled job {} for @{} ({})",
                    job.id,
                    profile.username,
                    profile.platform,
                )

                # Run in a background thread
                t = threading.Thread(
                    target=_run_job_safe,
                    args=(job.id, profile.id),
                    name=f"scrape-{profile.platform}-{profile.username}",
                    daemon=True,
                )
                try:
                    t.start()
                except Exception as exc:
                    # No carrier thread: the job would stay `queued` for ever
                    # and freeze this profile until the next boot.
                    logger.exception(
                        "Could not start scrape thread for @{}: {}",
                        profile.username,
                        exc,
                    )
                    _fail_job(job.id, f"Could not start scrape thread: {exc}")
                    continue

                # Delay between profiles to be polite to platform servers
                delay_seconds = DELAY_BETWEEN_PROFILES_MS / 1000
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
            except Exception as exc:
                logger.exception(
                    "Error while scheduling @{}: {}", profile.username, exc
                )
                try:
                    db.rollback()
                except Exception:
                    pass

    except Exception as exc:
        logger.exception("Error in check_due_profiles: {}", exc)
    finally:
        db.close()


def retry_failed_media() -> None:
    """
    Find media items in failed states (download_failed, upload_failed) with
    retry_count < 5 and reset them to the appropriate pending state.
    """
    logger.debug("Checking for failed media items to retry...")
    db = SessionLocal()
    try:
        max_retries = 5

        # Retry failed downloads
        download_failures = (
            db.query(MediaItem)
            .filter(
                MediaItem.status == "download_failed",
                MediaItem.retry_count < max_retries,
            )
            .all()
        )

        for mi in download_failures:
            mi.status = "pending"
            mi.error_message = None
            # No increment here: the counter is already bumped by the pipeline
            # on the real failure (pipeline.py:304). Counting twice capped the
            # media at 3 real attempts instead of 5 (risque #27, §4.12).
            logger.debug(
                "Reset media {} (post {}) for download retry (attempt {})",
                mi.id,
                mi.post_id,
                mi.retry_count + 1,
            )

        # Retry failed uploads
        upload_failures = (
            db.query(MediaItem)
            .filter(
                MediaItem.status == "upload_failed",
                MediaItem.retry_count < max_retries,
            )
            .all()
        )

        for mi in upload_failures:
            mi.status = "downloaded"
            mi.error_message = None
            # No increment here either: pipeline.py:366 already counted this
            # failed attempt (risque #27, §4.12).
            logger.debug(
                "Reset media {} (post {}) for upload retry (attempt {})",
                mi.id,
                mi.post_id,
                mi.retry_count + 1,
            )

        total_reset = len(download_failures) + len(upload_failures)
        if total_reset > 0:
            db.commit()
            logger.info(
                "Reset {} failed media items for retry "
                "({} downloads, {} uploads)",
                total_reset,
                len(download_failures),
                len(upload_failures),
            )

            # Trigger scrape jobs for affected profiles so the pipeline
            # picks up the reset items
            affected_profile_ids = set()
            for mi in download_failures + upload_failures:
                affected_profile_ids.add(mi.profile_id)

            for pid in affected_profile_ids:
                profile = db.query(Profile).filter_by(id=pid).first()
                if not profile or not profile.is_active:
                    continue

                # Only create a job if one is not already running/queued
                existing = (
                    db.query(ScrapeJob)
                    .filter(
                        ScrapeJob.profile_id == pid,
                        ScrapeJob.status.in_(["queued", "running"]),
                    )
                    .first()
                )
                if existing:
                    continue

                job = ScrapeJob(
                    profile_id=pid,
                    status="queued",
                    triggered_by="scheduler",
                )
                db.add(job)
                db.commit()

                t = threading.Thread(
                    target=_run_job_safe,
                    args=(job.id, pid),
                    name=f"retry-{pid}",
                    daemon=True,
                )
                try:
                    t.start()
                except Exception as exc:
                    # Same guard as check_due_profiles: the job is already
                    # committed as `queued`, so without a carrier thread it
                    # would freeze this profile for ever, and the raised
                    # exception would skip the remaining affected profiles
                    # (risque #54, §4.2 chemin C).
                    logger.exception(
                        "Could not start retry thread for profile {}: {}",
                        pid,
                        exc,
                    )
                    _fail_job(job.id, f"Could not start retry thread: {exc}")
                    continue
        else:
            logger.debug("No failed media items to retry")

    except Exception as exc:
        logger.exception("Error in retry_failed_media: {}", exc)
        db.rollback()
    finally:
        db.close()


def check_due_posts() -> None:
    """
    Find scheduled posts whose scheduled_at has passed and transition
    them to 'ready' status so the user gets notified on the calendar.
    """
    logger.debug("Checking for due scheduled posts...")
    db = SessionLocal()
    try:
        now = _now_ts()

        due_posts = (
            db.query(ScheduledPost)
            .filter(
                ScheduledPost.status == "scheduled",
                ScheduledPost.scheduled_at <= now,
            )
            .all()
        )

        if not due_posts:
            logger.debug("No scheduled posts are due")
            return

        for post in due_posts:
            post.status = "ready"
            post.updated_at = now
            logger.info(
                "Post {} '{}' is now due — marked as ready",
                post.id,
                post.title or "Sans titre",
            )

        db.commit()
        logger.info("{} post(s) marked as ready for publishing", len(due_posts))

    except Exception as exc:
        logger.exception("Error in check_due_posts: {}", exc)
        db.rollback()
    finally:
        db.close()


#: Âge minimum d'un fichier avant qu'il soit considéré comme abandonné. Un
#: téléchargement ou un rendu vidéo en cours ne doit jamais être effacé sous
#: les pieds de celui qui l'écrit.
_CLEANUP_MAX_AGE_SECONDS = 24 * 3600  # 24 heures

def _est_fichier_de_service(nom: str) -> bool:
    """Fichiers jamais balayés : `.gitkeep` (versionné), `.DS_Store`, marqueur
    de volume… Les supprimer ne libère rien et casse l'arborescence attendue."""
    return nom.startswith(".")


def _cleanup_targets() -> list:
    """Répertoires balayés par le ménage.

    Résolus à l'APPEL depuis `app.config` (et non importés par valeur en tête
    de module) pour deux raisons : `DOWNLOAD_DIR` reste substituable par les
    tests, et les trois autres répertoires suivent `DATA_DIR` quel que soit le
    montage. Avant le lot 3.4, seul `DOWNLOAD_DIR` était balayé — et seulement
    à son premier niveau : `.thumbs` (risque #51), les répertoires de l'éditeur
    et `CALENDAR_DIR` (risque #56) grossissaient sans limite jusqu'à saturer le
    volume, ce que rien ne signalait.
    """
    from app.config import CALENDAR_DIR, EDITOR_OUTPUT_DIR, EDITOR_UPLOAD_DIR

    return [DOWNLOAD_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR, CALENDAR_DIR]


def _referenced_paths(db) -> set[str]:
    """Chemins absolus référencés en base — intouchables par le ménage.

    Trois familles : les médias téléchargés (`MediaItem.local_path`), les
    visuels du calendrier (`ScheduledPost`) et les memes sauvegardés
    (`SavedMeme`, qui vivent dans `EDITOR_OUTPUT_DIR/memes`). Sans les deux
    dernières, étendre le balayage aux répertoires de l'éditeur et du
    calendrier détruirait des fichiers encore utilisés.
    """
    referenced: set[str] = set()
    for model, colonnes in (
        (MediaItem, ("local_path",)),
        (ScheduledPost, ("media_path", "thumbnail_path")),
        (SavedMeme, ("file_path", "thumbnail_path")),
    ):
        for nom_colonne in colonnes:
            colonne = getattr(model, nom_colonne, None)
            if colonne is None:  # pragma: no cover - schéma plus ancien
                continue
            try:
                rows = db.query(colonne).filter(colonne.isnot(None)).all()
            except Exception as exc:  # pragma: no cover - table absente
                logger.warning(
                    "Ménage : lecture de {}.{} impossible ({}) — "
                    "ces fichiers sont épargnés par précaution",
                    model.__name__,
                    nom_colonne,
                    exc,
                )
                db.rollback()
                continue
            for (path,) in rows:
                if path:
                    referenced.add(os.path.abspath(path))
    return referenced


def _sweep_directory(directory, referenced: set[str], stems: set[str], now: float) -> int:
    """Supprime récursivement les fichiers orphelins et vieux de *directory*."""
    if not directory.exists():
        return 0

    removed = 0
    for racine, _sous_dossiers, fichiers in os.walk(str(directory)):
        for nom in fichiers:
            if _est_fichier_de_service(nom):
                continue

            file_path = os.path.abspath(os.path.join(racine, nom))

            # Fichier encore référencé en base
            if file_path in referenced:
                continue

            # Vignette d'un média vivant : `.thumbs/<nanoid>.jpg` porte le même
            # radical que `downloads/<nanoid>.mp4`. Sans cette garde, le ménage
            # détruirait chaque nuit les vignettes de toute la bibliothèque, à
            # regénérer une par une à coups de ffmpeg au prochain affichage.
            if os.path.splitext(nom)[0] in stems:
                continue

            try:
                if now - os.stat(file_path).st_mtime < _CLEANUP_MAX_AGE_SECONDS:
                    continue
            except OSError:
                continue

            try:
                os.unlink(file_path)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove temp file {}: {}", file_path, exc)

    return removed


def cleanup_temp_files() -> None:
    """
    Remove orphaned files older than 24 hours, and not referenced in the
    database, from every directory the application writes to: the download
    directory (including `.thumbs`), the editor's upload/output directories
    and the calendar media directory.
    """
    logger.debug("Running temp file cleanup...")
    db = SessionLocal()
    try:
        referenced = _referenced_paths(db)
        stems = {os.path.splitext(os.path.basename(p))[0] for p in referenced}

        now = time.time()
        removed = 0
        for directory in _cleanup_targets():
            removed += _sweep_directory(directory, referenced, stems, now)

        if removed > 0:
            logger.info("Cleaned up {} orphaned temp files", removed)

    except Exception as exc:
        logger.exception("Error in cleanup_temp_files: {}", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Santé de session — sonde active périodique
# ---------------------------------------------------------------------------
def sonder_les_sessions() -> None:
    """Sonde active de chaque plateforme qui possède des cookies (toutes les 6 h).

    TROIS PROMESSES, tenues par construction :

    1. Elle ne bloque JAMAIS un scrape. Elle n'acquiert pas
       `_scrape_semaphore` et ne réserve aucun slot de scrape : APScheduler
       l'exécute dans son propre thread, et `session_health` protège la sonde
       par un verrou qui lui est PROPRE (`_VERROU_SONDE`).
    2. Elle est bornée dans le temps. Chaque sonde a un délai franc
       (`DELAI_SONDE_S`) ; au-delà elle rend « inconnu » et rend la main.
    3. Elle ne lève jamais. Un job planifié qui explose ferait taire tous ses
       passages suivants dans les logs — on journalise et on continue.

    Les plateformes SANS fichier de cookies sont ignorées : il n'y a rien à
    sonder, et un navigateur coûte trop cher pour l'apprendre.
    """
    from app.scraper.session_health import (
        SondeOccupee,
        plateformes_sondables,
        sonder_si_libre,
    )

    plateformes = plateformes_sondables()
    if not plateformes:
        logger.info("Sonde de session : aucune plateforme avec des cookies, rien a faire")
        return

    for plateforme in plateformes:
        try:
            etat = sonder_si_libre(plateforme)
            niveau = logger.warning if etat.urgent else logger.info
            niveau(
                "Sante de session {} : {} — {}", plateforme, etat.etat, etat.message
            )
        except SondeOccupee:
            logger.info("Sonde de session {} ignoree : une sonde tourne deja", plateforme)
        except Exception as exc:
            logger.error("Sonde de session {} en echec: {}", plateforme, exc)


def _recover_stale_jobs() -> None:
    """Fail jobs left in 'queued'/'running' by a previous process.

    After a container restart, no thread exists to process those jobs, yet
    check_due_profiles skips any profile that has a queued/running job — so
    a single stale job blocks its profile from ever being scraped again.
    Runs once at boot, before any new job threads are spawned.
    """
    db = SessionLocal()
    try:
        stale = (
            db.query(ScrapeJob)
            .filter(ScrapeJob.status.in_(["queued", "running"]))
            .all()
        )
        for job in stale:
            job.status = "failed"
            job.error_message = "Interrupted by server restart"
            job.completed_at = _now_ts()
        if stale:
            db.commit()
            logger.warning(
                "Recovered {} stale job(s) left over from a previous run: {}",
                len(stale),
                [j.id for j in stale],
            )
    except Exception as exc:
        db.rollback()
        logger.error("Failed to recover stale jobs: {}", exc)
    finally:
        db.close()


def start_scheduler() -> None:
    """
    Register recurring jobs and start the APScheduler background scheduler.
    """
    if scheduler.running:
        logger.warning("Scheduler is already running")
        return

    # Unblock profiles whose jobs were orphaned by the previous process
    # (must run before initial_check creates new jobs).
    _recover_stale_jobs()

    # Job 1: Check due profiles every 30 minutes
    scheduler.add_job(
        check_due_profiles,
        trigger="interval",
        minutes=30,
        id="check_due_profiles",
        name="Check due profiles",
        replace_existing=True,
    )

    # Job 2: Retry failed media every 2 hours
    scheduler.add_job(
        retry_failed_media,
        trigger="interval",
        hours=2,
        id="retry_failed_media",
        name="Retry failed media",
        replace_existing=True,
    )

    # Job 3: Clean up temp files daily at 03:00 UTC
    scheduler.add_job(
        cleanup_temp_files,
        trigger="cron",
        hour=3,
        minute=0,
        id="cleanup_temp_files",
        name="Cleanup temp files",
        replace_existing=True,
    )

    # Job 4: Check for due scheduled posts every 5 minutes
    scheduler.add_job(
        check_due_posts,
        trigger="interval",
        minutes=5,
        id="check_due_posts",
        name="Check due posts",
        replace_existing=True,
    )

    # Job 5: Collect Instagram stats via Graph API every 6 hours
    from app.analytics.ig_collector import collect_ig_stats, collect_media_insights

    scheduler.add_job(
        collect_ig_stats,
        trigger="interval",
        hours=6,
        id="collect_ig_stats",
        name="Collect IG stats (Graph API)",
        replace_existing=True,
    )

    # Job 6: Collect media insights once a day at 06:00 UTC
    scheduler.add_job(
        collect_media_insights,
        trigger="cron",
        hour=6,
        minute=30,
        id="collect_media_insights",
        name="Collect IG media insights",
        replace_existing=True,
    )

    # Job 7: Sonde active de santé de session toutes les 6 heures.
    # Espacée volontairement : chaque passage lance un navigateur furtif par
    # plateforme. `next_run_time` n'est PAS forcé au démarrage — le boot ne
    # doit pas payer un navigateur, le signal passif suffit à l'affichage
    # immédiat, et le bouton « Vérifier maintenant » reste disponible.
    scheduler.add_job(
        sonder_les_sessions,
        trigger="interval",
        hours=6,
        id="sonder_les_sessions",
        name="Sonde de sante des sessions",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with 7 recurring jobs")

    # Run an initial check immediately so we do not wait 30 minutes for
    # the first pass after server startup
    scheduler.add_job(
        check_due_profiles,
        trigger="date",
        id="initial_check",
        name="Initial due-profile check",
        replace_existing=True,
    )

    # Also collect IG stats on boot (with a 15s delay to let app start)
    import threading

    def _delayed_ig_collect():
        import time
        time.sleep(15)
        try:
            collect_ig_stats()
        except Exception as e:
            logger.warning("Initial IG stats collection failed: {}", e)

    threading.Thread(target=_delayed_ig_collect, daemon=True, name="ig-initial").start()


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")


def enqueue_manual_scrape(profile_id: int, job_id: int) -> None:
    """
    Trigger an immediate scrape job in a background thread.

    This is called from the web API when a user manually requests a scrape.
    The job should already exist in the database with status 'queued'.
    """
    logger.info(
        "Enqueuing manual scrape: profile_id={}, job_id={}",
        profile_id,
        job_id,
    )

    t = threading.Thread(
        target=_run_job_safe,
        args=(job_id, profile_id),
        name=f"manual-scrape-{profile_id}",
        daemon=True,
    )
    try:
        t.start()
    except Exception as exc:
        # Same guard as check_due_profiles: a job committed by the view with
        # no carrier thread would stay `queued` for ever and freeze the
        # profile (risque #54, §4.2 chemin C).
        _fail_job(job_id, f"Could not start scrape thread: {exc}")
        raise
