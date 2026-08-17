"""
LOT A — BACKEND DE SCRAPE APIFY (`app/scraper/instagram_apify.py`).

Ce module vérifie :
  1. le CONTRAT `ExtractorResult` du nouvel extracteur (mêmes règles que
     tests/test_extracteurs_contrat.py : forme du résultat, `total_seen`,
     filtrage par `known_post_ids`, jamais un vide silencieux) ;
  2. le MAPPING TOLÉRANT des items Apify (les acteurs varient : shortCode|
     code|id, videoUrl|displayUrl, caption|text, timestamp|takenAt, carrousels
     childPosts|sidecarItems — champ manquant -> None, JAMAIS d'exception) ;
  3. les ERREURS D'APPEL (401, 402, 404, timeout, corps illisible, dataset
     vide) -> `fetch_error` avec un message français actionnable ;
  4. l'AIGUILLAGE À CHAUD du pipeline (`_choisir_extracteur`) : APIFY_TOKEN
     posé -> Apify pour Instagram, sinon navigateur ; relu à chaque job.

────────────────────────────────────────────────────────────────────────────
AUCUN APPEL RÉSEAU RÉEL (LOI ABSOLUE n°3 / consigne n°8)
────────────────────────────────────────────────────────────────────────────
Toutes les réponses Apify de ce module sont FABRIQUÉES À LA MAIN contre le
schéma documenté de `run-sync-get-dataset-items` (un tableau JSON d'items) et
rejouées par `httpx.MockTransport` — l'outil que le garde-fou réseau du socle
laisse volontairement passer (le blocage est posé sur `HTTPTransport`).
Ce que ces tests NE PEUVENT PAS prouver, et que seul le jeton Apify du
propriétaire permettra de vérifier : la forme RÉELLE des items renvoyés par
l'acteur choisi, la facturation, et les délais réels de `run-sync`.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest
from loguru import logger

from app.scraper import instagram_apify as apify_mod
from app.scraper import pipeline
from app.scraper.base import (
    ExtractOptions,
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)
from conftest import FIXED_NOW

pytestmark = pytest.mark.extractor

_PROFILE_URL = "https://www.instagram.com/samourais/"
_JETON = "apify_api_jeton-de-test"
_ACTEUR_DEFAUT = "apify~instagram-scraper"

#: 2023-11-14T22:13:20Z == FIXED_NOW — les horodatages ISO des charges utiles
#: dérivent de FIXED_NOW, jamais de l'horloge murale (LOI ABSOLUE n°5).
_ISO_FIXED_NOW = "2023-11-14T22:13:20.000Z"


# ===========================================================================
# Doublure HTTP : httpx.MockTransport injecté via `instagram_apify._client`
# ===========================================================================


@pytest.fixture
def apify_http(monkeypatch):
    """Installe une fausse réponse pour l'appel Apify et capture les requêtes.

    Usage :
        etat = apify_http([{...item...}])                 # dataset nominal
        etat = apify_http(status=401, corps={"error": {}}) # erreur HTTP
        etat = apify_http(exception=httpx.ReadTimeout(...))
    """

    def _installer(corps=None, *, status=201, brut=None, exception=None):
        etat = SimpleNamespace(requetes=[])

        def _handler(request: httpx.Request) -> httpx.Response:
            etat.requetes.append(request)
            if exception is not None:
                raise exception
            if brut is not None:
                return httpx.Response(status, content=brut)
            return httpx.Response(
                status, json=corps if corps is not None else []
            )

        transport = httpx.MockTransport(_handler)
        monkeypatch.setattr(
            apify_mod, "_client", lambda: httpx.Client(transport=transport)
        )
        return etat

    return _installer


@pytest.fixture
def jeton_pose(monkeypatch):
    """Pose le jeton SANS passer par le .env : patch de `get_apify_settings`.

    Les tests d'aiguillage À CHAUD, eux, écrivent le vrai fichier (cf. section
    aiguillage) ; ici on isole le mapping de l'extracteur.
    """
    monkeypatch.setattr(
        apify_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR_DEFAUT)
    )


@pytest.fixture
def extraire(jeton_pose, apify_http):
    """Lance `extract()` sur un dataset Apify fabriqué ; rend (result, etat)."""

    def _run(corps=None, *, known_post_ids=None, options=None, **kwargs):
        etat = apify_http(corps, **kwargs)
        result = apify_mod.InstagramApifyExtractor().extract(
            _PROFILE_URL,
            set(known_post_ids or ()),
            options or ExtractOptions(scrape_mode="daily", max_scrolls=2),
        )
        return result, etat

    return _run


def _nettoyer_env_apify():
    """Rétablit les valeurs forcées par le socle après un `load_dotenv(override=True)`.

    `get_apify_settings` recharge le .env persistant avec `override=True` :
    c'est le comportement voulu en production, mais il MUTE `os.environ` du
    process de test. On revient aux valeurs de `conftest._FORCED_ENV`.
    """
    os.environ["APIFY_TOKEN"] = ""
    os.environ["APIFY_ACTOR"] = ""


@pytest.fixture
def env_apify_reel(settings_env_file):
    """Fichier de réglages RÉEL (DATA_DIR de test) + nettoyage d'os.environ."""
    yield settings_env_file
    _nettoyer_env_apify()


