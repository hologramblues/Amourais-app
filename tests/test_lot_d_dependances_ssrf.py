"""
LOT D — dépendances externes (CDN) et SSRF (résolution DNS + redirections).

Deux sujets, un seul lot, parce qu'ils partagent la même racine : l'application
faisait confiance à quelque chose qu'elle ne contrôlait pas — un CDN tiers pour
le code exécuté dans l'éditeur, une simple comparaison de CHAÎNES pour décider
qu'une URL est publique.

Aucun de ces deux bugs n'avait de marqueur `xfail(strict=True)` dans la suite
de la vague 0 : la table de `tests/README.md` §5 ne mentionne ni le CDN ni la
résolution DNS (le §6.4 d'AUDIT.md ne demandait qu'une liste blanche de
domaines, posée au lot 2.4). Ces tests-ci sont donc écrits VERTS avec la
correction, et ils rougissent si on la retire — la vérification par mutation
est décrite dans chaque docstring.

Aucun accès réseau : `socket.getaddrinfo` et le transport httpx sont simulés.
"""

from __future__ import annotations

import base64
import hashlib
import re
import socket
from pathlib import Path

import httpx
import pytest

from app.scraper import quick_download as qd


RACINE = Path(__file__).resolve().parent.parent
EDITOR_HTML = RACINE / "app/web/templates/editor.html"
VENDOR = RACINE / "app/web/static/vendor"


# ===========================================================================
# D.1 — Fabric.js n'est plus chargé depuis un CDN tiers
# ===========================================================================
# Constat de départ : `editor.html` chargeait
# https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js.
# Hors ligne — ou si cdnjs tombe — `fabric` reste indéfini, `init()` meurt sur
# `new fabric.Canvas` (editor.js:261) et l'écran ne survit QUE par le cache du
# navigateur. C'est aussi du code tiers exécuté sans contrôle d'intégrité.

#: SRI publié par cdnjs pour fabric.js 5.3.1 (api.cdnjs.com/libraries/fabric.js/5.3.1).
SRI_FABRIC_531 = (
    "sha512-CeIsOAsgJnmevfCi2C7Zsyy6bQKi43utIjdA87Q0ZY84oDqnI0uwfM9+"
    "bKiIkI75lUeI00WG/+uJzOmuHlesMA=="
)


def test_le_fichier_fabric_est_servi_par_lapplication():
    """Le paquet est dans le dépôt, pas chez un tiers."""
    fichier = VENDOR / "fabric-5.3.1.min.js"
    assert fichier.is_file(), f"{fichier} absent : l'éditeur redépend du CDN"
    assert fichier.stat().st_size > 200_000, "fichier tronqué"


def test_le_fichier_fabric_est_bien_le_build_5_3_1_de_cdnjs():
    """Intégrité : même octets que le paquet publié, donc même comportement.

    Ce test est la seule garantie qu'une mise à jour de vendor/ n'introduit
    pas un fichier différent de celui que l'audit a vu tourner.
    """
    contenu = (VENDOR / "fabric-5.3.1.min.js").read_bytes()
    empreinte = "sha512-" + base64.b64encode(hashlib.sha512(contenu).digest()).decode()
    assert empreinte == SRI_FABRIC_531


def test_editor_html_ne_charge_plus_aucun_script_depuis_un_cdn():
    """Mutation : remettre la balise cdnjs → ce test rougit."""
    source = EDITOR_HTML.read_text("utf-8")
    balises = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", source)
    externes = [
        s for s in balises
        if s.startswith(("http://", "https://", "//"))
        and "apis.google.com" not in s
    ]
    assert externes == [], f"scripts encore chargés depuis un tiers : {externes}"


def test_editor_html_pointe_sur_la_copie_locale_de_fabric():
    source = EDITOR_HTML.read_text("utf-8")
    balises = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", source)
    assert any("/static/vendor/fabric-5.3.1.min.js" in s for s in balises)
    assert not any("cdnjs" in s for s in balises)


# ===========================================================================
# D.2 — L'API Google ne peut pas être rapatriée : sa panne doit être
#       VISIBLE et NON BLOQUANTE
# ===========================================================================
def test_le_script_google_ne_bloque_plus_le_rendu_de_la_page():
    """La balise était SYNCHRONE : hors ligne, la page restait suspendue
    jusqu'au timeout DNS/TCP avant même d'afficher l'éditeur.

    Mutation : retirer `async` → ce test rougit.
    """
    source = EDITOR_HTML.read_text("utf-8")
    balise = re.search(r"<script[^>]*apis\.google\.com[^>]*>", source)
    assert balise is not None, "la balise a disparu — l'assertion ne garde plus rien"
    assert "async" in balise.group(0)


