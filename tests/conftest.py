"""
SOCLE DE TEST — SAMOURAIS SCRAPPER (vague 0, lot 0.1).

CE FICHIER EST LA FONDATION. Les modules de test écrits par les autres agents
en dépendent et ne doivent PAS le modifier.

────────────────────────────────────────────────────────────────────────────
POURQUOI L'ORDRE DES LIGNES DE CE FICHIER EST CRITIQUE
────────────────────────────────────────────────────────────────────────────
`app/config.py` fige toutes ses constantes À L'IMPORT :

    config.py:16   DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
    config.py:60   DB_PATH  = DATA_DIR / "samourais.db"
    db.py:237      engine   = create_engine(f"sqlite:///{DB_PATH}")

Conséquence : `os.environ["DATA_DIR"]` DOIT être positionné **avant le tout
premier `import app.*`**, sinon la suite de tests écrit dans la base de
PRODUCTION `data/samourais.db`. C'est la responsabilité n°1 de ce socle, et
c'est pourquoi la « PHASE 0 » ci-dessous précède tout import applicatif.

`load_dotenv()` (config.py:14) n'écrase pas les variables déjà présentes dans
l'environnement (override=False), donc ce que nous posons ici gagne.

────────────────────────────────────────────────────────────────────────────
CE QUE LE SOCLE FOURNIT
────────────────────────────────────────────────────────────────────────────
Isolation      : `test_data_dir`, `settings_env_file`
Base           : `db_session`, `db_engine`, nettoyage automatique entre tests
Flask          : `make_flask_app`, `flask_app`/`client` (sans auth),
                 `auth_flask_app`/`auth_client`/`auth_header` (avec auth)
Fabriques      : `factories` (+ raccourcis `make_profile`, `make_media_item`,
                 `make_scrape_job`, `make_scheduled_post`)
Garde-fous     : `_guard_no_network`, `_guard_no_production_data_write`
                 (autouse, non désactivables), exceptions via `guard_errors`
Temps          : `FIXED_NOW` (horodatage figé — n'utilisez jamais time.time())

────────────────────────────────────────────────────────────────────────────
LIMITE CONNUE À GARDER EN TÊTE (§7.1 AUDIT.md)
────────────────────────────────────────────────────────────────────────────
Les constantes de `app.config` sont importées PAR VALEUR par les modules
consommateurs (`storage.py:16-22`, `pipeline.py:21`, `routes.py:15-23`).
Monkeypatcher `app.config.X` n'a donc AUCUN effet sur ces modules : il faut
patcher le nom dans le module consommateur, p. ex.
`monkeypatch.setattr("app.web.routes.DOWNLOAD_DIR", tmp_path)`.
Exception notable : `create_app()` importe `APP_PASSWORD`/`APP_USERNAME`
DANS le corps de la fonction (app.py:52-57), donc patcher `app.config` avant
l'appel fonctionne — c'est ce qu'exploite `make_flask_app`.
"""

from __future__ import annotations

# ===========================================================================
# PHASE 0 — ISOLATION.  AUCUN `import app.*` AU-DESSUS DE CE BLOC.
# ===========================================================================
import atexit
import builtins
import io
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Répertoire de production, intouchable (LOI ABSOLUE n°1).
PRODUCTION_DATA_DIR = _PROJECT_ROOT / "data"
PRODUCTION_DB_PATH = PRODUCTION_DATA_DIR / "samourais.db"

if "app.config" in sys.modules or "app.db" in sys.modules:  # pragma: no cover
    raise RuntimeError(
        "app.config / app.db ont été importés AVANT ce conftest : l'isolation "
        "de DATA_DIR est rompue et les tests écriraient dans data/ (production)."
    )

#: DATA_DIR éphémère de la suite. Créé une fois par session pytest.
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="samourais-tests-")).resolve()
for _sub in (
    "downloads",
    "sessions",
    "cookies",
    "calendar",
    "editor/uploads",
    "editor/outputs",
):
    (TEST_DATA_DIR / _sub).mkdir(parents=True, exist_ok=True)


