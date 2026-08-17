"""
LOT B — DÉTECTION IMAGE/VIDÉO ROBUSTE + INCRÉMENTAL ÉCONOMIQUE.

Ce module vérifie, SANS AUCUN APPEL RÉSEAU RÉEL (loi n°5 — les réponses Apify
sont FABRIQUÉES à la main d'après les vrais champs observés dans la console du
propriétaire et rejouées par `httpx.MockTransport`) :

PARTIE 1 — DÉTECTION (le signal fiable d'`apify~instagram-scraper` : chaque item
porte `type` ∈ {"Video","Image","Sidecar"} et `videoUrl` n'existe QUE sur les
vidéos) :
  * image seule -> media_type="image" ;
  * reel/vidéo (type "Video" et/ou videoUrl) -> "video" ;
  * carrousel MIXTE (une image ET une vidéo dans le même Sidecar) -> un
    MediaItemData par enfant, chacun avec SON type ;
  * le `type` tranche même quand la présence d'URL seule serait ambiguë.

  Et pour le QUICK-DOWNLOAD (URL collée) :
  * jeton posé -> route par Apify, le `type` choisit image vs vidéo ;
  * panne Apify -> erreur VISIBLE et actionnable (jamais un vide muet) ;
  * pas de jeton -> chemin embed conservé (Apify jamais appelé).

PARTIE 2 — INCRÉMENTAL (`onlyPostsNewerThan`, nom exact vérifié dans la doc de
l'acteur) :
  * `borne_incrementale` PURE : profil neuf -> None ; profil connu -> date ISO
    du média le plus récent ;
  * le pipeline pose la borne sur les options en mode `daily`, jamais en
    `backfill` ;
  * l'extracteur Apify n'ajoute `onlyPostsNewerThan` à la charge QUE si la
    borne est posée.

CE QUE CES TESTS NE PEUVENT PAS PROUVER (seul le jeton du propriétaire le
pourra, contre de vrais runs facturés) : la forme EXACTE des items renvoyés par
l'acteur, le fait qu'il honore réellement `onlyPostsNewerThan`, et la baisse de
coût effective. Cf. `limites` du compte-rendu.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from loguru import logger

from app.scraper import instagram_apify as apify_mod
from app.scraper import pipeline
from app.scraper import quick_download as qd_mod
from app.scraper.base import ExtractOptions, MediaItemData
from app.scraper.pipeline import borne_incrementale
from conftest import FIXED_NOW

pytestmark = pytest.mark.extractor

_PROFILE_URL = "https://www.instagram.com/samourais/"
_JETON = "apify_api_jeton-de-test"
_ACTEUR = "apify~instagram-scraper"

#: 2023-11-14T22:13:20Z == FIXED_NOW (loi n°5 : jamais l'horloge murale).
_ISO_FIXED_NOW = "2023-11-14T22:13:20Z"


# ===========================================================================
# Doublure HTTP — capture les requêtes, rejoue une réponse fabriquée
# ===========================================================================
@pytest.fixture
def apify_http(monkeypatch):
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
            apify_mod, "_client", lambda: httpx.Client(transport=transport)
        )
        return etat

    return _installer


@pytest.fixture
def jeton_pose(monkeypatch):
    monkeypatch.setattr(
        apify_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR)
    )


def _extraire(options=None, **kwargs):
    return apify_mod.InstagramApifyExtractor().extract(
        _PROFILE_URL, set(), options or ExtractOptions(scrape_mode="daily")
    )


# ===========================================================================
# PARTIE 1 — Détection image / vidéo par le champ `type`
# ===========================================================================
def test_type_image_donne_media_image(jeton_pose, apify_http):
    """`type` == "Image" (le vrai signal de l'acteur) -> media_type image."""
    apify_http([{
        "shortCode": "IMG1", "type": "Image",
        "displayUrl": "https://cdn.apify.invalid/IMG1.jpg",
    }])
    result = _extraire()
    assert [m.media_type for m in result.media] == ["image"]
    assert result.media[0].media_url == "https://cdn.apify.invalid/IMG1.jpg"


def test_type_video_donne_media_video(jeton_pose, apify_http):
    """Un reel : `type` == "Video" ET `videoUrl` présent -> media_type video."""
    apify_http([{
        "shortCode": "VID1", "type": "Video",
        "videoUrl": "https://cdn.apify.invalid/VID1.mp4",
        "displayUrl": "https://cdn.apify.invalid/VID1_thumb.jpg",
    }])
    result = _extraire()
    assert [m.media_type for m in result.media] == ["video"]
    # La vidéo prime sur la vignette : c'est l'URL vidéo qu'on stocke.
    assert result.media[0].media_url == "https://cdn.apify.invalid/VID1.mp4"


def test_carrousel_mixte_image_et_video(jeton_pose, apify_http):
    """Un Sidecar peut contenir une image ET une vidéo : chaque enfant porte
    SON propre `type`, et produit son propre MediaItemData."""
    apify_http([{
        "shortCode": "CAR1", "type": "Sidecar",
        "childPosts": [
            {"id": "CAR1-a", "type": "Image",
             "displayUrl": "https://cdn.apify.invalid/CAR1-a.jpg"},
            {"id": "CAR1-b", "type": "Video",
             "videoUrl": "https://cdn.apify.invalid/CAR1-b.mp4",
             "displayUrl": "https://cdn.apify.invalid/CAR1-b_thumb.jpg"},
        ],
    }])
    result = _extraire()

    assert result.total_seen == 1, "un carrousel reste UN post"
    assert [m.media_type for m in result.media] == ["image", "video"]
    assert {m.post_id for m in result.media} == {"CAR1"}
    urls = [m.media_url for m in result.media]
    assert urls == [
        "https://cdn.apify.invalid/CAR1-a.jpg",
        "https://cdn.apify.invalid/CAR1-b.mp4",
    ]


def test_type_video_sans_videourl_reste_video(jeton_pose, apify_http):
    """`type` == "Video" mais `videoUrl` manquant (acteur partiel) : on garde
    la classification vidéo — le `type` tranche, pas seulement l'URL — et on
    retombe sur l'image plutôt que de PERDRE le média."""
    apify_http([{
        "shortCode": "VID2", "type": "Video",
        "displayUrl": "https://cdn.apify.invalid/VID2_thumb.jpg",
    }])
    result = _extraire()
    assert [m.media_type for m in result.media] == ["video"]
    assert result.media[0].media_url == "https://cdn.apify.invalid/VID2_thumb.jpg"


def test_videourl_sans_type_reste_video(jeton_pose, apify_http):
    """Acteur « variante » sans `type` : la présence de `videoUrl` suffit."""
    apify_http([{
        "code": "VID3",
        "video_url": "https://cdn.apify.invalid/VID3.mp4",
    }])
    result = _extraire()
    assert [m.media_type for m in result.media] == ["video"]


# ===========================================================================
# PARTIE 1 (bis) — Quick-download d'un post collé, routé par Apify
# ===========================================================================
@pytest.fixture
def quick_apify(monkeypatch, apify_http):
    """Jeton posé des DEUX côtés (quick_download ET instagram_apify) + download
    stub — pour tester le routage du quick-download par Apify sans réseau."""
    monkeypatch.setattr(apify_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR))
    monkeypatch.setattr(qd_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR))

    telecharges: list[str] = []

    def _faux_download(url: str):
        telecharges.append(url)
        return qd_mod.DownloadResult(
            local_path=f"/tmp/testscrape/{len(telecharges)}.bin",
            file_size=10, mime_type="application/octet-stream", content_hash="h",
        )

    monkeypatch.setattr(qd_mod, "download_media", _faux_download)
    return SimpleNamespace(apify_http=apify_http, telecharges=telecharges)


