"""
CONTRAT DES EXTRACTEURS — vague 0 (§7.4 T2 / T21 d'AUDIT.md).

Ce module ne teste pas « Instagram », « TikTok », « Twitter » et « Reddit » comme
quatre sujets indépendants : il teste **le contrat commun** que les quatre classes
enregistrées dans `pipeline._EXTRACTORS` doivent respecter. La forme paramétrée
est volontaire — c'est elle qui rend le contrat exécutable et qui fera échouer
toute cinquième plateforme non conforme le jour où elle sera ajoutée
(cf. `test_le_registre_ne_contient_que_les_plateformes_sous_contrat`).

────────────────────────────────────────────────────────────────────────────
AUCUN RÉSEAU, AUCUN NAVIGATEUR (LOI ABSOLUE n°3)
────────────────────────────────────────────────────────────────────────────
Les 4 extracteurs font `from scrapling.fetchers import StealthyFetcher` (import
PAR VALEUR, §7.1 AUDIT.md) puis appellent `StealthyFetcher.fetch(url, ...)`.
On substitue donc le **nom dans le module consommateur** — jamais la classe
réelle — par un `_FakeFetcher` qui :
  * exécute le `page_action` sur une fausse `page` Playwright,
  * rejoue des réponses HTTP fabriquées dans le listener `page.on("response")`,
  * renvoie un faux adaptor Scrapling (`.css()` / `.html` / `.get_all_text()`).
Toutes les charges utiles JSON sont écrites à la main ci-dessous : aucune n'est
une capture réseau réelle.

Le garde-fou `_guard_no_network` du socle reste actif : si un jour un extracteur
contourne `StealthyFetcher`, ces tests lèveront `NetworkAccessAttempted` au lieu
de passer au vert en silence.

────────────────────────────────────────────────────────────────────────────
DÉTERMINISME (LOI ABSOLUE n°5)
────────────────────────────────────────────────────────────────────────────
Les 4 extracteurs calculent `cutoff_ts = time.time() - 2 ans` au niveau module.
La fixture `_horloge_figee` remplace l'attribut `time` de chacun des 4 modules
par un stub rendant `FIXED_NOW` : aucune assertion ne dépend de l'horloge murale,
et `page.wait_for_timeout()` est un no-op (aucune attente réelle).

────────────────────────────────────────────────────────────────────────────
BUGS GARDÉS PAR UN xfail(strict=True) (LOI ABSOLUE n°4)
────────────────────────────────────────────────────────────────────────────
risque #5  (lot 1.5)  — `total_seen` non incrémenté par tiktok/twitter/reddit
risque #53 (lot 1.4b) — échec total de fetch → résultat vide silencieux
risque #23 (lot ~1.5) — tiktok/reddit s'arrêtent au 1er post connu
risque #45 (lot 1.5b) — Instagram : deux espaces de `post_id`
risque #30 (lot 1.7)  — Instagram : captions GraphQL perdues (précédence)
§3 l.157              — `backfill_from/to` lus par twitter.py seul
§3 l.155 + pipeline.py:198 — Instagram jette followers/biography/media_count
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.scraper import instagram as ig_mod
from app.scraper import reddit as rd_mod
from app.scraper import tiktok as tk_mod
from app.scraper import twitter as tw_mod
from app.scraper.base import (
    ExtractOptions,
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)
from conftest import FIXED_NOW

pytestmark = pytest.mark.extractor


# ===========================================================================
# Doublures : page Playwright, adaptor Scrapling, fetcher
# ===========================================================================


class _Nodes(list):
    """Liste de nœuds façon Scrapling : expose `.first` (None si vide)."""

    @property
    def first(self):
        return self[0] if self else None


class _Element:
    """Élément DOM minimal : `.text`, `.attrib`, `.css()`."""

    def __init__(self, text: str = "", attrib: dict | None = None,
                 children: dict[str, list] | None = None):
        self.text = text
        self.attrib = dict(attrib or {})
        self._children = children or {}

    def css(self, selector: str) -> _Nodes:
        return _Nodes(self._children.get(selector, []))


class _FakeAdaptor:
    """Faux adaptor Scrapling : ne connaît que les sélecteurs qu'on lui donne."""

    def __init__(self, selectors: dict[str, list] | None = None,
                 html: str = "<html><body></body></html>", text: str = ""):
        self._selectors = selectors or {}
        self.html = html
        self._text = text
        self.queried: list[str] = []

    def css(self, selector: str) -> _Nodes:
        self.queried.append(selector)
        return _Nodes(self._selectors.get(selector, []))

    def get_all_text(self) -> str:
        return self._text


class _FakeResponse:
    """Réponse Playwright interceptée par `page.on("response", ...)`."""

    def __init__(self, url: str, body: Any):
        self.url = url
        self._body = body

    def json(self) -> Any:
        return self._body


