"""
MODULE WEB — routes, authentification, robustesse des entrées (vague 0).

Périmètre : `app/web/app.py` (factory + hook d'auth), `app/web/routes.py`
(pages HTML, `/media/file`, `/media/thumb`) et les points d'entrée les plus
exposés de `app/web/api.py` (`/api/quick-download`, `/api/settings/env`).

────────────────────────────────────────────────────────────────────────────
CONVENTION DE CE FICHIER (LOI ABSOLUE n°4)
────────────────────────────────────────────────────────────────────────────
Les tests VERTS décrivent le comportement RÉEL d'aujourd'hui — y compris
quand il est discutable (application ouverte sans mot de passe).
Les bugs documentés dans AUDIT.md ne sont JAMAIS écrits en rouge : on écrit
le test du comportement CORRECT attendu et on le marque
`@pytest.mark.xfail(strict=True, reason="risque #N — ... ; lot X")`.
Le jour où le lot cité corrige le bug, le test passe XPASS et fait ÉCHOUER
la suite en criant « retire ce xfail ».

────────────────────────────────────────────────────────────────────────────
AUCUN RÉSEAU (LOI ABSOLUE n°3)
────────────────────────────────────────────────────────────────────────────
Aucun test ici ne fait sortir une requête : `/api/quick-download` n'est
appelé qu'avec des corps invalides ou des URL non reconnues (rejetées par
`detect_platform` AVANT le `threading.Thread` de `web/api.py:720`), et la
fixture `pas_de_thread_de_telechargement` fait échouer tout test qui
créerait malgré tout ce thread. `/media/thumb` n'est sollicité que sur des
chemins rejetés avant `subprocess.run`, et `ffmpeg_interdit` le vérifie.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote

import pytest

import app.web.api as web_api
import app.web.app as web_app_module
import app.web.routes as web_routes
from conftest import TEST_APP_PASSWORD, TEST_APP_USERNAME, TEST_DATA_DIR

pytestmark = pytest.mark.web


# ===========================================================================
# Outillage local (le socle est en lecture seule : tout ce qui manque est ici)
# ===========================================================================

#: Les 8 pages HTML de l'application (`routes.py`).
PAGES = (
    "/",
    "/profiles",
    "/jobs",
    "/settings",
    "/viewer",
    "/editor",
    "/calendar",
    "/analytics",
)

#: Routes GET non-page, dont deux endpoints sensibles (§6.1 AUDIT.md).
#: `/api/debug/volume` n'y figure pas : depuis la correction du risque #6 il
#: rend 404 hors mode debug, et les tests tournent avec `FLASK_DEBUG=0`.
#: Il a son test dédié, `test_endpoint_de_diagnostic_ne_devrait_pas_etre_public`.
GET_API_ROUTES = (
    "/api/status",
    "/api/jobs/recent",
    "/api/jobs/list",
)

#: Mot de passe accentué du risque #59. `hmac.compare_digest` lève un
#: `TypeError` dès qu'une des deux chaînes sort de l'ASCII.
MOT_DE_PASSE_ACCENTUE = "pässwörd"


def _basic(username: str, password: str) -> dict[str, str]:
    """En-tête `Authorization: Basic` (encodage UTF-8, comme un navigateur)."""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _fetch(client, path: str, **kwargs) -> tuple[int, bytes, dict]:
    """GET qui referme la réponse.

    `send_from_directory` renvoie un flux de fichier ouvert : sans `close()`,
    le ramasse-miettes émet un `ResourceWarning` que `filterwarnings = error`
    (pytest.ini) transforme en échec — un échec sans rapport avec le test.
    """
    response = client.get(path, **kwargs)
    try:
        return response.status_code, response.data, dict(response.headers)
    finally:
        response.close()


@pytest.fixture
def pas_de_thread_de_telechargement(monkeypatch):
    """Fait échouer tout test qui lancerait le thread de quick-download.

    `web/api.py:647` fait `import threading` DANS la vue : l'attribut est
    résolu à l'appel, donc patcher le module `threading` suffit. Ce thread
    lance un chromium (`quick_download.py`) — il est donc formellement
    interdit ici (LOI ABSOLUE n°3).
    """

    class ThreadInterdit:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "La vue a créé un thread de téléchargement : un navigateur "
                "réel allait être lancé (LOI ABSOLUE n°3)."
            )

    monkeypatch.setattr(threading, "Thread", ThreadInterdit)


@pytest.fixture
def ffmpeg_interdit(monkeypatch):
    """Enregistre les appels `subprocess.run` et interdit ffmpeg.

    Retourne la liste des commandes tentées : un test de traversée doit la
    trouver VIDE (la validation de chemin intervient avant ffmpeg).
    """
    appels: list[list[str]] = []

    def faux_run(cmd, *args, **kwargs):
        appels.append(list(cmd))
        raise AssertionError(f"ffmpeg réellement lancé : {cmd!r} (LOI ABSOLUE n°3)")

    monkeypatch.setattr(subprocess, "run", faux_run)
    return appels


#: Marqueur présent UNIQUEMENT dans les leurres posés hors de `DOWNLOAD_DIR`.
MARQUEUR_SECRET = b"SECRET-HORS-DOWNLOAD_DIR"


@pytest.fixture
def download_dir_des_routes(monkeypatch, tmp_path) -> Path:
    """Redirige `DOWNLOAD_DIR` de `routes.py` vers un sous-dossier de `tmp_path`.

    NOM : suffixé `_des_routes` pour ne pas être l'homonyme du `download_dir`
    de tests/test_stockage.py, qui redirige la MÊME constante mais dans
    `downloaders.py` et sans y déposer de fichier piège. Deux fixtures
    homonymes aux effets différents rendraient la suite illisible d'un module
    à l'autre.

    §7.1 AUDIT.md : `routes.py:15-23` importe les constantes PAR VALEUR, donc
    patcher `app.config` serait sans effet — on patche le nom dans le module
    consommateur. `serve_media_file` / `serve_media_thumbnail` lisent la
    globale à chaque appel, la substitution est donc bien prise en compte.

    ARBORESCENCE (tout est SOUS `tmp_path` — aucune écriture partagée entre
    tests, contrairement à une version antérieure qui déposait le leurre dans
    `tmp_path.parent`, répertoire commun à toute la session pytest) ::

        tmp_path/
          secret-2.mp4          <- cible des échappements à DEUX niveaux
          racine/
            secret.mp4          <- cible des échappements à UN niveau
            downloads/          <- DOWNLOAD_DIR
              sous-dossier/     <- point de départ de `sous-dossier/../../…`

    Les leurres portent une extension média (`.mp4`) pour que
    `serve_media_thumbnail` les considère comme des sources valides : sans
    cela, une traversée réussie sur `/media/thumb` s'arrêterait au
    `if not source_path.exists()` et le test resterait vert pour une mauvaise
    raison.
    """
    racine = tmp_path / "racine"
    dl = racine / "downloads"
    dl.mkdir(parents=True)
    (dl / "sous-dossier").mkdir()

    (racine / "secret.mp4").write_bytes(MARQUEUR_SECRET + b"-un-niveau")
    (tmp_path / "secret-2.mp4").write_bytes(MARQUEUR_SECRET + b"-deux-niveaux")

    monkeypatch.setattr(web_routes, "DOWNLOAD_DIR", dl)
    return dl


# ===========================================================================
# 0. ISOLATION — à vérifier AVANT d'écrire quoi que ce soit via l'API
# ===========================================================================


@pytest.mark.socle
def test_les_ecritures_de_settings_visent_le_data_dir_de_test():
    """`save_env` ne doit jamais pouvoir toucher le `data/.env` du propriétaire.

    `web/api.py:20` importe `SETTINGS_ENV` PAR VALEUR au chargement du module :
    ce test vérifie que la valeur figée à l'import est bien celle de la
    sandbox. C'est le préalable de tous les tests `POST /api/settings/env`
    plus bas (LOI ABSOLUE n°1).
    """
    settings_env = Path(web_api.SETTINGS_ENV).resolve()

    assert settings_env.parent == TEST_DATA_DIR
    assert settings_env == TEST_DATA_DIR / ".env"


@pytest.mark.socle
def test_les_medias_servis_viennent_du_data_dir_de_test():
    """`routes.DOWNLOAD_DIR` (importé par valeur, `routes.py:16`) reste en sandbox."""
    assert Path(web_routes.DOWNLOAD_DIR).resolve() == (TEST_DATA_DIR / "downloads").resolve()


# ===========================================================================
# 1. AUTHENTIFICATION — APP_PASSWORD VIDE (comportement actuel, §6.1)
# ===========================================================================
# Risque #6 / §6.1 : « Application entièrement publique par défaut ».
# Ces tests documentent l'état des lieux SANS le juger : `APP_PASSWORD` vide
# est le défaut de `config.py:30`, et `app.py:81-82` retourne avant tout
# contrôle. Ils resteront verts après les correctifs de vague 2 tant que la
# configuration de test laisse le mot de passe vide.


@pytest.mark.parametrize("path", PAGES)
def test_sans_mot_de_passe_les_pages_sont_ouvertes(client, path):
    """`APP_PASSWORD` vide (`app.py:81-82`) : aucune page ne demande d'auth."""
    status, body, headers = _fetch(client, path)

    assert status == 200
    assert "WWW-Authenticate" not in headers


