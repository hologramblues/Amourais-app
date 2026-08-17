import os
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Load .env files
# 1) Project-root .env  — base defaults (local dev, Dockerfile defaults)
# 2) DATA_DIR/.env       — user settings saved via Settings UI
#    On Railway DATA_DIR=/data (persistent volume), so settings survive redeploy.
#    Locally DATA_DIR=<project>/data.
# ---------------------------------------------------------------------------
load_dotenv(BASE_DIR / ".env")

#: `DATA_DIR` a-t-il été posé EXPLICITEMENT dans l'environnement ?
#: Railway le définit (/data, le volume persistant). En développement local il
#: est souvent absent — et le repli silencieux sur `<projet>/data` fait alors
#: écrire dans les données de PRODUCTION : base, médias, sessions.
#: Ce défaut a déjà coûté un profil au propriétaire (un script lancé sans la
#: variable a créé des jobs et supprimé un profil dans la vraie base).
DATA_DIR_EXPLICITE = os.getenv("DATA_DIR") is not None

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))

if not DATA_DIR_EXPLICITE:
    # On NE CHANGE PAS le comportement par défaut : Railway définit la
    # variable, le développement local dépend du repli. On rend le risque
    # VISIBLE, c'est tout. Un avertissement sur trois lignes, impossible à
    # confondre avec le bruit habituel du démarrage.
    logger.warning(
        "\n"
        "  ================================================================\n"
        "   DATA_DIR N'EST PAS DEFINI — LES DONNEES DE PRODUCTION VONT\n"
        "   ETRE UTILISEES.\n"
        "   Repli automatique sur : {}\n"
        "   Base, medias, sessions et .env de ce dossier seront ECRITS.\n"
        "   Pour un essai sans risque : DATA_DIR=/chemin/bac-a-sable <cmd>\n"
        "  ================================================================",
        DATA_DIR,
    )

# User-settings .env — lives on the persistent volume so it survives redeploy
SETTINGS_ENV = DATA_DIR / ".env"
load_dotenv(SETTINGS_ENV, override=True)


