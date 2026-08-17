"""Extracteur Twitter/X via l'API Apify — backend PAYANT, sans navigateur.

Jumeau de `app/scraper/instagram_apify.py` pour Twitter/X, MAIS l'acteur par
défaut (`easyapi~twitter-x-video-downloader`, cf. `config.TWITTER_ACTOR`) est
un TÉLÉCHARGEUR de vidéo, pas un scraper de profil : on lui donne des URLs et
il renvoie, pour chacune, l'URL directe de la vidéo du tweet et son texte.

Conséquence assumée (impédance à DÉCLARER) : le contrat `PlatformExtractor`
fournit une URL de PROFIL (`https://x.com/<user>/media`), or ce téléchargeur
attend des URLs de TWEET. On lui transmet l'URL disponible ; ce que SEUL le
jeton du propriétaire pourra confirmer, c'est le comportement RÉEL de son
acteur sur une URL de profil (nombre d'items renvoyés, présence de vidéos).

Schéma d'ENTRÉE (vérifié sur la page Apify, 2026) :
    `links` — tableau d'URLs de tweets/posts X à traiter.
On envoie AUSSI les variantes `urls` / `startUrls` (forks) : chaque acteur
ignore les clés qu'il ne connaît pas, exactement comme `instagram_apify`.

Schéma de SORTIE (mapping TOLÉRANT — champ absent -> None, jamais d'exception) :
    item.result.medias[].url (ou videoUrl/…)  -> media_url (type « video ») ;
    item.result.title / text                  -> caption ;
    id du tweet (extrait de l'URL /status/<id>) -> post_id ;
    item.result.author                         -> nom de profil.
Un tweet SANS vidéo -> aucun média, mais le post reste COMPTÉ dans
`total_seen` (le pipeline le marquera « empty », pas « failed » : la
plateforme A répondu). Seul un dataset TOTALEMENT vide devient un
`fetch_error` (jamais un vide silencieux — LOI n°8).

RÈGLES (identiques à instagram_apify) :
  * mapping tolérant — un champ manquant vaut None, JAMAIS une exception ;
  * erreurs d'appel (401/402/404/timeout/réseau/corps illisible/dataset vide)
    -> `result.fetch_error` avec un message français actionnable ;
  * le téléchargement des médias reste `downloaders.py`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

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

_TIMEOUT_S = 120.0

#: Extensions considérées comme vidéo dans les médias renvoyés.
_EXT_VIDEO = ("mp4", "mov", "m4v", "webm", "mkv")


def _client() -> httpx.Client:
    """Client HTTP de l'appel Apify — isolé pour être substituable en test."""
    return httpx.Client(timeout=httpx.Timeout(_TIMEOUT_S))


# ---------------------------------------------------------------------------
# Petites conversions tolérantes (un champ manquant vaut None, jamais KeyError)
# ---------------------------------------------------------------------------


def _premier(noeud: dict, *cles: str) -> Any:
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


# ---------------------------------------------------------------------------
# Identité du tweet
# ---------------------------------------------------------------------------


def _tweet_id_depuis_url(url: str | None) -> str | None:
    """`https://x.com/user/status/123` -> `123`."""
    if not isinstance(url, str):
        return None
    match = re.search(r"/status(?:es)?/(\d+)", url)
    return match.group(1) if match else None


