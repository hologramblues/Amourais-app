"""
Tests du LOT SÉCURITÉ (fin de vague 2, AUDIT.md §9) — périmètre Python.

Un fichier séparé de `test_web.py` : la vague 2 fait travailler plusieurs
agents en parallèle et ce module ne recouvre aucun test existant.

Couverture, lot par lot :

* **2.1**  — `save_env` refuse `\\n`, `\\r`, `\\0` dans une valeur (injection de
  variables supplémentaires dans le `.env`), et `PORT` quitte
  `ALLOWED_ENV_KEYS` (le `.env` du volume est chargé avec `override=True` :
  y écrire PORT écrase la variable de Railway et tue le déploiement).
* **2.1b** — écriture ATOMIQUE du `.env` (fichier temporaire + `os.replace`) :
  `config.get_proxy_for_platform` relit ce fichier à chaud depuis d'autres
  threads et lisait jusqu'ici un fichier à moitié écrit (§4.15).
* **2.3**  — rejet des requêtes inter-sites (il n'existe AUCUNE protection CSRF).
* **2.3b** — cookies de session : sauvegarde horodatée de l'ancien fichier et
  refus 400 si le cookie d'authentification manque (risque #61, §6.14).
* **2.4**  — SSRF : l'URL du quick-download est chargée par un navigateur
  headless côté serveur ; schémas et adresses internes filtrés, et nombre de
  téléchargements simultanés borné.
* **2.4b** — plus aucun `str(exc)` renvoyé au client (chemins absolus du volume,
  instructions SQL complètes).

Aucun test ici ne sort sur le réseau ni ne lance de navigateur.
"""
from __future__ import annotations

import io
import json
import os
import threading

import pytest

from app.web import api as web_api
from app.scraper import quick_download as qd


pytestmark = pytest.mark.web


# ===========================================================================
# Fixtures locales
# ===========================================================================
@pytest.fixture
def sessions_propres():
    """Nettoie `SESSIONS_DIR` (dans TEST_DATA_DIR) avant ET après le test."""
    from app.config import SESSIONS_DIR

    def _purge():
        if SESSIONS_DIR.exists():
            for fichier in SESSIONS_DIR.glob("*.json*"):
                fichier.unlink()

    _purge()
    yield SESSIONS_DIR
    _purge()


@pytest.fixture
def threads_factices(monkeypatch):
    """Remplace `threading.Thread` par un pantin : rien ne démarre jamais.

    Retourne la liste des noms de threads « créés » — la preuve qu'une vue a
    bien accepté (ou refusé) la demande, sans qu'aucun chromium ne se lance.
    """
    crees: list[str] = []

    class ThreadFactice:
        def __init__(self, *args, **kwargs):
            crees.append(kwargs.get("name", "?"))

        def start(self):
            return None

    monkeypatch.setattr(threading, "Thread", ThreadFactice)
    return crees


@pytest.fixture
def quotas_isoles(monkeypatch):
    """Sémaphore de quick-download neuf et local au test (2 places)."""
    monkeypatch.setattr(web_api, "_QUICK_DOWNLOAD_MAX", 2)
    monkeypatch.setattr(web_api, "_QUICK_DOWNLOAD_SLOTS", threading.BoundedSemaphore(2))


# ===========================================================================
# 2.1 — injection de lignes dans le .env
# ===========================================================================
@pytest.mark.security
@pytest.mark.parametrize(
    "valeur",
    [
        pytest.param("http://p:1\nAPP_PASSWORD=intrus", id="saut-de-ligne-unix"),
        pytest.param("http://p:1\r\nAPP_PASSWORD=intrus", id="saut-de-ligne-windows"),
        pytest.param("http://p:1\rAPP_PASSWORD=intrus", id="retour-chariot-seul"),
        pytest.param("http://p:1\0APP_PASSWORD=intrus", id="octet-nul"),
    ],
)
def test_save_env_refuse_une_valeur_qui_forge_une_ligne(
    client, settings_env_file, valeur
):
    """Une valeur multi-lignes écrivait DEUX variables au lieu d'une.

    `PROXY_URL` est whitelistée, `APP_PASSWORD` ne l'est pas : sans ce contrôle
    la seconde ligne atterrissait quand même sur le volume, dans un fichier
    chargé avec `override=True`.
    """
    reponse = client.post("/api/settings/env", data={"PROXY_URL": valeur})
    try:
        contenu = (
            settings_env_file.read_text(encoding="utf-8")
            if settings_env_file.exists()
            else ""
        )
        assert reponse.status_code == 400, f"{valeur!r} accepté"
        assert "APP_PASSWORD" not in contenu
        assert "PROXY_URL" not in contenu
    finally:
        reponse.close()