@pytest.mark.parametrize("path", GET_API_ROUTES)
def test_sans_mot_de_passe_les_api_get_sont_ouvertes(client, path):
    """`APP_PASSWORD` vide : l'API répond sans le moindre contrôle (§6.1)."""
    status, body, headers = _fetch(client, path)

    assert status == 200
    assert "WWW-Authenticate" not in headers


def test_sans_mot_de_passe_les_post_sont_ouverts_aussi(
    client, pas_de_thread_de_telechargement
):
    """Le hook `_require_auth` ne distingue pas la méthode : POST ouvert aussi.

    Deux POST choisis pour leur INNOCUITÉ : une URL non reconnue est rejetée
    par `detect_platform` (`web/api.py:658-662`) avant tout thread, et un
    formulaire vide est rejeté par `web/api.py:513-514` avant toute écriture.
    Le code de statut prouve que la requête a atteint la vue — donc qu'aucun
    401 n'a été opposé.
    """
    reponse_dl = client.post("/api/quick-download", json={"url": "https://exemple.invalid/x"})
    assert reponse_dl.status_code == 400
    assert reponse_dl.is_json
    assert "non reconnue" in reponse_dl.get_json()["error"]

    reponse_env = client.post("/api/settings/env", data={})
    assert reponse_env.status_code == 400
    assert b"Aucun champ valide" in reponse_env.data


def test_sans_mot_de_passe_health_reste_public(client):
    """`/health` (app.py:68-70) répond 200 dans les deux configurations."""
    status, body, _ = _fetch(client, "/health")

    assert status == 200
    assert body == b"ok"


@pytest.mark.security
def test_endpoint_de_diagnostic_ne_devrait_pas_etre_public(client):
    """`/api/debug/volume` cartographie le volume : il ne doit pas être public.

    Corrigé (risque #6, AUDIT.md §6.1/§6.11) : la route rend 404 hors mode
    debug.  Avec `APP_PASSWORD` vide l'application entière est ouverte, cet
    endpoint doit donc disparaître en production plutôt que compter sur l'auth.
    """
    status, _, _ = _fetch(client, "/api/debug/volume")

    assert status in (401, 403, 404)


# ===========================================================================
# 2. AUTHENTIFICATION — APP_PASSWORD DÉFINI (ASCII)
# ===========================================================================


@pytest.mark.parametrize("path", PAGES + GET_API_ROUTES)
def test_avec_mot_de_passe_sans_en_tete_tout_est_401(auth_client, path):
    """`app.py:88-92` : 401 + `WWW-Authenticate`, sur pages comme sur API."""
    status, _, headers = _fetch(auth_client, path)

    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Basic ")


@pytest.mark.parametrize("path", PAGES + GET_API_ROUTES)
def test_avec_mot_de_passe_et_en_tete_valide_tout_repond_200(
    auth_client, auth_header, path
):
    status, _, _ = _fetch(auth_client, path, headers=auth_header)

    assert status == 200


@pytest.mark.parametrize(
    "en_tete",
    [
        pytest.param({}, id="aucun-en-tete"),
        pytest.param({"Authorization": "Bearer x"}, id="bearer"),
        pytest.param({"Authorization": "Garbage"}, id="illisible"),
        pytest.param(_basic("admin", "mauvais"), id="mauvais-mot-de-passe"),
        pytest.param(_basic("personne", TEST_APP_PASSWORD), id="mauvais-utilisateur"),
    ],
)
def test_avec_mot_de_passe_les_en_tetes_invalides_donnent_401(auth_client, en_tete):
    """Aucun en-tête hostile ASCII ne doit produire autre chose qu'un 401 propre."""
    status, _, _ = _fetch(auth_client, "/", headers=en_tete)

    assert status == 401


