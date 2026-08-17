"""Extracteur Instagram via l'API Apify — backend PAYANT, sans navigateur.

Le scrape navigateur d'Instagram est fragile (session morte, rotation des
`doc_id` GraphQL). Ce module offre un second backend : un acteur Apify est
lancé de façon synchrone et son dataset est mappé vers le MÊME contrat que les
extracteurs navigateur (`ExtractorResult` de `app/scraper/base.py`). Le
pipeline choisit le backend À CHAUD selon la présence d'`APIFY_TOKEN`
(cf. `pipeline._choisir_extracteur`) ; le backend navigateur n'est PAS
supprimé.

Appel API (documenté par Apify) :
    POST https://api.apify.com/v2/acts/{ACTEUR}/run-sync-get-dataset-items?token=...
    corps JSON : {"username": [...], "directUrls": [...], "resultsLimit": N}
    réponse    : un TABLEAU JSON d'items du dataset (200/201).

Les deux clés d'entrée (`username` ET `directUrls`) sont envoyées ensemble :
les acteurs du marché n'acceptent pas les mêmes — `apify~instagram-post-scraper`
lit `username`, `apify~instagram-scraper` lit `directUrls` — et chacun ignore
les champs qu'il ne connaît pas.

RÈGLES DE CE MODULE :
  * mapping TOLÉRANT — les acteurs varient ; un champ manquant vaut None,
    JAMAIS une exception ;
  * toute erreur d'appel (401, 402, 404, timeout, réseau, corps illisible,
    dataset vide) finit dans `result.fetch_error` avec un message français
    actionnable — jamais un résultat vide silencieux ;
  * le téléchargement des médias reste `downloaders.py` : les URLs CDN
    renvoyées par Apify expirent comme les autres, rien de nouveau ici.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from loguru import logger

from app.config import get_apify_settings
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

#: Timeout FRANC de l'appel synchrone. Apify coupe lui-même `run-sync` à
#: 300 s ; au-delà de 120 s on préfère un échec net et actionnable.
_TIMEOUT_S = 120.0

_POST_URL_TEMPLATE = "https://www.instagram.com/p/{shortcode}/"

#: Chaque item de dataset est FACTURÉ par Apify. On borne donc la demande :
#: un scrape quotidien n'a pas besoin de 200 posts.
_LIMITE_DAILY = 30
_LIMITE_BACKFILL = 200


def _client() -> httpx.Client:
    """Client HTTP de l'appel Apify — isolé pour être substituable en test."""
    return httpx.Client(timeout=httpx.Timeout(_TIMEOUT_S))


class ApifyQuickError(RuntimeError):
    """Échec d'un quick-download Instagram routé par Apify.

    Porte un message français ACTIONNABLE (jamais un chemin ni une trace) :
    `quick_download` l'affiche tel quel, ce qui respecte la loi « échec Apify
    VISIBLE, jamais un résultat vide silencieux » sans fuiter d'interne.
    """


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
    """`https://www.instagram.com/samourais/` -> `samourais`."""
    try:
        segments = [s for s in urlsplit(profile_url).path.split("/") if s]
    except (TypeError, ValueError):
        return None
    return segments[0] if segments else None


# ---------------------------------------------------------------------------
# Mapping d'un post Apify vers MediaItemData
# ---------------------------------------------------------------------------


def _media_dun_noeud(noeud: dict) -> tuple[str, str] | None:
    """(media_type, media_url) d'un nœud, ou None si aucune URL exploitable.

    SIGNAL DE TYPE (lot B) — le champ FIABLE de `apify~instagram-scraper` :
    chaque item porte `type` ∈ {"Video", "Image", "Sidecar"} et `videoUrl`
    n'apparaît QUE sur les vidéos. On tranche donc :

        media_type = "video"  si  type == "Video"  OU  videoUrl présent
        media_type = "image"  sinon

    C'est bien plus sûr que l'ancien embed, qui devinait à partir de balises.
    `type` peut manquer (acteurs « variante » qui ne livrent que `video_url` /
    `displayUrl`) : le repli sur la présence de `videoUrl` couvre ce cas.
    La vidéo prime sur l'image quand les deux URLs coexistent (un reel porte
    aussi sa vignette `displayUrl`) : c'est la vidéo qu'on stocke.

    Un `Sidecar` (carrousel) n'est jamais mappé ici : `_items_dun_post`
    descend dans ses enfants, et CHAQUE enfant porte SON propre `type` — une
    image et une vidéo peuvent coexister dans le même carrousel.
    """
    type_brut = _texte(noeud.get("type"))
    type_est_video = type_brut is not None and type_brut.strip().lower() == "video"

    video = _texte(_premier(noeud, "videoUrl", "video_url"))
    image = _texte(
        _premier(noeud, "displayUrl", "display_url", "imageUrl", "image_url")
    )

    if type_est_video or video:
        # Vidéo selon le type OU la présence de videoUrl. On préfère l'URL
        # vidéo ; à défaut (type "Video" mais videoUrl absent, acteur partiel)
        # on retombe sur l'image plutôt que de perdre le média.
        url = video or image
        if url:
            return ("video", url)
        return None
    if image:
        return ("image", image)
    return None