def test_lechec_de_lapi_google_est_constate_et_non_avale():
    """`onerror` ET `onload` renseignent l'état lu par le garde-fou."""
    source = EDITOR_HTML.read_text("utf-8")
    balise = re.search(r"<script[^>]*apis\.google\.com[^>]*>", source, re.DOTALL)
    assert "onerror" in balise.group(0)
    assert "onload" in balise.group(0)
    assert "SAMOURAIS_GAPI" in source


def test_le_clic_drive_est_intercepte_quand_gapi_manque():
    """`openGoogleDrivePicker` (editor.js:1342) appelle `gapi.load` sans garde.

    Le garde-fou doit s'interposer en phase de CAPTURE — un `addEventListener`
    en phase de bouillonnement passerait APRÈS le gestionnaire d'editor.js,
    donc après la ReferenceError. Le troisième argument `true` est donc le
    cœur de la correction, pas un détail.
    """
    source = EDITOR_HTML.read_text("utf-8")
    bloc = source[source.index("SAMOURAIS_GAPI"):]
    assert "connect-drive-btn" in bloc
    assert re.search(
        r"addEventListener\(\s*'click'.*?\}\s*,\s*true\s*\)", bloc, re.DOTALL
    ), "l'écouteur n'est pas en phase de capture"
    assert "stopImmediatePropagation" in bloc


def test_la_panne_drive_est_dite_a_lutilisateur_et_desactive_le_bouton():
    """« se désactiver proprement en le disant » : un refus muet ne suffit pas."""
    source = EDITOR_HTML.read_text("utf-8")
    bloc = source[source.index("SAMOURAIS_GAPI"):]
    assert "apis.google.com" in bloc, "le message ne nomme pas la cause"
    assert "notify" in bloc, "aucun message n'est affiché"
    assert "disabled = true" in bloc, "le bouton reste cliquable"
    assert "aria-disabled" in bloc, "rien pour les lecteurs d'écran"


def test_le_reste_de_lediteur_ne_depend_pas_de_lapi_google():
    """Fabric est local et chargé AVANT api.js : une panne Google ne peut
    pas empêcher le canvas de démarrer."""
    source = EDITOR_HTML.read_text("utf-8")
    assert source.index("fabric-5.3.1.min.js") < source.index("apis.google.com")


# ===========================================================================
# D.3 — SSRF : la validation résout le NOM et juge les ADRESSES
# ===========================================================================
def _resolveur(*adresses: str):
    """Faux `getaddrinfo` rendant les adresses demandées."""
    def _faux(hote, port=None, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in adresses
        ]
    return _faux


def _resolveur_qui_compte(journal: list, *adresses: str):
    def _faux(hote, port=None, *args, **kwargs):
        journal.append(hote)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in adresses
        ]
    return _faux


@pytest.mark.security
def test_un_nom_ordinaire_qui_resout_en_loopback_est_refuse(monkeypatch):
    """LE bug du lot D. `localtest.me` est un nom parfaitement banal : aucune
    comparaison de chaînes ne peut le distinguer, il faut le RÉSOUDRE.

    La caractérisation du bug est l'assertion du milieu : au niveau du texte,
    l'hôte est jugé public. Seule la résolution le démasque.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur("127.0.0.1"))

    assert qd._host_is_internal("localtest.me") is False  # le texte ne dit rien

    faute = qd.validate_public_url(
        "http://localtest.me/instagram.com/p/aaa", exiger_plateforme=False
    )
    assert faute is not None
    assert "resout" in faute


@pytest.mark.security
@pytest.mark.parametrize(
    "adresse, cas",
    [
        ("127.0.0.1", "loopback"),
        ("169.254.169.254", "metadonnees-cloud"),
        ("10.1.2.3", "prive-10"),
        ("172.20.0.9", "prive-172"),
        ("192.168.0.7", "prive-192"),
        ("0.0.0.0", "non-specifiee"),
        ("::1", "loopback-v6"),
        ("fe80::1", "lien-local-v6"),
        ("::ffff:127.0.0.1", "v4-mappee-en-v6"),
        ("fd00::1", "unique-local-v6"),
    ],
)
def test_toutes_les_familles_dadresses_internes_sont_refusees(
    monkeypatch, adresse, cas
):
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur(adresse))
    faute = qd.validate_public_url("https://cdn.exemple.test/x", exiger_plateforme=False)
    assert faute is not None, f"{cas} ({adresse}) est passé"


@pytest.mark.security
@pytest.mark.parametrize(
    "adresses",
    [
        pytest.param(("93.184.216.34", "127.0.0.1"), id="interne-en-dernier"),
        pytest.param(("1.1.1.1", "127.0.0.1"), id="interne-apres-tri"),
        pytest.param(("127.0.0.1", "93.184.216.34"), id="interne-en-premier"),
    ],
)
def test_une_seule_adresse_interne_dans_la_reponse_suffit_a_refuser(
    monkeypatch, adresses
):
    """Un nom peut rendre PLUSIEURS adresses. En juger une seule laisserait
    passer l'attaque « round-robin » : une adresse publique et une adresse
    interne dans la même réponse DNS.

    Les trois cas couvrent les deux ordres de la réponse ET les deux ordres
    après tri — sans quoi la mutation « ne juger que la première » survivrait
    (`sorted(["93.184.216.34", "127.0.0.1"])[0]` vaut déjà `127.0.0.1`).
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur(*adresses))
    assert qd.validate_public_url(
        "https://cdn.exemple.test/x", exiger_plateforme=False
    ) is not None