@pytest.mark.security
@pytest.mark.parametrize(
    "path,corps",
    [
        pytest.param("/api/quick-download", {"url": 1}, id="quick-download-corps-hostile"),
        pytest.param("/api/settings/env", {"APP_PASSWORD": "x"}, id="settings-env"),
    ],
)
def test_avec_mot_de_passe_les_post_sont_401_avant_toute_execution(
    auth_client, pas_de_thread_de_telechargement, path, corps
):
    """LACUNE COMBLÉE : les tests d'auth ne couvraient que des GET.

    Or les deux routes dangereuses de l'audit sont des POST : `{"url": 1}` sur
    `/api/quick-download` est la charge du risque #8 (§6.3), et
    `/api/settings/env` est celle du risque #52 (§6.13). Il faut prouver que
    `_require_auth` les intercepte AVANT `dispatch_request` — sinon un tiers
    anonyme atteindrait la console de debug malgré un mot de passe posé.

    Le 401 est la preuve que la vue n'a pas été exécutée : si elle l'avait
    été, `{"url": 1}` aurait levé l'`AttributeError` du risque #8 (500).
    """
    reponse = auth_client.post(path, json=corps)
    try:
        assert reponse.status_code == 401
        assert reponse.headers["WWW-Authenticate"].startswith("Basic ")
    finally:
        reponse.close()


def test_avec_mot_de_passe_un_post_authentifie_atteint_bien_la_vue(
    auth_client, auth_header, pas_de_thread_de_telechargement
):
    """Contre-épreuve du test précédent : sans elle, un 401 systématique
    (un `before_request` cassé) passerait pour une protection."""
    reponse = auth_client.post(
        "/api/quick-download",
        json={"url": "https://exemple.invalid/x"},
        headers=auth_header,
    )
    try:
        assert reponse.status_code == 400
        assert "non reconnue" in reponse.get_json()["error"]
    finally:
        reponse.close()


@pytest.mark.security
@pytest.mark.static_check
def test_la_comparaison_des_identifiants_reste_a_temps_constant():
    """Garde-fou permanent sur `hmac.compare_digest` (`app.py:75-76`).

    Aujourd'hui, remplacer `compare_digest` par un `==` naïf est détecté — mais
    seulement PAR ACCIDENT, via les xfail du risque #59 (le `==` supprime le
    `TypeError` sur non-ASCII, donc ils passent XPASS). Le jour où le lot 1.0a
    corrigera le risque #59, ces xfail seront retirés et plus rien ne
    garderait la comparaison à temps constant : une régression vers `==`
    rouvrirait une attaque temporelle sur le mot de passe, en silence.

    Assertion statique : `_check_auth` doit continuer d'utiliser `compare_digest`.
    """
    source = Path(web_app_module.__file__).read_text(encoding="utf-8")

    assert "compare_digest" in source, (
        "app/web/app.py n'utilise plus hmac.compare_digest : la comparaison "
        "des identifiants n'est plus à temps constant"
    )
    assert source.count("compare_digest") >= 2, (
        "identifiant ET mot de passe doivent tous deux être comparés à temps "
        "constant"
    )


def test_avec_mot_de_passe_une_url_inexistante_repond_401_et_non_404(auth_client):
    """Caractérisation : `before_request` s'exécute avant `dispatch_request`.

    Une URL inconnue reçoit donc 401 (et non 404) tant qu'on n'est pas
    authentifié — l'application ne divulgue pas sa table de routes. Avec un
    en-tête valide, le 404 réapparaît.
    """
    status_anonyme, _, _ = _fetch(auth_client, "/route-qui-nexiste-pas")
    assert status_anonyme == 401

    status_authentifie, _, _ = _fetch(
        auth_client, "/route-qui-nexiste-pas", headers=_basic(TEST_APP_USERNAME, TEST_APP_PASSWORD)
    )
    assert status_authentifie == 404


@pytest.mark.parametrize(
    "en_tete",
    [
        pytest.param({}, id="aucun-en-tete"),
        pytest.param({"Authorization": "Bearer x"}, id="bearer"),
        pytest.param(_basic("admin", "mauvais"), id="mauvais-mot-de-passe"),
    ],
)
def test_health_reste_public_avec_authentification_activee(auth_client, en_tete):
    """`app.py:84-85` exempte `/health` AVANT `_check_auth` (`:86`).

    Non-régression : le healthcheck Railway doit continuer de passer quel que
    soit l'en-tête envoyé.
    """
    status, body, _ = _fetch(auth_client, "/health", headers=en_tete)

    assert status == 200
    assert body == b"ok"


# ===========================================================================
# 3. RISQUE #59 — mot de passe non-ASCII (§6.3, second déclencheur)
# ===========================================================================
# `hmac.compare_digest(auth.password or "", APP_PASSWORD)` (`app.py:76`)
# s'exécute même quand la comparaison d'identifiant de `:75` a déjà échoué :
# avec un mot de passe accentué, TOUT en-tête `Authorization` lève un
# `TypeError` non capturé. En production (`FLASK_DEBUG=1` par défaut) ce n'est
# pas une page 500 mais la console de debug Werkzeug, en root.


@pytest.fixture
def client_mot_de_passe_accentue(make_flask_app):
    """Client Flask avec `APP_PASSWORD="pässwörd"` (risque #59)."""
    return make_flask_app(
        APP_USERNAME=TEST_APP_USERNAME, APP_PASSWORD=MOT_DE_PASSE_ACCENTUE
    ).test_client()


@pytest.mark.security
@pytest.mark.parametrize(
    "en_tete",
    [
        pytest.param({"Authorization": "Bearer x"}, id="bearer"),
        pytest.param({"Authorization": "Garbage"}, id="illisible"),
        pytest.param(_basic("admin", "mauvais"), id="basic-mauvais-mot-de-passe"),
        pytest.param(_basic("personne", "x"), id="basic-mauvais-utilisateur"),
    ],
)
def test_mot_de_passe_accentue_en_tete_hostile_doit_donner_401(
    client_mot_de_passe_accentue, en_tete
):
    """Un tiers anonyme ne doit obtenir qu'un 401, jamais une exception.

    Non-régression du lot 1.0a (risque #59) : `_check_auth` compare des
    `bytes` (`app.py:75-84`), plus de `TypeError` sur du non-ASCII.
    """
    status, _, _ = _fetch(client_mot_de_passe_accentue, "/", headers=en_tete)

    assert status == 401