@pytest.mark.security
def test_write_env_file_refuse_lui_meme_une_valeur_toxique(settings_env_file):
    """Le garde-fou est DANS l'écrivain, pas seulement dans la vue.

    `setup_ig_api` appelle `_write_env_file` directement avec des valeurs
    issues d'un JSON : la vue `save_env` n'est pas le seul chemin d'écriture.
    """
    with pytest.raises(ValueError):
        web_api._write_env_file({"IG_ACCESS_TOKEN": "tok\nAPP_PASSWORD=intrus"})

    assert not settings_env_file.exists()


@pytest.mark.security
@pytest.mark.static_check
def test_port_nest_plus_ecrivable_depuis_les_reglages():
    """Le `.env` du volume est chargé avec `override=True` : il ÉCRASE les
    variables de Railway. Y écrire PORT fait écouter le conteneur sur un port
    que la plateforme n'interroge pas — le déploiement meurt à la sauvegarde.
    """
    assert "PORT" not in web_api.ALLOWED_ENV_KEYS


@pytest.mark.security
def test_save_env_ignore_le_champ_port_du_formulaire(client, settings_env_file):
    """Volet de bout en bout : PORT posté seul → 400, rien sur le volume."""
    reponse = client.post("/api/settings/env", data={"PORT": "9999"})
    try:
        assert reponse.status_code == 400
        assert b"Aucun champ valide" in reponse.data
        assert not settings_env_file.exists()
    finally:
        reponse.close()


# ===========================================================================
# 2.1b — écriture atomique du .env
# ===========================================================================
def test_le_env_est_publie_par_un_os_replace(settings_env_file, monkeypatch):
    """Preuve mécanique : la publication passe par `os.replace`, depuis un
    fichier temporaire situé DANS le même répertoire (condition d'un rename
    atomique — un rename inter-systèmes de fichiers n'existe pas).
    """
    appels: list[tuple[str, str]] = []
    vrai_replace = os.replace

    def espion(src, dst, *args, **kwargs):
        appels.append((str(src), str(dst)))
        return vrai_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", espion)

    web_api._write_env_file({"LOG_LEVEL": "DEBUG"})

    assert len(appels) == 1, "le .env doit être publié en UN seul rename"
    src, dst = appels[0]
    assert dst == str(settings_env_file)
    assert os.path.dirname(src) == os.path.dirname(dst)
    assert settings_env_file.read_text(encoding="utf-8").strip() == "LOG_LEVEL=DEBUG"


def test_un_echec_de_publication_laisse_lancien_env_intact(
    settings_env_file, monkeypatch
):
    """Si la publication échoue, le lecteur à chaud voit l'ANCIEN fichier
    complet — jamais un fichier tronqué — et aucun `.tmp` ne subsiste.
    """
    settings_env_file.write_text("PROXY_URL=http://ancien:1\n", encoding="utf-8")

    def replace_qui_echoue(src, dst, *args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", replace_qui_echoue)

    with pytest.raises(OSError):
        web_api._write_env_file({"PROXY_URL": "http://nouveau:2"})

    assert settings_env_file.read_text(encoding="utf-8") == "PROXY_URL=http://ancien:1\n"
    residus = list(settings_env_file.parent.glob(".env.*.tmp"))
    assert residus == [], f"fichiers temporaires abandonnés : {residus}"


def test_deux_sauvegardes_concurrentes_ne_se_perdent_pas(settings_env_file):
    """Le verrou sérialise le read-modify-write : les deux clés survivent."""
    barriere = threading.Barrier(2)

    def sauver(cle, valeur):
        barriere.wait()
        web_api._write_env_file({cle: valeur})

    fils = [
        threading.Thread(target=sauver, args=("PROXY_INSTAGRAM", "http://a:1")),
        threading.Thread(target=sauver, args=("PROXY_TIKTOK", "http://b:2")),
    ]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=10)

    contenu = settings_env_file.read_text(encoding="utf-8")
    assert "PROXY_INSTAGRAM=http://a:1" in contenu
    assert "PROXY_TIKTOK=http://b:2" in contenu