def test_quick_download_ig_route_par_apify_type_video(quick_apify):
    """URL /reel/ collée, jeton posé : Apify sert, le `type` donne la vidéo."""
    quick_apify.apify_http([{
        "shortCode": "ABC123", "type": "Video",
        "url": "https://www.instagram.com/reel/ABC123/",
        "videoUrl": "https://cdn.apify.invalid/ABC123.mp4",
    }])
    res = qd_mod.quick_download("https://www.instagram.com/reel/ABC123/")

    assert res.error is None
    assert res.platform == "instagram"
    assert len(res.media_items) == 1
    assert res.media_items[0]["media_type"] == "video"
    assert quick_apify.telecharges == ["https://cdn.apify.invalid/ABC123.mp4"]


def test_quick_download_ig_route_par_apify_type_image(quick_apify):
    """URL /p/ collée, jeton posé : le `type` "Image" donne bien une image."""
    quick_apify.apify_http([{
        "shortCode": "IMGP", "type": "Image",
        "displayUrl": "https://cdn.apify.invalid/IMGP.jpg",
    }])
    res = qd_mod.quick_download("https://www.instagram.com/p/IMGP/")

    assert res.error is None
    assert [mi["media_type"] for mi in res.media_items] == ["image"]


def test_quick_download_ig_panne_apify_est_visible(quick_apify):
    """Loi n°8 : un 401 Apify doit ressortir en ERREUR actionnable, jamais un
    résultat vide silencieux ni un repli muet sur l'embed."""
    quick_apify.apify_http(status=401, corps={"error": {"type": "token"}})
    res = qd_mod.quick_download("https://www.instagram.com/p/BOOM/")

    assert res.media_items == []
    assert res.error is not None
    assert "401" in res.error and "APIFY_TOKEN" in res.error
    assert quick_apify.telecharges == [], "aucun téléchargement sur un échec"