class _FakePage:
    """Page Playwright minimale.

    `wait_for_timeout` est un no-op : aucune attente réelle (LOI ABSOLUE n°5).
    `locator` lève — les 4 extracteurs enveloppent chaque usage dans un
    `try/except Exception`, ce que ce test verrouille au passage.
    """

    def __init__(self, url: str, responses: list[_FakeResponse]):
        self.url = url
        self._responses = responses
        self.context = SimpleNamespace(add_cookies=lambda cookies: None)
        self.scripts: list[str] = []
        self.waited_ms = 0

    def on(self, event: str, handler: Callable) -> None:
        if event != "response":
            return
        for response in self._responses:
            handler(response)

    def evaluate(self, script: str):
        self.scripts.append(script)
        return 0  # hauteur constante → l'heuristique de stall se déclenche

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waited_ms += int(milliseconds)

    def goto(self, url: str, **kwargs) -> None:
        self.url = url

    def reload(self, **kwargs) -> None:
        return None

    def locator(self, selector: str):
        raise LookupError(f"aucun élément pour {selector!r}")


class _FakeFetcher:
    """Remplace `StealthyFetcher` dans le module d'un extracteur."""

    def __init__(self, adaptor: _FakeAdaptor,
                 responses: list[_FakeResponse] | None = None,
                 error: BaseException | None = None):
        self.adaptor = adaptor
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        page_action = kwargs.get("page_action")
        if page_action is not None:
            page_action(_FakePage(url, self.responses))
        return self.adaptor


@dataclass
class _Payload:
    """Ce qu'une plateforme « renvoie » : un DOM + des réponses interceptées."""

    adaptor: _FakeAdaptor
    responses: list[_FakeResponse]


# ===========================================================================
# Charges utiles fabriquées à la main (aucune capture réseau)
# ===========================================================================

_CAPTION = "Entraînement du dimanche"
_AVATAR = "https://cdn.invalid/avatar.jpg"

#: (identifiant de post, décalage en secondes par rapport à FIXED_NOW)
_Post = tuple[str, float]


# -- Instagram --------------------------------------------------------------

def _ig_node(pid: str, ts: float, *, caption_graphql: bool = True) -> dict:
    node: dict[str, Any] = {
        "pk": pid,
        "id": pid,
        "shortcode": f"SC{pid}",
        "display_url": f"https://cdn.instagram.invalid/{pid}.jpg",
        "taken_at_timestamp": int(ts),
        "dimensions": {"width": 1080, "height": 1350},
        "edge_media_preview_like": {"count": 42},
        "edge_media_to_comment": {"count": 7},
    }
    if caption_graphql:
        node["edge_media_to_caption"] = {"edges": [{"node": {"text": _CAPTION}}]}
    return node


def _ig_payload(posts: list[_Post], *, caption_graphql: bool = True) -> _Payload:
    nodes = [_ig_node(pid, ts, caption_graphql=caption_graphql) for pid, ts in posts]
    blob = {
        "require": [[
            "PolarisProfilePostsQuery", "instance", [],
            [{"user": {"edge_owner_to_timeline_media": {
                "edges": [{"node": node} for node in nodes]
            }}}],
        ]]
    }
    script = _Element(text=json.dumps(blob))
    adaptor = _FakeAdaptor(
        selectors={
            'script[type="application/json"]': [script],
            "title": [_Element(text="Les Samouraïs (@samourais)")],
        },
        text="Les Samouraïs (@samourais) • Photos et vidéos",
    )
    # Le profil arrive par une réponse API interceptée : c'est le seul chemin
    # que `instagram.py` inspecte réellement (phase 3, :482-489).
    user_body = {
        "data": {"user": {
            "full_name": "Les Samouraïs",
            "profile_pic_url_hd": _AVATAR,
            "biography": "Dojo de Lyon",
            "is_verified": True,
            "edge_followed_by": {"count": 12345},
            "edge_follow": {"count": 321},
            "edge_owner_to_timeline_media": {"count": 99},
        }}
    }
    return _Payload(
        adaptor=adaptor,
        responses=[_FakeResponse(
            "https://www.instagram.com/api/v1/users/web_profile_info/", user_body
        )],
    )


# -- TikTok -----------------------------------------------------------------

def _tk_item(pid: str, ts: float) -> dict:
    return {
        "id": pid,
        "desc": _CAPTION,
        "createTime": int(ts),
        "author": {"uniqueId": "samourais", "nickname": "Les Samouraïs"},
        "video": {
            "playAddr": f"https://cdn.tiktok.invalid/{pid}.mp4",
            "duration": 15,
            "width": 1080,
            "height": 1920,
        },
    }


def _tk_payload(posts: list[_Post]) -> _Payload:
    blob = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {"userInfo": {"user": {
                "uniqueId": "samourais",
                "nickname": "Les Samouraïs",
                "avatarLarger": _AVATAR,
            }}},
            "webapp.post-list": {
                "itemList": [_tk_item(pid, ts) for pid, ts in posts]
            },
        }
    }
    script = _Element(text=json.dumps(blob))
    adaptor = _FakeAdaptor(
        selectors={"script#__UNIVERSAL_DATA_FOR_REHYDRATION__": [script]}
    )
    return _Payload(adaptor=adaptor, responses=[])


# -- Twitter / X ------------------------------------------------------------

def _tw_tweet(pid: str, ts: float) -> dict:
    from datetime import datetime, timezone

    created = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%a %b %d %H:%M:%S +0000 %Y"
    )
    return {
        "rest_id": pid,
        "legacy": {
            "id_str": pid,
            "full_text": _CAPTION,
            "created_at": created,
            "extended_entities": {"media": [{
                "type": "photo",
                "media_url_https": f"https://pbs.twimg.com/media/{pid}.jpg",
                "original_info": {"width": 1200, "height": 900},
            }]},
        },
    }