@pytest.mark.security
def test_mot_de_passe_accentue_les_bons_identifiants_doivent_ouvrir_la_page(
    client_mot_de_passe_accentue,
):
    """Aggravant sémantique : le mot de passe accentué ferme l'app à son auteur."""
    status, _, _ = _fetch(
        client_mot_de_passe_accentue,
        "/",
        headers=_basic(TEST_APP_USERNAME, MOT_DE_PASSE_ACCENTUE),
    )

    assert status == 200


def test_mot_de_passe_accentue_sans_en_tete_le_401_fonctionne(
    client_mot_de_passe_accentue,
):
    """Non-régression : `auth is None` provoque un retour anticipé (`app.py:73-74`).

    C'est le seul chemin qui n'atteint pas la ligne fautive `:76`. Il doit le
    rester après le correctif du lot 1.0a.
    """
    status, _, headers = _fetch(client_mot_de_passe_accentue, "/")

    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Basic ")


@pytest.mark.parametrize(
    "en_tete",
    [
        pytest.param({"Authorization": "Bearer x"}, id="bearer"),
        pytest.param({"Authorization": "Garbage"}, id="illisible"),
    ],
)
def test_mot_de_passe_accentue_health_repond_200(client_mot_de_passe_accentue, en_tete):
    """`/health` échappe au `TypeError` : exempté en `app.py:84-85`.

    C'est l'unique contre-exemple au « toute requête » du risque #59, et la
    raison pour laquelle la panne est invisible du healthcheck Railway.
    """
    status, body, _ = _fetch(client_mot_de_passe_accentue, "/health", headers=en_tete)

    assert status == 200
    assert body == b"ok"


# ===========================================================================
# 4. RISQUE #8 — corps JSON hostile sur /api/quick-download (§6.3, test T20)
# ===========================================================================
# `data = request.get_json(silent=True) or {}` puis `(data.get("url") or "").strip()`
# (`web/api.py:649-650`) : `get_json` rend n'importe quel type JSON et `or {}`
# ne remplace que les valeurs falsy. La route n'a AUCUN `except` et il
# n'existe AUCUN `errorhandler` dans `app/`.


@pytest.mark.security
@pytest.mark.parametrize(
    "corps",
    [
        pytest.param({"url": 1}, id="url-entier"),
        pytest.param({"url": {"a": 1}}, id="url-objet"),
        pytest.param({"url": ["https://x"]}, id="url-tableau"),
        pytest.param([1, 2], id="corps-tableau"),
        pytest.param("abc", id="corps-chaine"),
        pytest.param(5, id="corps-entier"),
        pytest.param(True, id="corps-booleen"),
    ],
)
def test_quick_download_corps_hostile_doit_donner_400_json(
    client, pas_de_thread_de_telechargement, corps
):
    """Un corps JSON mal typé doit produire un 400 JSON, jamais un traceback.

    C'est le test qui garde la fermeture de la faille la plus grave de
    l'audit (lot 1.0, risque #8) : `quick_download_url` valide désormais le
    TYPE du corps avant de le manipuler (`web/api.py:669-681`).
    """
    reponse = client.post("/api/quick-download", json=corps)
    try:
        assert reponse.status_code == 400, (
            f"corps {corps!r} → {reponse.status_code} "
            f"({reponse.headers.get('Content-Type')})"
        )
        assert reponse.is_json, "la réponse doit être du JSON, pas du HTML Werkzeug"
        assert "error" in reponse.get_json()
    finally:
        reponse.close()


@pytest.mark.parametrize(
    "corps",
    [
        pytest.param(None, id="null"),
        pytest.param({}, id="objet-vide"),
        pytest.param({"url": ""}, id="url-vide"),
        pytest.param({"url": "   "}, id="url-espaces"),
        pytest.param({"autre": "champ"}, id="cle-absente"),
    ],
)
def test_quick_download_corps_falsy_donne_deja_un_400_json(
    client, pas_de_thread_de_telechargement, corps
):
    """Non-régression : les corps FALSY sont déjà correctement rejetés.

    `or {}` les neutralise (`web/api.py:649`) et `if not url` renvoie 400
    (`:652-653`). Ce sont les seuls cas que le code gère aujourd'hui — ils
    doivent le rester après le lot 1.0.
    """
    reponse = client.post("/api/quick-download", json=corps)
    try:
        assert reponse.status_code == 400
        assert reponse.is_json
        assert reponse.get_json()["error"] == "URL requise"
    finally:
        reponse.close()


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://exemple.invalid/post/1", id="domaine-inconnu"),
        pytest.param("pas-une-url", id="pas-une-url"),
        pytest.param("ftp://instagram.invalid/x", id="schema-exotique"),
    ],
)
def test_quick_download_url_non_reconnue_donne_400_sans_lancer_de_thread(
    client, pas_de_thread_de_telechargement, url
):
    """`detect_platform` (`web/api.py:658-662`) filtre AVANT le thread.

    La fixture `pas_de_thread_de_telechargement` fait de ce test une preuve :
    si un jour la route lançait un navigateur sur une URL non reconnue, il
    deviendrait rouge.
    """
    reponse = client.post("/api/quick-download", json={"url": url})
    try:
        assert reponse.status_code == 400
        assert reponse.is_json
        assert "non reconnue" in reponse.get_json()["error"]
    finally:
        reponse.close()


# ===========================================================================
# 5. ABSENCE TOTALE D'ERRORHANDLER (risque #8, §6.3)
# ===========================================================================
# 0 occurrence d'`errorhandler` dans `app/` : une exception dans une vue ne
# produit aucune réponse structurée. Sous `test_client` on obtient du HTML
# Werkzeug ; en production (`FLASK_DEBUG=1`) on obtient la console de debug.


@pytest.fixture
def app_avec_vue_qui_leve(flask_app):
    """Ajoute une vue `/api/__boom__` qui lève, SANS toucher à `app/`.

    L'ajout se fait sur l'instance Flask produite par la factory : le code
    applicatif n'est pas modifié (LOI ABSOLUE n°2), on se contente de mettre
    la factory en situation.
    """

    @flask_app.route("/api/__boom__")
    def _boom():  # pragma: no cover - lève toujours
        raise RuntimeError("panne simulée dans une vue")

    return flask_app


