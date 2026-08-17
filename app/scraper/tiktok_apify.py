"""Extracteur TikTok via l'API Apify — backend PAYANT, sans navigateur.

Jumeau de `app/scraper/instagram_apify.py` pour TikTok : un acteur Apify est
lancé de façon synchrone (`run-sync-get-dataset-items`) et son dataset est
mappé vers le MÊME contrat que les extracteurs navigateur (`ExtractorResult`
de `app/scraper/base.py`). Le pipeline choisit le backend À CHAUD selon la
présence d'`APIFY_TOKEN` (cf. `pipeline._choisir_extracteur`) ; le backend
navigateur n'est PAS supprimé.

Acteur par défaut : `clockworks~tiktok-scraper` (cf. `config.TIKTOK_ACTOR`).

Schéma d'ENTRÉE (vérifié sur la page Apify de l'acteur, 2026) :
    `profiles`        — tableau de pseudos / IDs / URLs de profil ;
    `postURLs`        — tableau d'URLs de vidéos (mode « posts ») ;
    `resultsPerPage`  — nombre de vidéos par profil.
Comme `instagram_apify`, on envoie AUSSI la variante `resultsLimit` : les
forks de l'acteur diffèrent et chacun ignore les clés qu'il ne connaît pas.
Ce que SEUL le jeton du propriétaire pourra confirmer : la forme RÉELLE des
items renvoyés par SON acteur, la facturation, et les délais de `run-sync`.

Schéma de SORTIE (mapping TOLÉRANT — champ absent -> None, jamais d'exception) :
    id | videoId                                   -> post_id ;
    webVideoUrl | videoUrl | downloadAddr | playAddr -> media_url (vidéo) ;
    covers | originCover                           -> image de secours ;
    text | desc                                    -> caption ;
    createTimeISO | createTime                     -> horodatage ;
    diggCount | playCount | commentCount           -> stats.

RÈGLES (identiques à instagram_apify) :
  * mapping tolérant — un champ manquant vaut None, JAMAIS une exception ;
  * toute erreur d'appel (401/402/404/timeout/réseau/corps illisible/dataset
    vide) finit dans `result.fetch_error` avec un message français actionnable,
    jamais un résultat vide silencieux ;
  * le téléchargement des médias reste `downloaders.py` : les URLs CDN
    renvoyées par Apify expirent comme les autres.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from app.config import get_apify_settings_for
from app.scraper.base import (
    ExtractOptions,
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_API_URL_TEMPLATE = (
    "https://api.apify.com/v2/acts/{acteur}/run-sync-get-dataset-items"
)

#: Timeout FRANC de l'appel synchrone (cf. instagram_apify) : Apify coupe
#: `run-sync` à 300 s ; au-delà de 120 s on préfère un échec net.
_TIMEOUT_S = 120.0

#: Chaque item de dataset est FACTURÉ par Apify — on borne la demande.
_LIMITE_DAILY = 30
_LIMITE_BACKFILL = 200

_VIDEO_URL_TEMPLATE = "https://www.tiktok.com/@{auteur}/video/{post_id}"


def _client() -> httpx.Client:
    """Client HTTP de l'appel Apify — isolé pour être substituable en test."""
    return httpx.Client(timeout=httpx.Timeout(_TIMEOUT_S))


# ---------------------------------------------------------------------------
# Petites conversions tolérantes (un champ manquant vaut None, jamais KeyError)
# ---------------------------------------------------------------------------


def _premier(noeud: dict, *cles: str) -> Any:
    """Première valeur non-None parmi *cles* — les acteurs varient."""
    for cle in cles:
        valeur = noeud.get(cle)
        if valeur is not None:
            return valeur
    return None


def _texte(valeur: Any) -> str | None:
    if isinstance(valeur, str) and valeur.strip():
        return valeur
    return None


def _entier(valeur: Any) -> int | None:
    try:
        if valeur is None or isinstance(valeur, bool):
            return None
        return int(valeur)
    except (TypeError, ValueError):
        return None