# ===========================================================================
# Charges utiles fabriquées à la main (schéma documenté, aucune capture)
# ===========================================================================


def _post_image(pid: str = "SCimg1", **surcharges) -> dict:
    post = {
        "shortCode": pid,
        "url": f"https://www.instagram.com/p/{pid}/",
        "displayUrl": f"https://cdn.apify.invalid/{pid}.jpg",
        "caption": "Entraînement du dimanche",
        "timestamp": _ISO_FIXED_NOW,
        "likesCount": 42,
        "commentsCount": 7,
        "dimensionsWidth": 1080,
        "dimensionsHeight": 1350,
        "ownerFullName": "Les Samouraïs",
        "ownerUsername": "samourais",
    }
    post.update(surcharges)
    return post


def _post_video_variante(pid: str = "SCvid1") -> dict:
    """Un acteur « variante » : code/text/takenAt/likes au lieu des clés Apify."""
    return {
        "code": pid,
        "video_url": f"https://cdn.apify.invalid/{pid}.mp4",
        "text": "La vidéo du dojo",
        "takenAt": FIXED_NOW - 3600,
        "likes": 10,
        "videoDuration": 12.5,
        "videoViewCount": 900,
    }


def _post_carrousel(pid: str = "SCcar1") -> dict:
    return {
        "shortCode": pid,
        "caption": "Carrousel du stage",
        "timestamp": _ISO_FIXED_NOW,
        "likesCount": 5,
        "childPosts": [
            {"id": f"{pid}-a", "displayUrl": f"https://cdn.apify.invalid/{pid}-a.jpg"},
            {"id": f"{pid}-b", "videoUrl": f"https://cdn.apify.invalid/{pid}-b.mp4"},
            {"id": f"{pid}-c", "displayUrl": f"https://cdn.apify.invalid/{pid}-c.jpg"},
        ],
    }


# ===========================================================================
# 1. Contrat ExtractorResult — cas nominal
# ===========================================================================