def _tw_payload(posts: list[_Post]) -> _Payload:
    entries = [
        {"content": {"itemContent": {"tweet_results": {"result": _tw_tweet(pid, ts)}}}}
        for pid, ts in posts
    ]
    body = {
        "data": {"user": {"result": {
            "__typename": "User",
            "legacy": {
                "name": "Les Samouraïs",
                "profile_image_url_https": "https://pbs.twimg.com/pp_normal.jpg",
            },
            "timeline_v2": {"timeline": {"instructions": [
                {"type": "TimelineAddEntries", "entries": entries}
            ]}},
        }}}
    }
    return _Payload(
        adaptor=_FakeAdaptor(),
        responses=[_FakeResponse(
            "https://x.com/i/api/graphql/abc/UserMedia?variables=%7B%7D", body
        )],
    )


# -- Reddit -----------------------------------------------------------------

def _rd_post(pid: str, ts: float) -> dict:
    return {
        "id": pid,
        "permalink": f"/user/samourais/comments/{pid}/entrainement/",
        "title": _CAPTION,
        "created_utc": float(ts),
        "post_hint": "image",
        "url": f"https://i.redd.it/{pid}.jpg",
        "preview": {"images": [{"source": {"width": 1200, "height": 900}}]},
    }


def _rd_payload(posts: list[_Post]) -> _Payload:
    body = {
        "kind": "Listing",
        "data": {"children": [
            {"kind": "t3", "data": _rd_post(pid, ts)} for pid, ts in posts
        ]},
    }
    return _Payload(
        adaptor=_FakeAdaptor(selectors={"script": []}),
        responses=[_FakeResponse(
            "https://www.reddit.com/user/samourais/submitted.json", body
        )],
    )


# ===========================================================================
# Spécification d'une plateforme sous contrat
# ===========================================================================


@dataclass(frozen=True)
class _Spec:
    platform: str
    module: Any
    extractor_cls: type[PlatformExtractor]
    profile_url: str
    build: Callable[..., _Payload]
    #: identifiants des posts DANS LA CHARGE UTILE, dans l'ordre
    ids: tuple[str, str]
    #: charge utile -> identifiant réellement ÉMIS dans `result.media[i].post_id`.
    #: Les deux coïncident partout sauf sur Instagram : depuis le lot 1.5b
    #: (risque #45, §4.17) le chemin JSON identifie un post par son `shortcode`
    #: — `SC111` pour le nœud dont le `pk` vaut `111` — comme `_dom_fallback`.
    emis: Callable[[str], str] = lambda pid: pid

    def payload(self, posts: list[_Post], **kwargs) -> _Payload:
        return self.build(posts, **kwargs)


SPECS: tuple[_Spec, ...] = (
    _Spec(
        platform="instagram",
        module=ig_mod,
        extractor_cls=ig_mod.InstagramExtractor,
        profile_url="https://www.instagram.com/samourais/",
        build=_ig_payload,
        ids=("111", "222"),
        emis=lambda pid: f"SC{pid}",  # `_ig_node` : shortcode = "SC" + pk
    ),
    _Spec(
        platform="tiktok",
        module=tk_mod,
        extractor_cls=tk_mod.TikTokExtractor,
        profile_url="https://www.tiktok.com/@samourais",
        build=_tk_payload,
        ids=("7300000000000000001", "7300000000000000002"),
    ),
    _Spec(
        platform="twitter",
        module=tw_mod,
        extractor_cls=tw_mod.TwitterExtractor,
        profile_url="https://x.com/samourais",
        build=_tw_payload,
        ids=("1720000000000000001", "1720000000000000002"),
    ),
    _Spec(
        platform="reddit",
        module=rd_mod,
        extractor_cls=rd_mod.RedditExtractor,
        profile_url="https://www.reddit.com/user/samourais/",
        build=_rd_payload,
        ids=("abc123", "def456"),
    ),
)


def _params(xfails: dict[str, str] | None = None) -> list:
    """Paramètres `(spec)` avec un xfail strict ciblé par plateforme."""
    xfails = xfails or {}
    out = []
    for spec in SPECS:
        reason = xfails.get(spec.platform)
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        out.append(pytest.param(spec, id=spec.platform, marks=marks))
    return out


# ===========================================================================
# Fixtures locales (le socle `conftest.py` n'est jamais modifié)
# ===========================================================================


@pytest.fixture(autouse=True)
def _horloge_figee(monkeypatch):
    """Fige `time.time()` des 4 modules d'extraction sur FIXED_NOW.

    Les 4 extracteurs calculent `cutoff_ts = time.time() - 2 ans`. Sans ce gel,
    des fixtures datées deviendraient « trop vieilles » avec le temps et les
    tests changeraient de résultat au fil des mois (LOI ABSOLUE n°5).
    Le stub est posé sur l'attribut `time` **du module consommateur** : le
    module `time` global n'est pas touché.
    """
    stub = SimpleNamespace(time=lambda: float(FIXED_NOW))
    for module in (ig_mod, tk_mod, tw_mod, rd_mod):
        monkeypatch.setattr(module, "time", stub)


