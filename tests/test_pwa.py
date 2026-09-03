"""
Application installable (PWA) — le manifeste, les icônes, le worker.

CE QUE CE MODULE PROTÈGE
------------------------
Un service worker est le seul code de cette application capable de la
casser DURABLEMENT. Il survit au rechargement et au redéploiement : un
mauvais cache sert une version morte du site à un téléphone pendant des
semaines, et aucun bouton « actualiser » n'y change rien. C'est la panne
qu'on ne peut plus corriger à distance.

§1 rejoue les règles du worker dans un faux environnement (voir
`tests/sw_harness.mjs`) : une page part toujours au réseau, l'API et les
médias ne sont jamais mis en conserve, une écriture n'est jamais
interceptée. §2 vérifie que le worker change d'octets dès qu'un fichier
statique bouge — sinon un correctif partirait derrière un worker
identique, qui continuerait de servir l'ancien cache. §3 fige les deux
détails sans lesquels l'application n'est PAS installable, et qui ne
lèvent aucune erreur visible quand ils manquent.

CE QU'IL NE PROTÈGE PAS
-----------------------
Le banc d'essai n'est pas un navigateur : il ne rejoue ni le cycle
install/activate réel, ni les priorités de cache du moteur. Il juge les
décisions d'aiguillage. L'installation sur un vrai téléphone — l'icône
sur l'écran d'accueil, le plein écran — reste à vérifier à la main.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
STATIC = RACINE / "app" / "web" / "static"
BANC = Path(__file__).resolve().parent / "sw_harness.mjs"
PARTIEL_PWA = RACINE / "app" / "web" / "templates" / "partials" / "pwa.html"

from tests.test_web import PAGES

besoin_de_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node absent : le banc d'essai du service worker ne peut pas tourner",
)


@pytest.fixture(scope="module")
def decisions() -> dict:
    """Exécute le VRAI sw.js dans un faux ServiceWorkerGlobalScope."""
    sortie = subprocess.run(
        [shutil.which("node") or "node", str(BANC)],
        capture_output=True, text=True, timeout=60,
    )
    assert sortie.returncode == 0, f"le banc d'essai a échoué :\n{sortie.stderr}"
    return json.loads(sortie.stdout)


# ===========================================================================
# §1. Les règles du worker
# ===========================================================================


@besoin_de_node
def test_une_page_part_toujours_au_reseau_meme_si_elle_est_en_cache(decisions):
    """LA règle qui empêche un déploiement de rester invisible.

    Servir une page depuis le cache en premier, c'est figer la version que
    le téléphone a vue ce jour-là. Le cache n'est qu'un filet de coupure.
    """
    nav = decisions["navigation"]
    assert nav["intercepte"] is True
    assert nav["reseauAppele"] is True
    assert nav["provenance"] == "reseau", (
        "la page a été servie depuis le cache alors que le réseau répondait"
    )


@besoin_de_node
def test_hors_ligne_une_page_deja_vue_ressort_du_cache(decisions):
    hl = decisions["navigationHorsLigne"]
    assert hl["provenance"] == "cache"
    assert hl["corps"] == "VIEILLE PAGE"


@besoin_de_node
def test_hors_ligne_une_page_jamais_vue_donne_la_page_de_secours(decisions):
    inconnue = decisions["navigationInconnueHorsLigne"]
    assert inconnue["corps"] == "coquille", "pas de page de secours hors ligne"


@besoin_de_node
def test_l_api_n_est_jamais_mise_en_cache(decisions):
    """`/api/…` renvoie l'état vivant : compteurs, corbeille, jobs. Une
    réponse ressortie d'un cache serait un mensonge à l'écran."""
    assert decisions["api"]["intercepte"] is False


@besoin_de_node
def test_les_medias_ne_sont_jamais_mis_en_cache(decisions):
    """Un média peut disparaître (vidage de la corbeille) : une vignette
    ressortie du cache montrerait un fichier supprimé."""
    assert decisions["media"]["intercepte"] is False


@besoin_de_node
def test_une_ecriture_n_est_jamais_interceptee(decisions):
    """Suppression, phrase, vidage : rien de tout ça ne doit rencontrer le
    worker, même sur une URL qu'il gère en lecture."""
    assert decisions["ecriture"]["intercepte"] is False


