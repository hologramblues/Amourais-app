"""
LOT C — serveur de production : gunicorn, utilisateur non-root, arrêt gracieux.

Couvre les lots 4.1 à 4.4 d'AUDIT.md §9, et notamment le TROISIÈME VOLET DU
TEST T15 que `tests/test_conteneur.py` laissait explicitement de côté
(« `requirements.txt` contient un serveur WSGI […] appartient au lot 4.2 »).

CE QUE CES TESTS NE PEUVENT PAS FAIRE
─────────────────────────────────────
Docker n'est pas disponible sur la machine de développement : rien ici ne
construit l'image ni ne lance de conteneur. Les assertions sur le `Dockerfile`
sont donc STATIQUES — elles vérifient que les décisions critiques du lot sont
écrites dans le fichier, pas qu'elles produisent une image qui démarre.
La liste de ce qui reste à vérifier au premier déploiement réel est dans le
rapport du lot C.

En revanche, tout ce qui concerne `run.py` est vérifié POUR DE VRAI : le garde
de processus, l'idempotence de l'arrêt et l'attente bornée des threads sont
exécutés, pas relus.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

import run

RACINE = Path(__file__).resolve().parents[1]
DOCKERFILE = RACINE / "Dockerfile"
REQUIREMENTS = RACINE / "requirements.txt"


def _directives() -> list[tuple[int, str]]:
    """Les lignes RÉELLES du Dockerfile (numéro, texte), commentaires exclus.

    Indispensable : les commentaires de ce Dockerfile citent nommément
    « patchright install » et « USER » pour expliquer l'ordre critique du lot
    4.3. Chercher ces chaînes sans filtrer les commentaires ferait porter les
    assertions sur la documentation au lieu du code.
    """
    lignes = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    return [(i, l) for i, l in enumerate(lignes) if not l.strip().startswith("#")]


def _cmd_aplati() -> str:
    """La directive CMD du Dockerfile, continuations « \\ » recollées."""
    contenu = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    lignes = [l for l in contenu.splitlines() if l.strip().startswith("CMD")]
    assert len(lignes) == 1, f"un et un seul CMD attendu, trouve {len(lignes)}"
    return " ".join(lignes[0].split())


@pytest.fixture
def run_isole(monkeypatch):
    """Neutralise l'ordonnanceur réel et repart d'un état d'arrêt vierge.

    `start_scheduler` / `stop_scheduler` sont patchés DANS `run` : ils y sont
    importés par valeur (limite connue du socle, §7.1 AUDIT.md), patcher
    `app.scheduler` n'aurait donc aucun effet ici.
    """
    appels: list[str] = []
    monkeypatch.setattr(run, "start_scheduler", lambda: appels.append("start"))
    monkeypatch.setattr(run, "stop_scheduler", lambda: appels.append("stop"))
    monkeypatch.delenv(run._SCHEDULER_OWNER_ENV, raising=False)
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(run, "_shutdown_done", False)
    yield appels
    run._shutdown_done = False


# ===========================================================================
# Lot 4.1 — le boot est extrait, donc importable et testable
# ===========================================================================


@pytest.mark.static_check
def test_le_boot_est_extrait_en_fonctions_importables():
    """Tout le démarrage vivait dans `if __name__ == "__main__"` : ni
    importable, ni testable (lot 4.1)."""
    for nom in ("boot", "main", "create_wsgi_app", "shutdown",
                "start_scheduler_once", "install_signal_handlers"):
        assert callable(getattr(run, nom, None)), f"run.{nom}() manquant"


@pytest.mark.static_check
def test_importer_run_ne_demarre_rien():
    """L'import doit rester SANS EFFET DE BORD : la cible gunicorn est une
    FABRIQUE (`run:create_wsgi_app()`), pas une variable de module. Un
    `app = create_app()` au niveau module ferait démarrer base et ordonnanceur
    au simple import — y compris depuis cette suite."""
    from app.scheduler import scheduler

    assert not scheduler.running, (
        "importer `run` a demarre l'ordonnanceur : effet de bord interdit"
    )


# ===========================================================================
# Lot 4.2 — vrai serveur WSGI, un seul worker, un seul ordonnanceur
# ===========================================================================


@pytest.mark.static_check
def test_requirements_declare_un_serveur_wsgi():
    """Troisième volet de T15, laissé au lot 4.2 par test_conteneur.py."""
    contenu = REQUIREMENTS.read_text(encoding="utf-8").lower()
    assert "gunicorn" in contenu, (
        "aucun serveur WSGI de production dans requirements.txt : "
        "l'application tourne sur le serveur de developpement Werkzeug"
    )


@pytest.mark.static_check
def test_le_conteneur_ne_demarre_plus_le_serveur_de_developpement():
    cmd = _cmd_aplati()
    assert "gunicorn" in cmd, f"le CMD ne lance pas gunicorn : {cmd!r}"
    assert "run.py" not in cmd, (
        f"le CMD lance encore le serveur de developpement Werkzeug : {cmd!r}"
    )


@pytest.mark.static_check
def test_un_seul_worker_gunicorn():
    """CONTRAINTE ABSOLUE du lot 4.2. Les verrous anti-doublon de
    l'ordonnanceur sont EN MÉMOIRE : deux workers = deux ordonnanceurs qui ne
    se voient pas = deux navigateurs sur le même profil (risque #8, §4.21)."""
    cmd = _cmd_aplati()
    assert "--workers 1" in cmd, (
        f"« --workers 1 » absent du CMD : plus d'un worker recree le bug de "
        f"duplication de l'ordonnanceur. CMD lu : {cmd!r}"
    )
    assert "--threads" in cmd, (
        "la montee en charge doit passer par --threads, jamais par --workers"
    )


@pytest.mark.static_check
def test_ni_preload_ni_max_requests():
    """`--preload` démarrerait l'ordonnanceur AVANT le fork (les threads ne
    survivent pas à `fork()`) ; `--max-requests` recyclerait le worker, donc
    redémarrerait l'ordonnanceur et tuerait les scrapes en cours."""
    cmd = _cmd_aplati()
    assert "--preload" not in cmd, "--preload est interdit (cf. en-tete de run.py)"
    assert "--max-requests" not in cmd, "--max-requests recyclerait l'ordonnanceur"


@pytest.mark.static_check
def test_le_cmd_exec_pour_recevoir_sigterm_en_pid_1():
    """Sans `exec`, /bin/sh serait PID 1 et NE RELAIERAIT PAS SIGTERM :
    l'arrêt gracieux du lot 4.4 ne se déclencherait jamais en production."""
    cmd = _cmd_aplati()
    assert cmd.startswith("CMD exec "), (
        f"le CMD doit utiliser « exec » pour que gunicorn soit PID 1 et "
        f"recoive SIGTERM directement. CMD lu : {cmd!r}"
    )


def test_le_garde_de_processus_refuse_un_second_demarrage(run_isole):
    """Second rempart derrière `--workers 1` : même processus → un seul start."""
    assert run.start_scheduler_once() is True
    assert run.start_scheduler_once() is False
    assert run_isole == ["start"], (
        f"l'ordonnanceur a ete demarre plusieurs fois : {run_isole}"
    )


def test_le_garde_refuse_si_un_autre_processus_vivant_le_detient(run_isole, monkeypatch):
    """Le jeton vit dans l'ENVIRONNEMENT, hérité par fork/exec : un enfant
    (rechargeur, worker respawné) doit constater qu'un processus VIVANT détient
    déjà l'ordonnanceur. On se désigne nous-même comme ce processus vivant."""
    monkeypatch.setenv(run._SCHEDULER_OWNER_ENV, str(os.getppid()))
    assert run.start_scheduler_once() is False
    assert run_isole == []


def test_le_garde_reprend_apres_un_processus_mort(run_isole, monkeypatch):
    """Symétrique : un jeton hérité d'un processus MORT ne doit pas condamner
    définitivement la planification."""
    monkeypatch.setenv(run._SCHEDULER_OWNER_ENV, "999999")
    assert run.start_scheduler_once() is True
    assert run_isole == ["start"]


def test_scheduler_enabled_0_est_une_trappe_de_secours(run_isole, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    assert run.start_scheduler_once() is False
    assert run_isole == []


# ===========================================================================
# Lot 4.3 — utilisateur non-root, et le piège du chemin des navigateurs
# ===========================================================================


@pytest.mark.static_check
def test_le_conteneur_ne_tourne_pas_en_root():
    contenu = DOCKERFILE.read_text(encoding="utf-8")
    users = [l.split()[1] for l in contenu.splitlines()
             if l.strip().startswith("USER ") and len(l.split()) > 1]
    assert users, "aucune directive USER : le conteneur tourne en root (lot 4.3)"
    assert users[-1] != "root", "le dernier USER est root"


@pytest.mark.static_check
def test_lutilisateur_est_cree_avant_installation_des_navigateurs():
    """LE PIÈGE DU LOT 4.3. Patchright télécharge ses navigateurs dans le CACHE
    DE L'UTILISATEUR COURANT. Installés en root, ils atterrissent dans
    /root/.cache/ms-playwright, illisible pour l'utilisateur final : le
    scraping casse SILENCIEUSEMENT (l'échec de fetch est avalé et rendu comme
    un job `empty`, indiscernable d'un scrape sain — §4.20)."""
    directives = _directives()

    i_user = next((i for i, l in directives
                   if l.strip().startswith("USER ")), None)
    i_install = next((i for i, l in directives
                      if "patchright install" in l), None)

    assert i_user is not None, "aucune directive USER dans le Dockerfile"
    assert i_install is not None, "« patchright install » introuvable"
    assert i_user < i_install, (
        f"« patchright install » (ligne {i_install + 1}) s'execute AVANT le "
        f"passage en utilisateur non privilegie (ligne {i_user + 1}) : les "
        f"navigateurs seront installes dans le home de root et introuvables "
        f"a l'execution"
    )


@pytest.mark.static_check
def test_le_chemin_des_navigateurs_est_explicite():
    """Ceinture et bretelles : le chemin ne doit dépendre d'aucune résolution
    implicite de $HOME, et doit être posé AVANT l'installation pour que la
    même valeur serve à l'installation et à l'exécution."""
    directives = _directives()
    i_path = next((i for i, l in directives
                   if "PLAYWRIGHT_BROWSERS_PATH" in l), None)
    i_install = next((i for i, l in directives
                      if "patchright install" in l), None)
    assert i_path is not None, "PLAYWRIGHT_BROWSERS_PATH n'est pas fixe"
    assert i_install is not None
    assert i_path < i_install, (
        "PLAYWRIGHT_BROWSERS_PATH doit etre pose AVANT « patchright install »"
    )


# ===========================================================================
# Lot 4.4 — arrêt gracieux (risque #65 : stop_scheduler() sans appelant)
# ===========================================================================


def test_shutdown_appelle_stop_scheduler(run_isole):
    """Le cœur du risque #65 : `stop_scheduler()` existait mais n'avait AUCUN
    appelant, et il n'y avait ni atexit ni handler de signal."""
    run.start_scheduler_once()
    run.shutdown(reason="TEST")
    assert "stop" in run_isole, "stop_scheduler() n'a pas ete appele"


def test_shutdown_est_idempotent(run_isole):
    """Plusieurs chemins mènent au même arrêt : le handler SIGTERM, puis
    `atexit` quand l'interpréteur se ferme."""
    run.start_scheduler_once()
    run.shutdown(reason="SIGTERM")
    run.shutdown(reason="atexit")
    assert run_isole.count("stop") == 1, (
        f"l'arret n'est pas idempotent : {run_isole}"
    )


def test_un_processus_non_proprietaire_narrete_rien(run_isole, monkeypatch):
    """Un processus qui n'a jamais démarré d'ordonnanceur n'a rien à arrêter —
    sans quoi il couperait celui d'un autre."""
    monkeypatch.setenv(run._SCHEDULER_OWNER_ENV, str(os.getppid()))
    run.shutdown(reason="TEST")
    assert "stop" not in run_isole


def test_larret_attend_un_thread_de_scrape_mais_de_facon_bornee(run_isole, monkeypatch):
    """Les threads de scrape sont `daemon=True` : l'interpréteur ne les attend
    pas et les tue en vol. On leur offre un délai — on ne le promet pas, et
    surtout on ne dépasse JAMAIS le plafond, sinon Railway SIGKILLe avant."""
    monkeypatch.setenv("SHUTDOWN_WAIT_SECONDS", "1")
    run.start_scheduler_once()

    fin = threading.Event()
    t = threading.Thread(target=lambda: fin.wait(20),
                         name="scrape-instagram-cobaye", daemon=True)
    t.start()
    try:
        depart = time.monotonic()
        run.shutdown(reason="TEST")
        duree = time.monotonic() - depart
    finally:
        fin.set()
        t.join(timeout=5)

    assert duree >= 0.9, (
        f"l'arret n'a pas attendu le thread de scrape ({duree:.2f}s)"
    )
    assert duree < 5, (
        f"l'attente n'est pas bornee ({duree:.2f}s pour un plafond de 1s) : "
        f"le SIGKILL de Railway tomberait avant la fin de l'arret"
    )


def test_larret_nattend_pas_le_minuteur_de_demarrage(run_isole, monkeypatch):
    """`ig-initial` n'est pas un porteur de job mais un `time.sleep(15)` de
    démarrage (scheduler.py:716). L'attendre brûlait TOUT le budget d'arrêt sur
    un thread endormi et affichait un avertissement FAUX (« des jobs resteront
    running ») alors qu'aucun job n'existe."""
    monkeypatch.setenv("SHUTDOWN_WAIT_SECONDS", "5")
    run.start_scheduler_once()

    fin = threading.Event()
    t = threading.Thread(target=lambda: fin.wait(20), name="ig-initial", daemon=True)
    t.start()
    try:
        depart = time.monotonic()
        run.shutdown(reason="TEST")
        duree = time.monotonic() - depart
    finally:
        fin.set()
        t.join(timeout=5)

    assert duree < 1, (
        f"l'arret a attendu le minuteur de demarrage ({duree:.2f}s) : ce delai "
        f"est vole au vrai scrape avant le SIGKILL"
    )


@pytest.mark.static_check
def test_le_healthcheck_reste_sur_lendpoint_public():
    """Garde-fou de non-régression du lot C sur l'acquis du lot 1.8 : le
    passage à gunicorn ne doit pas casser le healthcheck Railway. `/health` est
    public par conception (exemption dans `_require_auth`, app/web/app.py)."""
    contenu = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    hc = [l for l in contenu.splitlines() if l.strip().startswith("HEALTHCHECK")]
    assert len(hc) == 1 and "/health" in hc[0], (
        f"le HEALTHCHECK ne sonde plus /health : {hc!r}"
    )