def test_contrat_forme_du_resultat(extraire):
    """Même forme de résultat que les 4 extracteurs navigateur."""
    result, etat = extraire([_post_image(), _post_video_variante()])

    assert isinstance(result, ExtractorResult)
    assert isinstance(result.profile_info, ProfileInfo)
    assert isinstance(result.media, list)
    assert result.fetch_error is None
    assert result.total_seen == 2
    assert len(result.media) == 2
    assert len(etat.requetes) == 1, "un seul appel API par extraction"

    image, video = result.media
    assert isinstance(image, MediaItemData)
    assert image.post_id == "SCimg1"
    assert image.media_type == "image"
    assert image.media_url == "https://cdn.apify.invalid/SCimg1.jpg"
    assert image.post_url == "https://www.instagram.com/p/SCimg1/"
    assert image.caption == "Entraînement du dimanche"
    assert image.posted_at is not None
    assert image.posted_at.timestamp() == pytest.approx(FIXED_NOW)
    assert image.like_count == 42
    assert image.comment_count == 7
    assert image.width == 1080 and image.height == 1350

    # Variante d'acteur : code / video_url / text / takenAt / likes.
    assert video.post_id == "SCvid1"
    assert video.media_type == "video"
    assert video.media_url == "https://cdn.apify.invalid/SCvid1.mp4"
    assert video.caption == "La vidéo du dojo"
    assert video.posted_at.timestamp() == pytest.approx(FIXED_NOW - 3600)
    assert video.like_count == 10
    assert video.duration == 12.5
    assert video.view_count == 900
    # `post_url` reconstruit depuis le code quand l'acteur ne le donne pas.
    assert video.post_url == "https://www.instagram.com/p/SCvid1/"

    # Le profil est glané dans les items (owner*).
    assert result.profile_info.display_name == "Les Samouraïs"


def test_lappel_api_respecte_le_schema_documente(extraire):
    """POST /v2/acts/{acteur}/run-sync-get-dataset-items?token=... + JSON."""
    _, etat = extraire([_post_image()])

    requete = etat.requetes[0]
    assert requete.method == "POST"
    assert (
        requete.url.path
        == f"/v2/acts/{_ACTEUR_DEFAUT}/run-sync-get-dataset-items"
    )
    assert requete.url.params["token"] == _JETON

    charge = json.loads(requete.content)
    assert charge["username"] == ["samourais"]
    assert charge["directUrls"] == [_PROFILE_URL]
    assert isinstance(charge["resultsLimit"], int) and charge["resultsLimit"] > 0


def test_le_mode_backfill_demande_plus_de_posts(extraire):
    """`resultsLimit` suit le mode : chaque item Apify est facturé."""
    _, etat_daily = extraire(
        [_post_image()], options=ExtractOptions(scrape_mode="daily")
    )
    _, etat_backfill = extraire(
        [_post_image()], options=ExtractOptions(scrape_mode="backfill")
    )

    limite_daily = json.loads(etat_daily.requetes[0].content)["resultsLimit"]
    limite_backfill = json.loads(etat_backfill.requetes[0].content)["resultsLimit"]
    assert limite_backfill > limite_daily


# ===========================================================================
# 2. Carrousel : plusieurs MediaItemData, un seul post_id
# ===========================================================================


def test_carrousel_childposts(extraire):
    result, _ = extraire([_post_carrousel("SCcar1")])

    assert result.total_seen == 1, "un carrousel est UN post"
    assert len(result.media) == 3
    assert {item.post_id for item in result.media} == {"SCcar1"}
    assert [item.media_type for item in result.media] == ["image", "video", "image"]
    assert len({item.media_url for item in result.media}) == 3
    # Les métadonnées du parent se propagent aux enfants.
    assert all(item.caption == "Carrousel du stage" for item in result.media)
    assert all(item.like_count == 5 for item in result.media)


def test_carrousel_variante_sidecaritems(extraire):
    """`sidecarItems` (autre acteur) est accepté au même titre que childPosts."""
    post = {
        "id": "12345",
        "sidecarItems": [
            {"displayUrl": "https://cdn.apify.invalid/s-a.jpg"},
            {"displayUrl": "https://cdn.apify.invalid/s-b.jpg"},
        ],
    }
    result, _ = extraire([post])

    assert [item.post_id for item in result.media] == ["12345", "12345"]


# ===========================================================================
# 3. Tolérance : champ manquant -> None, JAMAIS d'exception
# ===========================================================================