@pytest.mark.security
def test_une_exception_dans_une_vue_api_devrait_produire_du_json(
    app_avec_vue_qui_leve,
):
    """Une panne serveur sur `/api/*` doit rester une réponse JSON 500."""
    reponse = app_avec_vue_qui_leve.test_client().get("/api/__boom__")
    try:
        assert reponse.status_code == 500
        assert reponse.is_json, (
            "réponse non-JSON : "
            f"{reponse.headers.get('Content-Type')} — {reponse.data[:80]!r}"
        )
    finally:
        reponse.close()


@pytest.mark.security
@pytest.mark.static_check
def test_le_code_applicatif_devrait_declarer_un_errorhandler():
    """Contrepartie statique du test précédent, indépendante de Flask."""
    racine = Path(web_api.__file__).resolve().parent.parent
    occurrences = [
        fichier.name
        for fichier in racine.rglob("*.py")
        if "errorhandler" in fichier.read_text(encoding="utf-8")
    ]

    assert occurrences, "aucun errorhandler déclaré dans app/"


def test_une_exception_dans_une_vue_ne_divulgue_pas_la_trace(app_avec_vue_qui_leve):
    """Non-régression : la trace Python ne doit jamais atteindre le client.

    Vrai aujourd'hui uniquement parce que `PROPAGATE_EXCEPTIONS=False` est
    posé par le socle ; en production `FLASK_DEBUG=1` (`config.py:24`) rend au
    contraire la console interactive — c'est exactement le risque #8. Ce test
    verrouille la propriété côté réponse pour qu'elle survive au lot 3.2.
    """
    reponse = app_avec_vue_qui_leve.test_client().get("/api/__boom__")
    try:
        assert reponse.status_code == 500
        assert "panne simulée" not in reponse.get_data(as_text=True)
        assert b"Traceback" not in reponse.data
        assert b"console" not in reponse.data.lower()
    finally:
        reponse.close()


# ===========================================================================
# 6. RISQUE #52 — /api/settings/env accepte les identifiants de l'app (§6.13)
# ===========================================================================
# Tous les tests de cette section écrivent dans `TEST_DATA_DIR/.env` via la
# fixture `settings_env_file` du socle, qui supprime le fichier avant ET après
# le test. Le vrai `data/.env` n'est jamais approché (cf. §0 plus haut).


@pytest.mark.security
@pytest.mark.static_check
@pytest.mark.parametrize(
    "cle", ["APP_USERNAME", "APP_PASSWORD", "FLASK_SECRET_KEY"]
)
def test_les_identifiants_ne_devraient_pas_etre_whitelistes(cle):
    """Assertion statique sur `ALLOWED_ENV_KEYS` (volet 1 du test T22)."""
    assert cle not in web_api.ALLOWED_ENV_KEYS


@pytest.mark.security
@pytest.mark.parametrize(
    "cle,valeur",
    [
        ("APP_USERNAME", "intrus"),
        ("APP_PASSWORD", "motdepasse-de-lattaquant"),
        ("FLASK_SECRET_KEY", "cle-de-lattaquant"),
    ],
)
def test_settings_env_doit_rejeter_les_identifiants_de_lapplication(
    client, settings_env_file, cle, valeur
):
    """Volet « de bout en bout » du test T22 : rejet ET fichier intact."""
    reponse = client.post("/api/settings/env", data={cle: valeur})
    try:
        contenu = settings_env_file.read_text(encoding="utf-8") if settings_env_file.exists() else ""

        assert reponse.status_code == 400, f"{cle} accepté ({reponse.status_code})"
        assert cle not in contenu, f"{cle} écrit dans {settings_env_file}"
    finally:
        reponse.close()


#: Les 26 clés LÉGITIMES de `ALLOWED_ENV_KEYS` (`web/api.py:28-43`), c'est-à-dire
#: la whitelist PRIVÉE des trois identifiants d'application que le lot 1.0b doit
#: en retirer (risque #52). Cette liste doit donc rester exacte APRÈS le lot 1.0b.
#:
#: `PORT` en est sorti au lot 2.1 : le `.env` du volume est chargé avec
#: `override=True`, donc y écrire PORT ÉCRASE la variable injectée par Railway
#: et le conteneur se met à écouter sur le mauvais port — une sauvegarde depuis
#: l'écran Réglages suffisait à tuer le déploiement. Le port appartient à la
#: plateforme d'hébergement, pas à l'interface.
CLES_ENV_LEGITIMES = frozenset({
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "GOOGLE_REFRESH_TOKEN", "GDRIVE_ROOT_FOLDER_NAME", "STORAGE_MODE",
    "FB_APP_ID", "FB_APP_SECRET", "IG_ACCESS_TOKEN", "IG_USER_ID",
    "DEFAULT_SCRAPE_INTERVAL_MINUTES", "BROWSER_POOL_SIZE", "SCROLL_PAUSE_MS",
    "MAX_SCROLLS", "DELAY_BETWEEN_PROFILES_MS", "BACKFILL_MAX_SCROLLS",
    "DAILY_MAX_SCROLLS", "DAILY_SCRAPE_INTERVAL_MINUTES", "MAX_CONCURRENT_SCRAPES",
    "PROXY_URL", "PROXY_INSTAGRAM", "PROXY_TIKTOK", "PROXY_TWITTER", "PROXY_REDDIT",
    "LOG_LEVEL", "EDITOR_MAX_FILE_SIZE_MB",
})


@pytest.mark.static_check
def test_la_whitelist_env_conserve_toutes_les_cles_legitimes():
    """LACUNE COMBLÉE : rien ne gardait le CONTENU utile d'`ALLOWED_ENV_KEYS`.

    Vérifié par mutation : retirer `PROXY_URL` de la whitelist ne faisait
    virer AUCUN test de TOUTE la suite (432 tests). Seul `PORT` était couvert,
    et par accident — il figure dans la paramétrisation du risque #41.

    Conséquence concrète : la refonte UI de la vague 2 peut supprimer une clé
    par inadvertance ; le champ correspondant du formulaire Settings serait
    alors silencieusement ignoré (`web/api.py:505-508` se contente d'un
    warning) et l'utilisateur croirait avoir sauvegardé.
    """
    manquantes = CLES_ENV_LEGITIMES - set(web_api.ALLOWED_ENV_KEYS)

    assert not manquantes, (
        f"clés retirées d'ALLOWED_ENV_KEYS : {sorted(manquantes)} — les champs "
        f"correspondants du formulaire Settings sont désormais ignorés"
    )