def _flottant(valeur: Any) -> float | None:
    try:
        if valeur is None or isinstance(valeur, bool):
            return None
        return float(valeur)
    except (TypeError, ValueError):
        return None


def _vers_datetime(valeur: Any) -> datetime | None:
    """Horodatage Apify -> datetime UTC. Accepte unix (int/float) et ISO 8601."""
    if valeur is None:
        return None
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        try:
            return datetime.fromtimestamp(float(valeur), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(valeur, str):
        brut = valeur.strip()
        if not brut:
            return None
        # Certains acteurs sérialisent createTime en secondes sous forme de
        # chaîne ("1700000000") : on tente l'unix avant l'ISO.
        if brut.isdigit():
            return _vers_datetime(int(brut))
        try:
            if brut.endswith(("Z", "z")):
                brut = brut[:-1] + "+00:00"
            dt = datetime.fromisoformat(brut)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _username_depuis_url(profile_url: str) -> str | None:
    """`https://www.tiktok.com/@samourais` -> `samourais`."""
    if not isinstance(profile_url, str):
        return None
    match = re.search(r"tiktok\.com/@([^/?#]+)", profile_url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Mapping d'un item TikTok vers MediaItemData
# ---------------------------------------------------------------------------


def _premiere_url_liste(valeur: Any) -> str | None:
    """Premier élément textuel exploitable d'une liste (covers, mediaUrls...)."""
    if isinstance(valeur, list):
        for element in valeur:
            texte = _texte(element)
            if texte:
                return texte
    return _texte(valeur)


def _media_dun_item(item: dict) -> tuple[str, str] | None:
    """(media_type, media_url) d'un item TikTok, ou None si aucune URL.

    TikTok est quasi toujours de la vidéo : on privilégie donc l'URL vidéo
    dans l'ordre fixé par le propriétaire (webVideoUrl d'abord), et l'on ne
    retombe sur l'image (couverture) que pour les rares diaporamas.
    """
    video = _texte(
        _premier(item, "webVideoUrl", "videoUrl", "downloadAddr", "playAddr")
    )
    if not video:
        video = _premiere_url_liste(item.get("mediaUrls"))
    if video:
        return ("video", video)
    image = _premiere_url_liste(_premier(item, "covers", "originCover", "cover"))
    if image:
        return ("image", image)
    return None


def _post_id_dun_item(item: dict) -> str | None:
    """id | videoId — coercé en chaîne. None si tout manque."""
    brut = _premier(item, "id", "videoId")
    if brut is None:
        return None
    texte = str(brut).strip()
    return texte or None


def _auteur_dun_item(item: dict, repli: str | None) -> str | None:
    auteur = item.get("authorMeta")
    if isinstance(auteur, dict):
        nom = _texte(_premier(auteur, "name", "nickName", "uniqueId"))
        if nom:
            return nom
    nom = _texte(_premier(item, "authorName", "author", "uniqueId"))
    return nom or repli


def _item_vers_media(item: dict, username: str | None) -> tuple[str | None, MediaItemData | None]:
    """Mappe UN item TikTok vers (post_id, MediaItemData|None).

    Le post_id est rendu même quand aucun média n'est exploitable : le pipeline
    compte alors le post comme VU (`total_seen`) sans produire d'item — comme
    pour un post Instagram sans média.
    """
    post_id = _post_id_dun_item(item)
    if post_id is None:
        return None, None

    media = _media_dun_item(item)
    if media is None:
        return post_id, None
    media_type, media_url = media

    auteur = _auteur_dun_item(item, username)
    post_url = _texte(_premier(item, "webVideoUrl", "postPage", "url"))
    if not post_url:
        post_url = _VIDEO_URL_TEMPLATE.format(auteur=auteur or "_", post_id=post_id)

    caption = _texte(_premier(item, "text", "desc", "description"))
    posted_at = _vers_datetime(
        _premier(item, "createTimeISO", "createTime", "createTimeUnix")
    )

    video_meta = item.get("videoMeta")
    if not isinstance(video_meta, dict):
        video_meta = {}

    return post_id, MediaItemData(
        post_id=post_id,
        post_url=post_url,
        media_type=media_type,
        media_url=media_url,
        caption=caption,
        posted_at=posted_at,
        width=_entier(_premier(item, "videoWidth", "width") or video_meta.get("width")),
        height=_entier(
            _premier(item, "videoHeight", "height") or video_meta.get("height")
        ),
        duration=_flottant(
            _premier(item, "videoDuration", "duration") or video_meta.get("duration")
        ),
        like_count=_entier(_premier(item, "diggCount", "likesCount", "likes")),
        comment_count=_entier(_premier(item, "commentCount", "commentsCount")),
        view_count=_entier(_premier(item, "playCount", "viewsCount", "playsCount"))
        if media_type == "video"
        else None,
    )


# ---------------------------------------------------------------------------
# Profil : glané dans les items (authorMeta) — comme owner* côté Instagram
# ---------------------------------------------------------------------------


def _absorber_profil(item: dict, profil: ProfileInfo) -> None:
    auteur = item.get("authorMeta")
    if not isinstance(auteur, dict):
        auteur = {}

    if profil.display_name is None:
        profil.display_name = _texte(
            _premier(auteur, "nickName", "name")
            or _premier(item, "authorName")
        )
    if profil.avatar_url is None:
        profil.avatar_url = _texte(
            _premier(auteur, "avatar", "avatarLarger", "avatarMedium")
        )
    if profil.biography is None:
        profil.biography = _texte(_premier(auteur, "signature", "biography"))
    if profil.is_verified is None:
        verifie = _premier(auteur, "verified", "isVerified")
        if isinstance(verifie, bool):
            profil.is_verified = verifie
    if profil.followers_count is None:
        profil.followers_count = _entier(
            _premier(auteur, "fans", "followers", "followerCount")
        )
    if profil.following_count is None:
        profil.following_count = _entier(_premier(auteur, "following", "followingCount"))
    if profil.media_count is None:
        profil.media_count = _entier(_premier(auteur, "video", "videoCount"))


# ---------------------------------------------------------------------------
# Erreurs HTTP -> message français actionnable
# ---------------------------------------------------------------------------


def _message_erreur_http(status: int, acteur: str, corps: str) -> str:
    if status == 401:
        return (
            "Jeton Apify invalide (401) : régénère APIFY_TOKEN sur "
            "console.apify.com (Settings → API tokens) puis colle-le dans "
            "Réglages."
        )
    if status in (402, 429):
        return (
            f"Crédit ou quota Apify épuisé ({status}) : recharge le compte sur "
            "console.apify.com (Billing) ou attends le renouvellement mensuel."
        )
    if status == 403:
        return (
            "Accès refusé par Apify (403) : le jeton APIFY_TOKEN n'a pas le "
            "droit de lancer cet acteur — vérifie le compte et l'acteur dans "
            "Réglages."
        )
    if status == 404:
        return (
            f"Acteur Apify introuvable (404) : « {acteur} ». Vérifie "
            "TIKTOK_ACTOR dans Réglages (défaut : clockworks~tiktok-scraper)."
        )
    extrait = (corps or "").strip().replace("\n", " ")[:200]
    return (
        f"L'API Apify a répondu {status} au lieu d'un dataset. "
        f"Détail : {extrait or 'aucun corps de réponse'}"
    )


# ---------------------------------------------------------------------------
# Extracteur
# ---------------------------------------------------------------------------


class TikTokApifyExtractor(PlatformExtractor):
    """Backend TikTok par l'API Apify — même contrat que les extracteurs."""

    platform = "tiktok"

    def extract(
        self,
        profile_url: str,
        known_post_ids: set[str],
        options: ExtractOptions | None = None,
    ) -> ExtractorResult:
        options = options or ExtractOptions()
        result = ExtractorResult()

        token, acteur = get_apify_settings_for("tiktok")
        if not token:
            # Défense en profondeur : l'aiguillage ne choisit ce backend QUE si
            # le jeton est posé ; s'il disparaît entre les deux lectures,
            # l'échec doit rester explicite.
            result.fetch_error = (
                "APIFY_TOKEN n'est pas configuré — renseigne-le dans Réglages "
                "(section Apify) avant d'utiliser le backend Apify."
            )
            return result

        username = _username_depuis_url(profile_url)
        limite = (
            _LIMITE_BACKFILL if options.scrape_mode == "backfill" else _LIMITE_DAILY
        )
        cible = username or profile_url
        charge = {
            # `profiles` (clé vérifiée de clockworks) accepte pseudos et URLs.
            "profiles": [cible] if cible else [],
            # Les deux clés de limite sont envoyées ensemble : `resultsPerPage`
            # est la clé vérifiée, `resultsLimit` couvre les forks — chacun
            # ignore celle qu'il ne connaît pas.
            "resultsPerPage": limite,
            "resultsLimit": limite,
        }
        url = _API_URL_TEMPLATE.format(acteur=quote(acteur, safe="~"))

        logger.info(
            "Apify: lancement de l'acteur {} pour @{} (limite {} vidéos)",
            acteur, username or profile_url, limite,
        )

        try:
            with _client() as client:
                reponse = client.post(url, params={"token": token}, json=charge)
        except httpx.TimeoutException:
            result.fetch_error = (
                f"Apify n'a pas répondu en {int(_TIMEOUT_S)} s : l'acteur "
                f"« {acteur} » est peut-être surchargé ou trop lent — réessaie "
                "plus tard, ou choisis un acteur plus rapide dans Réglages."
            )
            return result
        except httpx.HTTPError as exc:
            result.fetch_error = (
                f"Connexion à l'API Apify impossible : {exc}. Vérifie le réseau "
                "sortant du conteneur."
            )
            return result

        if not (200 <= reponse.status_code < 300):
            result.fetch_error = _message_erreur_http(
                reponse.status_code, acteur, reponse.text
            )
            return result

        try:
            items = reponse.json()
        except ValueError:
            result.fetch_error = (
                "Réponse Apify illisible (le corps n'est pas du JSON) — "
                f"acteur « {acteur} », statut {reponse.status_code}."
            )
            return result

        if not isinstance(items, list):
            result.fetch_error = (
                "Réponse Apify inattendue (un tableau d'items était attendu) — "
                f"acteur « {acteur} »."
            )
            return result

        erreurs_acteur: list[str] = []
        posts_traites: set[str] = set()

        for brut in items:
            if not isinstance(brut, dict):
                continue
            try:
                _absorber_profil(brut, result.profile_info)
                post_id, media_item = _item_vers_media(brut, username)
                if post_id is None:
                    description = _texte(
                        _premier(brut, "errorDescription", "error")
                    )
                    if description:
                        erreurs_acteur.append(description)
                    continue
                if post_id in posts_traites:
                    continue
                posts_traites.add(post_id)
                result.total_seen += 1
                if post_id in known_post_ids:
                    continue
                if media_item is not None:
                    result.media.append(media_item)
            except Exception as exc:  # noqa: BLE001 — mapping jamais fatal
                logger.debug("Apify: item TikTok ignoré (forme inattendue) : {}", exc)

        if result.total_seen == 0:
            detail = (
                f" Détail de l'acteur : {erreurs_acteur[0][:200]}"
                if erreurs_acteur
                else ""
            )
            result.fetch_error = (
                f"L'acteur Apify « {acteur} » n'a renvoyé aucune vidéo pour "
                f"@{username or profile_url} : profil privé, inexistant, ou "
                "acteur incompatible — vérifie TIKTOK_ACTOR dans Réglages."
                + detail
            )
            return result

        logger.info(
            "Apify: {} vidéos vues, {} médias nouveaux pour @{}",
            result.total_seen, len(result.media), username or profile_url,
        )
        return result