@pytest.fixture
def extraire(monkeypatch):
    """Lance `extract()` d'un extracteur sur une charge utile fabriquée.

    Renvoie `(result, fetcher)` — `fetcher.calls` permet de vérifier l'URL et
    les kwargs réellement passés à `StealthyFetcher.fetch`.
    """

    def _run(
        spec: _Spec,
        *,
        posts: list[_Post] | None = None,
        payload: _Payload | None = None,
        known_post_ids: set[str] | None = None,
        options: ExtractOptions | None = None,
        error: BaseException | None = None,
        **payload_kwargs,
    ) -> tuple[ExtractorResult, _FakeFetcher]:
        if payload is None:
            if posts is None:
                posts = [(spec.ids[0], FIXED_NOW)]
            payload = spec.payload(posts, **payload_kwargs)
        fetcher = _FakeFetcher(payload.adaptor, payload.responses, error=error)
        monkeypatch.setattr(spec.module, "StealthyFetcher", fetcher)
        opts = options or ExtractOptions(scrape_mode="daily", max_scrolls=2)
        result = spec.extractor_cls().extract(
            spec.profile_url, set(known_post_ids or ()), opts
        )
        return result, fetcher

    return _run


# ===========================================================================
# 1. Le registre : toute nouvelle plateforme doit entrer dans ce contrat
# ===========================================================================


def test_le_registre_ne_contient_que_les_plateformes_sous_contrat():
    """`pipeline._EXTRACTORS` et les specs de ce module doivent coïncider.

    C'est le verrou qui rend le contrat contagieux : ajouter une 5ᵉ plateforme
    à `pipeline.py:43-46` sans l'ajouter ici fait échouer ce test.
    `conftest._reset_module_singletons` revide `_EXTRACTORS` après le test.
    """
    from app.scraper import pipeline

    pipeline._get_extractor("instagram")  # peuple le registre paresseux

    assert set(pipeline._EXTRACTORS) == {spec.platform for spec in SPECS}
    for spec in SPECS:
        assert pipeline._EXTRACTORS[spec.platform] is spec.extractor_cls
        assert spec.extractor_cls.platform == spec.platform
        assert issubclass(spec.extractor_cls, PlatformExtractor)


# ===========================================================================
# 2. Forme du résultat — identique pour les 4 plateformes
# ===========================================================================


@pytest.mark.parametrize("spec", _params())
def test_contrat_forme_du_resultat(spec, extraire):
    """Chaque extracteur renvoie un `ExtractorResult` de forme identique."""
    result, fetcher = extraire(spec, posts=[(spec.ids[0], FIXED_NOW)])

    assert isinstance(result, ExtractorResult)
    assert isinstance(result.profile_info, ProfileInfo)
    assert isinstance(result.media, list)
    assert isinstance(result.total_seen, int)

    assert len(fetcher.calls) == 1, "un seul fetch par extraction"
    assert len(result.media) == 1, "la charge utile contient exactement 1 post"

    item = result.media[0]
    assert isinstance(item, MediaItemData)
    assert item.post_id == spec.emis(spec.ids[0])
    assert item.media_type in ("image", "video")
    assert item.media_url.startswith("https://")
    assert item.post_url.startswith("https://")
    assert item.posted_at is not None
    assert item.posted_at.timestamp() == pytest.approx(FIXED_NOW)
    # La caption reste optionnelle au niveau du contrat : Instagram la perd
    # (risque #30), c'est un test dédié qui le garde.
    assert item.caption is None or isinstance(item.caption, str)


@pytest.mark.parametrize("spec", _params())
def test_contrat_le_proxy_est_transmis_au_fetcher(spec, extraire):
    """`ExtractOptions.proxy` doit atteindre `StealthyFetcher.fetch`."""
    opts = ExtractOptions(
        scrape_mode="daily", max_scrolls=2, proxy="http://user:pw@proxy.invalid:8080"
    )
    _, fetcher = extraire(spec, options=opts)

    url, kwargs = fetcher.calls[0]
    assert kwargs["proxy"] == "http://user:pw@proxy.invalid:8080"
    assert kwargs["headless"] is True


# ===========================================================================
# 3. risque #5 / lot 1.5 — `total_seen`
# ===========================================================================

_RAISON_TOTAL_SEEN = (
    "risque #5 AUDIT.md (§4.3) — `total_seen` n'est incrémenté que dans "
    "instagram.py:509,593 ; sans lui `pipeline.py:442` garde le profil en "
    "backfill pour toujours (200 scrolls à vide) ; lot 1.5"
)


@pytest.mark.parametrize("spec", _params())
def test_contrat_total_seen_compte_les_posts_vus(spec, extraire):
    """Chaque extracteur doit compter les posts VUS, pas seulement les nouveaux.

    C'est la variable dont dépend la transition backfill → daily
    (`pipeline.py:441-461`) et la distinction `empty` / `completed`
    (`pipeline.py:417-420`, risque #3).
    """
    posts = [(spec.ids[0], FIXED_NOW), (spec.ids[1], FIXED_NOW - 3600)]
    result, _ = extraire(spec, posts=posts)

    assert len(result.media) == 2, "les 2 posts sont nouveaux"
    assert result.total_seen == 2, (
        f"{spec.platform}: total_seen={result.total_seen} alors que 2 posts "
        "ont été parcourus"
    )


