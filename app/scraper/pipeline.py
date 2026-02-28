"""
Scraping pipeline orchestration.

Coordinates the full lifecycle of a scrape job:
    extract -> insert -> download -> upload -> cleanup -> update stats
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.config import (
    BACKFILL_MAX_SCROLLS,
    DAILY_MAX_SCROLLS,
    DAILY_SCRAPE_INTERVAL_MINUTES,
    STORAGE_MODE,
    get_proxy_for_platform,
)
from app.db import MediaItem, Profile, ScrapeJob, SessionLocal
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_ts() -> int:
    """Current unix timestamp as integer."""
    return int(datetime.now().timestamp())


def _mark_job(db, job: ScrapeJob, status: str, error: str | None = None) -> None:
    """Update job status and optional error message."""
    job.status = status
    if status == "running" and job.started_at is None:
        job.started_at = _now_ts()
    if status in ("completed", "failed", "partial"):
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
        extractor = _get_extractor(profile.platform)
        result = extractor.extract(profile.profile_url, known_post_ids, options)
    except Exception as exc:
        logger.exception("Extraction failed for job {}: {}", job_id, exc)
        _mark_job(db, job, "failed", f"Extraction error: {exc}")
        return

    media_found = len(result.media)
    total_seen = getattr(result, "total_seen", media_found)
    logger.info(
        "Extracted {} new media items for job {} (total seen on page: {})",
        media_found, job_id, total_seen,
    )

    # ------------------------------------------------------------------
    # 6. Update profile display_name / avatar_url
    # ------------------------------------------------------------------
    if result.profile_info:
        if result.profile_info.display_name:
            profile.display_name = result.profile_info.display_name
        if result.profile_info.avatar_url:
            profile.avatar_url = result.profile_info.avatar_url
        profile.updated_at = _now_ts()
        db.commit()

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
            posted_at=(
                int(item.posted_at.timestamp()) if item.posted_at else None
            ),
            status="pending",
        )
        try:
            db.add(media_item)
            db.flush()
            media_new += 1
        except IntegrityError:
            db.rollback()
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
                gdrive = upload_to_gdrive(
                    local_path=mi.local_path,
                    platform=profile.platform,
                    username=profile.username,
                    post_id=mi.post_id,
                    mime_type=mi.mime_type if hasattr(mi, "mime_type") and mi.mime_type else "application/octet-stream",
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

    final_status = "completed"
    if media_downloaded < len(pending_items) or media_uploaded < len(downloaded_items):
        final_status = "partial"

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