@besoin_de_node
def test_une_autre_origine_n_est_jamais_touchee(decisions):
    assert decisions["autreOrigine"]["intercepte"] is False


@besoin_de_node
def test_un_statique_deja_en_cache_est_servi_sans_attendre_le_reseau(decisions):
    """Les URLs statiques portent `?v=<mtime>` : une URL ne désigne jamais
    deux contenus, le cache-first est donc sûr."""
    stat = decisions["statiqueEnCache"]
    assert stat["provenance"] == "cache"
    assert stat["corps"] == "CSS EN CACHE"


@besoin_de_node
def test_le_worker_prend_la_main_sans_attendre_et_purge_les_vieux_caches(decisions):
    """Sans `skipWaiting`/`claim`, la version suivante attendrait que tous
    les onglets soient fermés — c'est-à-dire, sur un téléphone, longtemps."""
    assert decisions["skipWaitingALInstall"] is True
    assert decisions["claim"] is True
    assert decisions["cachesSupprimes"] == ["samourais-ancien"], (
        "les caches des versions précédentes ne sont pas purgés"
    )


@besoin_de_node
def test_la_page_de_secours_est_mise_en_cache_a_l_installation(decisions):
    assert decisions["coquilleEnCache"] is True


# ===========================================================================
# §2. Le worker change quand les fichiers changent
# ===========================================================================


def test_le_worker_porte_une_empreinte_des_statiques(client, tmp_path):
    """Les navigateurs n'installent un nouveau worker que si ses OCTETS
    changent. Avec un numéro de version écrit à la main, une correction de
    CSS partirait en production derrière un worker identique, qui
    continuerait de servir l'ancien cache."""
    from app.web import routes

    avant = client.get("/sw.js").get_data(as_text=True)
    assert 'const BUILD = "dev"' not in avant, "l'empreinte n'est pas injectée"

    # Un fichier statique change → l'empreinte doit changer.
    temoin = routes._SW_SOURCE.parent / "_temoin_pwa.txt"
    try:
        temoin.write_text("empreinte", encoding="utf-8")
        apres = client.get("/sw.js").get_data(as_text=True)
    finally:
        temoin.unlink(missing_ok=True)

    ligne_avant = [l for l in avant.splitlines() if "const BUILD" in l][0]
    ligne_apres = [l for l in apres.splitlines() if "const BUILD" in l][0]
    assert ligne_avant != ligne_apres, (
        "un fichier statique a changé sans que le worker change : "
        "le navigateur garderait l'ancien"
    )


def test_le_worker_est_servi_depuis_la_racine_et_jamais_mis_en_cache_http(client):
    """La portée d'un worker ne remonte pas au-dessus de son chemin :
    /static/sw.js ne contrôlerait aucune page. Et un worker figé dans le
    cache HTTP est précisément la panne qu'on ne peut plus corriger."""
    r = client.get("/sw.js")

    assert r.status_code == 200
    assert "javascript" in r.headers["Content-Type"]
    assert r.headers.get("Service-Worker-Allowed") == "/"
    assert "no-store" in r.headers.get("Cache-Control", "")


# ===========================================================================
# §3. Ce sans quoi l'application n'est pas installable
# ===========================================================================


@pytest.mark.parametrize("page", PAGES)
def test_chaque_ecran_declare_le_manifeste_et_enregistre_le_worker(client, page):
    """CHAQUE écran, pas seulement ceux qui héritent du gabarit.

    Quatre des huit pages (bibliothèque, éditeur, calendrier, statistiques)
    ont leur propre <head> et n'étendent pas layout.html. Vérifier le seul
    gabarit laissait passer exactement ça : la bibliothèque — qui est le
    `start_url` du manifeste — sans manifeste ni worker. On ne pouvait pas
    installer l'application depuis l'écran par lequel on entre, et rien ne
    le signalait.
    """
    r = client.get(page)
    try:
        assert r.status_code == 200
        html = r.get_data(as_text=True)
    finally:
        r.close()

    assert 'rel="manifest"' in html, f"{page} ne déclare pas le manifeste"
    assert "navigator.serviceWorker.register" in html, (
        f"{page} n'enregistre pas le service worker"
    )
    assert 'rel="apple-touch-icon"' in html, f"{page} n'a pas d'icône iOS"


