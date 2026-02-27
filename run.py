#!/usr/bin/env python3
"""
SAMOURAIS SCRAPPER -- entry point.

Initialises the database, starts the background scheduler, and launches
the Flask web application.
"""
import os
import time
from pathlib import Path

from app.db import init_db
from app.web.app import create_app
from app.scheduler import start_scheduler
from app.config import (
    PORT, DEBUG, DATA_DIR, DB_PATH, DOWNLOAD_DIR, SESSIONS_DIR,
    COOKIES_DIR, CALENDAR_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR,
)
from loguru import logger


VOLUME_MARKER = DATA_DIR / ".samourais_volume_marker"


def diagnose_volume():
    """Log detailed volume / persistence diagnostics at startup."""
    logger.info("=" * 60)
    logger.info("SAMOURAIS SCRAPPER — Volume Diagnostics")
    logger.info("=" * 60)
    logger.info("DATA_DIR         = {}", DATA_DIR)
    logger.info("DATA_DIR (env)   = {}", os.getenv("DATA_DIR", "<not set>"))
    logger.info("DB_PATH          = {}", DB_PATH)
    logger.info("DATA_DIR exists  = {}", DATA_DIR.exists())
    logger.info("DATA_DIR is_mount= {}", os.path.ismount(str(DATA_DIR)))

    # Check disk usage on the DATA_DIR mount
    try:
        stat = os.statvfs(str(DATA_DIR))
        total_gb = (stat.f_frsize * stat.f_blocks) / (1024 ** 3)
        free_gb = (stat.f_frsize * stat.f_bavail) / (1024 ** 3)
        used_gb = total_gb - free_gb
        logger.info("Volume disk: {:.2f} GB total, {:.2f} GB used, {:.2f} GB free",
                     total_gb, used_gb, free_gb)
    except Exception as e:
        logger.warning("Could not read disk stats: {}", e)

    # Check the marker file — tells us if previous deploy data is still here
    if VOLUME_MARKER.exists():
        prev_ts = VOLUME_MARKER.read_text().strip()
        logger.info("✅ VOLUME PERSISTS — marker from previous boot: {}", prev_ts)
    else:
        logger.warning("⚠️  NO MARKER FOUND — volume is fresh or not persistent!")

    # Write / update marker for next boot
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        VOLUME_MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
        logger.info("Marker written to {}", VOLUME_MARKER)
    except Exception as e:
        logger.error("❌ CANNOT WRITE to DATA_DIR: {}", e)

    # Count existing data
    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0
    download_count = len(list(DOWNLOAD_DIR.glob("**/*"))) if DOWNLOAD_DIR.exists() else 0

    logger.info("DB exists        = {} ({})", db_exists,
                f"{db_size / 1024:.1f} KB" if db_exists else "—")
    logger.info("Downloads found  = {} files", download_count)

    # List /data contents at top level
    if DATA_DIR.exists():
        contents = list(DATA_DIR.iterdir())
        logger.info("/data contents   = {}", [c.name for c in contents])
    else:
        logger.warning("/data does not exist yet!")

    logger.info("=" * 60)


def ensure_data_dirs():
    """Create all required data subdirectories (idempotent)."""
    for d in (DATA_DIR, DOWNLOAD_DIR, SESSIONS_DIR, COOKIES_DIR,
              CALENDAR_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Data directories ready at {}", DATA_DIR)


if __name__ == "__main__":
    diagnose_volume()
    ensure_data_dirs()
    init_db()
    start_scheduler()
    app = create_app()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
