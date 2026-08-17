#!/usr/bin/env python3
"""
SAMOURAIS SCRAPPER -- point d'entrée.

Deux façons SUPPORTÉES de démarrer l'application :

  * PRODUCTION  : ``gunicorn --workers 1 --threads N "run:create_wsgi_app()"``
                  Le serveur de développement Werkzeug n'est pas un serveur de
                  production (mono-processus, pas de limite de connexions, pas
                  de garde-fou mémoire) — lot 4.2 d'AUDIT.md §9.

  * DÉVELOPPEMENT : ``python run.py``  → ``main()`` → serveur Werkzeug.

Les deux passent par ``boot()``, qui fait l'amorçage UNE SEULE FOIS : diagnostic
du volume, création des répertoires, migration de la base, puis démarrage de
l'ordonnanceur.

────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE N'A AUCUN EFFET DE BORD À L'IMPORT
────────────────────────────────────────────────────────────────────────────
Tout l'amorçage vivait auparavant dans ``if __name__ == "__main__"`` : le boot
n'était ni importable ni testable (lot 4.1). Il est maintenant découpé en
fonctions, et l'objet WSGI est construit par une FABRIQUE — pas par une
variable de module. Gunicorn sait appeler une fabrique avec la syntaxe
``"run:create_wsgi_app()"``.

C'est délibéré : un ``app = create_app()`` au niveau module ferait démarrer la
base ET l'ordonnanceur au simple ``import run`` — y compris depuis la suite de
tests. Avec la fabrique, importer ce module ne fait rien.

────────────────────────────────────────────────────────────────────────────
POURQUOI UN SEUL WORKER (contrainte absolue du lot 4.2)
────────────────────────────────────────────────────────────────────────────
L'ordonnanceur est un ``BackgroundScheduler`` APScheduler dont les verrous
anti-doublon sont EN MÉMOIRE (``_running_profiles`` / ``_running_lock``,
app/scheduler.py). Deux processus qui l'exécutent ne se voient pas : ils
lancent deux navigateurs sur le même profil, insèrent des doublons et
déclenchent le rollback global du pipeline (AUDIT.md §2.1, §4.21, risque #8).

La montée en charge se fait donc par THREADS (``--threads``), jamais par
workers. Et ``--preload`` est INTERDIT : il ferait construire l'application
dans le maître, donc démarrer l'ordonnanceur AVANT le fork ; les threads ne
survivent pas à ``fork()``, le worker hériterait d'un ordonnanceur dont
l'attribut ``running`` vaut True mais dont plus aucun thread ne tourne —
panne silencieuse totale de la planification.

``start_scheduler_once()`` reste un second rempart si l'un de ces réglages
venait à changer par accident.
"""
from __future__ import annotations

import atexit
import os
import signal
import threading
import time

from loguru import logger

from app.config import (
    PORT, DEBUG, DATA_DIR, DB_PATH, DOWNLOAD_DIR, SESSIONS_DIR,
    COOKIES_DIR, CALENDAR_DIR, EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR,
)
from app.db import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.web.app import create_app


VOLUME_MARKER = DATA_DIR / ".samourais_volume_marker"

# ---------------------------------------------------------------------------
# Garde de processus de l'ordonnanceur (lot 4.2)
# ---------------------------------------------------------------------------
# Le jeton est posé dans l'ENVIRONNEMENT, pas seulement dans une variable de
# module : un simple drapeau de module est invisible d'un processus à l'autre.
# L'environnement, lui, est hérité par `fork()` comme par `exec()`, donc un
# enfant (rechargeur Werkzeug, worker gunicorn respawné, `python run.py` lancé
# par erreur à côté) peut constater que l'ordonnanceur appartient déjà à un
# autre processus VIVANT et s'abstenir.
#
# Le PID est stocké — pas un simple "1" — pour distinguer deux cas que rien ne
# séparerait autrement : « c'est MOI qui l'ai démarré » (double import dans le
# même processus : le module vaut `__main__` sous `python run.py` et `run` sous
# gunicorn, donc deux objets module distincts) et « un AUTRE processus l'a
# démarré ».
_SCHEDULER_OWNER_ENV = "SAMOURAIS_SCHEDULER_PID"

# Préfixes des threads qui PORTENT UN JOB OUVERT, lancés par app/scheduler.py :
# `scrape-{plateforme}-{user}` (:238), `manual-scrape-{id}` (:664) et
# `retry-{id}` (:373). Ils sont tous `daemon=True` : à l'arrêt du processus,
# l'interpréteur ne les attend PAS et les tue en vol, laissant des jobs
# `running` et des fichiers partiels (AUDIT.md risque #65). L'arrêt gracieux
# leur laisse une chance bornée de finir.
#
# `ig-initial` (:716) est VOLONTAIREMENT EXCLU : ce n'est pas un porteur de
# job mais un simple minuteur de démarrage (`time.sleep(15)` avant une collecte
# Graph API). L'attendre brûlait la TOTALITÉ du budget d'arrêt sur un thread
# endormi — mesuré : 8 s à chaque arrêt — et faisait afficher un avertissement
# FAUX (« des jobs resteront running ») alors qu'aucun job n'existe. Sur
# Railway, où le SIGKILL tombe quelques secondes après le SIGTERM, ce délai
# volait au vrai scrape le temps qui lui était destiné.
_SCRAPE_THREAD_PREFIXES = ("scrape-", "manual-scrape-", "retry-")