# ===========================================================================
# 2.3 — requêtes inter-sites (aucune protection CSRF aujourd'hui)
# ===========================================================================
@pytest.mark.security
@pytest.mark.parametrize(
    "entetes,attendu",
    [
        pytest.param({"Sec-Fetch-Site": "same-origin"}, False, id="sec-fetch-same-origin"),
        pytest.param({"Sec-Fetch-Site": "same-site"}, False, id="sec-fetch-same-site"),
        pytest.param({"Sec-Fetch-Site": "none"}, False, id="sec-fetch-none"),
        pytest.param({"Sec-Fetch-Site": "cross-site"}, True, id="sec-fetch-cross-site"),
        pytest.param({"Origin": "http://localhost"}, False, id="origin-identique"),
        pytest.param({"Origin": "https://pirate.example"}, True, id="origin-etranger"),
        pytest.param({"Origin": "null"}, True, id="origin-null"),
        pytest.param({}, False, id="aucun-entete-client-non-navigateur"),
    ],
)
def test_verdict_inter_site_sur_un_post(flask_app, entetes, attendu):
    """`Sec-Fetch-Site` prime, `Origin` sert de repli, l'absence des deux
    laisse passer (un navigateur envoie TOUJOURS `Origin` sur un POST
    cross-origin : son absence n'est pas falsifiable depuis un autre site).
    """
    with flask_app.test_request_context(
        "/api/settings/env", method="POST", headers=entetes
    ):
        assert web_api.request_is_cross_site() is attendu


@pytest.mark.security
def test_un_get_nest_jamais_considere_inter_site(flask_app):
    """Les méthodes sûres ne changent pas d'état : rien à protéger."""
    with flask_app.test_request_context(
        "/settings", method="GET", headers={"Sec-Fetch-Site": "cross-site"}
    ):
        assert web_api.request_is_cross_site() is False


@pytest.mark.security
def test_le_refus_inter_site_est_un_403_json_sous_api(flask_app):
    with flask_app.test_request_context(
        "/api/settings/env", method="POST", headers={"Sec-Fetch-Site": "cross-site"}
    ):
        reponse = web_api.reject_cross_site_request()
        assert reponse is not None
        corps, code = reponse
        assert code == 403
        assert corps.is_json
        assert "inter-site" in corps.get_json()["error"]


@pytest.mark.security
def test_une_requete_legitime_traverse_le_hook(flask_app):
    with flask_app.test_request_context(
        "/api/settings/env", method="POST", headers={"Sec-Fetch-Site": "same-origin"}
    ):
        assert web_api.reject_cross_site_request() is None


# ===========================================================================
# 2.3b — upload des cookies de session (risque #61, §6.14)
# ===========================================================================
def _envoyer_cookies(client, plateforme, cookies):
    corps = json.dumps(cookies).encode("utf-8")
    return client.post(
        "/api/settings/session",
        data={
            "platform": plateforme,
            "cookies": (io.BytesIO(corps), "cookies.json"),
        },
        content_type="multipart/form-data",
    )


@pytest.mark.security
def test_un_export_sans_cookie_dauthentification_est_refuse(client, sessions_propres):
    """Le voyant des Réglages se fonde sur la DATE DE MODIFICATION du fichier :
    il passait au VERT à l'instant précis où la session était détruite.
    """
    dest = sessions_propres / "instagram.json"
    dest.write_text(
        json.dumps([{"name": "sessionid", "value": "vivant"}]), encoding="utf-8"
    )
    avant = dest.read_text(encoding="utf-8")

    reponse = _envoyer_cookies(client, "instagram", [{"name": "csrftoken", "value": "x"}])
    try:
        assert reponse.status_code == 400
        assert b"incomplets" in reponse.data
        assert dest.read_text(encoding="utf-8") == avant, "session écrasée par un export inerte"
    finally:
        reponse.close()


