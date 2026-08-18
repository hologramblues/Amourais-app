"""
MOTEUR DE SANTÉ DE SESSION — tests, sans le moindre octet de réseau.

Ce que ces tests protègent, dans l'ordre d'importance :

1. Une session MORTE ne doit jamais être annoncée vivante. Le cas de référence
   est réel : `data/sessions/instagram.json`, cookies du 10/03/2026, `ds_user_id`
   expiré depuis 68 jours, et une page Instagram qui est un mur de connexion.
   La page réellement servie ce 15/08/2026 est rejouée ici sous forme d'extrait
   (`_PAGE_MUR_DE_CONNEXION`), avec ses vrais marqueurs.
2. « inconnu » n'est jamais maquillé en « connecté ». Sonde en échec, délai
   dépassé, page illisible : le verdict reste « inconnu ».
3. Les deux signaux restent DEUX. Le passif se calcule sans réseau, la sonde
   est injectable, et l'arbitrage entre les deux est explicite.

Aucun test ne lance de navigateur : `sonder()` accepte un `fetcher` injecté.
Le socle (`tests/conftest.py`) coupe de toute façon le réseau et patche
`StealthyFetcher.fetch`.
"""

from __future__ import annotations

import json
import time

import pytest

from app.scraper import session_health as sante
from app.scraper.session_health import (
    ETAT_BLOQUE,
    ETAT_CONNECTE,
    ETAT_DECONNECTE,
    ETAT_INCONNU,
)
from conftest import FIXED_NOW

# ===========================================================================
# Matériel de test
# ===========================================================================

#: Extrait FIDÈLE de la page qu'Instagram a réellement servie aux cookies morts
#: le 15/08/2026 (GET /accounts/edit/ → 302 → https://www.instagram.com/).
#: On y retrouve les deux marqueurs retenus par le classeur — un drapeau d'état
#: et une racine GraphQL — et AUCUN libellé d'interface.
_PAGE_MUR_DE_CONNEXION = """
<!DOCTYPE html><html lang="fr"><head><title>Instagram</title></head><body>
<script type="application/json">
["InstagramSecurityConfig",[],{"csrf_token":"sXNDJJUVRxBo5kf1NWFWDhswKJpbuMvO"},7467],
["PolarisExperimentUtils",[],{"is_logged_out_user":true,"is_logged_out_user_ssr":false,
"use_landing_dialog_v2":true,"landing_dialog_v2_variant":0},7965],
["CAAFetaIGLoginHomepageQueryRelayPreloader_6a80947f21da11916798708",
{"__bbox":{"complete":true,"result":{"data":{"caa_feta_logged_out_homepage_root":
{"renderer":{"__typename":"CAAFetaAYMHMultipleProfileRenderer"}}}}}}]
</script>
<div id="react-root"></div></body></html>
"""

#: Ce que le serveur repose dans ce cas : ni `sessionid`, ni `ds_user_id`.
_COOKIES_MUR = {"datr": "x", "csrftoken": "y", "ig_did": "z", "mid": "m", "rur": "r"}

#: Page authentifiée : le drapeau est à `true` et l'utilisateur est nommé.
_PAGE_AUTHENTIFIEE = (
    '<html><body><script>{"config":{"csrf_token":"a","viewer":'
    '{"id":"17841400000000000"}},"is_logged_in":true,'
    '"logged_in_user":{"username":"yugnat999"}}</script></body></html>'
)


class FausseReponse:
    """Sosie minimal de `scrapling.Response` : ce que la sonde lit, rien d'autre."""

    def __init__(self, url, status=200, html="", cookies=None):
        self.url = url
        self.status = status
        self.html_content = html
        self.cookies = cookies if cookies is not None else {}


def _cookie(nom, valeur="v", expire=None, domaine=".instagram.com"):
    cookie = {"name": nom, "value": valeur, "domain": domaine, "path": "/"}
    if expire is not None:
        cookie["expirationDate"] = expire
    return cookie


@pytest.fixture
def sessions_dir(test_data_dir):
    """Dossier de cookies de la sandbox, vidé avant ET après chaque test."""
    dossier = test_data_dir / "sessions"
    dossier.mkdir(parents=True, exist_ok=True)

    def _purger():
        for fichier in dossier.glob("*.json"):
            fichier.unlink()

    _purger()
    yield dossier
    _purger()