def _post_id_dun_item(item: dict, result_bloc: dict, url_soumise: str | None) -> str | None:
    """post_id du tweet, dans l'ordre : champ explicite, puis URL /status/<id>.

    Un downloader nourri d'une URL de PROFIL n'a pas d'id de tweet : on retombe
    alors sur un identifiant STABLE dérivé de l'URL (dernier segment de chemin,
    sinon l'URL entière), pour ne pas perdre un média éventuel ni casser la
    déduplication en base.
    """
    brut = _premier(item, "id", "tweetId", "id_str") or _premier(
        result_bloc, "id", "tweetId", "id_str"
    )
    if brut is not None:
        texte = str(brut).strip()
        if texte:
            return texte

    url = (
        _texte(_premier(result_bloc, "url", "tweetUrl"))
        or _texte(_premier(item, "url", "tweetUrl"))
        or url_soumise
    )
    tweet_id = _tweet_id_depuis_url(url)
    if tweet_id:
        return tweet_id

    # Repli : dernier segment de chemin de l'URL, sinon l'URL nettoyée. Assure
    # un post_id NON vide dès qu'une URL existe (cas d'une URL de profil).
    if url:
        try:
            segments = [s for s in urlsplit(url).path.split("/") if s]
        except (TypeError, ValueError):
            segments = []
        if segments:
            return segments[-1]
        return url.strip() or None
    return None


# ---------------------------------------------------------------------------
# Média : URL vidéo dans item.result.medias[] (ou variantes plates)
# ---------------------------------------------------------------------------


def _url_ressemble_video(url: str) -> bool:
    chemin = urlsplit(url).path.lower()
    return any(chemin.endswith("." + ext) for ext in _EXT_VIDEO) or ".mp4" in url.lower()


def _meilleure_video(medias: Any) -> tuple[str | None, dict]:
    """(url_video, media_dict) : la meilleure entrée vidéo d'une liste `medias`.

    On privilégie une entrée dont l'extension/URL est manifestement vidéo ;
    à défaut, la première entrée porteuse d'une URL (les downloaders listent
    parfois plusieurs qualités).
    """
    if not isinstance(medias, list):
        return None, {}

    repli: tuple[str | None, dict] = (None, {})
    for media in medias:
        if not isinstance(media, dict):
            texte = _texte(media)
            if texte and repli[0] is None:
                repli = (texte, {})
            continue
        url = _texte(_premier(media, "url", "videoUrl", "downloadUrl", "src"))
        if not url:
            continue
        ext = _texte(media.get("extension"))
        if (ext and ext.lower() in _EXT_VIDEO) or _url_ressemble_video(url):
            return url, media
        if repli[0] is None:
            repli = (url, media)
    return repli


def _media_dun_tweet(item: dict, result_bloc: dict) -> tuple[str, str, dict] | None:
    """(media_type, media_url, media_meta) ou None si le tweet n'a pas de vidéo.

    Toujours de type « video » : cet acteur est un téléchargeur de vidéos.
    """
    medias = _premier(result_bloc, "medias", "media") or _premier(
        item, "medias", "media"
    )
    url, media_meta = _meilleure_video(medias)
    if not url:
        url = _texte(
            _premier(result_bloc, "videoUrl", "video", "downloadUrl")
            or _premier(item, "videoUrl", "video", "downloadUrl")
        )
        media_meta = {}
    if not url:
        return None
    return ("video", url, media_meta if isinstance(media_meta, dict) else {})


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
            "TWITTER_ACTOR dans Réglages (défaut : "
            "easyapi~twitter-x-video-downloader)."
        )
    extrait = (corps or "").strip().replace("\n", " ")[:200]
    return (
        f"L'API Apify a répondu {status} au lieu d'un dataset. "
        f"Détail : {extrait or 'aucun corps de réponse'}"
    )


# ---------------------------------------------------------------------------
# Extracteur
# ---------------------------------------------------------------------------