@pytest.mark.security
def test_un_cookie_critique_vide_vaut_un_cookie_absent(client, sessions_propres):
    reponse = _envoyer_cookies(client, "twitter", [{"name": "auth_token", "value": ""}])
    try:
        assert reponse.status_code == 400
        assert not (sessions_propres / "twitter.json").exists()
    finally:
        reponse.close()


def test_un_export_valide_sauvegarde_lancienne_session(client, sessions_propres):
    """L'ancien fichier part dans une copie horodatée avant l'écrasement."""
    dest = sessions_propres / "instagram.json"
    dest.write_text(
        json.dumps([{"name": "sessionid", "value": "ancien"}]), encoding="utf-8"
    )

    reponse = _envoyer_cookies(
        client, "instagram", [{"name": "sessionid", "value": "nouveau"}]
    )
    try:
        assert reponse.status_code == 200
        assert "nouveau" in dest.read_text(encoding="utf-8")

        copies = list(sessions_propres.glob("instagram.json.*.bak"))
        assert len(copies) == 1, f"aucune sauvegarde : {copies}"
        assert "ancien" in copies[0].read_text(encoding="utf-8")
    finally:
        reponse.close()


def test_le_premier_upload_ne_cree_aucune_sauvegarde(client, sessions_propres):
    """Rien à sauvegarder quand il n'y avait pas de session : pas de fichier vide."""
    reponse = _envoyer_cookies(
        client, "reddit", [{"name": "reddit_session", "value": "abc"}]
    )
    try:
        assert reponse.status_code == 200
        assert list(sessions_propres.glob("reddit.json.*.bak")) == []
    finally:
        reponse.close()


# ===========================================================================
# 2.4 — SSRF du quick-download
# ===========================================================================
@pytest.mark.security
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://127.0.0.1/instagram.com/p/aaa", id="loopback-v4"),
        pytest.param("http://127.9.9.9/instagram.com/p/aaa", id="loopback-v4-etendu"),
        pytest.param("http://[::1]/instagram.com/p/aaa", id="loopback-v6"),
        pytest.param("http://localhost/instagram.com/p/aaa", id="localhost"),
        pytest.param("http://10.0.0.5/instagram.com/p/aaa", id="prive-10"),
        pytest.param("http://172.16.3.4/instagram.com/p/aaa", id="prive-172-16"),
        pytest.param("http://192.168.1.1/instagram.com/p/aaa", id="prive-192-168"),
        pytest.param("http://169.254.169.254/instagram.com/p/aaa", id="metadonnees-cloud"),
        pytest.param(
            "http://metadata.google.internal/instagram.com/p/aaa", id="metadonnees-par-nom"
        ),
        pytest.param("file:///etc/passwd#instagram.com/p/aaa", id="schema-file"),
        pytest.param("ftp://ftp.invalid/instagram.com/p/aaa", id="schema-ftp"),
        # Formes DÉTOURNÉES de la même adresse. Le navigateur headless applique
        # l'analyseur d'URL WHATWG, pas `ipaddress` : il résout chacune de ces
        # écritures vers 127.0.0.1 ou vers l'endpoint de métadonnées. Une liste
        # noire qui ne compare que des chaînes les laisse toutes passer.
        pytest.param("http://2130706433/instagram.com/p/aaa", id="loopback-decimal"),
        pytest.param("http://0x7f000001/instagram.com/p/aaa", id="loopback-hexa"),
        pytest.param("http://0177.0.0.1/instagram.com/p/aaa", id="loopback-octal"),
        pytest.param("http://127.1/instagram.com/p/aaa", id="loopback-abrege"),
        pytest.param("http://2852039166/instagram.com/p/aaa", id="metadonnees-decimal"),
        pytest.param("http://127。0。0。1/instagram.com/p/aaa", id="point-ideographique"),
        pytest.param(
            "http://[::ffff:169.254.169.254]/instagram.com/p/aaa", id="metadonnees-v4-mappee"
        ),
    ],
)
def test_validate_public_url_refuse_les_cibles_internes(url):
    """`detect_platform` n'est PAS un filtre : ses motifs utilisent `search`,
    donc toutes ces URL sont « reconnues » comme Instagram tout en visant le
    réseau interne du serveur — que le navigateur headless habite.
    """
    assert qd.detect_platform(url) is not None, "l'URL doit bien passer detect_platform"
    assert qd.validate_public_url(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/AbCdEf/",
        "https://x.com/user/status/123456",
        "https://www.reddit.com/r/pics/comments/abc123/titre/",
    ],
)
def test_validate_public_url_laisse_passer_le_cas_nominal(url):
    assert qd.validate_public_url(url) is None