@pytest.mark.security
def test_un_nom_introuvable_est_refuse_et_non_suppose_public(monkeypatch):
    """Fail CLOSED : ne pas savoir n'est pas une autorisation."""
    def _echoue(*args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _echoue)
    assert qd.validate_public_url(
        "https://cdn.exemple.test/x", exiger_plateforme=False
    ) is not None


def test_un_nom_public_est_accepte(monkeypatch):
    """Le cas nominal reste ouvert : sans lui, ces tests seraient tautologiques."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur("93.184.216.34"))
    assert qd.validate_public_url(
        "https://cdn.syndication.twimg.com/tweet-result?id=1",
        exiger_plateforme=False,
    ) is None


# ---------------------------------------------------------------------------
# L'URL D'ENTRÉE : contrainte à une plateforme, donc jamais résolue
# ---------------------------------------------------------------------------
@pytest.mark.security
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localtest.me/instagram.com/p/aaa", id="chemin"),
        pytest.param("http://attaquant.test/?x=instagram.com/p/aaa", id="requete"),
        pytest.param("https://evil.test/#instagram.com/p/aaa", id="fragment"),
        pytest.param("https://evil-instagram.com/instagram.com/p/aaa", id="prefixe-colle"),
        pytest.param(
            "https://instagram.com.evil.test/instagram.com/p/aaa", id="suffixe-usurpe"
        ),
    ],
)
def test_lurl_soumise_doit_appartenir_a_une_plateforme_servie(url):
    """`detect_platform` n'est PAS un filtre : ses motifs sont en `search`, donc
    le motif peut matcher dans le CHEMIN, la REQUÊTE ou le FRAGMENT.

    Mutation : supprimer la branche `exiger_plateforme` → ces cinq cas passent.
    """
    assert qd.detect_platform(url) is not None, "l'URL passe bien detect_platform"
    faute = qd.validate_public_url(url)
    assert faute is not None
    assert "plateformes" in faute


@pytest.mark.security
@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/AbCdEf/",
        "https://instagram.com/reel/AbCdEf/",
        "https://x.com/user/status/123456",
        "https://twitter.com/user/status/123456",
        "https://www.tiktok.com/@moi/video/7123456789",
        "https://vm.tiktok.com/ZMabcdef/",
        "https://www.reddit.com/r/pics/comments/abc123/titre/",
        "https://redd.it/abc123",
    ],
)
def test_les_urls_legitimes_des_quatre_plateformes_restent_acceptees(url):
    """Garde anti-régression de PRODUCTION : la liste blanche ne doit
    refuser aucune forme réellement utilisée, sous-domaines compris."""
    assert qd.detect_platform(url) is not None
    assert qd.validate_public_url(url) is None


def test_une_url_de_plateforme_ne_declenche_aucune_resolution_dns(monkeypatch):
    """L'hôte est déjà contraint : une requête DNS synchrone sur le chemin
    chaud n'apporterait rien (le DNS d'instagram.com n'est pas sous le
    contrôle d'un attaquant) et coûterait une latence par clic.
    """
    appels: list[str] = []
    monkeypatch.setattr(
        socket, "getaddrinfo", _resolveur_qui_compte(appels, "93.184.216.34")
    )
    assert qd.validate_public_url("https://www.instagram.com/p/AbCdEf/") is None
    assert appels == [], f"résolution inutile de {appels}"


def test_les_ip_litterales_ne_sont_pas_resolues(monkeypatch):
    """Une adresse est déjà une adresse : la passer à `getaddrinfo` serait un
    aller-retour pour rien — et une occasion de plus de se tromper."""
    appels: list[str] = []
    monkeypatch.setattr(
        socket, "getaddrinfo", _resolveur_qui_compte(appels, "93.184.216.34")
    )
    assert qd.validate_public_url(
        "http://127.0.0.1/x", exiger_plateforme=False
    ) is not None
    assert appels == []


# ===========================================================================
# D.4 — SSRF : CHAQUE saut de la chaîne de redirections est jugé
# ===========================================================================
class _TransportFactice:
    """Répond à partir d'une table {url: (statut, en-têtes)} et journalise."""

    def __init__(self, reponses: dict):
        self.reponses = reponses
        self.demandes: list[str] = []

    def __call__(self, requete):
        url = str(requete.url)
        self.demandes.append(url)
        statut, entetes, corps = self.reponses.get(
            url, (404, {}, b"introuvable")
        )
        return httpx.Response(statut, headers=entetes, content=corps)