@pytest.mark.parametrize("spec", _params())
def test_contrat_total_seen_compte_aussi_les_posts_deja_connus(spec, extraire):
    """Un post déjà connu est filtré de `media` mais compte dans `total_seen`.

    C'est précisément le cas nominal d'un scrape quotidien sain : sans cette
    propriété, `media_new == 0 and total_seen == 0` (pipeline.py:442) est
    toujours vrai et le profil ne quitte jamais le mode backfill.
    """
    result, _ = extraire(
        spec,
        posts=[(spec.ids[0], FIXED_NOW)],
        known_post_ids={spec.emis(spec.ids[0])},
        options=ExtractOptions(scrape_mode="backfill", max_scrolls=2),
    )

    assert result.media == [], "le post connu ne doit pas être re-remonté"
    assert result.total_seen == 1, (
        f"{spec.platform}: un post connu a été parcouru sans être compté"
    )


# ===========================================================================
# 4. Fenêtre de backfill (§3 AUDIT.md l.157) — lue par twitter.py seul
# ===========================================================================

_RAISON_BACKFILL = (
    "AUDIT.md §3 (l.157) — `backfill_from`/`backfill_to` ne sont lus que par "
    "twitter.py:252-253 ; instagram/tiktok/reddit ignorent la fenêtre saisie "
    "dans l'UI et rescrapent tout l'historique (2 ans en dur) ; lot à créer "
    "(voisin de 1.5)"
)


@pytest.mark.parametrize("spec", _params({
    "instagram": _RAISON_BACKFILL,
    "tiktok": _RAISON_BACKFILL,
    "reddit": _RAISON_BACKFILL,
}))
def test_contrat_honore_backfill_to(spec, extraire):
    """Un post plus RÉCENT que `backfill_to` est hors fenêtre : à exclure."""
    opts = ExtractOptions(
        scrape_mode="backfill",
        max_scrolls=2,
        backfill_from=float(FIXED_NOW - 30 * 86400),
        backfill_to=float(FIXED_NOW - 86400),
    )
    result, _ = extraire(spec, posts=[(spec.ids[0], FIXED_NOW)], options=opts)

    assert result.media == [], (
        f"{spec.platform}: post daté FIXED_NOW remonté alors que "
        "backfill_to = FIXED_NOW - 1 jour"
    )


@pytest.mark.parametrize("spec", _params({
    "instagram": _RAISON_BACKFILL,
    "tiktok": _RAISON_BACKFILL,
    "reddit": _RAISON_BACKFILL,
}))
def test_contrat_honore_backfill_from(spec, extraire):
    """Un post plus ANCIEN que `backfill_from` est hors fenêtre : à exclure."""
    vieux = FIXED_NOW - 200 * 86400
    opts = ExtractOptions(
        scrape_mode="backfill",
        max_scrolls=2,
        backfill_from=float(FIXED_NOW - 30 * 86400),
        backfill_to=float(FIXED_NOW),
    )
    result, _ = extraire(spec, posts=[(spec.ids[0], vieux)], options=opts)

    assert result.media == [], (
        f"{spec.platform}: post vieux de 200 jours remonté alors que "
        "backfill_from = FIXED_NOW - 30 jours"
    )


# ===========================================================================
# 5. risque #53 / lot 1.4b — un échec de fetch n'est pas un résultat vide
# ===========================================================================

_RAISON_FETCH = (
    "risque #53 AUDIT.md (§4.20) — les 4 extracteurs font `except Exception: "
    "logger.error(...); return result` autour de StealthyFetcher.fetch "
    "(instagram.py:436-439, tiktok.py:331-334, twitter.py:330-333, "
    "reddit.py:391-394) : un navigateur mort produit un job `empty` sans "
    "error_message au lieu de `failed` ; lot 1.4b"
)


@pytest.mark.parametrize("spec", _params())
def test_contrat_echec_de_fetch_non_silencieux(spec, extraire):
    """Un `StealthyFetcher.fetch` qui lève ne doit PAS produire un vide muet.

    Deux formes sont acceptées par ce contrat (cf. lot 1.4b) :
      * l'exception remonte au pipeline (`pipeline.py:164-167` → `failed`) ;
      * ou le résultat porte un `fetch_error` non vide, que le pipeline consomme.
    Aujourd'hui : ni l'un ni l'autre — `ExtractorResult()` vide, indiscernable
    d'un profil sans nouveauté.
    """
    try:
        result, _ = extraire(spec, error=RuntimeError("navigateur indisponible"))
    except Exception:
        return  # comportement correct : l'échec remonte au pipeline

    assert getattr(result, "fetch_error", None), (
        f"{spec.platform}: échec total de fetch renvoyé comme un "
        f"ExtractorResult vide (media={result.media}, "
        f"total_seen={result.total_seen}) sans aucun signal d'erreur"
    )