@pytest.mark.security
def test_quick_download_refuse_une_cible_interne_sans_lancer_de_thread(
    client, threads_factices
):
    reponse = client.post(
        "/api/quick-download", json={"url": "http://169.254.169.254/instagram.com/p/aaa"}
    )
    try:
        assert reponse.status_code == 400
        assert reponse.is_json
        assert threads_factices == [], "un navigateur allait s'ouvrir sur le réseau interne"
    finally:
        reponse.close()


@pytest.mark.security
def test_le_nombre_de_telechargements_simultanes_est_borne(
    client, threads_factices, quotas_isoles
):
    """Chaque quick-download ouvre un chromium (~300 Mo) : sans plafond, une
    rafale de clics fait tuer le conteneur par l'OOM killer.
    """
    url = {"url": "https://www.instagram.com/p/AbCdEf/"}
    acceptees = []
    for _ in range(3):
        reponse = client.post("/api/quick-download", json=url)
        try:
            acceptees.append(reponse.status_code)
        finally:
            reponse.close()

    assert acceptees[:2] == [200, 200]
    assert acceptees[2] == 429
    assert len(threads_factices) == 2


def test_une_place_est_rendue_quand_le_thread_se_termine(client, monkeypatch, quotas_isoles):
    """Le worker relâche son jeton, sinon le plafond se vide définitivement."""
    executes: list[str] = []

    class ThreadSynchrone:
        def __init__(self, *args, **kwargs):
            self._cible = kwargs["target"]

        def start(self):
            executes.append("run")
            self._cible()

    monkeypatch.setattr(threading, "Thread", ThreadSynchrone)
    monkeypatch.setattr(
        qd, "quick_download",
        lambda url: qd.QuickDownloadResult(
            platform="instagram", post_id="x", post_url=url, error="stop"
        ),
    )

    for _ in range(4):  # 4 requêtes pour un plafond de 2 : aucun 429 attendu
        reponse = client.post(
            "/api/quick-download", json={"url": "https://www.instagram.com/p/AbCdEf/"}
        )
        try:
            assert reponse.status_code == 200
        finally:
            reponse.close()

    assert len(executes) == 4
    assert web_api._QUICK_DOWNLOAD_SLOTS.acquire(blocking=False) is True
    web_api._QUICK_DOWNLOAD_SLOTS.release()


# ===========================================================================
# 2.4b — plus de str(exc) renvoyé au client
# ===========================================================================
@pytest.mark.security
def test_un_echec_de_telechargement_ne_revele_pas_le_chemin_du_volume(monkeypatch):
    """`OSError` porte le chemin ABSOLU du volume, `SQLAlchemyError` toute
    l'instruction SQL : ni l'un ni l'autre ne doit revenir au client.
    """
    from app.scraper.base import MediaItemData

    item = MediaItemData(
        post_id="p1",
        post_url="https://www.instagram.com/p/AbCdEf/",
        media_type="image",
        media_url="https://cdn.invalid/i.jpg",
    )
    monkeypatch.setitem(qd._PLATFORM_HANDLERS, "instagram", lambda url, pid: [item])

    def telechargement_qui_echoue(url, *args, **kwargs):
        raise OSError("[Errno 28] No space left on device: '/data/downloads/secret.jpg'")

    monkeypatch.setattr(qd, "download_media", telechargement_qui_echoue)

    resultat = qd.quick_download("https://www.instagram.com/p/AbCdEf/")

    message = resultat.media_items[0]["error"]
    assert "/data/downloads" not in message
    assert "Errno" not in message