@pytest.fixture
def transport(monkeypatch):
    """Branche un transport simulé sous `httpx.Client` (conftest fait lever
    `HTTPTransport.handle_request` ; on le remplace, on ne le contourne pas)."""
    def _installer(reponses: dict) -> _TransportFactice:
        faux = _TransportFactice(reponses)

        def handle_request(self, request):
            return faux(request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
        return faux
    return _installer


DEPART = "https://www.instagram.com/p/AbCdEf/embed/captioned/"


@pytest.mark.security
def test_une_redirection_vers_le_reseau_interne_nest_pas_suivie(
    monkeypatch, transport
):
    """LE second bug du lot D. `follow_redirects=True` ne jugeait que l'URL de
    DÉPART : un 302 vers 169.254.169.254 était suivi sans aucun contrôle.

    Mutation : rendre `_http_get_public` à `client.get(..., follow_redirects=
    True)` → la requête interne part et ce test rougit.
    """
    faux = transport({
        DEPART: (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
    })

    resp = qd._http_get_public(
        DEPART, headers={}, timeout=5, contexte="test",
    )

    assert resp is None, "la chaîne aurait dû être coupée"
    assert faux.demandes == [DEPART], (
        f"la requête interne est partie : {faux.demandes}"
    )


@pytest.mark.security
def test_une_redirection_vers_un_nom_qui_resout_en_interne_est_coupee(
    monkeypatch, transport
):
    """Le saut porte un NOM, pas une IP : sans résolution, il passe."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur("169.254.169.254"))
    faux = transport({
        DEPART: (302, {"location": "https://redirecteur.test/x"}, b""),
    })

    assert qd._http_get_public(DEPART, headers={}, timeout=5, contexte="test") is None
    assert faux.demandes == [DEPART]


@pytest.mark.security
def test_une_redirection_relative_est_resolue_avant_detre_jugee(transport):
    """Une `Location` relative rend un hôte VIDE à `urlsplit` : jugée telle
    quelle, elle serait refusée à tort (régression fonctionnelle) — ou pire,
    acceptée sans hôte. Elle doit être recomposée sur l'URL courante.
    """
    suite = "https://www.instagram.com/p/AbCdEf/embed/final/"
    faux = transport({
        DEPART: (302, {"location": "/p/AbCdEf/embed/final/"}, b""),
        suite: (200, {}, b"<html>ok</html>"),
    })

    resp = qd._http_get_public(DEPART, headers={}, timeout=5, contexte="test")

    assert resp is not None and resp.status_code == 200
    assert faux.demandes == [DEPART, suite]


@pytest.mark.security
def test_une_redirection_publique_reste_suivie(monkeypatch, transport):
    """Le contrôle ne doit pas casser le cas nominal : Instagram redirige
    réellement les pages `embed`."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolveur("93.184.216.34"))
    suite = "https://scontent.cdninstagram.com/v/photo.jpg"
    faux = transport({
        DEPART: (302, {"location": suite}, b""),
        suite: (200, {}, b"binaire"),
    })

    resp = qd._http_get_public(DEPART, headers={}, timeout=5, contexte="test")

    assert resp is not None and resp.content == b"binaire"
    assert faux.demandes == [DEPART, suite]


@pytest.mark.security
def test_une_boucle_de_redirections_est_bornee(transport):
    """Sans borne, une boucle 302 ↔ 302 tient le thread indéfiniment."""
    a = "https://www.instagram.com/a"
    b = "https://www.instagram.com/b"
    faux = transport({
        a: (302, {"location": b}, b""),
        b: (302, {"location": a}, b""),
    })

    assert qd._http_get_public(a, headers={}, timeout=5, contexte="test") is None
    assert len(faux.demandes) <= qd._MAX_REDIRECTIONS + 1


@pytest.mark.security
def test_les_deux_chemins_api_passent_par_le_controle_des_sauts():
    """Assertion statique : `_try_instagram_embed` et `_try_twitter_syndication`
    ne doivent plus appeler `httpx.get(..., follow_redirects=True)`.

    Sans elle, un futur correctif pourrait recréer le trou dans l'un des deux
    chemins sans qu'aucun test dynamique ne s'en aperçoive.
    """
    source = (RACINE / "app/scraper/quick_download.py").read_text("utf-8")
    assert "httpx.get(" not in source, "un GET direct court-circuite le controle"
    assert "follow_redirects=False" in source
    assert source.count("_http_get_public(") >= 3  # 1 définition + 2 appels


# ---------------------------------------------------------------------------
# Les quatre chemins navigateur
# ---------------------------------------------------------------------------
class _CadreFactice:
    def __init__(self, url):
        self.url = url


class _PageFactice:
    """Le strict minimum de l'API Playwright utilisé par l'observateur."""

    def __init__(self):
        self.ecouteurs: dict[str, list] = {}
        self.main_frame = _CadreFactice("about:blank")

    def on(self, evenement, rappel):
        self.ecouteurs.setdefault(evenement, []).append(rappel)

    def naviguer(self, url):
        self.main_frame = _CadreFactice(url)
        for rappel in self.ecouteurs.get("framenavigated", []):
            rappel(self.main_frame)


@pytest.mark.security
def test_lobservateur_signale_un_saut_du_navigateur_vers_le_reseau_interne():
    page = _PageFactice()
    journal: list[str] = []
    qd._surveiller_les_sauts(page, journal)

    page.naviguer("https://www.tiktok.com/@moi/video/7123")
    assert journal == [], "un saut légitime a été signalé"

    page.naviguer("http://169.254.169.254/latest/meta-data/")
    assert len(journal) == 1
    assert "169.254.169.254" in journal[0]


def test_lobservateur_ignore_les_etapes_internes_du_navigateur():
    """`about:blank` est émis à chaque ouverture d'onglet : le juger ferait
    échouer TOUS les scrapes, un faux positif bien plus coûteux que le bug."""
    page = _PageFactice()
    journal: list[str] = []
    qd._surveiller_les_sauts(page, journal)

    for interne in ("about:blank", "blob:https://x.com/1234", "data:text/html,x"):
        page.naviguer(interne)
    assert journal == []


@pytest.mark.security
def test_les_quatre_extracteurs_installent_lobservateur_et_jettent_le_resultat():
    """Assertion statique — un vrai navigateur est interdit dans la suite
    (LOI ABSOLUE n°3). On vérifie donc le CÂBLAGE des quatre chemins : le bug
    d'origine était précisément qu'un seul d'entre eux aurait été corrigé.
    """
    source = (RACINE / "app/scraper/quick_download.py").read_text("utf-8")
    for extracteur in (
        "_extract_instagram", "_extract_tiktok", "_extract_twitter", "_extract_reddit",
    ):
        debut = source.index(f"def {extracteur}(")
        corps = source[debut:source.index("\ndef ", debut + 10)]
        assert "_surveiller_les_sauts(page, sauts_interdits)" in corps, extracteur
        assert "if sauts_interdits:" in corps, extracteur
        assert "return []" in corps.split("if sauts_interdits:")[1][:400], extracteur


@pytest.mark.security
def test_lurl_normalisee_de_twitter_est_revalidee():
    """`re.sub(r"twitter\\.com", "x.com", url)` porte sur la chaîne ENTIÈRE :
    l'URL réellement ouverte n'est pas celle qui a été validée. Le contrôle
    doit donc être refait sur `normalized`.
    """
    source = (RACINE / "app/scraper/quick_download.py").read_text("utf-8")
    debut = source.index("def _extract_twitter(")
    corps = source[debut:source.index("\ndef ", debut + 10)]
    position_sub = corps.index("normalized = re.sub")
    position_fetch = corps.index("StealthyFetcher.fetch(normalized")
    entre_les_deux = corps[position_sub:position_fetch]
    assert "validate_public_url(normalized)" in entre_les_deux