@pytest.mark.parametrize("spec", _params())
def test_contrat_echec_de_fetch_ne_fabrique_pas_de_media(spec, extraire):
    """Caractérisation du comportement ACTUEL sur échec de fetch.

    Complément du test précédent : quelle que soit la correction retenue au lot
    1.4b, un échec de fetch ne doit jamais inventer de média ni de profil.

    Les DEUX formes acceptables du contrat sont vérifiées, et aucune ne sort
    du test sans assertion. Un `except: return` muet ferait passer ce test au
    vert sans rien exécuter le jour PRÉCIS où le lot 1.4b fait remonter
    l'erreur — le garde-fou s'évaporerait au moment où le code change.
    """
    try:
        result, _ = extraire(spec, error=RuntimeError("navigateur indisponible"))
    except Exception as exc:
        # Forme B (attendue après le lot 1.4b) : l'échec remonte au pipeline.
        # Il doit alors porter la cause, pas la masquer.
        assert "navigateur indisponible" in str(exc), (
            f"{spec.platform}: l'exception remontée doit conserver la cause "
            f"d'origine, reçu {exc!r}"
        )
        return

    # Forme A (comportement actuel) : ExtractorResult vide, mais VIDE.
    assert result.media == []
    assert result.total_seen == 0
    assert result.profile_info.display_name is None


# ===========================================================================
# 6. risque #23 — s'arrêter au 1er post connu annule tout le scrape
# ===========================================================================

_RAISON_PREMIER_CONNU = (
    "risque #23 AUDIT.md — tiktok.py:398-401 et reddit.py:473-478 posent "
    "`stop_early = True` dès le PREMIER post connu, là où instagram.py:512-520 "
    "(seuil 8) et twitter.py:369-377 (seuil 3) tolèrent les posts épinglés : "
    "un post épinglé annule tout le scrape quotidien ; lot voisin de 1.5"
)


@pytest.mark.parametrize("spec", _params({
    "tiktok": _RAISON_PREMIER_CONNU,
    "reddit": _RAISON_PREMIER_CONNU,
}))
def test_contrat_mode_daily_ne_sabote_pas_tout_sur_un_post_epingle(spec, extraire):
    """Un post connu en tête (épinglé) ne doit pas masquer les posts suivants."""
    posts = [(spec.ids[0], FIXED_NOW), (spec.ids[1], FIXED_NOW - 3600)]
    result, _ = extraire(
        spec,
        posts=posts,
        known_post_ids={spec.emis(spec.ids[0])},
        options=ExtractOptions(scrape_mode="daily", max_scrolls=2),
    )

    assert [item.post_id for item in result.media] == [spec.emis(spec.ids[1])], (
        f"{spec.platform}: le 2ᵉ post (nouveau) a été perdu parce que le 1ᵉʳ "
        "était déjà connu"
    )


# ===========================================================================
# 7. risque #45 / lot 1.5b — Instagram : deux espaces d'identifiants
# ===========================================================================

_SPEC_IG = SPECS[0]

_RAISON_ID_SPACE = (
    "risque #45 AUDIT.md (§4.17) — le chemin JSON émet `pk`/`id` "
    "(instagram.py:134) tandis que `_dom_fallback` émet le `shortcode` "
    "(instagram.py:614) : deux espaces d'identifiants, `known_post_ids` "
    "(pipeline.py:150-155) ne reconnaît rien et la contrainte "
    "(profile_id, post_id, media_url) de db.py:86 ne bloque pas le doublon "
    "basse résolution ; lot 1.5b"
)


def test_instagram_le_fallback_dom_emet_le_shortcode():
    """Caractérisation : `_dom_fallback` identifie les posts par leur shortcode.

    C'est la moitié « DOM » des deux espaces d'identifiants du risque #45.
    Ce test décrit le comportement ACTUEL et doit rester vert après le lot 1.5b
    (c'est cet espace-là qui doit devenir l'espace unique).
    """
    img = _Element(attrib={"src": "https://cdn.instagram.invalid/vignette.jpg"})
    lien = _Element(attrib={"href": "/p/SC111/"}, children={"img": [img]})
    adaptor = _FakeAdaptor(
        selectors={'a[href*="/p/"], a[href*="/reel/"]': [lien]}
    )
    result = ExtractorResult()

    ig_mod.InstagramExtractor._dom_fallback(
        adaptor,
        result,
        set(),
        set(),
        ExtractOptions(scrape_mode="daily", max_scrolls=2),
        float(FIXED_NOW - 2 * 365 * 86400),
    )

    assert [item.post_id for item in result.media] == ["SC111"]
    assert result.total_seen == 1  # instagram.py:593 — le seul incrément hors :509


def test_instagram_le_chemin_json_utilise_le_meme_espace_que_le_dom(extraire):
    """Le chemin JSON doit identifier le post par son shortcode, comme le DOM."""
    result, _ = extraire(_SPEC_IG, posts=[("111", FIXED_NOW)])

    assert [item.post_id for item in result.media] == ["SC111"], (
        "le chemin JSON émet le `pk` alors que `_dom_fallback` émet le "
        "shortcode : le même post porte deux identifiants"
    )


def test_instagram_dedup_entre_les_deux_chemins(extraire):
    """Un post déjà stocké par le fallback DOM doit être filtré par le JSON.

    Scénario réel : un scrape dégradé (session morte) est passé par le fallback
    DOM et a stocké le post sous son shortcode ; le scrape suivant, nominal,
    passe par le JSON. Sans espace unifié, le post est stocké — et retéléchargé
    — une seconde fois, en basse résolution (§4.17).
    """
    result, _ = extraire(
        _SPEC_IG,
        posts=[("111", FIXED_NOW)],
        known_post_ids={"SC111"},
        options=ExtractOptions(scrape_mode="backfill", max_scrolls=2),
    )

    assert result.media == [], (
        "post déjà connu sous son shortcode remonté une seconde fois par le "
        "chemin JSON (doublon basse résolution garanti)"
    )