@pytest.fixture
def ecrire_cookies(sessions_dir):
    def _ecrire(plateforme, cookies):
        chemin = sessions_dir / f"{plateforme}.json"
        chemin.write_text(json.dumps(cookies), encoding="utf-8")
        return chemin

    return _ecrire


# ===========================================================================
# 1. Le classeur de page — fonction pure, cœur de la sonde
# ===========================================================================
class TestClasserReponse:
    def test_la_vraie_page_morte_est_classee_deconnectee(self):
        """LE test du lot : la page réellement servie aux cookies morts.

        Ni l'URL de login (Instagram redirige vers `/`, pas vers
        `/accounts/login`), ni un `<input name=password>` (la page est rendue
        par React) ne sont disponibles ici. Ce sont les drapeaux d'état qui
        tranchent — exactement comme sur la vraie sonde.
        """
        etat, message, indices = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/",
            statut_http=200,
            html=_PAGE_MUR_DE_CONNEXION,
            cookies=_COOKIES_MUR,
        )
        assert etat == ETAT_DECONNECTE
        assert "mur de connexion" in message
        assert any("is_logged_out_user" in i for i in indices)
        assert any("ds_user_id" in i for i in indices)

    def test_aucun_libelle_anglais_ne_sert_de_critere(self):
        """Les chaînes d'interface observées ne décident de RIEN.

        « Use another profile » / « Create new account » changent à chaque
        refonte et à chaque langue : une page qui ne contient QU'ELLES ne doit
        pas suffire à conclure.
        """
        etat, _, _ = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/accounts/edit/",
            statut_http=200,
            html="<html><body>Use another profile — Create new account</body></html>",
            cookies={"ds_user_id": "42"},
        )
        assert etat != ETAT_DECONNECTE

    def test_redirection_vers_la_page_de_login(self):
        etat, _, indices = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/accounts/login/?next=/accounts/edit/",
            statut_http=200,
            html="<html></html>",
        )
        assert etat == ETAT_DECONNECTE
        assert any("/accounts/login" in i for i in indices)

    def test_formulaire_de_connexion_rendu(self):
        etat, _, indices = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/accounts/edit/",
            statut_http=200,
            html='<form><input name="username"><input name="password" type="password"></form>',
        )
        assert etat == ETAT_DECONNECTE
        assert any("formulaire" in i for i in indices)

    def test_page_reservee_quittee_sans_autre_indice(self):
        """Indice de protocole : la page de compte n'a pas été servie."""
        etat, _, indices = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/",
            statut_http=200,
            html="<html><body>page quelconque</body></html>",
        )
        assert etat == ETAT_DECONNECTE
        assert any("page réservée quittée" in i for i in indices)

    def test_page_authentifiee(self):
        etat, _, indices = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/accounts/edit/",
            statut_http=200,
            html=_PAGE_AUTHENTIFIEE,
            cookies={"ds_user_id": "17841400000000000", "sessionid": "s"},
        )
        assert etat == ETAT_CONNECTE
        assert any("authentification" in i for i in indices)

    def test_marqueur_authentifie_l_emporte_sur_la_redirection(self):
        """Une page authentifiée qui redirige ailleurs reste « connectée ».

        La règle « page réservée quittée » est délibérément placée APRÈS la
        preuve positive : elle ne doit pas déclasser une session vivante que la
        plateforme aurait redirigée pour une autre raison.
        """
        etat, _, _ = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/fr/accounts/",
            statut_http=200,
            html=_PAGE_AUTHENTIFIEE,
        )
        assert etat == ETAT_CONNECTE

    @pytest.mark.parametrize(
        "url, statut",
        [
            ("https://www.instagram.com/challenge/?next=/", 200),
            ("https://www.instagram.com/accounts/suspended/", 200),
            ("https://www.instagram.com/accounts/edit/", 429),
            ("https://www.instagram.com/accounts/edit/", 403),
        ],
    )
    def test_defi_captcha_et_limitation_donnent_bloque(self, url, statut):
        etat, _, _ = sante.classer_reponse(
            "instagram", url_finale=url, statut_http=statut, html="<html></html>"
        )
        assert etat == ETAT_BLOQUE

    def test_page_vide_donne_inconnu_jamais_connecte(self):
        etat, _, _ = sante.classer_reponse(
            "instagram", url_finale="", statut_http=None, html=""
        )
        assert etat == ETAT_INCONNU

    def test_page_illisible_donne_inconnu(self):
        etat, _, _ = sante.classer_reponse(
            "instagram",
            url_finale="https://www.instagram.com/accounts/edit/",
            statut_http=200,
            html="<html><body>du texte sans le moindre marqueur</body></html>",
        )
        assert etat == ETAT_INCONNU

    def test_plateforme_inconnue(self):
        etat, _, _ = sante.classer_reponse(
            "myspace", url_finale="https://x", statut_http=200, html="<html>"
        )
        assert etat == ETAT_INCONNU