# Attente maximale, en secondes, des threads de scrape à l'arrêt. Volontairement
# INFÉRIEURE au `--graceful-timeout` de gunicorn (30 s) et au délai que Railway
# ou Docker laissent avant le SIGKILL : dépasser ce délai ne rendrait pas
# l'arrêt plus propre, il le rendrait brutal.
_SHUTDOWN_WAIT_DEFAULT = 8.0

_shutdown_lock = threading.Lock()
_shutdown_done = False


# ---------------------------------------------------------------------------
# Petits lecteurs d'environnement (tolérants : une valeur illisible ne doit
# jamais empêcher le démarrage — c'est la leçon du risque #41 / §4.14)
# ---------------------------------------------------------------------------
def _env_flag(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Valeur non numerique pour {} ({!r}) — repli sur {}",
                       key, raw, default)
        return default
    return value if value >= 0 else default


def _pid_is_alive(pid: int) -> bool:
    """Le processus `pid` existe-t-il encore ?

    Le signal 0 ne fait que tester l'existence. En cas de doute on répond True :
    l'erreur « je ne démarre pas un second ordonnanceur » est rattrapable
    (l'ordonnanceur existant continue de tourner), alors que « j'en démarre un
    second » est précisément le bug que ce lot supprime.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # existe, appartient à un autre utilisateur
    except OSError:
        return True          # cas inconnu — on reste prudent
    return True


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Démarrage / arrêt de l'ordonnanceur (lots 4.2 et 4.4)
# ---------------------------------------------------------------------------
def start_scheduler_once() -> bool:
    """Démarrer l'ordonnanceur si, et seulement si, aucun autre ne tourne.

    Renvoie True s'il a été démarré par CET appel, False sinon.

    Trois cas d'abstention :
      1. ``SCHEDULER_ENABLED=0`` — trappe de secours pour lancer une instance
         purement web (diagnostic, migration) sans toucher à la planification.
      2. Le jeton d'environnement porte NOTRE propre PID : ce processus a déjà
         démarré l'ordonnanceur (double import du module).
      3. Le jeton porte le PID d'un AUTRE processus encore vivant.
    """
    if not _env_flag("SCHEDULER_ENABLED", True):
        logger.warning(
            "SCHEDULER_ENABLED=0 — ordonnanceur NON demarre : aucun scrape "
            "automatique, aucun post planifie ne partira depuis ce processus."
        )
        return False

    me = os.getpid()
    owner_raw = os.environ.get(_SCHEDULER_OWNER_ENV, "").strip()

    if owner_raw:
        try:
            owner = int(owner_raw)
        except ValueError:
            logger.warning(
                "Jeton {}={!r} illisible — il est ignore et reecrit.",
                _SCHEDULER_OWNER_ENV, owner_raw,
            )
            owner = 0

        if owner == me:
            logger.warning(
                "Ordonnanceur deja demarre par CE processus (pid={}) — "
                "second appel ignore.", me,
            )
            return False

        if owner and _pid_is_alive(owner):
            logger.warning(
                "Ordonnanceur deja detenu par le processus vivant pid={} — "
                "ce processus (pid={}) n'en demarre PAS un second "
                "(AUDIT.md §4.21, risque #8).", owner, me,
            )
            return False

        logger.info(
            "Jeton d'ordonnanceur herite d'un processus mort (pid={}) — "
            "reprise par pid={}.", owner, me,
        )

    # Le jeton est posé AVANT le démarrage : si start_scheduler() lève, le
    # `shutdown()` de ce même processus doit tout de même se reconnaître
    # propriétaire et tenter l'arrêt d'un ordonnanceur partiellement démarré.
    os.environ[_SCHEDULER_OWNER_ENV] = str(me)
    try:
        start_scheduler()
    except Exception:
        os.environ.pop(_SCHEDULER_OWNER_ENV, None)
        raise
    logger.info("Ordonnanceur demarre par le processus pid={}", me)
    return True


def _wait_for_scrape_threads(timeout_s: float) -> list[str]:
    """Attendre, dans une limite globale de `timeout_s`, les threads de scrape.

    Renvoie les noms de ceux qui n'ont PAS fini. Ils sont `daemon=True`, donc
    aucun `join()` ne peut les garantir : on leur offre un délai, on ne le
    promet pas.
    """
    if timeout_s <= 0:
        return []

    deadline = time.monotonic() + timeout_s
    survivants: list[str] = []
    courant = threading.current_thread()

    for t in list(threading.enumerate()):
        if t is courant or not t.is_alive():
            continue
        if not t.name.startswith(_SCRAPE_THREAD_PREFIXES):
            continue

        restant = deadline - time.monotonic()
        if restant <= 0:
            survivants.append(t.name)
            continue

        logger.info("Attente du thread de scrape {} ({:.1f} s max)", t.name, restant)
        t.join(timeout=restant)
        if t.is_alive():
            survivants.append(t.name)

    return survivants


def shutdown(reason: str = "atexit") -> None:
    """Arrêt gracieux, idempotent, sûr à appeler depuis n'importe quel chemin.

    Corrige le risque #65 / lot 4.4 : ``stop_scheduler()`` existait mais
    n'avait AUCUN appelant, et il n'y avait ni ``atexit`` ni handler de signal.
    Chaque redéploiement Railway tuait donc APScheduler et les threads de
    scrape en vol.

    Idempotent parce que plusieurs chemins peuvent y mener dans le même arrêt :
    le handler SIGTERM, puis ``atexit`` au moment où l'interpréteur se ferme.
    """
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True

    # Seul le propriétaire arrête l'ordonnanceur. Un processus qui n'a jamais
    # démarré le sien (worker sans jeton) n'a rien à arrêter.
    if os.environ.get(_SCHEDULER_OWNER_ENV, "").strip() != str(os.getpid()):
        return

    logger.info("Arret gracieux ({}) — arret de l'ordonnanceur…", reason)
    try:
        stop_scheduler()
    except Exception as exc:
        # Un arrêt qui échoue ne doit jamais empêcher le processus de mourir.
        logger.error("Echec de l'arret de l'ordonnanceur : {}", exc)

    attente = _env_float("SHUTDOWN_WAIT_SECONDS", _SHUTDOWN_WAIT_DEFAULT)
    survivants = _wait_for_scrape_threads(attente)
    if survivants:
        logger.warning(
            "{} thread(s) de scrape encore en cours apres {:.0f} s : {} — "
            "leurs jobs resteront `running` et seront recuperes au prochain "
            "demarrage.", len(survivants), attente, ", ".join(survivants),
        )
    else:
        logger.info("Aucun thread de scrape en cours — arret propre.")

    os.environ.pop(_SCHEDULER_OWNER_ENV, None)
    logger.info("Arret termine.")


def install_signal_handlers() -> None:
    """Intercepter SIGTERM / SIGINT pour passer par ``shutdown()``.

    ⚠️ À N'APPELER QUE SUR LE CHEMIN DE DÉVELOPPEMENT (``main()``).

    Sous gunicorn, le worker installe SES PROPRES handlers (``init_signals()``)
    AVANT de charger l'application : écraser SIGTERM ici casserait l'arrêt
    gracieux de gunicorn lui-même. Sur ce chemin-là, c'est ``atexit`` — posé
    par ``boot()`` — qui déclenche ``shutdown()`` quand le worker se termine.

    ``signal.signal`` n'est de toute façon utilisable que depuis le thread
    principal ; l'échec est donc absorbé, il ne doit pas empêcher le démarrage.
    """
    def _handler(signum, _frame):  # noqa: ANN001, ANN202
        nom = signal.Signals(signum).name
        logger.info("Signal {} recu", nom)
        shutdown(reason=nom)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError, AttributeError) as exc:
            logger.warning("Handler {} non installe : {}", sig, exc)


# ---------------------------------------------------------------------------
# Amorçage
# ---------------------------------------------------------------------------
def boot() -> None:
    """Amorçage complet du processus, hors serveur HTTP (lot 4.1).

    Extrait de ``if __name__ == "__main__"`` pour être importable et testable.
    """
    diagnose_volume()
    ensure_data_dirs()
    init_db()
    atexit.register(shutdown, "atexit")
    start_scheduler_once()


def create_wsgi_app():
    """Fabrique WSGI — c'est la cible de gunicorn : ``"run:create_wsgi_app()"``.

    Appelée UNE FOIS par worker. Avec ``--workers 1`` (et sans ``--preload``),
    cela veut dire une fois par déploiement, dans le processus qui servira les
    requêtes : un seul amorçage, un seul ordonnanceur.
    """
    boot()
    return create_app()


def main() -> int:
    """Chemin de DÉVELOPPEMENT : serveur Werkzeug (lot 4.1).

    N'est plus utilisé en production, où c'est gunicorn qui appelle
    ``create_wsgi_app()``. Conservé pour ``python run.py`` en local.
    """
    boot()
    install_signal_handlers()
    app = create_app()

    logger.warning(
        "Serveur de DEVELOPPEMENT Werkzeug (port {}). En production, utiliser "
        "gunicorn — cf. la directive CMD du Dockerfile.", PORT,
    )
    # use_reloader=False: le rechargeur Werkzeug ré-exécute ce module dans un
    # sous-processus, ce qui démarrerait un SECOND ordonnanceur dont les verrous
    # en mémoire sont invisibles du premier (AUDIT.md §2.1 / §4.21, risque #8).
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
