#!/usr/bin/env python3
"""
SAMOURAIS SCRAPPER -- entry point.

Initialises the database, starts the background scheduler, and launches
the Flask web application.
"""
from app.db import init_db
from app.web.app import create_app
from app.scheduler import start_scheduler
from app.config import (
    PORT, DEBUG, DATA_DIR, DOWNLOAD_DIR, SESSIONS_DIR,
    COOKIES_DIR, CALENDAR_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR,
)
from loguru import logger


def ensure_data_dirs():
    """Create all required data subdirectories (idempotent)."""
    for d in (DATA_DIR, DOWNLOAD_DIR, SESSIONS_DIR, COOKIES_DIR,
              CALENDAR_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Data directories ready at {}", DATA_DIR)


if __name__ == "__main__":
    ensure_data_dirs()
    init_db()
    start_scheduler()
    app = create_app()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