def test_quick_download_ig_dataset_sans_media_est_visible(quick_apify):
    """Dataset renvoyé mais sans média exploitable : erreur claire (post privé /
    supprimé), pas un vide muet."""
    quick_apify.apify_http([{"error": "no_items",
                             "errorDescription": "Ce post est privé."}])
    res = qd_mod.quick_download("https://www.instagram.com/p/PRIVE/")

    assert res.media_items == []
    assert res.error is not None
    assert "Ce post est privé." in res.error


def test_quick_download_ig_sans_jeton_ne_touche_pas_apify(monkeypatch):
    """Pas de jeton : le chemin embed est conservé, Apify n'est jamais appelé."""
    monkeypatch.setattr(qd_mod, "get_apify_settings", lambda: ("", _ACTEUR))

    appels = {"apify": 0, "embed": 0}

    def _apify_interdit(url):
        appels["apify"] += 1
        raise AssertionError("Apify ne doit pas être appelé sans jeton")

    def _faux_embed(post_id, post_url):
        appels["embed"] += 1
        return [MediaItemData(post_id=post_id, post_url=post_url,
                              media_type="image", media_url="https://x.invalid/e.jpg")]

    monkeypatch.setattr(qd_mod, "extraire_post_unique", _apify_interdit)
    monkeypatch.setattr(qd_mod, "_try_instagram_embed", _faux_embed)
    monkeypatch.setattr(qd_mod, "download_media", lambda url: qd_mod.DownloadResult(
        local_path="/tmp/testscrape/e.bin", file_size=1,
        mime_type="image/jpeg", content_hash="h"))

    res = qd_mod.quick_download("https://www.instagram.com/p/EMBED/")

    assert appels == {"apify": 0, "embed": 1}
    assert res.error is None
    assert [mi["media_type"] for mi in res.media_items] == ["image"]


# ===========================================================================
# PARTIE 2 — borne_incrementale (fonction PURE)
# ===========================================================================
def test_borne_profil_neuf_est_none():
    """Aucun média -> pas de borne -> premier run complet."""
    assert borne_incrementale([]) is None
    assert borne_incrementale([None, None]) is None