@atexit.register
def _cleanup_test_data_dir() -> None:  # pragma: no cover - fin de process
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


# Environnement figé : la suite doit produire le même résultat sur n'importe
# quelle machine, quel que soit le contenu du `.env` du propriétaire.
# `load_dotenv(override=False)` ne peut plus rien écraser ici.
_FORCED_ENV = {
    "DATA_DIR": str(TEST_DATA_DIR),
    # Serveur
    "PORT": "8080",
    "FLASK_DEBUG": "0",
    "FLASK_SECRET_KEY": "socle-de-test-secret-key",
    # Auth désactivée par défaut (APP_PASSWORD vide) — cf. app.py:79.
    "APP_USERNAME": "admin",
    "APP_PASSWORD": "",
    # Stockage : jamais Google Drive dans les tests.
    "STORAGE_MODE": "local",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_REDIRECT_URI": "http://localhost:8080/auth/google/callback",
    "GOOGLE_REFRESH_TOKEN": "",
    "GDRIVE_ROOT_FOLDER_NAME": "SAMOURAIS SCRAPPER",
    # Scraper — valeurs par défaut de config.py, figées explicitement.
    "BROWSER_POOL_SIZE": "2",
    "DEFAULT_SCRAPE_INTERVAL_MINUTES": "360",
    "SCROLL_PAUSE_MS": "3000",
    "MAX_SCROLLS": "30",
    "BACKFILL_MAX_SCROLLS": "200",
    "DAILY_MAX_SCROLLS": "40",
    "DAILY_SCRAPE_INTERVAL_MINUTES": "360",
    "DELAY_BETWEEN_PROFILES_MS": "10000",
    "MAX_CONCURRENT_SCRAPES": "2",
    # Proxies : aucun.
    "PROXY_URL": "",
    "PROXY_INSTAGRAM": "",
    "PROXY_TIKTOK": "",
    "PROXY_TWITTER": "",
    "PROXY_REDDIT": "",
    # Apify : aucun jeton — le backend de scrape reste « navigateur » dans les
    # tests, quel que soit le .env du propriétaire (lot A).
    "APIFY_TOKEN": "",
    "APIFY_ACTOR": "",
    # Instagram Graph API : aucun jeton.
    "IG_ACCESS_TOKEN": "",
    "IG_USER_ID": "",
    "FB_APP_ID": "",
    "FB_APP_SECRET": "",
    # Éditeur
    "EDITOR_MAX_FILE_SIZE_MB": "100",
    "LOG_LEVEL": "WARNING",
    # Fuseau figé, volontairement DIFFÉRENT d'UTC : un décalage de fuseau
    # (risque #46) doit rester visible. Un test qui veut un autre fuseau
    # utilise la fixture `timezone`.
    "TZ": "Europe/Paris",
}
os.environ.update(_FORCED_ENV)
if hasattr(time, "tzset"):
    time.tzset()

# Empreinte de la base de production relevée AVANT tout import applicatif.
# `test_socle.py` vérifie qu'elle n'a pas bougé. `os.stat` ne lit aucun octet
# du fichier : la LOI ABSOLUE n°1 est respectée.
try:
    _st = os.stat(PRODUCTION_DB_PATH)
    PRODUCTION_DB_FINGERPRINT: tuple | None = (_st.st_size, _st.st_mtime_ns)
except OSError:  # pragma: no cover - base absente sur une autre machine
    PRODUCTION_DB_FINGERPRINT = None

# ---------------------------------------------------------------------------
# À partir d'ici seulement, les imports applicatifs sont sûrs.
# ---------------------------------------------------------------------------
import pytest  # noqa: E402
from sqlalchemy.orm import close_all_sessions  # noqa: E402

import app.config as app_config  # noqa: E402
import app.db as app_db  # noqa: E402
from app.db import (  # noqa: E402
    Base,
    IgInsightSnapshot,
    MediaComment,
    MediaItem,
    MediaRating,
    Profile,
    ProfileSnapshot,
    SavedMeme,
    ScheduledPost,
    ScrapeJob,
    SessionLocal,
)