def test_une_cle_de_proxy_est_reellement_persistee(client, settings_env_file):
    """Volet de bout en bout du test précédent, sur la clé qui a survécu à la
    mutation `PROXY_URL` (aucune écriture réseau : on écrit juste le `.env`)."""
    reponse = client.post("/api/settings/env", data={"PROXY_URL": "http://127.0.0.1:9"})
    try:
        assert reponse.status_code == 200
        assert "PROXY_URL=http://127.0.0.1:9" in settings_env_file.read_text(
            encoding="utf-8"
        )
    finally:
        reponse.close()


def test_settings_env_rejette_deja_data_dir(client, settings_env_file):
    """Non-régression : `DATA_DIR` n'est PAS dans la whitelist (§5, correctif noté).

    Le champ existe pourtant dans `settings.html:401` : il est silencieusement
    ignoré (`web/api.py:505-508`). C'est la garantie qui empêche l'UI de
    déplacer le volume de données — elle ne doit pas se perdre.
    """
    assert "DATA_DIR" not in web_api.ALLOWED_ENV_KEYS

    reponse = client.post("/api/settings/env", data={"DATA_DIR": "/tmp/pirate"})
    try:
        assert reponse.status_code == 400
        assert b"Aucun champ valide" in reponse.data
        assert not settings_env_file.exists()
    finally:
        reponse.close()


def test_settings_env_ecrit_une_cle_legitime_dans_le_env_de_test(
    client, settings_env_file
):
    """Caractérisation du chemin nominal — et preuve de l'isolation.

    Une clé légitime est bien persistée, et elle l'est dans le `.env` de la
    sandbox : si l'isolation de `DATA_DIR` se rompait, ce test échouerait au
    lieu d'écrire discrètement dans `data/.env` (LOI ABSOLUE n°1).
    """
    reponse = client.post("/api/settings/env", data={"LOG_LEVEL": "DEBUG"})
    try:
        assert reponse.status_code == 200
        assert settings_env_file.exists()
        assert "LOG_LEVEL=DEBUG" in settings_env_file.read_text(encoding="utf-8")
    finally:
        reponse.close()


# ===========================================================================
# 7. RISQUE #41 — valeurs non numériques acceptées par /api/settings/env
# ===========================================================================
# `config.py:23,45-52,56,68` caste 11 constantes par `int()` À L'IMPORT.
# Une valeur non entière persistée sur le volume empêche l'application de
# redémarrer, et l'UI Settings — seul moyen de corriger — devient inaccessible.


@pytest.mark.security
@pytest.mark.parametrize(
    "cle,valeur",
    [
        ("PORT", "abc"),
        ("PORT", ""),
        ("BROWSER_POOL_SIZE", "deux"),
        ("SCROLL_PAUSE_MS", "3000ms"),
        ("EDITOR_MAX_FILE_SIZE_MB", "100.5"),
        ("DEFAULT_SCRAPE_INTERVAL_MINUTES", "-"),
    ],
)
def test_settings_env_doit_rejeter_une_valeur_non_numerique(
    client, settings_env_file, cle, valeur
):
    """Une valeur non castable par `int()` doit être refusée en 400.

    Et surtout : elle ne doit pas atteindre le fichier persistant (lot 1.3b,
    risque #41). `NUMERIC_ENV_KEYS` / `_is_int` (`web/api.py:46-63`) refusent
    l'écriture ENTIÈRE, et `config._env_int` retombe sur le défaut si une
    valeur toxique est déjà sur le volume.
    """
    reponse = client.post("/api/settings/env", data={cle: valeur})
    try:
        contenu = settings_env_file.read_text(encoding="utf-8") if settings_env_file.exists() else ""

        assert reponse.status_code == 400, f"{cle}={valeur!r} accepté"
        assert f"{cle}=" not in contenu
    finally:
        reponse.close()


def test_settings_env_accepte_une_valeur_numerique_valide(client, settings_env_file):
    """Non-régression : le lot 1.3b ne doit pas casser le cas nominal."""
    reponse = client.post("/api/settings/env", data={"MAX_SCROLLS": "42"})
    try:
        assert reponse.status_code == 200
        assert "MAX_SCROLLS=42" in settings_env_file.read_text(encoding="utf-8")
    finally:
        reponse.close()


def test_la_valeur_ecrite_est_bien_relue_par_les_deux_lecteurs(
    client, settings_env_file
):
    """`_read_env_file` de `web/api.py` relit ce que `save_env` a écrit.

    (Celui de `routes.py:33-51` ne lit QUE `BASE_DIR/.env` — c'est le
    risque #7 / §4.6, hors périmètre de ce module.)
    """
    reponse = client.post("/api/settings/env", data={"GDRIVE_ROOT_FOLDER_NAME": "DOSSIER"})
    reponse.close()

    assert web_api._read_env_file()["GDRIVE_ROOT_FOLDER_NAME"] == "DOSSIER"


# ===========================================================================
# 8. /media/file et /media/thumb — NON-RÉGRESSION de la traversée de chemin
# ===========================================================================
# Conclusion vérifiée de l'audit (§5, « corrections d'une revue précédente ») :
# la traversée est IMPOSSIBLE. `send_from_directory` applique `safe_join`, et
# `serve_media_thumbnail` appelle `safe_join` explicitement (`routes.py:267`).
# Ces tests sont VERTS aujourd'hui et doivent le rester : ils protègent une
# garantie que la refonte pourrait perdre par inadvertance.

#: ÉCHAPPEMENTS RÉELS — charges dont la cible EXISTE hors de `DOWNLOAD_DIR`.
#:
#: C'est la seule catégorie qui garde quelque chose. Une charge dont la cible
#: n'existe pas (`../../etc/passwd` depuis un `tmp_path` profond de 8 niveaux,
#: par exemple) produit un 404 même sur une implémentation NAÏVE : le test
#: passe alors sans rien prouver. Le test-témoin
#: `test_les_charges_de_traversee_visent_des_cibles_reelles` ci-dessous
#: interdit qu'une charge vide se glisse à nouveau dans cette liste.
ECHAPPEMENTS_REELS = (
    "../secret.mp4",
    "../../secret-2.mp4",
    "sous-dossier/../../secret.mp4",
    "..%2Fsecret.mp4",
    "%2e%2e%2fsecret.mp4",
    "..%2F..%2Fsecret-2.mp4",
)