def test_items_malformes_nempechent_pas_le_reste(extraire):
    """Items non-dict, dict vide, post sans média : ignorés sans lever."""
    corps = [
        "chaîne-parasite",
        42,
        None,
        {},  # aucun identifiant : ignoré
        {"shortCode": "SCsansmedia"},  # identifiant mais aucune URL média
        _post_image("SCok"),
    ]
    result, _ = extraire(corps)

    assert result.fetch_error is None
    # Le post sans média est VU (total_seen) mais ne produit aucun item.
    assert result.total_seen == 2
    assert [item.post_id for item in result.media] == ["SCok"]


def test_champs_optionnels_absents_valent_none(extraire):
    """Un post minimal (id + image) sort avec tous les optionnels à None."""
    result, _ = extraire(
        [{"id": "999", "imageUrl": "https://cdn.apify.invalid/999.jpg"}]
    )

    item = result.media[0]
    assert item.post_id == "999"
    assert item.media_type == "image"
    assert item.caption is None
    assert item.posted_at is None
    assert item.like_count is None
    assert item.comment_count is None
    assert item.width is None and item.height is None
    assert item.duration is None


def test_horodatage_illisible_vaut_none(extraire):
    result, _ = extraire([_post_image("SCts", timestamp="pas-une-date")])

    assert result.media[0].posted_at is None


def test_acteur_profil_avec_latestposts(extraire):
    """Un item « profil » (latestPosts) livre le profil ET ses posts."""
    corps = [{
        "username": "samourais",
        "fullName": "Les Samouraïs",
        "biography": "Dojo de Lyon",
        "verified": True,
        "followersCount": 12345,
        "followsCount": 321,
        "postsCount": 99,
        "profilePicUrlHD": "https://cdn.apify.invalid/avatar.jpg",
        "latestPosts": [_post_image("SCprofil1"), _post_video_variante("SCprofil2")],
    }]
    result, _ = extraire(corps)

    assert result.total_seen == 2
    assert [item.post_id for item in result.media] == ["SCprofil1", "SCprofil2"]
    info = result.profile_info
    assert info.display_name == "Les Samouraïs"
    assert info.biography == "Dojo de Lyon"
    assert info.is_verified is True
    assert info.followers_count == 12345
    assert info.following_count == 321
    assert info.media_count == 99
    assert info.avatar_url == "https://cdn.apify.invalid/avatar.jpg"


# ===========================================================================
# 4. known_post_ids : filtré de `media`, compté dans `total_seen`
# ===========================================================================


def test_post_connu_filtre_mais_compte(extraire):
    result, _ = extraire(
        [_post_image("SCconnu"), _post_image("SCneuf")],
        known_post_ids={"SCconnu"},
    )

    assert [item.post_id for item in result.media] == ["SCneuf"]
    assert result.total_seen == 2


def test_post_duplique_dans_le_dataset_compte_une_fois(extraire):
    result, _ = extraire([_post_image("SCdup"), _post_image("SCdup")])

    assert result.total_seen == 1
    assert len(result.media) == 1


# ===========================================================================
# 5. Erreurs d'appel -> fetch_error actionnable (jamais un vide muet)
# ===========================================================================


def _assert_echec(result: ExtractorResult, *fragments: str):
    assert result.media == []
    assert result.total_seen == 0
    assert result.fetch_error, "l'échec doit être signalé, pas rendu vide"
    for fragment in fragments:
        assert fragment in result.fetch_error, (
            f"message non actionnable : {result.fetch_error!r} "
            f"(fragment attendu : {fragment!r})"
        )


def test_401_jeton_invalide(extraire):
    result, _ = extraire(status=401, corps={"error": {"type": "token-not-found"}})
    _assert_echec(result, "401", "APIFY_TOKEN")


def test_402_credit_epuise(extraire):
    result, _ = extraire(status=402, corps={"error": {"type": "platform-limit"}})
    _assert_echec(result, "402", "rédit")  # « Crédit »


def test_404_acteur_inconnu(extraire):
    result, _ = extraire(status=404, corps={"error": {"type": "record-not-found"}})
    _assert_echec(result, "404", _ACTEUR_DEFAUT, "APIFY_ACTOR")