class TwitterApifyExtractor(PlatformExtractor):
    """Backend Twitter/X par l'API Apify — même contrat que les extracteurs."""

    platform = "twitter"

    def extract(
        self,
        profile_url: str,
        known_post_ids: set[str],
        options: ExtractOptions | None = None,
    ) -> ExtractorResult:
        options = options or ExtractOptions()
        result = ExtractorResult()

        token, acteur = get_apify_settings_for("twitter")
        if not token:
            result.fetch_error = (
                "APIFY_TOKEN n'est pas configuré — renseigne-le dans Réglages "
                "(section Apify) avant d'utiliser le backend Apify."
            )
            return result

        # Ce downloader attend des URLs ; on lui passe l'URL disponible. Les
        # trois clés d'entrée sont envoyées ensemble (l'acteur ignore celles
        # qu'il ne connaît pas), comme instagram_apify pour username/directUrls.
        charge = {
            "links": [profile_url],
            "urls": [profile_url],
            "startUrls": [{"url": profile_url}],
        }
        url = _API_URL_TEMPLATE.format(acteur=quote(acteur, safe="~"))

        logger.info(
            "Apify: lancement du téléchargeur {} pour {}", acteur, profile_url
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
                self._traiter_tweet(
                    brut, profile_url, known_post_ids, result,
                    posts_traites, erreurs_acteur,
                )
            except Exception as exc:  # noqa: BLE001 — mapping jamais fatal
                logger.debug("Apify: item Twitter ignoré (forme inattendue) : {}", exc)

        if result.total_seen == 0:
            detail = (
                f" Détail de l'acteur : {erreurs_acteur[0][:200]}"
                if erreurs_acteur
                else ""
            )
            result.fetch_error = (
                f"L'acteur Apify « {acteur} » n'a renvoyé aucun tweet pour "
                f"{profile_url} : compte privé, URL invalide, ou acteur "
                "incompatible — vérifie TWITTER_ACTOR dans Réglages." + detail
            )
            return result

        logger.info(
            "Apify: {} tweets vus, {} vidéos nouvelles pour {}",
            result.total_seen, len(result.media), profile_url,
        )
        return result

    @staticmethod
    def _traiter_tweet(
        brut: dict,
        url_soumise: str,
        known_post_ids: set[str],
        result: ExtractorResult,
        posts_traites: set[str],
        erreurs_acteur: list[str],
    ) -> None:
        result_bloc = brut.get("result")
        if not isinstance(result_bloc, dict):
            result_bloc = {}

        # Erreur explicite du downloader sur ce lien : notée, jamais fatale.
        if result_bloc.get("error") is True or brut.get("error") is True:
            description = _texte(
                _premier(result_bloc, "message", "errorDescription")
                or _premier(brut, "message", "errorDescription", "error")
            )
            if description:
                erreurs_acteur.append(description)

        post_id = _post_id_dun_item(brut, result_bloc, url_soumise)
        if post_id is None:
            description = _texte(_premier(brut, "errorDescription", "error"))
            if description:
                erreurs_acteur.append(description)
            return
        if post_id in posts_traites:
            return
        posts_traites.add(post_id)
        result.total_seen += 1

        # Profil glané dans le bloc résultat (author).
        info = result.profile_info
        if info.display_name is None:
            info.display_name = _texte(
                _premier(result_bloc, "author", "authorName", "name")
                or _premier(brut, "author", "authorName")
            )

        if post_id in known_post_ids:
            return

        media = _media_dun_tweet(brut, result_bloc)
        if media is None:
            # Tweet sans vidéo : COMPTÉ mais aucun média — pas une erreur.
            return
        media_type, media_url, media_meta = media

        post_url = (
            _texte(_premier(result_bloc, "url", "tweetUrl"))
            or _texte(_premier(brut, "url", "tweetUrl"))
            or url_soumise
        )
        caption = _texte(
            _premier(result_bloc, "title", "text", "description")
            or _premier(brut, "title", "text")
        )
        posted_at = _vers_datetime(
            _premier(result_bloc, "createdAt", "date", "timestamp")
            or _premier(brut, "createdAt", "date")
        )

        result.media.append(
            MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type=media_type,
                media_url=media_url,
                caption=caption,
                posted_at=posted_at,
                width=_entier(media_meta.get("width")),
                height=_entier(media_meta.get("height")),
                duration=_flottant(_premier(media_meta, "duration", "durationMs")),
                like_count=_entier(
                    _premier(result_bloc, "likeCount", "likes", "favoriteCount")
                ),
                comment_count=_entier(
                    _premier(result_bloc, "replyCount", "commentCount")
                ),
                view_count=_entier(_premier(result_bloc, "viewCount", "views")),
            )
        )
