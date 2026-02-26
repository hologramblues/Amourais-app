"""
APScheduler-based job scheduling for the SAMOURAIS SCRAPPER.

Runs three recurring tasks:
    1. Check due profiles every 30 minutes and enqueue scrape jobs.
    2. Retry failed media downloads/uploads every 2 hours.
    3. Clean up stale temp files daily at 03:00.

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
)
from app.db import MediaItem, Profile, ScheduledPost, ScrapeJob, SessionLocal

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
        return

    try:
        # Import here to avoid circular imports at module level
        from app.scraper.pipeline import run_scrape_job

        run_scrape_job(job_id)
    except Exception as exc:
        logger.exception("Job {} failed with unhandled error: {}", job_id, exc)
        # Mark the job as failed so it does not block future runs
        db = SessionLocal()
        try:
            job = db.query(ScrapeJob).filter_by(id=job_id).first()
            if job and job.status == "running":
                job.status = "failed"
                job.error_message = f"Unhandled: {str(exc)[:500]}"
                job.completed_at = _now_ts()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    finally:
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
            t.start()

            # Delay between profiles to be polite to platform servers
            delay_seconds = DELAY_BETWEEN_PROFILES_MS / 1000
            if delay_seconds > 0:
                time.sleep(delay_seconds)

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
                t.start()
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


def cleanup_temp_files() -> None:
    """
    Remove orphaned files from the download directory that are older
    than 24 hours and not referenced by any media item.
    """
    logger.debug("Running temp file cleanup...")
    db = SessionLocal()
    try:
        if not DOWNLOAD_DIR.exists():
            return

        # Build a set of currently-referenced local paths
        referenced_paths: set[str] = set()
        rows = (
            db.query(MediaItem.local_path)
            .filter(MediaItem.local_path.isnot(None))
            .all()
        )
        for (path,) in rows:
            if path:
                referenced_paths.add(os.path.abspath(path))

        now = time.time()
        max_age_seconds = 24 * 3600  # 24 hours
        removed = 0

        for entry in os.scandir(str(DOWNLOAD_DIR)):
            if not entry.is_file():
                continue

            file_path = os.path.abspath(entry.path)

            # Skip files still referenced in the DB
            if file_path in referenced_paths:
                continue

            # Only remove files older than max_age_seconds
            try:
                file_age = now - entry.stat().st_mtime
                if file_age < max_age_seconds:
                    continue
            except OSError:
                continue

            try:
                os.unlink(file_path)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove temp file {}: {}", file_path, exc)

        if removed > 0:
            logger.info("Cleaned up {} orphaned temp files", removed)

    except Exception as exc:
        logger.exception("Error in cleanup_temp_files: {}", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def start_scheduler() -> None:
    """
    Register recurring jobs and start the APScheduler background scheduler.
    """
    if scheduler.running:
        logger.warning("Scheduler is already running")
        return

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

    scheduler.start()
    logger.info("Scheduler started with 4 recurring jobs")

    # Run an initial check immediately so we do not wait 30 minutes for
    # the first pass after server startup
    scheduler.add_job(
        check_due_profiles,
        trigger="date",
        id="initial_check",
        name="Initial due-profile check",
        replace_existing=True,
    )


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
    t.start()