def test_le_manifeste_est_demande_avec_les_identifiants(client):
    """LE détail qui casse tout en silence : le manifeste est téléchargé
    SANS les identifiants de la page, et l'application tourne derrière une
    authentification HTTP Basic. Sans `use-credentials`, la requête repart
    en 401, le manifeste n'est jamais lu, et l'application n'est pas
    installable — sans le moindre message d'erreur."""
    for page in PAGES:
        r = client.get(page)
        try:
            html = r.get_data(as_text=True)
        finally:
            r.close()
        lien = [l for l in html.splitlines() if 'rel="manifest"' in l]
        assert lien, f"{page} : plus de lien vers le manifeste"
        assert 'crossorigin="use-credentials"' in lien[0], (
            f"{page} : le manifeste repartira en 401 derrière l'authentification Basic"
        )


def _manifeste(client) -> dict:
    """Lit le manifeste et REFERME la réponse.

    Les fichiers statiques sont servis par `send_file`, qui laisse un
    descripteur ouvert tant que la réponse n'est pas fermée : sans ce
    `close()`, pytest fait échouer le test sur un ResourceWarning qui n'a
    rien à voir avec ce qu'il vérifie.
    """
    r = client.get("/static/manifest.webmanifest")
    try:
        assert r.status_code == 200
        return json.loads(r.get_data(as_text=True))
    finally:
        r.close()


def test_le_manifeste_est_un_json_valide_et_complet(client):
    m = _manifeste(client)
    assert m["display"] == "standalone", "sans ça, l'app s'ouvre dans un onglet"
    assert m["start_url"].startswith("/")
    assert m["scope"] == "/"
    for champ in ("name", "short_name", "background_color", "theme_color"):
        assert m.get(champ), f"champ de manifeste manquant : {champ}"

    tailles = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= tailles, "Android exige 192 et 512"
    assert any(i.get("purpose") == "maskable" for i in m["icons"]), (
        "sans icône maskable, Android rogne l'icône dans un cercle blanc"
    )


def test_toutes_les_icones_du_manifeste_existent_vraiment(client):
    """Une seule icône manquante et l'installation est refusée."""
    m = _manifeste(client)

    sources = {i["src"] for i in m["icons"]}
    for raccourci in m.get("shortcuts", []):
        sources.update(i["src"] for i in raccourci.get("icons", []))

    for src in sorted(sources):
        r = client.get(src)
        try:
            assert r.status_code == 200, f"icône absente : {src}"
            assert r.get_data()[:8] == b"\x89PNG\r\n\x1a\n", f"pas un PNG : {src}"
        finally:
            r.close()


def test_les_raccourcis_du_manifeste_pointent_vers_des_pages_reelles(client):
    m = _manifeste(client)

    for url in [r["url"] for r in m.get("shortcuts", [])] + [m["start_url"]]:
        r = client.get(url)
        try:
            assert r.status_code == 200, f"lien mort dans le manifeste : {url}"
        finally:
            r.close()


def test_l_icone_ios_est_declaree_et_servie(client):
    """iOS ignore le manifeste pour l'icône d'écran d'accueil : sans
    `apple-touch-icon`, il pose une capture de la page à la place."""
    r = client.get("/static/icons/apple-touch-icon.png")
    try:
        assert r.status_code == 200
        assert r.get_data()[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        r.close()


def test_la_page_de_secours_ne_depend_de_rien_d_exterieur():
    """Elle ne s'affiche QUE sans réseau — c'est-à-dire au seul moment où
    une dépendance externe est certaine de manquer."""
    html = (STATIC / "offline.html").read_text(encoding="utf-8")

    assert "https://" not in html, "la page hors ligne appelle un serveur distant"
    assert "<link" not in html, "la page hors ligne dépend d'une feuille de style"


def test_l_enregistrement_du_worker_ne_casse_pas_la_page_s_il_echoue():
    """Un worker refusé (HTTP simple, navigateur ancien) doit laisser
    l'application marcher comme un site ordinaire."""
    html = PARTIEL_PWA.read_text(encoding="utf-8")

    bloc = html[html.index("serviceWorker' in navigator"):]
    bloc = bloc[:bloc.index("</script>")]
    assert ".catch(" in bloc, "un échec d'enregistrement remonterait en erreur non capturée"