def _caption_dun_post(post: dict) -> str | None:
    caption = _premier(post, "caption", "text")
    if isinstance(caption, dict):  # certains acteurs imbriquent {"text": ...}
        caption = caption.get("text")
    return _texte(caption)


def _post_id_dun_post(post: dict) -> str | None:
    """shortCode | code | id — coercé en chaîne. None si tout manque."""
    brut = _premier(post, "shortCode", "code", "id")
    if brut is None:
        return None
    texte = str(brut).strip()
    return texte or None


def _items_dun_post(post: dict) -> tuple[str | None, list[MediaItemData]]:
    """Mappe UN post Apify vers ses MediaItemData.

    Un carrousel (childPosts | sidecarItems) produit PLUSIEURS items portant
    tous le `post_id` du parent — c'est la contrainte de dédup en base
    (profile_id, post_id, media_url) qui distingue les enfants entre eux.
    """
    post_id = _post_id_dun_post(post)
    if post_id is None:
        return None, []

    shortcode = _texte(_premier(post, "shortCode", "code"))
    post_url = _texte(_premier(post, "url", "postUrl", "post_url"))
    if not post_url:
        post_url = _POST_URL_TEMPLATE.format(shortcode=shortcode or post_id)

    caption = _caption_dun_post(post)
    posted_at = _vers_datetime(
        _premier(post, "timestamp", "takenAt", "taken_at", "takenAtTimestamp")
    )
    like_count = _entier(_premier(post, "likesCount", "likes"))
    comment_count = _entier(_premier(post, "commentsCount", "comments"))
    view_count = _entier(
        _premier(post, "videoViewCount", "videoPlayCount", "viewsCount")
    )

    def _fabriquer(noeud: dict) -> MediaItemData | None:
        media = _media_dun_noeud(noeud)
        if media is None:
            return None
        media_type, media_url = media
        dimensions = noeud.get("dimensions")
        if not isinstance(dimensions, dict):
            dimensions = {}
        return MediaItemData(
            post_id=post_id,
            post_url=post_url,
            media_type=media_type,
            media_url=media_url,
            caption=caption,
            posted_at=posted_at,
            width=_entier(
                _premier(noeud, "dimensionsWidth", "width")
                or dimensions.get("width")
            ),
            height=_entier(
                _premier(noeud, "dimensionsHeight", "height")
                or dimensions.get("height")
            ),
            duration=_flottant(_premier(noeud, "videoDuration", "duration")),
            like_count=like_count,
            comment_count=comment_count,
            view_count=view_count if media_type == "video" else None,
        )

    enfants = _premier(post, "childPosts", "sidecarItems")
    items: list[MediaItemData] = []
    urls_vues: set[str] = set()

    sources: list[dict]
    if isinstance(enfants, list) and enfants:
        sources = [enfant for enfant in enfants if isinstance(enfant, dict)]
    else:
        sources = [post]

    for noeud in sources:
        item = _fabriquer(noeud)
        if item is not None and item.media_url not in urls_vues:
            urls_vues.add(item.media_url)
            items.append(item)

    return post_id, items


# ---------------------------------------------------------------------------
# Profil : glané dans les items eux-mêmes (les acteurs « posts » y recopient
# les champs owner*, les acteurs « profil » renvoient un objet avec latestPosts)
# ---------------------------------------------------------------------------


def _absorber_profil(item: dict, profil: ProfileInfo) -> None:
    if profil.display_name is None:
        profil.display_name = _texte(
            _premier(item, "ownerFullName", "fullName", "full_name")
        )
    if profil.avatar_url is None:
        profil.avatar_url = _texte(
            _premier(item, "profilePicUrlHD", "profilePicUrl", "profile_pic_url")
        )
    if profil.biography is None:
        profil.biography = _texte(item.get("biography"))
    if profil.is_verified is None and isinstance(
        _premier(item, "verified", "isVerified"), bool
    ):
        profil.is_verified = _premier(item, "verified", "isVerified")
    if profil.followers_count is None:
        profil.followers_count = _entier(item.get("followersCount"))
    if profil.following_count is None:
        profil.following_count = _entier(
            _premier(item, "followsCount", "followingCount")
        )
    if profil.media_count is None:
        profil.media_count = _entier(item.get("postsCount"))


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
            "APIFY_ACTOR dans Réglages (défaut : apify~instagram-post-scraper)."
        )
    extrait = (corps or "").strip().replace("\n", " ")[:200]
    return (
        f"L'API Apify a répondu {status} au lieu d'un dataset. "
        f"Détail : {extrait or 'aucun corps de réponse'}"
    )