def test_timeout_franc(extraire):
    result, _ = extraire(exception=httpx.ReadTimeout("délai dépassé"))
    _assert_echec(result, "120")


def test_erreur_reseau(extraire):
    result, _ = extraire(exception=httpx.ConnectError("connexion refusée"))
    _assert_echec(result, "Apify")


def test_corps_non_json(extraire):
    result, _ = extraire(brut=b"<html>maintenance</html>", status=201)
    _assert_echec(result, "JSON")


def test_dataset_vide_nest_pas_silencieux(extraire):
    """Un dataset vide part en `fetch_error` : le pipeline marquera `failed`
    avec une cause lisible, jamais un « empty » indiscernable d'un vrai vide."""
    result, _ = extraire([])
    _assert_echec(result, "aucun post")


def test_item_derreur_dacteur_remonte_sa_description(extraire):
    """Certains acteurs renvoient un item {"error": ...} au lieu d'un 4xx."""
    result, _ = extraire(
        [{"error": "no_items", "errorDescription": "Ce compte est privé."}]
    )
    _assert_echec(result, "Ce compte est privé.")


def test_sans_jeton_echec_explicite_sans_appel(apify_http, monkeypatch):
    """Défense en profondeur : jeton disparu entre l'aiguillage et l'appel."""
    monkeypatch.setattr(apify_mod, "get_apify_settings", lambda: ("", _ACTEUR_DEFAUT))
    etat = apify_http([_post_image()])

    result = apify_mod.InstagramApifyExtractor().extract(_PROFILE_URL, set(), None)

    _assert_echec(result, "APIFY_TOKEN")
    assert etat.requetes == [], "aucun appel API ne doit partir sans jeton"


# ===========================================================================
# 6. Aiguillage À CHAUD dans le pipeline
# ===========================================================================


def test_aiguillage_sans_jeton_reste_navigateur(env_apify_reel):
    extracteur, backend = pipeline._choisir_extracteur("instagram")

    assert backend == "navigateur"
    assert type(extracteur).__name__ == "InstagramExtractor"


def test_aiguillage_a_chaud_suit_le_env_persistant(env_apify_reel):
    """Le jeton est RELU à chaque job — posé puis retiré SANS redémarrage."""
    # 1. Sans jeton : navigateur.
    _, backend = pipeline._choisir_extracteur("instagram")
    assert backend == "navigateur"

    # 2. Le propriétaire colle son jeton dans Réglages : Apify au job suivant.
    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")
    extracteur, backend = pipeline._choisir_extracteur("instagram")
    assert backend == "apify"
    assert isinstance(extracteur, apify_mod.InstagramApifyExtractor)
    assert isinstance(extracteur, PlatformExtractor)
    assert extracteur.platform == "instagram"

    # 3. Jeton retiré (valeur vide) : retour au navigateur, toujours à chaud.
    env_apify_reel.write_text("APIFY_TOKEN=\n", encoding="utf-8")
    _, backend = pipeline._choisir_extracteur("instagram")
    assert backend == "navigateur"


def test_aiguillage_reddit_reste_navigateur_meme_avec_jeton(env_apify_reel):
    """Reddit n'a pas d'acteur Apify : il reste sur le navigateur, jeton ou non.

    (Depuis le lot A TikTok/Twitter, seule Reddit ne bascule jamais ; le
    basculement de TikTok et Twitter est couvert par test_tiktok_twitter_apify.)
    """
    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")

    _, backend = pipeline._choisir_extracteur("reddit")
    assert backend == "navigateur", "Reddit ne doit jamais passer par Apify"


def test_aiguillage_ne_pollue_pas_le_registre(env_apify_reel):
    """`_EXTRACTORS` reste le registre des extracteurs navigateur sous contrat
    (tests/test_extracteurs_contrat.py épingle exactement 4 plateformes)."""
    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")
    pipeline._choisir_extracteur("instagram")

    assert "instagram_apify" not in pipeline._EXTRACTORS
    assert apify_mod.InstagramApifyExtractor not in pipeline._EXTRACTORS.values()