#: CHARGES NEUTRALISÉES EN AMONT — elles n'atteignent jamais la vue sous une
#: forme échappante, quelle que soit l'implémentation de la vue :
#:   * `/etc/passwd` et `%2Fetc%2Fpasswd` produisent un `//` que Flask
#:     redirige (308) vers `/media/file/etc/passwd` — le `/` de tête est
#:     PERDU, la vue reçoit un chemin relatif inoffensif ;
#:   * `..\..\secret.mp4` : sous POSIX l'antislash est un caractère de nom de
#:     fichier ordinaire, pas un séparateur ;
#:   * `....//` ne vise qu'un filtre qui retirerait `../` une seule fois — il
#:     n'échappe rien face à une concaténation.
#: On les garde en non-régression (une modification du routage ne doit pas les
#: rendre échappantes) mais SANS prétendre qu'elles testent la vue.
CHARGES_NEUTRALISEES_EN_AMONT = (
    "/etc/passwd",
    "%2Fetc%2Fpasswd",
    "..%5C..%5Csecret.mp4",
    "....//secret.mp4",
)


@pytest.mark.security
def test_les_charges_de_traversee_visent_des_cibles_reelles(download_dir_des_routes):
    """TEST-TÉMOIN : sans lui, les tests de traversée peuvent devenir décoratifs.

    Pour chaque charge d'`ECHAPPEMENTS_REELS`, on vérifie que la
    concaténation naïve `DOWNLOAD_DIR + charge` — exactement ce que ferait une
    implémentation sans `safe_join` — désigne un fichier qui EXISTE et qui est
    HORS de `DOWNLOAD_DIR`. C'est la condition nécessaire pour que les deux
    tests suivants puissent virer au rouge le jour où quelqu'un remplace
    `send_from_directory` / `safe_join` par un `open()` concaténé.

    Défaut historique corrigé ici : 9 des 10 charges de la version précédente
    pointaient vers des fichiers inexistants (`/etc/passwd` depuis un
    `tmp_path` profond, `sous-dossier/` jamais créé…). Une traversée réelle
    introduite dans `serve_media_file` n'était détectée que par UNE charge sur
    dix.
    """
    racine = os.path.realpath(str(download_dir_des_routes))

    for charge in ECHAPPEMENTS_REELS:
        # `unquote` reproduit le décodage que Werkzeug applique avant routage.
        cible = os.path.join(racine, unquote(charge))

        assert os.path.exists(cible), (
            f"charge {charge!r} : la cible {cible!r} n'existe pas — le test de "
            f"traversée correspondant serait VIDE (404 même sans validation)"
        )
        assert not os.path.realpath(cible).startswith(racine + os.sep), (
            f"charge {charge!r} : la cible reste DANS DOWNLOAD_DIR, ce n'est "
            f"pas une traversée"
        )


@pytest.mark.security
@pytest.mark.parametrize("chemin", ECHAPPEMENTS_REELS)
def test_media_file_refuse_toute_traversee(client, download_dir_des_routes, chemin):
    """`/media/file/<path>` ne sert jamais un fichier hors de `DOWNLOAD_DIR`.

    `send_from_directory` applique `safe_join` (§2.3/§5 AUDIT.md). Vérifié par
    mutation : remplacer cet appel par `open(os.path.join(DOWNLOAD_DIR,
    filename))` rend ce test rouge sur les 6 charges.
    """
    reponse = client.get(f"/media/file/{chemin}", follow_redirects=True)
    try:
        assert reponse.status_code in (400, 404), (
            f"{chemin!r} → {reponse.status_code} : un fichier hors "
            f"DOWNLOAD_DIR a été servi"
        )
        assert MARQUEUR_SECRET not in reponse.data
    finally:
        reponse.close()


@pytest.mark.security
@pytest.mark.parametrize("chemin", ECHAPPEMENTS_REELS)
def test_media_thumb_refuse_toute_traversee(
    client, download_dir_des_routes, ffmpeg_interdit, chemin
):
    """`/media/thumb/<path>` : `safe_join` (`routes.py:267`) rejette avant ffmpeg.

    Les leurres portent l'extension `.mp4` : si `safe_join` disparaissait,
    `source_path.exists()` serait vrai et ffmpeg serait lancé sur un fichier
    hors périmètre — c'est `ffmpeg_interdit` qui l'attrape.
    """
    reponse = client.get(f"/media/thumb/{chemin}", follow_redirects=True)
    try:
        assert reponse.status_code in (400, 404)
        assert MARQUEUR_SECRET not in reponse.data
        assert ffmpeg_interdit == [], "ffmpeg lancé sur un chemin de traversée"
    finally:
        reponse.close()


@pytest.mark.security
@pytest.mark.parametrize("chemin", CHARGES_NEUTRALISEES_EN_AMONT)
def test_les_charges_neutralisees_en_amont_ne_fuient_rien(
    client, download_dir_des_routes, ffmpeg_interdit, chemin
):
    """Non-régression du ROUTAGE (pas de la vue) — cf. commentaire de la liste.

    Ces charges ne peuvent pas devenir rouges sur un défaut de la vue : elles
    surveillent que Werkzeug continue de les désamorcer avant le routage.
    """
    for prefixe in ("/media/file", "/media/thumb"):
        reponse = client.get(f"{prefixe}/{chemin}", follow_redirects=True)
        try:
            assert reponse.status_code in (400, 404)
            assert MARQUEUR_SECRET not in reponse.data
            assert b"root:" not in reponse.data
        finally:
            reponse.close()

    assert ffmpeg_interdit == []


def test_media_file_sert_bien_un_fichier_legitime(client, download_dir_des_routes):
    """Contrôle positif : sans lui, les tests de traversée passeraient à vide."""
    (download_dir_des_routes / "media-legitime.jpg").write_bytes(b"\xff\xd8\xffOK")

    reponse = client.get("/media/file/media-legitime.jpg")
    try:
        assert reponse.status_code == 200
        assert reponse.data == b"\xff\xd8\xffOK"
        assert reponse.headers["Cache-Control"] == "public, max-age=604800, immutable"
    finally:
        reponse.close()


def test_media_file_dans_un_sous_dossier_reste_servi(client, download_dir_des_routes):
    """Contrôle positif n°2 : `<path:filename>` autorise bien les sous-dossiers."""
    # `sous-dossier` est déjà créé par la fixture (départ de `sous-dossier/../../`).
    (download_dir_des_routes / "sous-dossier" / "x.jpg").write_bytes(b"\xff\xd8\xffSUB")

    reponse = client.get("/media/file/sous-dossier/x.jpg")
    try:
        assert reponse.status_code == 200
        assert reponse.data == b"\xff\xd8\xffSUB"
    finally:
        reponse.close()