def test_borne_profil_connu_est_le_media_le_plus_recent():
    """La borne = date ISO (UTC) du posted_at MAXIMAL."""
    plus_recent = FIXED_NOW  # 2023-11-14T22:13:20Z
    posted_ats = [FIXED_NOW - 86400, plus_recent, FIXED_NOW - 3600, None]
    assert borne_incrementale(posted_ats) == _ISO_FIXED_NOW


def test_borne_ignore_les_dates_absentes():
    """Des posted_at None n'empêchent pas de borner sur les médias datés."""
    assert borne_incrementale([None, FIXED_NOW - 3600, None]) is not None


# ===========================================================================
# PARTIE 2 — L'extracteur Apify n'ajoute la borne que si elle est posée
# ===========================================================================
def test_charge_contient_onlypostsnewerthan_si_borne(jeton_pose, apify_http):
    etat = apify_http([{"shortCode": "X", "type": "Image",
                        "displayUrl": "https://cdn.apify.invalid/X.jpg"}])
    _extraire(options=ExtractOptions(
        scrape_mode="daily", only_posts_newer_than=_ISO_FIXED_NOW))

    charge = json.loads(etat.requetes[0].content)
    assert charge["onlyPostsNewerThan"] == _ISO_FIXED_NOW
    assert charge["resultsLimit"] > 0, "resultsLimit reste le garde-fou (repli)"


def test_charge_sans_onlypostsnewerthan_si_pas_de_borne(jeton_pose, apify_http):
    etat = apify_http([{"shortCode": "X", "type": "Image",
                        "displayUrl": "https://cdn.apify.invalid/X.jpg"}])
    _extraire(options=ExtractOptions(scrape_mode="daily"))

    charge = json.loads(etat.requetes[0].content)
    assert "onlyPostsNewerThan" not in charge


# ===========================================================================
# PARTIE 2 — Le pipeline pose la borne (daily) ou non (neuf / backfill)
# ===========================================================================
def _preparer_pipeline_apify(monkeypatch, apify_http, tmp_path):
    monkeypatch.setattr(pipeline, "get_apify_settings", lambda: (_JETON, _ACTEUR))
    monkeypatch.setattr(apify_mod, "get_apify_settings", lambda: (_JETON, _ACTEUR))
    monkeypatch.setattr(pipeline, "get_proxy_for_platform", lambda platform: "")
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "local")
    monkeypatch.setattr(pipeline, "_now_ts", lambda: FIXED_NOW)

    from app.scraper.downloaders import DownloadResult

    def _dl(url: str):
        dest = tmp_path / f"m{len(list(tmp_path.iterdir()))}.bin"
        dest.write_bytes(b"octets")
        return DownloadResult(local_path=str(dest), file_size=6,
                              mime_type="application/octet-stream", content_hash="h")

    monkeypatch.setattr(pipeline, "download_media", _dl)


def test_pipeline_daily_profil_connu_pose_la_borne(
    factories, db_session, apify_http, monkeypatch, tmp_path
):
    """Profil DÉJÀ peuplé, mode daily : la charge Apify porte la borne = date du
    média le plus récent en base (le point de départ)."""
    _preparer_pipeline_apify(monkeypatch, apify_http, tmp_path)
    etat = apify_http([{"shortCode": "NEW", "type": "Image",
                        "displayUrl": "https://cdn.apify.invalid/NEW.jpg"}])

    profil = factories.profile(platform="instagram", username="samourais",
                               profile_url=_PROFILE_URL, scrape_mode="daily")
    # Média le plus récent connu -> point de départ.
    factories.media_item(profil, post_id="OLD1", posted_at=FIXED_NOW - 86400)
    factories.media_item(profil, post_id="OLD2", posted_at=FIXED_NOW)
    job = factories.scrape_job(profil)
    db_session.commit()

    pipeline.run_scrape_job(job.id)

    charge = json.loads(etat.requetes[0].content)
    assert charge["onlyPostsNewerThan"] == _ISO_FIXED_NOW