def _env_int(key: str, default: int) -> int:
    """Read an integer setting, falling back to `default` when unusable.

    Settings are persisted on the data volume (`DATA_DIR/.env`) and survive
    redeploys. A single non-numeric value used to raise `ValueError` at import
    time and brick the whole application — including the Settings UI, the only
    way to fix it (risque #41 / AUDIT.md §4.14). We log and fall back instead.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "Valeur non entiere pour {} ({!r}) — repli sur {}", key, raw, default
        )
        return default


# Server
PORT = _env_int("PORT", 8080)
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

# App authentication (HTTP Basic).
# Auth is ENABLED only when APP_PASSWORD is set (non-empty).
# This avoids locking out local dev / breaking the Railway healthcheck if unset.
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
# Flask session signing key — read from env, fall back to a stable dev default.
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "samourais-scrapper-secret-key")

# Google Drive OAuth2
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/google/callback")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_ROOT_FOLDER_NAME = os.getenv("GDRIVE_ROOT_FOLDER_NAME", "SAMOURAIS SCRAPPER")

# Storage mode: "local" (save to disk only) or "gdrive" (upload to Google Drive)
STORAGE_MODE = os.getenv("STORAGE_MODE", "local")

# Scraper
BROWSER_POOL_SIZE = _env_int("BROWSER_POOL_SIZE", 2)
DEFAULT_SCRAPE_INTERVAL_MINUTES = _env_int("DEFAULT_SCRAPE_INTERVAL_MINUTES", 360)
SCROLL_PAUSE_MS = _env_int("SCROLL_PAUSE_MS", 3000)
MAX_SCROLLS = _env_int("MAX_SCROLLS", 30)
BACKFILL_MAX_SCROLLS = _env_int("BACKFILL_MAX_SCROLLS", 200)
DAILY_MAX_SCROLLS = _env_int("DAILY_MAX_SCROLLS", 40)
DAILY_SCRAPE_INTERVAL_MINUTES = _env_int("DAILY_SCRAPE_INTERVAL_MINUTES", 360)
DELAY_BETWEEN_PROFILES_MS = _env_int("DELAY_BETWEEN_PROFILES_MS", 10000)
# Global cap on simultaneously-running scrape jobs (each spawns a headless
# browser). Prevents memory exhaustion / OOM kills on small containers when
# many profiles fall due at once. Default 2.
MAX_CONCURRENT_SCRAPES = _env_int("MAX_CONCURRENT_SCRAPES", 2)

# Paths (all derived from DATA_DIR)
DOWNLOAD_DIR = DATA_DIR / "downloads"
DB_PATH = DATA_DIR / "samourais.db"
SESSIONS_DIR = DATA_DIR / "sessions"
COOKIES_DIR = DATA_DIR / "cookies"
CALENDAR_DIR = DATA_DIR / "calendar"

# Editor
EDITOR_UPLOAD_DIR = DATA_DIR / "editor" / "uploads"
EDITOR_OUTPUT_DIR = DATA_DIR / "editor" / "outputs"
EDITOR_MAX_FILE_SIZE_MB = _env_int("EDITOR_MAX_FILE_SIZE_MB", 100)

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

# Apify — backend de scrape Instagram par API (payant, lot A).
# Quand APIFY_TOKEN est posé, le pipeline fait passer Instagram par l'acteur
# Apify au lieu du navigateur (cf. pipeline._choisir_extracteur). Le jeton est
# relu À CHAUD via get_apify_settings(), comme les proxys.
APIFY_ACTOR_DEFAULT = "apify~instagram-scraper"  # l'acteur reellement utilise par le proprietaire (2,70 $/1000 posts), verifie contre son vrai dataset
#: Acteurs par plateforme (lot A — TikTok/Twitter). Chaque plateforme branchée
#: sur Apify a SON acteur, avec un défaut vérifié dans la console du
#: propriétaire et un champ dédié dans Réglages, sur le modèle d'APIFY_ACTOR.
TIKTOK_ACTOR_DEFAULT = "clockworks~tiktok-scraper"
TWITTER_ACTOR_DEFAULT = "easyapi~twitter-x-video-downloader"
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR = os.getenv("APIFY_ACTOR", "") or APIFY_ACTOR_DEFAULT
TIKTOK_ACTOR = os.getenv("TIKTOK_ACTOR", "") or TIKTOK_ACTOR_DEFAULT
TWITTER_ACTOR = os.getenv("TWITTER_ACTOR", "") or TWITTER_ACTOR_DEFAULT

#: (variable d'environnement de l'acteur, acteur par défaut) par plateforme.
#: Le JETON est commun à toutes les plateformes ; seul l'acteur diffère.
_APIFY_ACTEUR_PAR_PLATEFORME: dict[str, tuple[str, str]] = {
    "instagram": ("APIFY_ACTOR", APIFY_ACTOR_DEFAULT),
    "tiktok": ("TIKTOK_ACTOR", TIKTOK_ACTOR_DEFAULT),
    "twitter": ("TWITTER_ACTOR", TWITTER_ACTOR_DEFAULT),
}


def get_apify_settings() -> tuple[str, str]:
    """(jeton, acteur Instagram) Apify, relus À CHAUD depuis le .env persistant.

    Même mécanique que `get_proxy_for_platform` : le fichier de réglages du
    volume est rechargé à chaque appel pour qu'un jeton posé (ou retiré) dans
    l'écran Réglages prenne effet au scrape suivant, sans redémarrage.
    L'acteur vide retombe sur `APIFY_ACTOR_DEFAULT`.

    Conservée telle quelle pour `instagram_apify` et l'aiguillage du pipeline
    (seul le jeton y compte) ; TikTok/Twitter passent par
    `get_apify_settings_for`.
    """
    from dotenv import load_dotenv
    load_dotenv(SETTINGS_ENV, override=True)

    token = (os.getenv("APIFY_TOKEN") or "").strip()
    acteur = (os.getenv("APIFY_ACTOR") or "").strip() or APIFY_ACTOR_DEFAULT
    return token, acteur


def get_apify_settings_for(platform: str) -> tuple[str, str]:
    """(jeton, acteur) Apify pour *platform*, relus À CHAUD du .env persistant.

    Le jeton `APIFY_TOKEN` est partagé ; l'acteur dépend de la plateforme
    (`APIFY_ACTOR`, `TIKTOK_ACTOR`, `TWITTER_ACTOR`) et retombe sur le défaut
    vérifié de la plateforme quand la variable est vide. Une plateforme
    inconnue retombe sur l'acteur Instagram par défaut.
    """
    from dotenv import load_dotenv
    load_dotenv(SETTINGS_ENV, override=True)

    token = (os.getenv("APIFY_TOKEN") or "").strip()
    env_key, defaut = _APIFY_ACTEUR_PAR_PLATEFORME.get(
        platform, ("APIFY_ACTOR", APIFY_ACTOR_DEFAULT)
    )
    acteur = (os.getenv(env_key) or "").strip() or defaut
    return token, acteur


# Instagram Graph API
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")
FB_APP_ID = os.getenv("FB_APP_ID", "")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Platform URL builders
PLATFORM_URLS = {
    "instagram": lambda u: f"https://www.instagram.com/{u}/",
    "reddit": lambda u: f"https://www.reddit.com/user/{u}/submitted",
    "tiktok": lambda u: f"https://www.tiktok.com/@{u}",
    "twitter": lambda u: f"https://x.com/{u}/media",
}