# Verrou dur : si l'isolation a échoué, on refuse de collecter le moindre test.
if Path(app_config.DATA_DIR).resolve() != TEST_DATA_DIR:  # pragma: no cover
    raise RuntimeError(
        f"Isolation rompue : app.config.DATA_DIR={app_config.DATA_DIR!r} "
        f"au lieu de {TEST_DATA_DIR!r}."
    )
if Path(app_config.DB_PATH).resolve() == PRODUCTION_DB_PATH.resolve():  # pragma: no cover
    raise RuntimeError(
        "Isolation rompue : les tests pointent sur la base de PRODUCTION."
    )


# ===========================================================================
# Constantes exposées aux modules de test
# ===========================================================================

#: Horodatage unix figé (2023-11-14 22:13:20 UTC). Rule 5 : aucun test ne doit
#: dépendre de l'horloge murale — dérivez toujours de FIXED_NOW.
FIXED_NOW = 1_700_000_000

#: Identifiants utilisés par `auth_client` / `auth_header`.
TEST_APP_USERNAME = "admin"
TEST_APP_PASSWORD = "socle-test-password"


# ===========================================================================
# Exceptions des garde-fous
# ===========================================================================
# Elles héritent de BaseException — et NON de Exception — volontairement :
# le code applicatif est truffé de `except Exception:` (scheduler.py:97,
# pipeline.py, web/api.py...). Un garde-fou avalé par un `except Exception`
# serait un garde-fou inutile : le test passerait au vert en ayant réellement
# tenté un accès réseau ou une écriture dans data/.
# ---------------------------------------------------------------------------


class SocleGuardError(BaseException):
    """Violation d'un garde-fou du socle de test."""


class NetworkAccessAttempted(SocleGuardError):
    """Un test a tenté une connexion sortante (LOI ABSOLUE n°3)."""


class ProductionDataWriteAttempted(SocleGuardError):
    """Un test a tenté d'écrire sous <projet>/data (LOI ABSOLUE n°1)."""