@pytest.mark.security
def test_un_echec_dextraction_ne_revele_pas_lexception(monkeypatch):
    def extracteur_qui_leve(url, pid):
        raise RuntimeError("SELECT * FROM media_items WHERE local_path = '/data/x.jpg'")

    monkeypatch.setitem(qd._PLATFORM_HANDLERS, "instagram", extracteur_qui_leve)

    resultat = qd.quick_download("https://www.instagram.com/p/AbCdEf/")

    assert "SELECT" not in (resultat.error or "")
    assert "/data/" not in (resultat.error or "")


# ===========================================================================
# LISSAGE VAGUE 2 — reprise des besoins signalés HORS PÉRIMÈTRE par les deux
# lots. Chacun de ces tests garde une correction que personne ne pouvait
# poser depuis son propre périmètre.
# ===========================================================================

# --- 2.3 (suite) — le hook inter-site est réellement ENREGISTRÉ ------------
# Les tests 2.3 ci-dessus éprouvent la FONCTION ; ceux-ci éprouvent le fait
# qu'elle s'exécute sur l'application réelle. Sans `app.before_request(...)`
# dans `create_app()`, la protection était du code mort en production.

def _routes_mutantes(flask_app):
    for regle in flask_app.url_map.iter_rules():
        methodes = regle.methods - {"GET", "HEAD", "OPTIONS"}
        if methodes and regle.endpoint != "static":
            yield sorted(methodes)[0], str(regle)


@pytest.mark.security
def test_le_hook_inter_site_est_enregistre_sur_lapplication(flask_app):
    """La fonction doit figurer dans les `before_request` de l'app."""
    hooks = flask_app.before_request_funcs.get(None, [])
    assert web_api.reject_cross_site_request in hooks


@pytest.mark.security
@pytest.mark.parametrize(
    "entetes",
    [
        pytest.param({"Sec-Fetch-Site": "cross-site"}, id="sec-fetch-cross-site"),
        pytest.param({"Origin": "https://pirate.example"}, id="origin-etranger"),
        pytest.param({"Origin": "null"}, id="iframe-sandbox"),
    ],
)
def test_aucune_route_mutante_nest_atteignable_depuis_un_autre_site(client, entetes):
    """Un formulaire hébergé ailleurs ne doit atteindre AUCUNE route mutante."""
    passees = [
        f"{methode} {chemin}"
        for methode, chemin in _routes_mutantes(client.application)
        if client.open(chemin, method=methode, headers=entetes).status_code != 403
    ]
    assert passees == []


@pytest.mark.security
@pytest.mark.parametrize(
    "entetes",
    [
        pytest.param(
            {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
             "HX-Request": "true"},
            id="htmx-meme-origine",
        ),
        pytest.param({"Sec-Fetch-Site": "same-site", "Origin": "http://localhost"},
                     id="sous-domaine"),
        pytest.param({"Origin": "http://localhost"}, id="navigateur-sans-sec-fetch"),
        pytest.param({}, id="curl-sans-entete"),
    ],
)
def test_le_hook_ne_bloque_aucune_requete_legitime(client, entetes):
    """Garde-fou anti-régression : brancher la protection ne doit casser
    AUCUN formulaire de l'application. Un 400/404/415 est acceptable (le
    corps envoyé ici est vide) ; un 403 ne l'est jamais."""
    bloquees = [
        f"{methode} {chemin}"
        for methode, chemin in _routes_mutantes(client.application)
        if client.open(chemin, method=methode, headers=entetes).status_code == 403
    ]
    assert bloquees == []


@pytest.mark.security
def test_le_health_check_reste_public_malgre_le_hook(client):
    assert client.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


# --- 2.4b (suite) — plus aucun str(exc) dans les 4 API hors périmètre ------