# ===========================================================================
# 2. Signal passif — le fichier de cookies
# ===========================================================================
class TestEtatDesCookies:
    def test_sans_fichier_l_etat_est_inconnu_et_jamais_connecte(self, sessions_dir):
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat == ETAT_INCONNU
        assert etat.etat != ETAT_CONNECTE
        assert etat.geste == sante.GESTE_IMPORTER

    def test_fichier_illisible_donne_inconnu(self, sessions_dir):
        (sessions_dir / "instagram.json").write_text("{pas du json", encoding="utf-8")
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat == ETAT_INCONNU

    def test_cookie_critique_absent_donne_deconnecte(self, ecrire_cookies):
        ecrire_cookies("instagram", [_cookie("csrftoken"), _cookie("mid")])
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat == ETAT_DECONNECTE
        assert "sessionid" in etat.message

    def test_cookie_expire_donne_deconnecte(self, ecrire_cookies):
        """Le cas RÉEL : `ds_user_id` expiré depuis 68 jours.

        Ce n'est pas une estimation : un cookie expiré n'est tout simplement
        pas envoyé par le navigateur, la requête part anonyme.
        """
        ecrire_cookies(
            "instagram",
            [
                _cookie("sessionid", expire=FIXED_NOW + 200 * 86400),
                _cookie("ds_user_id", expire=FIXED_NOW - 68 * 86400),
            ],
        )
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat == ETAT_DECONNECTE
        assert "68 j" in etat.message
        assert etat.urgent is True

    def test_alerte_avant_expiration(self, ecrire_cookies):
        ecrire_cookies(
            "instagram",
            [
                _cookie("sessionid", expire=FIXED_NOW + 3 * 86400),
                _cookie("ds_user_id", expire=FIXED_NOW + 90 * 86400),
            ],
        )
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat != ETAT_DECONNECTE
        assert etat.alerte is not None
        assert "3 j" in etat.alerte

    def test_cookies_sains_ne_donnent_pas_connecte(self, ecrire_cookies):
        """Un fichier bien formé ne prouve RIEN sur ce qu'en pense le serveur.

        C'est exactement le défaut corrigé par ce lot : l'ancien écran
        Réglages affichait « vert » sur la seule existence du fichier.
        """
        ecrire_cookies(
            "instagram",
            [
                _cookie("sessionid", expire=FIXED_NOW + 200 * 86400),
                _cookie("ds_user_id", expire=FIXED_NOW + 200 * 86400),
            ],
        )
        etat = sante.etat_des_cookies("instagram", FIXED_NOW)
        assert etat.etat == ETAT_INCONNU


# ===========================================================================
# 3. Signal passif — l'historique des jobs
# ===========================================================================
def _cookies_sains(ecrire_cookies, plateforme="instagram", *, base=FIXED_NOW):
    """Cookies valides 200 jours après `base`.

    `base` compte : les tests qui comparent à `FIXED_NOW` (novembre 2023) et
    ceux qui utilisent l'horloge murale — inévitable dès qu'on écrit puis relit
    la table `session_health` — ne peuvent pas partager la même échéance.
    """
    return ecrire_cookies(
        plateforme,
        [
            _cookie("sessionid", expire=base + 200 * 86400),
            _cookie("ds_user_id", expire=base + 200 * 86400),
            _cookie("auth_token", expire=base + 200 * 86400),
            _cookie("reddit_session", expire=base + 200 * 86400),
        ],
    )