def test_acteur_vide_retombe_sur_le_defaut(env_apify_reel):
    env_apify_reel.write_text(
        f"APIFY_TOKEN={_JETON}\nAPIFY_ACTOR=\n", encoding="utf-8"
    )
    from app.config import get_apify_settings

    token, acteur = get_apify_settings()
    assert token == _JETON
    assert acteur == _ACTEUR_DEFAUT


# ===========================================================================
# 7. Bout en bout : run_scrape_job passe par Apify et le note dans ses logs
# ===========================================================================


def test_pipeline_complet_via_apify(
    factories, db_session, apify_http, monkeypatch, tmp_path
):
    """Un job Instagram avec jeton posé : extraction Apify -> insertion ->
    téléchargement (stub local) -> `completed`, et le backend est loggé."""
    from app.db import MediaItem
    from app.scraper.downloaders import DownloadResult

    # Jeton posé — patché des DEUX côtés (import par valeur, §7.1 AUDIT.md).
    monkeypatch.setattr(
        pipeline, "get_apify_settings", lambda: (_JETON, _ACTEUR_DEFAUT)
    )
    monkeypatch.setattr(
        apify_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR_DEFAUT)
    )
    monkeypatch.setattr(pipeline, "get_proxy_for_platform", lambda platform: "")
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "local")
    monkeypatch.setattr(pipeline, "_now_ts", lambda: FIXED_NOW)

    apify_http([_post_image("SCbout1"), _post_carrousel("SCbout2")])

    def _faux_download(url: str) -> DownloadResult:
        destination = tmp_path / f"m{len(list(tmp_path.iterdir()))}.bin"
        destination.write_bytes(b"octets-de-test")
        return DownloadResult(
            local_path=str(destination),
            file_size=14,
            mime_type="application/octet-stream",
            content_hash="hash",
        )

    monkeypatch.setattr(pipeline, "download_media", _faux_download)

    profil = factories.profile(
        platform="instagram", username="samourais",
        profile_url=_PROFILE_URL, scrape_mode="daily",
    )
    job = factories.scrape_job(profil)

    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        db_session.commit()
        pipeline.run_scrape_job(job.id)
    finally:
        logger.remove(handler_id)
    db_session.expire_all()

    assert job.status == "completed", job.error_message
    assert job.media_found == 4  # 1 image + 3 enfants de carrousel
    post_ids = {
        row[0]
        for row in db_session.query(MediaItem.post_id)
        .filter(MediaItem.profile_id == profil.id)
        .all()
    }
    assert post_ids == {"SCbout1", "SCbout2"}
    # Le job note dans ses logs quel backend a servi (lot A).
    assert any("apify" in m for m in messages), messages


# ===========================================================================
# 8. Réglages : persistance et affichage des deux clés
# ===========================================================================


def test_settings_env_persiste_les_cles_apify(client, settings_env_file):
    reponse = client.post(
        "/api/settings/env",
        data={"APIFY_TOKEN": _JETON, "APIFY_ACTOR": "moi~mon-acteur"},
    )
    try:
        assert reponse.status_code == 200
        contenu = settings_env_file.read_text(encoding="utf-8")
        assert f"APIFY_TOKEN={_JETON}" in contenu
        assert "APIFY_ACTOR=moi~mon-acteur" in contenu
    finally:
        reponse.close()


def test_settings_page_affiche_le_champ_apify_masque(client):
    reponse = client.get("/settings")
    try:
        assert reponse.status_code == 200
        page = reponse.data.decode("utf-8")
        assert 'name="APIFY_TOKEN"' in page
        assert 'name="APIFY_ACTOR"' in page
        # Champ secret masqué, comme IG_ACCESS_TOKEN.
        import re

        champ = re.search(r'<input[^>]*name="APIFY_TOKEN"[^>]*>', page)
        assert champ is not None
        assert 'type="password"' in champ.group(0)
        assert "secret-field" in champ.group(0)
    finally:
        reponse.close()