@pytest.mark.security
@pytest.mark.parametrize(
    "module",
    ["app/web/viewer_api.py", "app/calendar/api.py",
     "app/analytics/api.py", "app/editor/api.py"],
)
def test_aucune_api_ne_renvoie_le_texte_de_lexception(module):
    """`str(exc)` renvoyé au client fuite l'instruction SQL complète sur une
    erreur SQLAlchemy et le chemin absolu du volume sur une OSError."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / module).read_text("utf-8")
    fautives = [
        f"{module}:{n}"
        for n, ligne in enumerate(source.splitlines(), 1)
        if "jsonify" in ligne and ("str(exc)" in ligne or "str(e)" in ligne)
    ]
    assert fautives == []


# --- 2.4 (suite) — bornage de la pagination du viewer ---------------------

@pytest.mark.security
@pytest.mark.parametrize(
    "requete,page_attendue,par_page_attendu",
    [
        pytest.param("", 1, 60, id="defaut"),
        pytest.param("?page=abc", 1, 60, id="page-non-numerique"),
        pytest.param("?per_page=abc", 1, 60, id="per_page-non-numerique"),
        pytest.param("?per_page=0", 1, 1, id="per_page-zero-division-par-zero"),
        pytest.param("?per_page=-5", 1, 1, id="per_page-negatif"),
        pytest.param("?page=-99", 1, 60, id="page-negative-offset-negatif"),
        pytest.param("?per_page=999999", 1, 200, id="per_page-demesure"),
        pytest.param("?per_page=200", 1, 200, id="per_page-a-la-borne"),
    ],
)
@pytest.mark.parametrize("route", ["/api/viewer/media", "/api/viewer/memes"])
def test_la_pagination_du_viewer_est_toujours_bornee(
    client, route, requete, page_attendue, par_page_attendu
):
    """Une pagination absurde retombe sur les bornes, elle ne part jamais en
    500 (ValueError sur `int()`, ZeroDivisionError sur `total_pages`) et ne
    charge jamais la médiathèque entière en mémoire."""
    reponse = client.get(route + requete)
    assert reponse.status_code == 200
    corps = reponse.get_json()
    assert corps["page"] == page_attendue
    assert corps["per_page"] == par_page_attendu


# --- Cohérence — les fragments HTMX passent par le système de jetons ------

@pytest.mark.security
def test_aucun_fragment_html_ne_pose_de_couleur_en_dur():
    """Les fragments renvoyés par l'API doivent utiliser les classes du
    système de jetons (.text-error / .text-success), sinon ils ignorent le
    thème sombre et la palette sémantique."""
    from pathlib import Path

    racine = Path(__file__).resolve().parent.parent
    fautifs = []
    for chemin in ("app/web/api.py", "app/web/templates/settings.html"):
        for n, ligne in enumerate((racine / chemin).read_text("utf-8").splitlines(), 1):
            if "color:red" in ligne or "color:green" in ligne:
                fautifs.append(f"{chemin}:{n}")
    assert fautifs == []


# --- Cache-busting — la globale Jinja attendue par layout.html existe -----

@pytest.mark.security
def test_le_cache_busting_est_calcule_sur_le_mtime(flask_app):
    """`layout.html` bascule sur `asset_version()` dès que la globale existe ;
    sans elle il retombait sur une constante à bumper à la main."""
    version = flask_app.jinja_env.globals["asset_version"]
    empreinte = version("samourais.css")
    assert empreinte.isdigit() and empreinte != "0"
    # Un fichier absent ne doit pas casser le rendu de toute la page.
    assert version("fichier-qui-nexiste-pas.css") == "0"


@pytest.mark.security
def test_le_port_nest_plus_un_champ_editable_des_reglages():
    """PORT a quitté ALLOWED_ENV_KEYS (lot 2.1) : le formulaire ne doit plus
    prétendre le rendre modifiable."""
    from pathlib import Path

    gabarit = (
        Path(__file__).resolve().parent.parent / "app/web/templates/settings.html"
    ).read_text("utf-8")
    assert 'name="PORT"' not in gabarit