# ---------------------------------------------------------------------------
# Extracteur
# ---------------------------------------------------------------------------


def _appeler_apify(
    token: str, acteur: str, charge: dict
) -> tuple[list | None, str | None]:
    """Lance l'acteur en synchrone et rend (items, None) OU (None, message).

    Le SEUL point d'appel réseau du module — partagé par l'extraction de profil
    et le quick-download d'un post. Toute panne (timeout, réseau, 4xx, corps
    illisible, forme inattendue) revient en message français actionnable ;
    jamais une exception ne s'échappe d'ici, jamais un vide muet.
    """
    url = _API_URL_TEMPLATE.format(acteur=quote(acteur, safe="~"))
    try:
        with _client() as client:
            reponse = client.post(url, params={"token": token}, json=charge)
    except httpx.TimeoutException:
        return None, (
            f"Apify n'a pas répondu en {int(_TIMEOUT_S)} s : l'acteur "
            f"« {acteur} » est peut-être surchargé ou trop lent — réessaie "
            "plus tard, ou choisis un acteur plus rapide dans Réglages."
        )
    except httpx.HTTPError as exc:
        return None, (
            f"Connexion à l'API Apify impossible : {exc}. Vérifie le réseau "
            "sortant du conteneur."
        )

    if not (200 <= reponse.status_code < 300):
        return None, _message_erreur_http(
            reponse.status_code, acteur, reponse.text
        )

    try:
        items = reponse.json()
    except ValueError:
        return None, (
            "Réponse Apify illisible (le corps n'est pas du JSON) — "
            f"acteur « {acteur} », statut {reponse.status_code}."
        )

    if not isinstance(items, list):
        return None, (
            "Réponse Apify inattendue (un tableau d'items était attendu) — "
            f"acteur « {acteur} »."
        )

    return items, None


def extraire_post_unique(url: str) -> list[MediaItemData]:
    """Quick-download d'UN post Instagram via Apify (URL collée : /p/, /reel/, /tv/).

    Bien plus fiable que la page `embed`, et surtout : le `type` de l'item
    (« Video » | « Image » | « Sidecar ») tranche image vs vidéo, exactement
    comme pour le scrape de profil. Un carrousel rend PLUSIEURS MediaItemData,
    chacun avec son propre type.

    Lève `ApifyQuickError` (message actionnable) sur toute panne, y compris un
    dataset sans média exploitable — JAMAIS une liste vide silencieuse.
    """
    token, acteur = get_apify_settings()
    if not token:
        raise ApifyQuickError(
            "APIFY_TOKEN n'est pas configuré — renseigne-le dans Réglages "
            "(section Apify) avant d'utiliser le backend Apify."
        )

    charge = {"directUrls": [url], "resultsLimit": 1}
    logger.info("Apify quick-download: acteur {} pour {}", acteur, url)

    items, erreur = _appeler_apify(token, acteur, charge)
    if erreur is not None:
        raise ApifyQuickError(erreur)

    medias: list[MediaItemData] = []
    descriptions: list[str] = []
    for brut in items:
        if not isinstance(brut, dict):
            continue
        post_id, items_du_post = _items_dun_post(brut)
        if post_id is None:
            description = _texte(_premier(brut, "errorDescription", "error"))
            if description:
                descriptions.append(description)
            continue
        medias.extend(items_du_post)

    if not medias:
        detail = f" Détail de l'acteur : {descriptions[0][:200]}" if descriptions else ""
        raise ApifyQuickError(
            f"L'acteur Apify « {acteur} » n'a renvoyé aucun média pour ce lien "
            "Instagram : post privé, supprimé, ou lien invalide — vérifie le "
            "lien, ou réessaie plus tard." + detail
        )

    return medias