class TestEtatPassif:
    def test_echecs_repetes_non_atteints_donnent_deconnecte(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        _cookies_sains(ecrire_cookies)
        profil = make_profile(platform="instagram")
        for i in range(sante.FENETRE_JOBS):
            make_scrape_job(
                profil,
                status="failed",
                error_message=(
                    "Aucune donnée reçue de la plateforme (navigateur, proxy, "
                    "session ou blocage) — le profil n'a pas été atteint"
                ),
                created_at=FIXED_NOW - (i + 1) * 3600,
                completed_at=FIXED_NOW - (i + 1) * 3600,
            )
        etat = sante.etat_passif("instagram", FIXED_NOW)
        assert etat.etat == ETAT_DECONNECTE
        assert etat.source == sante.SOURCE_JOBS
        assert etat.geste == sante.GESTE_REIMPORTER

    def test_un_scrape_reussi_donne_connecte(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        _cookies_sains(ecrire_cookies)
        profil = make_profile(platform="instagram")
        make_scrape_job(
            profil,
            status="completed",
            media_found=12,
            created_at=FIXED_NOW - 3600,
            completed_at=FIXED_NOW - 3500,
        )
        etat = sante.etat_passif("instagram", FIXED_NOW)
        assert etat.etat == ETAT_CONNECTE
        assert etat.dernier_succes == FIXED_NOW - 3500
        assert etat.geste is None

    def test_echecs_de_cause_indeterminee_ne_concluent_pas(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        _cookies_sains(ecrire_cookies)
        profil = make_profile(platform="instagram")
        make_scrape_job(
            profil,
            status="failed",
            error_message="Disque plein",
            created_at=FIXED_NOW - 600,
            completed_at=FIXED_NOW - 600,
        )
        etat = sante.etat_passif("instagram", FIXED_NOW)
        assert etat.etat == ETAT_INCONNU

    def test_jobs_trop_anciens_ne_concluent_pas(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        _cookies_sains(ecrire_cookies)
        profil = make_profile(platform="instagram")
        make_scrape_job(
            profil,
            status="completed",
            created_at=FIXED_NOW - 30 * 86400,
            completed_at=FIXED_NOW - 30 * 86400,
        )
        etat = sante.etat_passif("instagram", FIXED_NOW)
        assert etat.etat == ETAT_INCONNU

    def test_les_jobs_d_une_autre_plateforme_sont_ignores(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        _cookies_sains(ecrire_cookies)
        _cookies_sains(ecrire_cookies, "tiktok")
        profil_tiktok = make_profile(platform="tiktok")
        make_scrape_job(
            profil_tiktok,
            status="completed",
            created_at=FIXED_NOW - 600,
            completed_at=FIXED_NOW - 600,
        )
        assert sante.etat_passif("instagram", FIXED_NOW).etat == ETAT_INCONNU
        assert sante.etat_passif("tiktok", FIXED_NOW).etat == ETAT_CONNECTE

    def test_cookie_mort_l_emporte_sur_un_scrape_reussi(
        self, ecrire_cookies, make_profile, make_scrape_job
    ):
        """Preuve déterministe contre déduction : le cookie mort gagne."""
        ecrire_cookies(
            "instagram", [_cookie("sessionid", expire=FIXED_NOW - 10 * 86400)]
        )
        profil = make_profile(platform="instagram")
        make_scrape_job(
            profil,
            status="completed",
            created_at=FIXED_NOW - 600,
            completed_at=FIXED_NOW - 600,
        )
        etat = sante.etat_passif("instagram", FIXED_NOW)
        assert etat.etat == ETAT_DECONNECTE
        assert etat.source == sante.SOURCE_COOKIES

    def test_base_vide_ne_leve_pas(self, sessions_dir):
        for plateforme in sante.PLATEFORMES:
            assert sante.etat_passif(plateforme, FIXED_NOW).etat == ETAT_INCONNU


# ===========================================================================
# 4. Sonde active — avec un navigateur injecté (aucun réseau)
# ===========================================================================
class TestSonde:
    def test_sonde_sur_la_vraie_page_morte(self, ecrire_cookies):
        """Rejeu hors-ligne de la vérification exigée : verdict « déconnecté »."""
        ecrire_cookies(
            "instagram",
            [
                _cookie("sessionid", expire=FIXED_NOW + 200 * 86400),
                _cookie("ds_user_id", expire=FIXED_NOW + 200 * 86400),
            ],
        )
        appels = []

        def faux_fetch(url, cookies, delai, proxy):
            appels.append((url, [c["name"] for c in cookies]))
            return FausseReponse(
                "https://www.instagram.com/",
                200,
                _PAGE_MUR_DE_CONNEXION,
                _COOKIES_MUR,
            )

        etat = sante.sonder("instagram", fetcher=faux_fetch)
        assert etat.etat == ETAT_DECONNECTE
        assert etat.source == sante.SOURCE_SONDE
        assert etat.geste == sante.GESTE_REIMPORTER
        # La sonde vise bien une page RÉSERVÉE, et transmet les cookies stockés.
        assert appels[0][0] == "https://www.instagram.com/accounts/edit/"
        assert "sessionid" in appels[0][1]

    def test_sonde_en_echec_donne_inconnu_pas_connecte(self, ecrire_cookies):
        _cookies_sains(ecrire_cookies)

        def fetch_qui_explose(url, cookies, delai, proxy):
            raise RuntimeError("navigateur absent")

        etat = sante.sonder("instagram", fetcher=fetch_qui_explose)
        assert etat.etat == ETAT_INCONNU
        assert etat.etat != ETAT_CONNECTE
        assert etat.geste == sante.GESTE_RESONDER

    def test_sonde_qui_pend_est_coupee_net(self, ecrire_cookies):
        """Le délai est un MUR D'HORLOGE : la sonde rend la main, point."""
        _cookies_sains(ecrire_cookies)

        def fetch_qui_pend(url, cookies, delai, proxy):
            time.sleep(30)
            return FausseReponse("https://www.instagram.com/accounts/edit/", 200, "")

        debut = time.monotonic()
        etat = sante.sonder(
            "instagram", delai_s=0.2, marge_s=0.3, fetcher=fetch_qui_pend
        )
        duree = time.monotonic() - debut
        assert etat.etat == ETAT_INCONNU
        assert "délai" in etat.message
        assert duree < 5, "la sonde a attendu le faux navigateur"

    def test_sans_cookies_aucun_navigateur_n_est_lance(self, sessions_dir):
        def fetch_interdit(*args, **kwargs):  # pragma: no cover - ne doit pas courir
            raise AssertionError("la sonde a lancé un navigateur sans cookies")

        etat = sante.sonder("instagram", fetcher=fetch_interdit)
        assert etat.etat == ETAT_INCONNU
        assert etat.geste == sante.GESTE_IMPORTER

    def test_verrou_dedie_une_sonde_a_la_fois(self, ecrire_cookies):
        _cookies_sains(ecrire_cookies)
        assert sante.sonde_en_cours() is False
        assert sante._VERROU_SONDE.acquire(blocking=False)
        try:
            assert sante.sonde_en_cours() is True
            with pytest.raises(sante.SondeOccupee):
                sante.sonder_si_libre("instagram")
        finally:
            sante._VERROU_SONDE.release()
        assert sante.sonde_en_cours() is False

    def test_le_verrou_de_sonde_n_est_pas_celui_du_scrape(self):
        """Promesse n°1 du job planifié : la sonde ne prend aucun slot de scrape."""
        from app import scheduler

        assert sante._VERROU_SONDE is not scheduler._scrape_semaphore
        # Une sonde en cours ne consomme AUCUN slot de scrape.
        assert sante._VERROU_SONDE.acquire(blocking=False)
        try:
            assert scheduler._scrape_semaphore.acquire(blocking=False)
            scheduler._scrape_semaphore.release()
        finally:
            sante._VERROU_SONDE.release()


# ===========================================================================
# 5. Persistance et fusion des deux signaux
# ===========================================================================
class TestPersistance:
    def test_le_verdict_est_enregistre_et_relu(self, ecrire_cookies):
        _cookies_sains(ecrire_cookies)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse(
                "https://www.instagram.com/", 200, _PAGE_MUR_DE_CONNEXION, _COOKIES_MUR
            )

        sante.sonder("instagram", fetcher=faux_fetch)
        stocke = sante.lire_etat_enregistre("instagram")
        assert stocke["etat"] == ETAT_DECONNECTE
        assert stocke["derniere_sonde"] is not None
        assert isinstance(stocke["indices"], list) and stocke["indices"]

    def test_deux_sondes_ne_creent_qu_une_ligne(self, ecrire_cookies):
        from app.db import SessionHealth, SessionLocal

        _cookies_sains(ecrire_cookies)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse("https://www.instagram.com/accounts/edit/", 200, "")

        sante.sonder("instagram", fetcher=faux_fetch)
        sante.sonder("instagram", fetcher=faux_fetch)
        db = SessionLocal()
        try:
            assert db.query(SessionHealth).filter_by(platform="instagram").count() == 1
        finally:
            db.close()

    def test_table_vide_l_application_fonctionne(self, sessions_dir):
        assert sante.lire_etat_enregistre("instagram") is None
        etats = sante.etats_de_toutes_les_plateformes(maintenant=FIXED_NOW)
        assert len(etats) == len(sante.PLATEFORMES)
        assert {e["etat"] for e in etats} == {ETAT_INCONNU}

    def test_une_sonde_fraiche_tranche_un_passif_ambigu(self, ecrire_cookies):
        maintenant = int(time.time())
        _cookies_sains(ecrire_cookies, base=maintenant)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse(
                "https://www.instagram.com/", 200, _PAGE_MUR_DE_CONNEXION, _COOKIES_MUR
            )

        assert sante.etat_passif("instagram", maintenant).etat == ETAT_INCONNU
        sante.sonder("instagram", fetcher=faux_fetch)
        assert sante.etat_courant("instagram", maintenant).etat == ETAT_DECONNECTE

    def test_une_sonde_perimee_ne_tranche_plus(self, ecrire_cookies):
        from app.db import SessionHealth, SessionLocal

        maintenant = int(time.time())
        _cookies_sains(ecrire_cookies, base=maintenant)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse(
                "https://www.instagram.com/accounts/edit/", 200, _PAGE_AUTHENTIFIEE
            )

        sante.sonder("instagram", fetcher=faux_fetch)
        assert sante.etat_courant("instagram", maintenant).etat == ETAT_CONNECTE

        db = SessionLocal()
        try:
            ligne = db.query(SessionHealth).filter_by(platform="instagram").one()
            ligne.last_probe_at = maintenant - sante.FRAICHEUR_SONDE_S - 60
            db.commit()
        finally:
            db.close()
        assert sante.etat_courant("instagram", maintenant).etat == ETAT_INCONNU

    def test_un_cookie_mort_l_emporte_sur_une_sonde_fraiche_positive(
        self, ecrire_cookies
    ):
        """Le fichier est déterministe : aucune sonde ne peut le contredire."""
        maintenant = int(time.time())
        _cookies_sains(ecrire_cookies, base=maintenant)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse(
                "https://www.instagram.com/accounts/edit/", 200, _PAGE_AUTHENTIFIEE
            )

        sante.sonder("instagram", fetcher=faux_fetch)
        ecrire_cookies(
            "instagram", [_cookie("sessionid", expire=maintenant - 86400)]
        )
        etat = sante.etat_courant("instagram", maintenant)
        assert etat.etat == ETAT_DECONNECTE
        assert etat.source == sante.SOURCE_COOKIES

    def test_les_deux_signaux_restent_lisibles_separement(self, ecrire_cookies):
        _cookies_sains(ecrire_cookies)

        def faux_fetch(url, cookies, delai, proxy):
            return FausseReponse(
                "https://www.instagram.com/", 200, _PAGE_MUR_DE_CONNEXION, _COOKIES_MUR
            )

        sante.sonder("instagram", fetcher=faux_fetch)
        detail = sante.etats_detailles("instagram")
        assert set(detail["signaux"]) == {"passif", "sonde"}
        assert detail["signaux"]["passif"]["source"] in (
            sante.SOURCE_COOKIES,
            sante.SOURCE_JOBS,
        )
        assert detail["signaux"]["sonde"]["etat"] == ETAT_DECONNECTE

    def test_les_plus_urgents_sont_en_tete(self, ecrire_cookies):
        ecrire_cookies("instagram", [_cookie("csrftoken")])  # sessionid absent
        _cookies_sains(ecrire_cookies, "tiktok")
        etats = sante.etats_de_toutes_les_plateformes(maintenant=FIXED_NOW)
        assert etats[0]["plateforme"] == "instagram"
        assert etats[0]["etat"] == ETAT_DECONNECTE


# ===========================================================================
# 6. API HTTP
# ===========================================================================
class TestApi:
    def test_lecture_de_toutes_les_plateformes(self, client, sessions_dir):
        reponse = client.get("/api/sessions/health")
        assert reponse.status_code == 200
        charge = reponse.get_json()
        assert charge["ok"] is True
        assert len(charge["etats"]) == len(sante.PLATEFORMES)
        assert charge["sonde_en_cours"] is False

    def test_une_session_morte_est_signalee_par_l_api(self, client, ecrire_cookies):
        ecrire_cookies(
            "instagram", [_cookie("sessionid", expire=int(time.time()) - 68 * 86400)]
        )
        charge = client.get("/api/sessions/health").get_json()
        instagram = next(e for e in charge["etats"] if e["plateforme"] == "instagram")
        assert instagram["etat"] == ETAT_DECONNECTE
        assert instagram["urgent"] is True
        assert instagram["geste"]
        assert any(a["plateforme"] == "instagram" for a in charge["alertes"])

    def test_detail_par_plateforme(self, client, sessions_dir):
        charge = client.get("/api/sessions/instagram/health").get_json()
        assert charge["ok"] is True
        assert "signaux" in charge

    def test_plateforme_inconnue_donne_404(self, client):
        assert client.get("/api/sessions/myspace/health").status_code == 404
        assert client.post("/api/sessions/myspace/probe").status_code == 404

    def test_declenchement_de_la_sonde_ne_pend_pas(
        self, client, ecrire_cookies, monkeypatch
    ):
        """La requête HTTP répond tout de suite, la sonde part en arrière-plan."""
        _cookies_sains(ecrire_cookies)
        lancees = []

        def fausse_sonde(plateforme, **kwargs):
            lancees.append(plateforme)
            time.sleep(0.05)
            return sante.EtatSession(plateforme, ETAT_DECONNECTE, "simulée")

        monkeypatch.setattr(sante, "sonder_si_libre", fausse_sonde)

        debut = time.monotonic()
        reponse = client.post("/api/sessions/instagram/probe")
        duree = time.monotonic() - debut

        assert reponse.status_code == 202
        assert reponse.get_json()["lancee"] is True
        assert duree < 2, "l'endpoint a attendu la sonde"
        for _ in range(100):
            if lancees:
                break
            time.sleep(0.02)
        assert lancees == ["instagram"]

    def test_sonde_deja_en_cours_donne_409(self, client, ecrire_cookies):
        _cookies_sains(ecrire_cookies)
        assert sante._VERROU_SONDE.acquire(blocking=False)
        try:
            reponse = client.post("/api/sessions/instagram/probe")
            assert reponse.status_code == 409
            assert reponse.get_json()["lancee"] is False
        finally:
            sante._VERROU_SONDE.release()


# ===========================================================================
# 7. Ordonnanceur et garde-fou DATA_DIR
# ===========================================================================
class TestOrdonnanceur:
    def test_le_job_est_enregistre_toutes_les_6h(self):
        import inspect

        from app import scheduler

        source = inspect.getsource(scheduler.start_scheduler)
        assert "sonder_les_sessions" in source
        assert "hours=6" in source

    def test_le_job_ignore_les_plateformes_sans_cookies(self, sessions_dir, monkeypatch):
        from app import scheduler

        def sonde_interdite(*args, **kwargs):  # pragma: no cover
            raise AssertionError("sonde lancée sans cookies")

        monkeypatch.setattr(sante, "sonder_si_libre", sonde_interdite)
        scheduler.sonder_les_sessions()  # ne doit rien faire, ni lever

    def test_le_job_ne_leve_jamais(self, ecrire_cookies, monkeypatch):
        from app import scheduler

        _cookies_sains(ecrire_cookies)

        def sonde_qui_explose(plateforme, **kwargs):
            raise RuntimeError("navigateur mort")

        monkeypatch.setattr(sante, "sonder_si_libre", sonde_qui_explose)
        scheduler.sonder_les_sessions()  # journalise, ne propage pas


class TestGardeFouDataDir:
    def test_data_dir_explicite_est_expose(self):
        from app import config

        # La suite POSE DATA_DIR avant tout import (conftest, phase 0) : le
        # drapeau doit le refléter, sinon le garde-fou hurlerait à tort ici.
        assert config.DATA_DIR_EXPLICITE is True

    def test_l_avertissement_est_emis_quand_la_variable_manque(self):
        """Le repli silencieux sur `<projet>/data` doit être BRUYANT.

        On rejoue la condition du module plutôt que de le réimporter : un
        second import de `app.config` reconstruirait DATA_DIR et déplacerait la
        base de toute la suite.
        """
        import inspect

        from app import config

        source = inspect.getsource(config)
        assert 'DATA_DIR_EXPLICITE = os.getenv("DATA_DIR") is not None' in source
        assert "if not DATA_DIR_EXPLICITE:" in source
        assert "logger.warning(" in source.split("if not DATA_DIR_EXPLICITE:")[1][:800]
        assert "PRODUCTION" in source.split("if not DATA_DIR_EXPLICITE:")[1][:800]


# ===========================================================================
# Couverture Apify — une session morte ne crie plus quand Apify porte le scrape
# ===========================================================================
#
# Depuis le backend Apify, la session navigateur d'instagram/tiktok/twitter
# n'est plus que le chemin de repli. Une alerte rouge quotidienne pour un repli
# inutilisé apprendrait au propriétaire à ignorer les alertes — la maladie
# exacte que cet écran soigne. L'état reste FACTUEL (« déconnecté »), seule
# l'URGENCE tombe, et le message dit pourquoi.


def _etat_deconnecte(plateforme="instagram"):
    from app.scraper.session_health import ETAT_DECONNECTE, EtatSession

    return EtatSession(
        plateforme=plateforme, etat=ETAT_DECONNECTE,
        message="Cookie expiré : ds_user_id.", source="cookies",
    )


def test_session_morte_couverte_par_apify_nest_plus_urgente(monkeypatch):
    """Jeton posé -> l'état reste 'déconnecté' mais urgent tombe, message annoté."""
    from app.scraper import session_health as sante

    monkeypatch.setattr(sante, "_etat_brut", lambda p, m=None: _etat_deconnecte(p))
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_test")

    etat = sante.etat_courant("instagram")
    assert etat.etat == sante.ETAT_DECONNECTE  # toujours factuel
    assert etat.urgent is False                # mais plus d'alarme
    assert etat.couverte_par_apify is True
    assert "Apify" in etat.message             # et le message dit pourquoi
    assert etat.en_dict()["urgent"] is False


def test_session_morte_sans_apify_reste_urgente(monkeypatch):
    """Sans jeton, rien ne change : la session morte crie, comme avant."""
    from app.scraper import session_health as sante

    monkeypatch.setattr(sante, "_etat_brut", lambda p, m=None: _etat_deconnecte(p))
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    etat = sante.etat_courant("instagram")
    assert etat.urgent is True
    assert etat.couverte_par_apify is False
    assert "Apify" not in etat.message


def test_reddit_reste_urgent_meme_avec_jeton(monkeypatch):
    """Reddit n'a pas d'acteur Apify : sa session reste pleinement significative."""
    from app.scraper import session_health as sante

    monkeypatch.setattr(sante, "_etat_brut", lambda p, m=None: _etat_deconnecte(p))
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_test")

    etat = sante.etat_courant("reddit")
    assert etat.urgent is True
    assert etat.couverte_par_apify is False