@pytest.fixture(scope="session")
def guard_errors():
    """Expose les classes d'exception des garde-fous.

    Usage :
        with pytest.raises(guard_errors.network):
            httpx.get("https://example.com")
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        base=SocleGuardError,
        network=NetworkAccessAttempted,
        data=ProductionDataWriteAttempted,
    )


# ===========================================================================
# GARDE-FOU 1 — aucun accès réseau (LOI ABSOLUE n°3)
# ===========================================================================
# Le blocage est posé au niveau TRANSPORT de httpx (`HTTPTransport`) et non au
# niveau `Client.send`, précisément pour que `httpx.MockTransport` — l'outil
# recommandé par AUDIT.md §7.4 (T11) — continue de fonctionner.

_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo


def _blocked_network(*args, **kwargs):
    raise NetworkAccessAttempted(
        "Accès réseau interdit dans les tests (LOI ABSOLUE n°3). "
        "Simulez l'appel avec monkeypatch / unittest.mock / httpx.MockTransport."
    )


@pytest.fixture(autouse=True)
def _guard_no_network(monkeypatch):
    """Fait échouer tout test tentant une connexion sortante."""
    monkeypatch.setattr(socket.socket, "connect", _blocked_network, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_network, raising=True)
    monkeypatch.setattr(socket, "create_connection", _blocked_network, raising=True)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_network, raising=True)

    import httpx

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked_network)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _blocked_network
    )

    # Navigateur furtif : les 4 extracteurs + quick_download font
    # `from scrapling.fetchers import StealthyFetcher` (import PAR VALEUR),
    # mais tous référencent le même objet-classe : patcher la classe suffit.
    try:
        from scrapling.fetchers import StealthyFetcher
    except Exception:  # pragma: no cover - scrapling absent
        StealthyFetcher = None
    if StealthyFetcher is not None:
        monkeypatch.setattr(StealthyFetcher, "fetch", _blocked_network)
        if hasattr(StealthyFetcher, "async_fetch"):
            monkeypatch.setattr(StealthyFetcher, "async_fetch", _blocked_network)

    yield


# ===========================================================================
# GARDE-FOU 2 — aucune écriture sous <projet>/data (LOI ABSOLUE n°1)
# ===========================================================================

_FORBIDDEN_PREFIX = os.path.normcase(str(PRODUCTION_DATA_DIR))
_WRITE_MODE_CHARS = frozenset("wax+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _is_under_production_data(path) -> bool:
    if isinstance(path, int):  # descripteur de fichier déjà ouvert
        return False
    try:
        raw = os.fspath(path)
    except TypeError:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not raw:
        return False
    absolute = os.path.normcase(os.path.abspath(raw))
    return absolute == _FORBIDDEN_PREFIX or absolute.startswith(
        _FORBIDDEN_PREFIX + os.sep
    )


def _reject_if_production(path, operation: str) -> None:
    if _is_under_production_data(path):
        raise ProductionDataWriteAttempted(
            f"Écriture interdite dans la base de production : {operation} -> {path!r}. "
            f"Tout chemin d'écriture doit rester sous DATA_DIR de test "
            f"({TEST_DATA_DIR}) ou sous tmp_path (LOI ABSOLUE n°1)."
        )


@pytest.fixture(autouse=True)
def _guard_no_production_data_write(monkeypatch):
    """Fait échouer tout test dont un chemin d'écriture tombe sous data/.

    Défense en profondeur : la vraie protection est l'isolation de DATA_DIR
    (PHASE 0). Ce garde-fou attrape le cas où un test ou une fabrique
    construirait un chemin absolu à la main.

    La LECTURE reste autorisée (mode "r"), seules les opérations mutantes
    sont interceptées.
    """
    real_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open
    real_sqlite_connect = sqlite3.connect

    def guarded_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODE_CHARS & set(mode):
            _reject_if_production(file, f"open(mode={mode!r})")
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        if _WRITE_MODE_CHARS & set(mode):
            _reject_if_production(file, f"io.open(mode={mode!r})")
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS:
            _reject_if_production(path, "os.open")
        return real_os_open(path, flags, *args, **kwargs)

    def guarded_sqlite_connect(database, *args, **kwargs):
        # sqlite3.connect crée le fichier s'il n'existe pas et pose un -wal :
        # toujours une écriture potentielle. Cf. db.py:268.
        _reject_if_production(database, "sqlite3.connect")
        return real_sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(sqlite3, "connect", guarded_sqlite_connect)

    # Opérations mutantes du système de fichiers.
    def _wrap_first_arg(module, name: str):
        real = getattr(module, name)

        def guarded(path, *args, **kwargs):
            _reject_if_production(path, f"{module.__name__}.{name}")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(module, name, guarded)

    def _wrap_two_args(module, name: str):
        real = getattr(module, name)

        def guarded(src, dst, *args, **kwargs):
            _reject_if_production(src, f"{module.__name__}.{name} (source)")
            _reject_if_production(dst, f"{module.__name__}.{name} (destination)")
            return real(src, dst, *args, **kwargs)

        monkeypatch.setattr(module, name, guarded)

    for _name in ("mkdir", "makedirs", "remove", "unlink", "rmdir", "truncate"):
        _wrap_first_arg(os, _name)
    for _name in ("rename", "replace"):
        _wrap_two_args(os, _name)
    _wrap_first_arg(shutil, "rmtree")
    for _name in ("copy", "copy2", "copyfile", "move"):
        _wrap_two_args(shutil, _name)

    yield


# ===========================================================================
# Base de données éphémère
# ===========================================================================


@pytest.fixture(scope="session")
def db_engine():
    """Engine SQLAlchemy de la suite (celui de `app.db`, pointé sur TEST_DATA_DIR)."""
    return app_db.engine


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Crée le schéma une fois par session, sur une base neuve.

    On réutilise l'engine module-level de `app.db` : c'est le SEUL moyen que
    le code applicatif (qui fait `from app.db import SessionLocal`, import PAR
    VALEUR — cf. web/api.py:21, scheduler.py:27, pipeline.py:24) voie la même
    base que les tests. L'isolation entre tests est obtenue par purge des
    tables, pas par recréation de l'engine.
    """
    Base.metadata.create_all(app_db.engine)
    yield
    close_all_sessions()
    app_db.engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_database():
    """Isolation totale : chaque test démarre sur une base VIDE.

    La purge a lieu AVANT le test (et non après) pour qu'un test reste propre
    même si son prédécesseur s'est interrompu brutalement. Aucun ordre
    d'exécution implicite n'est donc requis (LOI ABSOLUE n°5).
    """
    close_all_sessions()
    with app_db.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
    close_all_sessions()


