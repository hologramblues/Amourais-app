"""
LOT A — BACKENDS DE SCRAPE APIFY TikTok & Twitter/X.

Jumeaux de `tests/test_instagram_apify.py` pour les deux nouveaux modules :
  * `app/scraper/tiktok_apify.py`  (acteur `clockworks~tiktok-scraper`) ;
  * `app/scraper/twitter_apify.py` (acteur `easyapi~twitter-x-video-downloader`,
    un TÉLÉCHARGEUR de vidéo).

Ce module vérifie :
  1. le CONTRAT `ExtractorResult` (forme, `total_seen`, filtrage par
     `known_post_ids`, jamais un vide silencieux) ;
  2. le MAPPING TOLÉRANT des items (champ manquant -> None, jamais d'exception ;
     variantes de clés d'un acteur à l'autre) ;
  3. les ERREURS D'APPEL (402 crédit épuisé, timeout, dataset vide) ->
     `fetch_error` français actionnable ;
  4. l'AIGUILLAGE À CHAUD du pipeline : APIFY_TOKEN posé -> Apify pour TikTok
     ET Twitter, relu à chaque job ; Reddit jamais.

────────────────────────────────────────────────────────────────────────────
AUCUN APPEL RÉSEAU RÉEL (LOI ABSOLUE / consigne n°5)
────────────────────────────────────────────────────────────────────────────
Toutes les réponses Apify sont FABRIQUÉES À LA MAIN contre les schémas
d'entrée/sortie observés sur les pages Apify des deux acteurs (input TikTok :
`profiles`/`resultsPerPage` ; input Twitter : `links` ; sortie Twitter :
`result.medias[].url`, `result.title`, `result.author`) et rejouées par
`httpx.MockTransport`. Ce que ces tests NE PEUVENT PAS prouver, et que seul le
jeton Apify du propriétaire permettra de vérifier : la forme RÉELLE des items
renvoyés par SES acteurs, la facturation, et — pour le téléchargeur Twitter —
son comportement réel sur une URL de PROFIL (il attend des URLs de tweet).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app.scraper import pipeline
from app.scraper import tiktok_apify as tk_mod
from app.scraper import twitter_apify as tw_mod
from app.scraper.base import (
    ExtractOptions,
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)
from conftest import FIXED_NOW

pytestmark = pytest.mark.extractor

_TIKTOK_URL = "https://www.tiktok.com/@samourais"
_TWITTER_URL = "https://x.com/samourais/media"
_JETON = "apify_api_jeton-de-test"
_ACTEUR_TIKTOK = "clockworks~tiktok-scraper"
_ACTEUR_TWITTER = "easyapi~twitter-x-video-downloader"

#: 2023-11-14T22:13:20Z == FIXED_NOW (jamais l'horloge murale — LOI n°5).
_ISO_FIXED_NOW = "2023-11-14T22:13:20.000Z"


# ===========================================================================
# Doublures HTTP + jeton (mêmes principes que test_instagram_apify.py)
# ===========================================================================


def _fabrique_http(monkeypatch, module):
    def _installer(corps=None, *, status=201, brut=None, exception=None):
        etat = SimpleNamespace(requetes=[])

        def _handler(request: httpx.Request) -> httpx.Response:
            etat.requetes.append(request)
            if exception is not None:
                raise exception
            if brut is not None:
                return httpx.Response(status, content=brut)
            return httpx.Response(status, json=corps if corps is not None else [])

        transport = httpx.MockTransport(_handler)
        monkeypatch.setattr(
            module, "_client", lambda: httpx.Client(transport=transport)
        )
        return etat

    return _installer


@pytest.fixture
def tiktok_http(monkeypatch):
    return _fabrique_http(monkeypatch, tk_mod)


@pytest.fixture
def twitter_http(monkeypatch):
    return _fabrique_http(monkeypatch, tw_mod)


@pytest.fixture
def jeton_tiktok(monkeypatch):
    monkeypatch.setattr(
        tk_mod, "get_apify_settings_for", lambda platform: (_JETON, _ACTEUR_TIKTOK)
    )


@pytest.fixture
def jeton_twitter(monkeypatch):
    monkeypatch.setattr(
        tw_mod, "get_apify_settings_for", lambda platform: (_JETON, _ACTEUR_TWITTER)
    )


@pytest.fixture
def extraire_tiktok(jeton_tiktok, tiktok_http):
    def _run(corps=None, *, known_post_ids=None, options=None, **kwargs):
        etat = tiktok_http(corps, **kwargs)
        result = tk_mod.TikTokApifyExtractor().extract(
            _TIKTOK_URL,
            set(known_post_ids or ()),
            options or ExtractOptions(scrape_mode="daily"),
        )
        return result, etat

    return _run


@pytest.fixture
def extraire_twitter(jeton_twitter, twitter_http):
    def _run(corps=None, *, known_post_ids=None, options=None, **kwargs):
        etat = twitter_http(corps, **kwargs)
        result = tw_mod.TwitterApifyExtractor().extract(
            _TWITTER_URL,
            set(known_post_ids or ()),
            options or ExtractOptions(scrape_mode="daily"),
        )
        return result, etat

    return _run


def _nettoyer_env_apify():
    os.environ["APIFY_TOKEN"] = ""
    os.environ["APIFY_ACTOR"] = ""
    os.environ["TIKTOK_ACTOR"] = ""
    os.environ["TWITTER_ACTOR"] = ""


@pytest.fixture
def env_apify_reel(settings_env_file):
    yield settings_env_file
    _nettoyer_env_apify()


def _assert_echec(result: ExtractorResult, *fragments: str):
    assert result.media == []
    assert result.total_seen == 0
    assert result.fetch_error, "l'échec doit être signalé, pas rendu vide"
    for fragment in fragments:
        assert fragment in result.fetch_error, (
            f"message non actionnable : {result.fetch_error!r} "
            f"(fragment attendu : {fragment!r})"
        )


# ===========================================================================
# Charges utiles TikTok fabriquées à la main
# ===========================================================================


def _tiktok_video(pid: str = "7300000000000000001", **surcharges) -> dict:
    post = {
        "id": pid,
        "webVideoUrl": f"https://www.tiktok.com/@samourais/video/{pid}",
        "text": "Kata du matin",
        "createTimeISO": _ISO_FIXED_NOW,
        "diggCount": 1200,
        "playCount": 45000,
        "commentCount": 33,
        "videoMeta": {"width": 1080, "height": 1920, "duration": 15},
        "authorMeta": {
            "name": "samourais",
            "nickName": "Les Samouraïs",
            "fans": 9000,
            "following": 12,
            "video": 210,
            "verified": True,
            "avatar": "https://cdn.tt.invalid/av.jpg",
        },
    }
    post.update(surcharges)
    return post


def _tiktok_variante(pid: str = "7300000000000000002") -> dict:
    """Autre acteur : videoId / videoUrl / desc / createTime unix / likesCount."""
    return {
        "videoId": pid,
        "videoUrl": f"https://cdn.tt.invalid/{pid}.mp4",
        "desc": "Bonus du dojo",
        "createTime": FIXED_NOW - 3600,
        "likesCount": 5,
    }


# ===========================================================================
# TikTok — 1. Contrat + mapping nominal
# ===========================================================================


def test_tiktok_contrat_forme_du_resultat(extraire_tiktok):
    result, etat = extraire_tiktok([_tiktok_video(), _tiktok_variante()])

    assert isinstance(result, ExtractorResult)
    assert isinstance(result.profile_info, ProfileInfo)
    assert result.fetch_error is None
    assert result.total_seen == 2
    assert len(result.media) == 2
    assert len(etat.requetes) == 1

    principal, variante = result.media
    assert isinstance(principal, MediaItemData)
    assert principal.post_id == "7300000000000000001"
    assert principal.media_type == "video"
    assert principal.media_url == (
        "https://www.tiktok.com/@samourais/video/7300000000000000001"
    )
    assert principal.caption == "Kata du matin"
    assert principal.posted_at is not None
    assert principal.posted_at.timestamp() == pytest.approx(FIXED_NOW)
    assert principal.like_count == 1200
    assert principal.view_count == 45000
    assert principal.comment_count == 33
    assert principal.width == 1080 and principal.height == 1920
    assert principal.duration == 15

    assert variante.post_id == "7300000000000000002"
    assert variante.media_type == "video"
    assert variante.media_url == "https://cdn.tt.invalid/7300000000000000002.mp4"
    assert variante.caption == "Bonus du dojo"
    assert variante.posted_at.timestamp() == pytest.approx(FIXED_NOW - 3600)
    assert variante.like_count == 5

    info = result.profile_info
    assert info.display_name == "Les Samouraïs"
    assert info.followers_count == 9000
    assert info.following_count == 12
    assert info.media_count == 210
    assert info.is_verified is True
    assert info.avatar_url == "https://cdn.tt.invalid/av.jpg"


def test_tiktok_input_respecte_le_schema(extraire_tiktok):
    _, etat = extraire_tiktok([_tiktok_video()])

    requete = etat.requetes[0]
    assert requete.method == "POST"
    assert (
        requete.url.path
        == f"/v2/acts/{_ACTEUR_TIKTOK}/run-sync-get-dataset-items"
    )
    assert requete.url.params["token"] == _JETON

    charge = json.loads(requete.content)
    # `profiles` = clé vérifiée de clockworks ; le pseudo est extrait de l'URL.
    assert charge["profiles"] == ["samourais"]
    # Les deux clés de limite sont envoyées ensemble (verifiée + variante).
    assert isinstance(charge["resultsPerPage"], int) and charge["resultsPerPage"] > 0
    assert isinstance(charge["resultsLimit"], int)


def test_tiktok_mode_backfill_demande_plus(extraire_tiktok):
    _, daily = extraire_tiktok(
        [_tiktok_video()], options=ExtractOptions(scrape_mode="daily")
    )
    _, backfill = extraire_tiktok(
        [_tiktok_video()], options=ExtractOptions(scrape_mode="backfill")
    )
    lim_d = json.loads(daily.requetes[0].content)["resultsPerPage"]
    lim_b = json.loads(backfill.requetes[0].content)["resultsPerPage"]
    assert lim_b > lim_d


def test_tiktok_repli_image_covers(extraire_tiktok):
    """Diaporama sans vidéo : on retombe sur la couverture (type image)."""
    result, _ = extraire_tiktok(
        [{"id": "diapo1", "covers": ["https://cdn.tt.invalid/cov.jpg"], "text": "Diapo"}]
    )
    item = result.media[0]
    assert item.media_type == "image"
    assert item.media_url == "https://cdn.tt.invalid/cov.jpg"


def test_tiktok_items_malformes_ignores(extraire_tiktok):
    corps = [
        "parasite",
        7,
        None,
        {},  # aucun id
        {"id": "sansmedia"},  # id mais aucune URL
        _tiktok_video("okvid"),
    ]
    result, _ = extraire_tiktok(corps)
    assert result.fetch_error is None
    assert result.total_seen == 2  # sansmedia (vu) + okvid
    assert [m.post_id for m in result.media] == ["okvid"]


def test_tiktok_optionnels_absents_valent_none(extraire_tiktok):
    result, _ = extraire_tiktok(
        [{"id": "min1", "videoUrl": "https://cdn.tt.invalid/min1.mp4"}]
    )
    item = result.media[0]
    assert item.caption is None
    assert item.posted_at is None
    assert item.like_count is None
    assert item.view_count is None
    assert item.width is None and item.height is None
    assert item.duration is None


def test_tiktok_horodatage_illisible_vaut_none(extraire_tiktok):
    result, _ = extraire_tiktok([_tiktok_video("ts1", createTimeISO="pas-une-date")])
    assert result.media[0].posted_at is None


def test_tiktok_known_post_ids_filtre_mais_compte(extraire_tiktok):
    result, _ = extraire_tiktok(
        [_tiktok_video("connu"), _tiktok_video("neuf")],
        known_post_ids={"connu"},
    )
    assert [m.post_id for m in result.media] == ["neuf"]
    assert result.total_seen == 2


def test_tiktok_duplique_compte_une_fois(extraire_tiktok):
    result, _ = extraire_tiktok([_tiktok_video("dup"), _tiktok_video("dup")])
    assert result.total_seen == 1
    assert len(result.media) == 1


# ===========================================================================
# TikTok — erreurs
# ===========================================================================


def test_tiktok_402_credit_epuise(extraire_tiktok):
    result, _ = extraire_tiktok(status=402, corps={"error": {"type": "platform-limit"}})
    _assert_echec(result, "402", "rédit")


def test_tiktok_404_mentionne_tiktok_actor(extraire_tiktok):
    result, _ = extraire_tiktok(status=404, corps={"error": {}})
    _assert_echec(result, "404", "TIKTOK_ACTOR", _ACTEUR_TIKTOK)


def test_tiktok_timeout_franc(extraire_tiktok):
    result, _ = extraire_tiktok(exception=httpx.ReadTimeout("délai"))
    _assert_echec(result, "120")


def test_tiktok_corps_non_json(extraire_tiktok):
    result, _ = extraire_tiktok(brut=b"<html>maintenance</html>", status=201)
    _assert_echec(result, "JSON")


def test_tiktok_dataset_vide_nest_pas_silencieux(extraire_tiktok):
    result, _ = extraire_tiktok([])
    _assert_echec(result, "aucune vidéo")


def test_tiktok_sans_jeton_echec_sans_appel(tiktok_http, monkeypatch):
    monkeypatch.setattr(
        tk_mod, "get_apify_settings_for", lambda platform: ("", _ACTEUR_TIKTOK)
    )
    etat = tiktok_http([_tiktok_video()])
    result = tk_mod.TikTokApifyExtractor().extract(_TIKTOK_URL, set(), None)
    _assert_echec(result, "APIFY_TOKEN")
    assert etat.requetes == []


# ===========================================================================
# Charges utiles Twitter fabriquées à la main (schéma du téléchargeur)
# ===========================================================================


def _tweet_video(tid: str = "1837626546074325240", **surcharges) -> dict:
    post = {
        "url": f"https://x.com/samourais/status/{tid}",
        "result": {
            "url": f"https://x.com/samourais/status/{tid}",
            "author": "samourais",
            "title": "Notre dernier combat",
            "medias": [
                {
                    "url": "https://cdn.x.invalid/vid.mp4",
                    "extension": "mp4",
                    "duration": 30,
                    "quality": "720p",
                    "width": 1280,
                    "height": 720,
                }
            ],
        },
    }
    post.update(surcharges)
    return post


def _tweet_sans_video(tid: str = "999000111") -> dict:
    return {
        "url": f"https://x.com/samourais/status/{tid}",
        "result": {"author": "samourais", "title": "Juste du texte", "medias": []},
    }


# ===========================================================================
# Twitter — contrat + mapping
# ===========================================================================


def test_twitter_contrat_forme_du_resultat(extraire_twitter):
    result, etat = extraire_twitter([_tweet_video()])

    assert isinstance(result, ExtractorResult)
    assert result.fetch_error is None
    assert result.total_seen == 1
    assert len(result.media) == 1
    assert len(etat.requetes) == 1

    item = result.media[0]
    assert item.post_id == "1837626546074325240"  # extrait de /status/<id>
    assert item.media_type == "video"
    assert item.media_url == "https://cdn.x.invalid/vid.mp4"
    assert item.caption == "Notre dernier combat"
    assert item.post_url == "https://x.com/samourais/status/1837626546074325240"
    assert item.duration == 30
    assert item.width == 1280 and item.height == 720
    assert result.profile_info.display_name == "samourais"


def test_twitter_input_respecte_le_schema(extraire_twitter):
    _, etat = extraire_twitter([_tweet_video()])

    requete = etat.requetes[0]
    assert (
        requete.url.path
        == f"/v2/acts/{_ACTEUR_TWITTER}/run-sync-get-dataset-items"
    )
    charge = json.loads(requete.content)
    # `links` = clé vérifiée du téléchargeur ; variantes envoyées ensemble.
    assert charge["links"] == [_TWITTER_URL]
    assert charge["urls"] == [_TWITTER_URL]
    assert charge["startUrls"] == [{"url": _TWITTER_URL}]


def test_twitter_tweet_sans_video_compte_sans_media(extraire_twitter):
    """Un tweet sans vidéo : VU (total_seen) mais aucun média — pas une erreur."""
    result, _ = extraire_twitter([_tweet_sans_video()])
    assert result.fetch_error is None
    assert result.total_seen == 1
    assert result.media == []


def test_twitter_melange_video_et_sans_video(extraire_twitter):
    result, _ = extraire_twitter([_tweet_video("111"), _tweet_sans_video("222")])
    assert result.total_seen == 2
    assert [m.post_id for m in result.media] == ["111"]


def test_twitter_post_id_repli_sur_segment_url(extraire_twitter):
    """URL de PROFIL (pas de /status) : post_id dérivé du dernier segment.

    Impédance connue du téléchargeur nourri d'une URL de profil : on garde
    quand même un média si l'acteur en renvoie un, avec un post_id STABLE.
    """
    post = {
        "url": "https://x.com/samourais/media",
        "result": {
            "author": "samourais",
            "medias": [{"url": "https://cdn.x.invalid/p.mp4", "extension": "mp4"}],
        },
    }
    result, _ = extraire_twitter([post])
    assert result.total_seen == 1
    assert result.media[0].post_id == "media"
    assert result.media[0].media_url == "https://cdn.x.invalid/p.mp4"


def test_twitter_known_post_ids_filtre_mais_compte(extraire_twitter):
    result, _ = extraire_twitter(
        [_tweet_video("111"), _tweet_video("222")],
        known_post_ids={"111"},
    )
    assert [m.post_id for m in result.media] == ["222"]
    assert result.total_seen == 2


def test_twitter_media_plat_sans_bloc_result(extraire_twitter):
    """Fork « plat » : videoUrl au premier niveau, sans result.medias."""
    post = {
        "url": "https://x.com/samourais/status/555",
        "videoUrl": "https://cdn.x.invalid/plat.mp4",
        "text": "Version plate",
    }
    result, _ = extraire_twitter([post])
    item = result.media[0]
    assert item.post_id == "555"
    assert item.media_url == "https://cdn.x.invalid/plat.mp4"
    assert item.caption == "Version plate"


# ===========================================================================
# Twitter — erreurs
# ===========================================================================


def test_twitter_402_credit_epuise(extraire_twitter):
    result, _ = extraire_twitter(status=402, corps={"error": {}})
    _assert_echec(result, "402", "rédit")


def test_twitter_404_mentionne_twitter_actor(extraire_twitter):
    result, _ = extraire_twitter(status=404, corps={"error": {}})
    _assert_echec(result, "404", "TWITTER_ACTOR", _ACTEUR_TWITTER)


def test_twitter_timeout_franc(extraire_twitter):
    result, _ = extraire_twitter(exception=httpx.ReadTimeout("délai"))
    _assert_echec(result, "120")


def test_twitter_dataset_vide_nest_pas_silencieux(extraire_twitter):
    result, _ = extraire_twitter([])
    _assert_echec(result, "aucun tweet")


def test_twitter_sans_jeton_echec_sans_appel(twitter_http, monkeypatch):
    monkeypatch.setattr(
        tw_mod, "get_apify_settings_for", lambda platform: ("", _ACTEUR_TWITTER)
    )
    etat = twitter_http([_tweet_video()])
    result = tw_mod.TwitterApifyExtractor().extract(_TWITTER_URL, set(), None)
    _assert_echec(result, "APIFY_TOKEN")
    assert etat.requetes == []


# ===========================================================================
# Aiguillage À CHAUD du pipeline (TikTok & Twitter basculent, Reddit non)
# ===========================================================================


def test_aiguillage_tiktok_bascule_a_chaud(env_apify_reel):
    _, backend = pipeline._choisir_extracteur("tiktok")
    assert backend == "navigateur"

    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")
    extracteur, backend = pipeline._choisir_extracteur("tiktok")
    assert backend == "apify"
    assert isinstance(extracteur, tk_mod.TikTokApifyExtractor)
    assert isinstance(extracteur, PlatformExtractor)
    assert extracteur.platform == "tiktok"

    env_apify_reel.write_text("APIFY_TOKEN=\n", encoding="utf-8")
    _, backend = pipeline._choisir_extracteur("tiktok")
    assert backend == "navigateur"


def test_aiguillage_twitter_bascule_a_chaud(env_apify_reel):
    _, backend = pipeline._choisir_extracteur("twitter")
    assert backend == "navigateur"

    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")
    extracteur, backend = pipeline._choisir_extracteur("twitter")
    assert backend == "apify"
    assert isinstance(extracteur, tw_mod.TwitterApifyExtractor)
    assert extracteur.platform == "twitter"

    env_apify_reel.write_text("APIFY_TOKEN=\n", encoding="utf-8")
    _, backend = pipeline._choisir_extracteur("twitter")
    assert backend == "navigateur"


def test_aiguillage_ne_pollue_pas_le_registre(env_apify_reel):
    env_apify_reel.write_text(f"APIFY_TOKEN={_JETON}\n", encoding="utf-8")
    pipeline._choisir_extracteur("tiktok")
    pipeline._choisir_extracteur("twitter")

    assert tk_mod.TikTokApifyExtractor not in pipeline._EXTRACTORS.values()
    assert tw_mod.TwitterApifyExtractor not in pipeline._EXTRACTORS.values()


def test_acteurs_par_defaut_par_plateforme(env_apify_reel):
    env_apify_reel.write_text(
        f"APIFY_TOKEN={_JETON}\nTIKTOK_ACTOR=\nTWITTER_ACTOR=\n", encoding="utf-8"
    )
    from app.config import get_apify_settings_for

    assert get_apify_settings_for("tiktok") == (_JETON, _ACTEUR_TIKTOK)
    assert get_apify_settings_for("twitter") == (_JETON, _ACTEUR_TWITTER)


def test_acteur_personnalise_par_plateforme(env_apify_reel):
    env_apify_reel.write_text(
        f"APIFY_TOKEN={_JETON}\nTIKTOK_ACTOR=moi~mon-tiktok\n", encoding="utf-8"
    )
    from app.config import get_apify_settings_for

    assert get_apify_settings_for("tiktok") == (_JETON, "moi~mon-tiktok")
    # Twitter garde son défaut quand seule la clé TikTok est posée.
    assert get_apify_settings_for("twitter") == (_JETON, _ACTEUR_TWITTER)


# ===========================================================================
# Réglages : la page affiche les champs d'acteur TikTok & Twitter
# ===========================================================================


def test_settings_page_affiche_les_champs_acteurs(client):
    reponse = client.get("/settings")
    try:
        assert reponse.status_code == 200
        page = reponse.data.decode("utf-8")
        assert 'name="TIKTOK_ACTOR"' in page
        assert 'name="TWITTER_ACTOR"' in page
    finally:
        reponse.close()
