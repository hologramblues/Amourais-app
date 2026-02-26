#!/usr/bin/env python3
"""
SAMOURAIS SCRAPPER -- entry point.

Initialises the database, starts the background scheduler, and launches
the Flask web application.
"""
from app.db import init_db
from app.web.app import create_app
from app.scheduler import start_scheduler
from app.config import PORT, DEBUG

if __name__ == "__main__":
    init_db()
    start_scheduler()
    app = create_app()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