@pytest.fixture(autouse=True)
def _reset_module_singletons():
    """Remet à zéro les états globaux partagés entre tests.

    Ne touche QUE les modules déjà importés : ne provoque aucun import
    parasite (et donc aucun effet de bord d'import) pour les tests qui ne s'en
    servent pas.
    """
    yield
    scheduler_mod = sys.modules.get("app.scheduler")
    if scheduler_mod is not None:
        with scheduler_mod._running_lock:
            scheduler_mod._running_profiles.clear()
    storage_mod = sys.modules.get("app.storage")
    if storage_mod is not None and hasattr(storage_mod, "clear_folder_cache"):
        storage_mod.clear_folder_cache()
    pipeline_mod = sys.modules.get("app.scraper.pipeline")
    if pipeline_mod is not None and hasattr(pipeline_mod, "_EXTRACTORS"):
        pipeline_mod._EXTRACTORS.clear()


@pytest.fixture
def db_session():
    """Session SQLAlchemy sur la base éphémère, refermée en fin de test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ===========================================================================
# Chemins de la sandbox
# ===========================================================================


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """DATA_DIR éphémère de la suite (== app.config.DATA_DIR)."""
    return TEST_DATA_DIR


@pytest.fixture
def settings_env_file() -> Path:
    """Chemin de `DATA_DIR/.env` (app.config.SETTINGS_ENV), nettoyé après le test.

    Cible de T6 / T22 (`_read_env_file` / `_write_env_file`).
    """
    path = TEST_DATA_DIR / ".env"
    if path.exists():
        path.unlink()
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def timezone():
    """Change le fuseau du process pour la durée d'un test.

    Usage : `timezone("UTC")`. Le fuseau de la suite (Europe/Paris) est
    rétabli en fin de test, y compris en cas d'échec.
    """
    previous = os.environ.get("TZ")

    def _set(name: str) -> None:
        os.environ["TZ"] = name
        if hasattr(time, "tzset"):
            time.tzset()

    yield _set

    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    if hasattr(time, "tzset"):
        time.tzset()


# ===========================================================================
# Application Flask
# ===========================================================================


@pytest.fixture
def make_flask_app(monkeypatch):
    """Fabrique d'application Flask, avec surcharges de `app.config`.

    `create_app()` importe APP_USERNAME / APP_PASSWORD / FLASK_SECRET_KEY /
    EDITOR_MAX_FILE_SIZE_MB DANS son corps (app.py:52-57) : les surcharges
    posées ici sont donc bien prises en compte. Ce n'est PAS vrai des
    constantes importées au niveau module par les blueprints (§7.1 AUDIT.md).

    Usage :
        app = make_flask_app(APP_PASSWORD="x", APP_USERNAME="admin")
    """

    def _make(**config_overrides):
        for key, value in config_overrides.items():
            if not hasattr(app_config, key):
                raise AttributeError(
                    f"app.config n'a pas d'attribut {key!r} — faute de frappe ?"
                )
            monkeypatch.setattr(app_config, key, value)

        from app.web.app import create_app

        flask_app = create_app()
        flask_app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        return flask_app

    return _make


@pytest.fixture
def flask_app(make_flask_app):
    """Application SANS authentification (APP_PASSWORD vide — app.py:79)."""
    return make_flask_app(APP_PASSWORD="")


@pytest.fixture
def client(flask_app):
    """Client de test Flask, authentification DÉSACTIVÉE."""
    return flask_app.test_client()


@pytest.fixture
def auth_flask_app(make_flask_app):
    """Application AVEC authentification HTTP Basic activée."""
    return make_flask_app(
        APP_USERNAME=TEST_APP_USERNAME, APP_PASSWORD=TEST_APP_PASSWORD
    )


@pytest.fixture
def auth_client(auth_flask_app):
    """Client de test Flask, authentification ACTIVÉE (aucun en-tête envoyé)."""
    return auth_flask_app.test_client()


@pytest.fixture
def auth_header() -> dict[str, str]:
    """En-tête `Authorization: Basic` valide pour `auth_client`."""
    import base64

    token = base64.b64encode(
        f"{TEST_APP_USERNAME}:{TEST_APP_PASSWORD}".encode("utf-8")
    ).decode("ascii")
    return {"Authorization": f"Basic {token}"}


# ===========================================================================
# Fabriques d'objets
# ===========================================================================


class Factories:
    """Fabriques d'entités persistées, à valeurs par défaut surchargeables.

    Chaque fabrique :
      * accepte n'importe quelle colonne du modèle en mot-clé ;
      * persiste (flush + commit) et retourne l'instance ;
      * garantit l'unicité des champs contraints (username, post_id...) via
        un compteur interne, pour que deux appels sans argument ne se heurtent
        jamais à `idx_profiles_platform_username` / `idx_media_dedup`.
    """

    def __init__(self, session):
        self.session = session
        self._counter = 0

    # -- interne ---------------------------------------------------------
    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def _persist(self, obj, commit: bool):
        self.session.add(obj)
        if commit:
            self.session.commit()
            self.session.refresh(obj)
        else:
            self.session.flush()
        return obj

    # -- Profile ---------------------------------------------------------
    def profile(self, *, commit: bool = True, **overrides) -> Profile:
        n = self._next()
        platform = overrides.pop("platform", "instagram")
        username = overrides.pop("username", f"user{n}")
        defaults = {
            "platform": platform,
            "username": username,
            "profile_url": f"https://{platform}.invalid/{username}/",
            "display_name": f"Display {username}",
            "avatar_url": None,
            "biography": None,
            "is_verified": False,
            "followers_count": 1000,
            "following_count": 100,
            "media_count": 10,
            "is_active": True,
            "scrape_mode": "backfill",
            "scrape_interval_minutes": 360,
            "last_scraped_at": None,
            "backfill_from": None,
            "backfill_to": None,
            "gdrive_folder_id": None,
            "created_at": FIXED_NOW,
            "updated_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(Profile(**defaults), commit)

    # -- MediaItem -------------------------------------------------------
    def media_item(self, profile: Profile | None = None, *, commit: bool = True, **overrides) -> MediaItem:
        if profile is None and "profile_id" not in overrides:
            profile = self.profile()
        n = self._next()
        profile_id = overrides.pop("profile_id", None) or profile.id
        platform = overrides.pop(
            "platform", profile.platform if profile is not None else "instagram"
        )
        post_id = overrides.pop("post_id", f"post{n}")
        media_type = overrides.pop("media_type", "image")
        ext = "mp4" if media_type == "video" else "jpg"
        defaults = {
            "profile_id": profile_id,
            "platform": platform,
            "post_id": post_id,
            "post_url": f"https://{platform}.invalid/p/{post_id}/",
            "media_type": media_type,
            "media_url": f"https://cdn.{platform}.invalid/{post_id}.{ext}",
            "content_hash": None,
            "file_size": None,
            "width": 1080,
            "height": 1080,
            "duration": 12.5 if media_type == "video" else None,
            "caption": f"caption {post_id}",
            "ig_like_count": 10,
            "ig_comment_count": 2,
            "ig_view_count": 100,
            "posted_at": FIXED_NOW - 3600,
            "status": "pending",
            "local_path": None,
            "gdrive_file_id": None,
            "gdrive_url": None,
            "error_message": None,
            "retry_count": 0,
            "discovered_at": FIXED_NOW,
            "downloaded_at": None,
            "uploaded_at": None,
        }
        defaults.update(overrides)
        return self._persist(MediaItem(**defaults), commit)

    # -- ScrapeJob -------------------------------------------------------
    def scrape_job(self, profile: Profile | None = None, *, commit: bool = True, **overrides) -> ScrapeJob:
        if profile is None and "profile_id" not in overrides:
            profile = self.profile()
        defaults = {
            "profile_id": overrides.pop("profile_id", None) or profile.id,
            "status": "queued",
            "triggered_by": "manual",
            "media_found": 0,
            "media_new": 0,
            "media_downloaded": 0,
            "media_uploaded": 0,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
            "created_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(ScrapeJob(**defaults), commit)

    # -- ScheduledPost ---------------------------------------------------
    def scheduled_post(self, *, commit: bool = True, **overrides) -> ScheduledPost:
        n = self._next()
        defaults = {
            "title": f"Post {n}",
            "caption": f"caption {n}",
            "media_path": str(TEST_DATA_DIR / "calendar" / f"post{n}.jpg"),
            "media_type": "image",
            "template_format": "square",
            "thumbnail_path": None,
            "source_media_id": None,
            "scheduled_at": FIXED_NOW + 3600,
            "status": "draft",
            "platforms": '["instagram"]',
            "publish_results": None,
            "created_at": FIXED_NOW,
            "updated_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(ScheduledPost(**defaults), commit)

    # -- Entités secondaires ---------------------------------------------
    def media_comment(self, media_item: MediaItem | None = None, *, commit: bool = True, **overrides) -> MediaComment:
        if media_item is None and "media_item_id" not in overrides:
            media_item = self.media_item()
        n = self._next()
        defaults = {
            "media_item_id": overrides.pop("media_item_id", None) or media_item.id,
            "user_name": f"commenter{n}",
            "comment_text": f"comment {n}",
            "created_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(MediaComment(**defaults), commit)

    def media_rating(self, media_item: MediaItem | None = None, *, commit: bool = True, **overrides) -> MediaRating:
        if media_item is None and "media_item_id" not in overrides:
            media_item = self.media_item()
        n = self._next()
        defaults = {
            "media_item_id": overrides.pop("media_item_id", None) or media_item.id,
            "user_name": f"rater{n}",
            "rating": 5,
            "created_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(MediaRating(**defaults), commit)

    def profile_snapshot(self, profile: Profile | None = None, *, commit: bool = True, **overrides) -> ProfileSnapshot:
        if profile is None and "profile_id" not in overrides:
            profile = self.profile()
        defaults = {
            "profile_id": overrides.pop("profile_id", None) or profile.id,
            "followers_count": 1000,
            "following_count": 100,
            "media_count": 10,
            "snapshot_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(ProfileSnapshot(**defaults), commit)

    def ig_insight_snapshot(self, profile: Profile | None = None, *, commit: bool = True, **overrides) -> IgInsightSnapshot:
        if profile is None and "profile_id" not in overrides:
            profile = self.profile()
        defaults = {
            "profile_id": overrides.pop("profile_id", None) or profile.id,
            "followers_count": 1000,
            "following_count": 100,
            "media_count": 10,
            "reach": 500,
            "impressions": 900,
            "accounts_engaged": 50,
            "profile_views": 20,
            "snapshot_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(IgInsightSnapshot(**defaults), commit)

    def saved_meme(self, *, commit: bool = True, **overrides) -> SavedMeme:
        n = self._next()
        defaults = {
            "title": f"Meme {n}",
            "caption": f"caption {n}",
            "media_type": "image",
            "template_format": "square",
            "file_path": str(TEST_DATA_DIR / "editor" / "outputs" / f"meme{n}.jpg"),
            "thumbnail_path": None,
            "file_size": 1234,
            "source_media_id": None,
            "created_at": FIXED_NOW,
        }
        defaults.update(overrides)
        return self._persist(SavedMeme(**defaults), commit)


@pytest.fixture
def factories(db_session) -> Factories:
    """Fabriques d'objets persistés sur la base éphémère."""
    return Factories(db_session)


@pytest.fixture
def make_profile(factories):
    return factories.profile


@pytest.fixture
def make_media_item(factories):
    return factories.media_item


@pytest.fixture
def make_scrape_job(factories):
    return factories.scrape_job


@pytest.fixture
def make_scheduled_post(factories):
    return factories.scheduled_post