# ===========================================================================
# 8. risque #30 / lot 1.7 — captions GraphQL perdues (précédence d'opérateurs)
# ===========================================================================

_RAISON_CAPTION = (
    "risque #30 AUDIT.md (§4.9) — instagram.py:109-113 : Python parse "
    "`A or B if cond else None` comme `(A or B) if cond else None`, donc "
    "`edges` vaut None dès que `node['caption']` n'est pas un dict — c'est le "
    "cas nominal GraphQL. Toutes les légendes partent en NULL en base "
    "(pipeline.py:243) ; correction = parenthéser ; lot 1.7"
)


def test_instagram_extract_caption_format_graphql():
    """Un nœud GraphQL (`edge_media_to_caption`) doit livrer sa légende."""
    node = {"edge_media_to_caption": {"edges": [{"node": {"text": _CAPTION}}]}}

    assert ig_mod._extract_caption(node) == _CAPTION


def test_instagram_extract_caption_format_api_v1():
    """Caractérisation : le format API v1 (`caption` dict) fonctionne, lui."""
    assert ig_mod._extract_caption({"caption": {"text": _CAPTION}}) == _CAPTION
    assert ig_mod._extract_caption({"caption": _CAPTION}) == _CAPTION
    assert ig_mod._extract_caption({}) is None


def test_instagram_la_caption_graphql_survit_a_extract(extraire):
    """Bout en bout : la légende GraphQL doit arriver dans `MediaItemData`."""
    result, _ = extraire(_SPEC_IG, posts=[("111", FIXED_NOW)], caption_graphql=True)

    assert result.media[0].caption == _CAPTION


def test_instagram_media_items_from_node_perd_la_caption_graphql():
    """Caractérisation nue du bug #30 au niveau du parseur de nœud.

    Ce test restera vert après le lot 1.7 **uniquement s'il est mis à jour** :
    il est volontairement écrit comme une comparaison avec le résultat de
    `_extract_caption`, la fonction fautive — il documente la propagation, pas
    la valeur fautive elle-même.
    """
    node = _ig_node("111", FIXED_NOW, caption_graphql=True)
    items = ig_mod._media_items_from_node(node)

    assert len(items) == 1
    assert items[0]["caption"] == ig_mod._extract_caption(node)


# ===========================================================================
# 9. Instagram calcule followers/biography/media_count puis les jette
# ===========================================================================

_RAISON_PROFIL = (
    "instagram.py:482-489 vs `_extract_profile_info` (instagram.py:233-277) + "
    "pipeline.py:198 — bug NON NUMÉROTÉ dans AUDIT.md à ce jour (à ouvrir en §4 "
    "pour que ce xfail cite un numéro comme les autres). `_extract_profile_info` "
    "(instagram.py:233-277) calcule followers_count / following_count / "
    "media_count / biography / is_verified, mais la phase 3 de `extract` "
    "(instagram.py:482-489) ne recopie que display_name et avatar_url. "
    "`result.profile_info.followers_count` reste None, donc pipeline.py:198 "
    "ne crée JAMAIS de ProfileSnapshot et les courbes analytics restent vides ; "
    "lot à créer (voisin de 1.5b)"
)


def test_instagram_extract_profile_info_calcule_bien_les_statistiques():
    """Caractérisation : la fonction de parsing, elle, calcule tout."""
    blob = {"data": {"user": {
        "full_name": "Les Samouraïs",
        "profile_pic_url_hd": _AVATAR,
        "biography": "Dojo de Lyon",
        "is_verified": True,
        "edge_followed_by": {"count": 12345},
        "edge_follow": {"count": 321},
        "edge_owner_to_timeline_media": {"count": 99},
    }}}

    info = ig_mod._extract_profile_info(blob)

    assert info is not None
    assert info.display_name == "Les Samouraïs"
    assert info.avatar_url == _AVATAR
    assert info.biography == "Dojo de Lyon"
    assert info.is_verified is True
    assert info.followers_count == 12345
    assert info.following_count == 321
    assert info.media_count == 99


def test_instagram_extract_remonte_bien_le_nom_et_avatar(extraire):
    """Caractérisation : seuls ces deux champs franchissent la phase 3."""
    result, _ = extraire(_SPEC_IG, posts=[("111", FIXED_NOW)])

    assert result.profile_info.display_name == "Les Samouraïs"
    assert result.profile_info.avatar_url == _AVATAR


@pytest.mark.xfail(strict=True, reason=_RAISON_PROFIL)
def test_instagram_extract_remonte_les_statistiques_de_compte(extraire):
    """`extract()` doit livrer les stats — c'est la condition du ProfileSnapshot.

    `pipeline.py:198` ne crée un `ProfileSnapshot` que
    `if result.profile_info.followers_count is not None`. Tant que la phase 3
    jette la valeur, aucun snapshot n'est écrit pour Instagram : les graphiques
    d'évolution d'audience sont structurellement vides.
    """
    result, _ = extraire(_SPEC_IG, posts=[("111", FIXED_NOW)])
    info = result.profile_info

    assert info.followers_count == 12345
    assert info.following_count == 321
    assert info.media_count == 99
    assert info.biography == "Dojo de Lyon"
    assert info.is_verified is True