def test_media_file_dl_1_force_le_telechargement(client, download_dir_des_routes):
    """Caractérisation de `routes.py:237` : `?dl=1` → `Content-Disposition`.

    Sans `?dl=1` le fichier est servi INLINE — c'est le vecteur du §6.6 (XSS
    stockée via l'extension), documenté ici tel quel.
    """
    (download_dir_des_routes / "media-legitime.jpg").write_bytes(b"\xff\xd8\xffOK")

    inline = client.get("/media/file/media-legitime.jpg")
    telechargement = client.get("/media/file/media-legitime.jpg?dl=1")
    try:
        assert "attachment" not in inline.headers.get("Content-Disposition", "")
        assert telechargement.headers["Content-Disposition"].startswith("attachment")
    finally:
        inline.close()
        telechargement.close()


def test_media_file_pose_nosniff(client, download_dir_des_routes):
    """Lot 2.5 (§6.6 / T10) : le navigateur ne devine JAMAIS le type servi.

    Sans `nosniff`, un fichier au type générique dont le contenu ressemble à du
    HTML est interprété comme du HTML sur l'origine de l'application.
    """
    (download_dir_des_routes / "media-legitime.jpg").write_bytes(b"\xff\xd8\xffOK")

    reponse = client.get("/media/file/media-legitime.jpg")
    try:
        assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        reponse.close()


@pytest.mark.security
@pytest.mark.parametrize("nom", ["piege.html", "piege.svg", "piege.bin"])
def test_media_file_ne_sert_jamais_inline_un_fichier_non_media(
    client, download_dir_des_routes, nom
):
    """Lot 2.5 : les fichiers écrits AVANT la liste blanche restent sur le volume.

    `_guess_extension` ne peut plus créer de `.html` ni de `.svg`, mais ceux
    déjà téléchargés étaient encore servis INLINE, same-origin : XSS stockée.
    Ils sortent désormais en pièce jointe et en `application/octet-stream`.
    """
    (download_dir_des_routes / nom).write_bytes(
        b"<html><script>alert(document.domain)</script></html>"
    )

    reponse = client.get(f"/media/file/{nom}")
    try:
        assert reponse.status_code == 200
        assert reponse.headers["Content-Disposition"].startswith("attachment")
        assert reponse.headers["Content-Type"].startswith("application/octet-stream")
        assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        reponse.close()


def test_media_file_sert_toujours_les_vrais_medias_inline(
    client, download_dir_des_routes
):
    """Contrôle négatif : la mesure ci-dessus ne casse ni le viewer ni le lecteur.

    Une vidéo servie en pièce jointe ne se lirait plus dans la lightbox.
    """
    (download_dir_des_routes / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    reponse = client.get("/media/file/clip.mp4")
    try:
        assert reponse.status_code == 200
        assert "attachment" not in reponse.headers.get("Content-Disposition", "")
        assert reponse.headers["Content-Type"].startswith("video/mp4")
    finally:
        reponse.close()


def test_media_thumb_sert_la_vignette_en_cache_sans_ffmpeg(
    client, download_dir_des_routes, ffmpeg_interdit
):
    """Caractérisation de `routes.py:261-264` : le cache court-circuite ffmpeg.

    (La branche cache précède toute validation de la source — c'est la cause C
    du §4.13, hors périmètre de ce module.)
    """
    (download_dir_des_routes / ".thumbs").mkdir()
    (download_dir_des_routes / ".thumbs" / "video.jpg").write_bytes(b"\xff\xd8\xffTHUMB")

    reponse = client.get("/media/thumb/video.mp4")
    try:
        assert reponse.status_code == 200
        assert reponse.data == b"\xff\xd8\xffTHUMB"
        assert reponse.headers["Content-Type"] == "image/jpeg"
        assert ffmpeg_interdit == []
    finally:
        reponse.close()


def test_media_thumb_source_absente_donne_404_sans_ffmpeg(
    client, download_dir_des_routes, ffmpeg_interdit
):
    """`routes.py:270-271` : pas de source, pas d'appel ffmpeg."""
    reponse = client.get("/media/thumb/inconnu.mp4")
    try:
        assert reponse.status_code == 404
        assert ffmpeg_interdit == []
    finally:
        reponse.close()


# ===========================================================================
# 9. SMOKE TEST DES 8 PAGES
# ===========================================================================


@pytest.mark.parametrize("path", PAGES)
def test_les_huit_pages_rendent_du_html(client, path):
    """Chaque page répond 200 et rend un document HTML complet.

    Filet de base de la vague 0 : une erreur de template, un filtre Jinja
    manquant (`app.py:97-99`) ou une requête ORM cassée (risque #1) rend ce
    test rouge immédiatement.
    """
    status, body, headers = _fetch(client, path)

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    texte = body.decode("utf-8")
    assert re.search(r"<!doctype html", texte, re.IGNORECASE)
    assert "</html>" in texte.lower()


def test_le_dashboard_affiche_les_profils_de_la_base(client, make_profile):
    """La page d'accueil lit bien la base éphémère (`routes.py:57-113`)."""
    make_profile(username="profil_de_test", platform="instagram")

    status, body, _ = _fetch(client, "/")

    assert status == 200
    assert b"profil_de_test" in body


def test_la_page_profils_affiche_les_profils(client, make_profile):
    """`routes.py:119-140`, second consommateur de `Profile` (risque #1)."""
    make_profile(username="autre_profil", platform="tiktok")

    status, body, _ = _fetch(client, "/profiles")

    assert status == 200
    assert b"autre_profil" in body


def test_les_pages_sont_toutes_servies_par_le_meme_blueprint():
    """Assertion statique : les 8 pages listées ici sont bien les 8 routes réelles.

    Si quelqu'un ajoute une page sans l'ajouter au smoke test, ce test le dit.
    Les routes techniques (`/health`, `/favicon.ico`, `/media/*`,
    `/auth/google*`) sont exclues : elles ne rendent pas d'écran et
    n'ont donc rien à faire dans le smoke test des pages.
    """
    from app.web.app import create_app

    regles = {
        str(regle)
        for regle in create_app().url_map.iter_rules()
        if str(regle).count("/") == 1
        and "<" not in str(regle)
        and str(regle) not in ("/health", "/static", "/favicon.ico")
    }

    assert regles == set(PAGES)