class InstagramApifyExtractor(PlatformExtractor):
    """Backend Instagram par l'API Apify — même contrat que les 4 extracteurs."""

    platform = "instagram"

    def extract(
        self,
        profile_url: str,
        known_post_ids: set[str],
        options: ExtractOptions | None = None,
    ) -> ExtractorResult:
        options = options or ExtractOptions()
        result = ExtractorResult()

        token, acteur = get_apify_settings()
        if not token:
            # Défense en profondeur : l'aiguillage du pipeline ne choisit ce
            # backend QUE si le jeton est posé — mais s'il disparaît entre les
            # deux lectures, l'échec doit rester explicite.
            result.fetch_error = (
                "APIFY_TOKEN n'est pas configuré — renseigne-le dans Réglages "
                "(section Apify) avant d'utiliser le backend Apify."
            )
            return result

        username = _username_depuis_url(profile_url)
        limite = (
            _LIMITE_BACKFILL if options.scrape_mode == "backfill" else _LIMITE_DAILY
        )
        charge = {
            "username": [username] if username else [],
            "directUrls": [profile_url],
            "resultsLimit": limite,
        }

        # INCRÉMENTAL ÉCONOMIQUE (lot B) — le pipeline pose sur les options la
        # date du post le plus récent DÉJÀ en base pour ce profil. On la passe
        # à l'acteur via `onlyPostsNewerThan` (nom exact vérifié dans la doc de
        # apify~instagram-scraper ; formats acceptés : "YYYY-MM-DD" ou ISO
        # complet, UTC). Un run quotidien ne PAIE alors que les posts nouveaux.
        #
        # REPLI SÛR : si l'acteur ne connaît pas ce champ, il l'ignore (les
        # acteurs Apify ignorent les clés inconnues, cf. en-tête de module) et
        # `resultsLimit` + le filtrage `known_post_ids` reprennent la main — le
        # scrape reste correct, seule l'optimisation de coût est inactive. On ne
        # casse JAMAIS le scrape pour une économie.
        borne = getattr(options, "only_posts_newer_than", None)
        if borne:
            charge["onlyPostsNewerThan"] = borne
            logger.info(
                "Apify: fenêtre incrémentale onlyPostsNewerThan={} — seuls les "
                "posts plus récents sont demandés (et facturés) pour @{}",
                borne, username or profile_url,
            )
        else:
            logger.debug(
                "Apify: pas de borne incrémentale (profil neuf ou premier run) "
                "— scrape complet borné par resultsLimit={} pour @{}",
                limite, username or profile_url,
            )

        logger.info(
            "Apify: lancement de l'acteur {} pour @{} (limite {} posts)",
            acteur, username or profile_url, limite,
        )

        items, erreur = _appeler_apify(token, acteur, charge)
        if erreur is not None:
            result.fetch_error = erreur
            return result

        erreurs_acteur: list[str] = []
        posts_traites: set[str] = set()

        def _traiter_post(post: dict) -> None:
            post_id, items_du_post = _items_dun_post(post)
            if post_id is None:
                # Item d'erreur d'acteur ({"error": ..., "errorDescription": ...})
                # ou forme inconnue : on le note, on ne compte rien.
                description = _texte(
                    _premier(post, "errorDescription", "error")
                )
                if description:
                    erreurs_acteur.append(description)
                return
            if post_id in posts_traites:
                return
            posts_traites.add(post_id)
            result.total_seen += 1
            if post_id in known_post_ids:
                return
            result.media.extend(items_du_post)

        for brut in items:
            if not isinstance(brut, dict):
                continue
            try:
                _absorber_profil(brut, result.profile_info)
                latest = brut.get("latestPosts")
                if isinstance(latest, list):
                    # Acteur « profil » : l'item décrit le compte et embarque
                    # ses derniers posts.
                    for sous_post in latest:
                        if isinstance(sous_post, dict):
                            _traiter_post(sous_post)
                else:
                    _traiter_post(brut)
            except Exception as exc:  # noqa: BLE001 — mapping jamais fatal
                logger.debug("Apify: item ignoré (forme inattendue) : {}", exc)

        if result.total_seen == 0:
            # Dataset vide : DEUX causes opposées, à ne surtout pas confondre.
            #
            #  (a) Une borne incrémentale était posée et RIEN de plus récent
            #      n'existe. C'est le SUCCÈS de l'optimisation, pas une panne :
            #      c'est même l'état stable normal d'un compte scrapé deux fois
            #      par jour. Poser `fetch_error` ici ferait échouer le job
            #      CHAQUE jour avec un message faux — « c'est le succès qui
            #      provoque l'échec ». On rend donc un résultat vide SANS
            #      erreur : le pipeline le traduit en `empty`/`completed`.
            #
            #  (b) Aucune borne (profil neuf, premier run) et pourtant zéro
            #      post : là c'est vraiment suspect — profil privé, inexistant,
            #      ou acteur incompatible. `fetch_error` est légitime.
            if borne:
                logger.info(
                    "Apify: aucun post plus récent que {} pour @{} — rien de "
                    "nouveau (l'incrémental a fait son travail).",
                    borne, username or profile_url,
                )
                return result

            detail = f" Détail de l'acteur : {erreurs_acteur[0][:200]}" if erreurs_acteur else ""
            result.fetch_error = (
                f"L'acteur Apify « {acteur} » n'a renvoyé aucun post pour "
                f"@{username or profile_url} : profil privé, inexistant, ou "
                "acteur incompatible — vérifie APIFY_ACTOR dans Réglages."
                + detail
            )
            return result

        logger.info(
            "Apify: {} posts vus, {} médias nouveaux pour @{}",
            result.total_seen, len(result.media), username or profile_url,
        )
        return result