# ===========================================================================
# 10. Parseurs PURS de twitter/tiktok/reddit (§7.2 : « testables sans réseau »)
# ===========================================================================
# La section 8 fait ce travail pour les trois parseurs Instagram. Les fonctions
# équivalentes des trois autres plateformes n'étaient traversées qu'indirecte-
# ment, par un unique payload nominal à un post — et
# `_pick_best_video_variant`, qui décide de la QUALITÉ réellement téléchargée,
# n'avait aucune couverture dans toute la suite.


def _variante(url: str, bitrate: int | None, content_type: str = "video/mp4") -> dict:
    variante = {"url": url, "content_type": content_type}
    if bitrate is not None:
        variante["bitrate"] = bitrate
    return variante


def test_twitter_choisit_la_variante_mp4_au_plus_haut_bitrate():
    """`_pick_best_video_variant` (twitter.py:112-125) : c'est cette fonction
    qui détermine la qualité de la vidéo réellement stockée."""
    variantes = [
        _variante("https://video.invalid/low.mp4", 256_000),
        _variante("https://video.invalid/high.mp4", 2_176_000),
        _variante("https://video.invalid/mid.mp4", 832_000),
    ]

    assert tw_mod._pick_best_video_variant(variantes) == "https://video.invalid/high.mp4"


def test_twitter_ignore_le_m3u8_quand_un_mp4_existe():
    """Le HLS de Twitter n'a pas de `bitrate` : sans le filtre `content_type`,
    il gagnerait le tri (bitrate par défaut 0 ... ou pas, selon l'ordre)."""
    variantes = [
        _variante("https://video.invalid/master.m3u8", None,
                  content_type="application/x-mpegURL"),
        _variante("https://video.invalid/720.mp4", 832_000),
    ]

    assert tw_mod._pick_best_video_variant(variantes) == "https://video.invalid/720.mp4"


def test_twitter_retombe_sur_nimporte_quelle_variante_sans_mp4():
    """Repli explicite de twitter.py:118-120 : mieux vaut du HLS que rien —
    `downloaders._is_hls_url` saura le router vers ffmpeg."""
    variantes = [
        _variante("https://video.invalid/master.m3u8", None,
                  content_type="application/x-mpegURL"),
    ]

    assert tw_mod._pick_best_video_variant(variantes) == (
        "https://video.invalid/master.m3u8"
    )


@pytest.mark.parametrize(
    "variantes",
    [[], [{"content_type": "video/mp4"}], [{"url": ""}]],
    ids=["liste-vide", "sans-url", "url-vide"],
)
def test_twitter_sans_variante_exploitable_rend_une_chaine_vide(variantes):
    """Caractérisation : la fonction ne lève jamais, elle rend `""`.

    C'est ce qui fait qu'un tweet vidéo illisible produit un `media_url` vide —
    rattrapé plus loin par le `ValueError` de `download_media`, cf.
    tests/test_stockage.py::test_download_media_refuse_une_url_vide.
    """
    assert tw_mod._pick_best_video_variant(variantes) == ""


def test_twitter_extrait_les_photos_dun_tweet():
    """`_extract_media_from_tweet` (twitter.py:128+), cas nominal photo."""
    tweet = {
        "rest_id": "1770000000000000001",
        "legacy": {
            "full_text": "Entraînement du dimanche",
            "created_at": "Sun Mar 17 10:00:00 +0000 2024",
            "favorite_count": 42,
            "reply_count": 3,
            "extended_entities": {
                "media": [
                    {
                        "type": "photo",
                        "media_url_https": "https://pbs.invalid/photo1.jpg",
                        "original_info": {"width": 1200, "height": 800},
                    }
                ]
            },
        },
    }

    items = tw_mod._extract_media_from_tweet(tweet)

    assert len(items) == 1
    assert items[0]["media_type"] == "image"
    # Le suffixe `:orig` demande la pleine résolution à Twitter — c'est lui qui
    # détermine la qualité stockée, exactement comme `_pick_best_video_variant`
    # côté vidéo. Le perdre dégraderait silencieusement toute la bibliothèque.
    assert items[0]["media_url"] == "https://pbs.invalid/photo1.jpg:orig"
    assert items[0]["post_id"] == "1770000000000000001"


def test_twitter_un_tweet_sans_media_ne_produit_rien():
    """Caractérisation : pas d'`extended_entities`, pas d'item — et pas d'erreur."""
    tweet = {"rest_id": "42", "legacy": {"full_text": "texte seul"}}

    assert tw_mod._extract_media_from_tweet(tweet) == []


def test_twitter_un_legacy_non_dict_ne_leve_pas():
    """Garde de twitter.py:137 : une charge utile inattendue ne casse pas le lot."""
    assert tw_mod._extract_media_from_tweet({"rest_id": "42", "legacy": None}) == []


def test_reddit_un_post_sans_media_exploitable_ne_produit_rien():
    """`_extract_media_from_post` : un post texte est ignoré silencieusement."""
    assert rd_mod._extract_media_from_post({"id": "abc", "title": "texte"}) == []