def test_pipeline_daily_profil_neuf_ne_pose_pas_de_borne(
    factories, db_session, apify_http, monkeypatch, tmp_path
):
    """Profil NEUF (aucun média) : premier run complet, pas de borne."""
    _preparer_pipeline_apify(monkeypatch, apify_http, tmp_path)
    etat = apify_http([{"shortCode": "FIRST", "type": "Image",
                        "displayUrl": "https://cdn.apify.invalid/FIRST.jpg"}])

    profil = factories.profile(platform="instagram", username="neuf",
                               profile_url=_PROFILE_URL, scrape_mode="daily")
    job = factories.scrape_job(profil)
    db_session.commit()

    pipeline.run_scrape_job(job.id)

    charge = json.loads(etat.requetes[0].content)
    assert "onlyPostsNewerThan" not in charge


def test_pipeline_backfill_ne_pose_jamais_de_borne(
    factories, db_session, apify_http, monkeypatch, tmp_path
):
    """En backfill on veut REMONTER le temps : une borne « plus récent que »
    est interdite, même quand des médias existent."""
    _preparer_pipeline_apify(monkeypatch, apify_http, tmp_path)
    etat = apify_http([{"shortCode": "B1", "type": "Image",
                        "displayUrl": "https://cdn.apify.invalid/B1.jpg"}])

    profil = factories.profile(platform="instagram", username="bf",
                               profile_url=_PROFILE_URL, scrape_mode="backfill")
    factories.media_item(profil, post_id="OLD", posted_at=FIXED_NOW)
    job = factories.scrape_job(profil)
    db_session.commit()

    pipeline.run_scrape_job(job.id)

    charge = json.loads(etat.requetes[0].content)
    assert "onlyPostsNewerThan" not in charge


# ===========================================================================
# LE BUG DU SUCCÈS QUI PROVOQUE L'ÉCHEC (régression trouvée par le critic)
# ===========================================================================
#
# Un compte scrapé deux fois par jour atteint vite son état stable : rien de
# nouveau depuis le dernier run. Avec `onlyPostsNewerThan` posé, l'acteur
# renvoie alors un dataset VIDE — c'est le SUCCÈS de l'optimisation. Le garde
# `total_seen == 0` posait pourtant `fetch_error`, et le pipeline marquait le
# job FAILED chaque jour avec un message faux. On distingue donc les deux
# datasets vides : avec borne = rien de nouveau (succès), sans borne = suspect.


def test_dataset_vide_AVEC_borne_est_un_succes_pas_une_erreur(jeton_pose, apify_http):
    """Incrémental à l'état stable : 0 post nouveau ne doit PAS être un échec."""
    apify_http(corps=[])  # l'acteur ne renvoie rien de plus récent
    res = _extraire(
        options=ExtractOptions(
            scrape_mode="daily",
            only_posts_newer_than="2026-08-17",
        )
    )
    assert res.fetch_error is None, (
        "un dataset vide alors qu'une borne incrémentale est posée est un "
        "SUCCÈS (rien de nouveau), pas une panne — sinon le job échoue chaque jour"
    )
    assert res.media == []
    assert res.total_seen == 0


def test_dataset_vide_SANS_borne_reste_une_erreur(jeton_pose, apify_http):
    """Profil neuf / premier run : 0 post est vraiment suspect (privé, inexistant)."""
    apify_http(corps=[])
    res = _extraire(options=ExtractOptions(scrape_mode="daily"))  # pas de borne
    assert res.fetch_error is not None, (
        "sans borne incrémentale, un dataset vide doit rester une erreur "
        "visible — profil privé, inexistant ou acteur incompatible"
    )
    assert res.media == []
