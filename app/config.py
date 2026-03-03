import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Load .env files
# 1) Project-root .env  — base defaults (local dev, Dockerfile defaults)
# 2) DATA_DIR/.env       — user settings saved via Settings UI
#    On Railway DATA_DIR=/data (persistent volume), so settings survive redeploy.
#    Locally DATA_DIR=<project>/data.
# ---------------------------------------------------------------------------
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))

# User-settings .env — lives on the persistent volume so it survives redeploy
SETTINGS_ENV = DATA_DIR / ".env"
load_dotenv(SETTINGS_ENV, override=True)

# Server
PORT = int(os.getenv("PORT", "8080"))
DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

# Google Drive OAuth2
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/google/callback")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_ROOT_FOLDER_NAME = os.getenv("GDRIVE_ROOT_FOLDER_NAME", "SAMOURAIS SCRAPPER")

# Storage mode: "local" (save to disk only) or "gdrive" (upload to Google Drive)
STORAGE_MODE = os.getenv("STORAGE_MODE", "local")

# Scraper
BROWSER_POOL_SIZE = int(os.getenv("BROWSER_POOL_SIZE", "2"))
DEFAULT_SCRAPE_INTERVAL_MINUTES = int(os.getenv("DEFAULT_SCRAPE_INTERVAL_MINUTES", "360"))
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "3000"))
MAX_SCROLLS = int(os.getenv("MAX_SCROLLS", "30"))
BACKFILL_MAX_SCROLLS = int(os.getenv("BACKFILL_MAX_SCROLLS", "200"))
DAILY_MAX_SCROLLS = int(os.getenv("DAILY_MAX_SCROLLS", "25"))
DAILY_SCRAPE_INTERVAL_MINUTES = int(os.getenv("DAILY_SCRAPE_INTERVAL_MINUTES", "1440"))
DELAY_BETWEEN_PROFILES_MS = int(os.getenv("DELAY_BETWEEN_PROFILES_MS", "10000"))

# Paths (all derived from DATA_DIR)
DOWNLOAD_DIR = DATA_DIR / "downloads"
DB_PATH = DATA_DIR / "samourais.db"
SESSIONS_DIR = DATA_DIR / "sessions"
COOKIES_DIR = DATA_DIR / "cookies"
CALENDAR_DIR = DATA_DIR / "calendar"

# Editor
EDITOR_UPLOAD_DIR = DATA_DIR / "editor" / "uploads"
EDITOR_OUTPUT_DIR = DATA_DIR / "editor" / "outputs"
EDITOR_MAX_FILE_SIZE_MB = int(os.getenv("EDITOR_MAX_FILE_SIZE_MB", "100"))

# Proxy — format: http://username:password@host:port
# Can be set globally or per-platform (platform-specific takes priority)
PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_INSTAGRAM = os.getenv("PROXY_INSTAGRAM", "")
PROXY_TIKTOK = os.getenv("PROXY_TIKTOK", "")
PROXY_TWITTER = os.getenv("PROXY_TWITTER", "")
PROXY_REDDIT = os.getenv("PROXY_REDDIT", "")

def get_proxy_for_platform(platform: str) -> str:
    """Return proxy URL for a given platform (platform-specific or global fallback).

    Re-reads the persistent .env every time so that changes from Settings UI
    take effect without requiring an app restart.
    """
    from dotenv import load_dotenv
    load_dotenv(SETTINGS_ENV, override=True)

    specific = {
        "instagram": os.getenv("PROXY_INSTAGRAM", ""),
        "tiktok": os.getenv("PROXY_TIKTOK", ""),
        "twitter": os.getenv("PROXY_TWITTER", ""),
        "reddit": os.getenv("PROXY_REDDIT", ""),
    }.get(platform, "")
    return specific or os.getenv("PROXY_URL", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Platform URL builders
PLATFORM_URLS = {
    "instagram": lambda u: f"https://www.instagram.com/{u}/",
    "reddit": lambda u: f"https://www.reddit.com/user/{u}/submitted",
    "tiktok": lambda u: f"https://www.tiktok.com/@{u}",
    "twitter": lambda u: f"https://x.com/{u}/media",
}
