"""
Quick download — download media from a single URL (post link).

Detects the platform from the URL, navigates to the page using
StealthyFetcher, extracts media links via API interception + DOM parsing,
and downloads the files.

Supports: Instagram, TikTok, Twitter/X, Reddit.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any

from loguru import logger
from scrapling.fetchers import StealthyFetcher

from app.scraper.base import MediaItemData
from app.scraper.downloaders import download_media, DownloadResult

from app.config import SESSIONS_DIR, get_proxy_for_platform


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class QuickDownloadResult:
    """Result of a quick download operation."""
    platform: str
    post_id: str
    post_url: str
    media_items: list[dict] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------
_PLATFORM_PATTERNS = [
    # Instagram
    (re.compile(r"instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)"), "instagram"),
    # TikTok
    (re.compile(r"tiktok\.com/.*?/video/(\d+)"), "tiktok"),
    (re.compile(r"tiktok\.com/@[\w.]+/video/(\d+)"), "tiktok"),
    (re.compile(r"vm\.tiktok\.com/(\w+)"), "tiktok"),
    # Twitter / X
    (re.compile(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)"), "twitter"),
    # Reddit
    (re.compile(r"reddit\.com/r/\w+/comments/([a-z0-9]+)"), "reddit"),
    (re.compile(r"redd\.it/([a-z0-9]+)"), "reddit"),
]


def detect_platform(url: str) -> tuple[str, str] | None:
    """
    Detect platform and post ID from a URL.
    Returns (platform, post_id) or None if unrecognized.
    """
    for pattern, platform in _PLATFORM_PATTERNS:
        m = pattern.search(url)
        if m:
            return platform, m.group(1)
    return None


# ---------------------------------------------------------------------------
# Cookie helpers (shared with extractors)
# ---------------------------------------------------------------------------
def _load_cookies(platform: str) -> list[dict]:
    """Load cookies for a platform, converted to Playwright format."""
    cookie_file = SESSIONS_DIR / f"{platform}.json"
    if not cookie_file.exists():
        return []
    try:
        with open(cookie_file, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            return []
        pw_cookies = []
        for c in raw:
            cookie = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
            }
            if "secure" in c:
                cookie["secure"] = bool(c["secure"])
            if "httpOnly" in c:
                cookie["httpOnly"] = bool(c["httpOnly"])
            if "expirationDate" in c and c["expirationDate"]:
                cookie["expires"] = float(c["expirationDate"])
            if "sameSite" in c:
                val = str(c["sameSite"]).capitalize()
                if val in ("Strict", "Lax", "None"):
                    cookie["sameSite"] = val
            pw_cookies.append(cookie)
        return pw_cookies
    except Exception as exc:
        logger.warning("Failed to load cookies for {}: {}", platform, exc)
        return []


# ---------------------------------------------------------------------------
# Instagram single post
# ---------------------------------------------------------------------------
def _extract_instagram(url: str, post_id: str) -> list[MediaItemData]:
    """Extract media from a single Instagram post/reel."""
    intercepted: list[dict] = []
    pw_cookies = _load_cookies("instagram")

    def page_action(page):
        nonlocal intercepted

        # Register response listener BEFORE any reload
        def on_response(response):
            resp_url = response.url
            if any(f in resp_url for f in ("/graphql", "/api/v1/media/", "/api/v1/feed/")):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        page.on("response", on_response)

        if pw_cookies:
            page.context.add_cookies(pw_cookies)

        # Use "load" — Instagram has constant background requests (analytics, tracking)
        # that prevent networkidle from ever being reached, causing timeouts.
        page.reload(wait_until="load")
        page.wait_for_timeout(4000)

    try:
        fetch_kwargs = dict(headless=True, page_action=page_action)
        proxy = get_proxy_for_platform("instagram")
        if proxy:
            fetch_kwargs["proxy"] = proxy
            fetch_kwargs["geoip"] = True
        adaptor = StealthyFetcher.fetch(url, **fetch_kwargs)
    except Exception as exc:
        logger.error("Instagram fetch failed: {}", exc)
        return []

    # Parse intercepted API data
    all_nodes = []
    for body in intercepted:
        _collect_instagram_nodes(body, all_nodes)

    # Parse embedded JSON
    try:
        for tag in adaptor.css('script[type="application/json"]'):
            try:
                blob = json.loads(tag.text)
                _collect_instagram_nodes(blob, all_nodes)
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass

    # Build media items from nodes
    items = []
    for node in all_nodes:
        shortcode = node.get("shortcode") or node.get("code") or ""
        if shortcode and shortcode != post_id:
            continue  # Skip unrelated posts

        for mi in _instagram_media_from_node(node, post_id):
            items.append(mi)

    # Fallback: try to extract from meta tags
    if not items:
        try:
            og_video = adaptor.css('meta[property="og:video"]')
            og_image = adaptor.css('meta[property="og:image"]')
            caption_tag = adaptor.css('meta[property="og:title"]')
            caption = caption_tag[0].attrib.get("content", "") if caption_tag else ""

            if og_video:
                media_url = og_video[0].attrib.get("content", "")
                if media_url:
                    items.append(MediaItemData(
                        post_id=post_id,
                        post_url=url,
                        media_type="video",
                        media_url=media_url,
                        caption=caption,
                    ))
            elif og_image:
                media_url = og_image[0].attrib.get("content", "")
                if media_url:
                    items.append(MediaItemData(
                        post_id=post_id,
                        post_url=url,
                        media_type="image",
                        media_url=media_url,
                        caption=caption,
                    ))
        except Exception:
            pass

    return items


def _collect_instagram_nodes(data: Any, out: list[dict]):
    """Recursively find Instagram media nodes in API/embedded JSON."""
    if isinstance(data, dict):
        if "shortcode" in data and ("display_url" in data or "video_url" in data
                                     or "image_versions2" in data or "video_versions" in data):
            out.append(data)
        if "code" in data and ("image_versions2" in data or "video_versions" in data):
            out.append(data)
        for v in data.values():
            _collect_instagram_nodes(v, out)
    elif isinstance(data, list):
        for v in data:
            _collect_instagram_nodes(v, out)


def _instagram_media_from_node(node: dict, post_id: str) -> list[MediaItemData]:
    """Convert an Instagram node dict to MediaItemData list."""
    items = []
    caption = ""
    edges = node.get("edge_media_to_caption", {}).get("edges", [])
    if edges:
        caption = edges[0].get("node", {}).get("text", "")
    if not caption:
        cap = node.get("caption")
        if isinstance(cap, dict):
            caption = cap.get("text", "")
        elif isinstance(cap, str):
            caption = cap

    post_url = f"https://www.instagram.com/p/{node.get('shortcode', post_id)}/"
    posted_at = None
    ts = node.get("taken_at_timestamp") or node.get("taken_at")
    if ts:
        try:
            posted_at = datetime.fromtimestamp(int(ts))
        except (ValueError, OSError):
            pass

    # Check for carousel/sidecar
    sidecar_children = (
        node.get("edge_sidecar_to_children", {}).get("edges", [])
        or node.get("carousel_media", [])
    )

    media_nodes = []
    if sidecar_children:
        for child in sidecar_children:
            cn = child.get("node", child)
            media_nodes.append(cn)
    else:
        media_nodes.append(node)

    for mn in media_nodes:
        is_video = (
            mn.get("is_video")
            or mn.get("media_type") == 2
            or mn.get("video_url")
            or mn.get("video_versions")
        )

        if is_video:
            media_url = mn.get("video_url", "")
            if not media_url:
                versions = mn.get("video_versions", [])
                if versions:
                    media_url = versions[0].get("url", "")
            media_type = "video"
        else:
            media_url = (
                mn.get("display_url")
                or mn.get("display_src")
                or mn.get("thumbnail_src")
            )
            if not media_url:
                candidates = mn.get("image_versions2", {}).get("candidates", [])
                if candidates:
                    media_url = candidates[0].get("url", "")
            media_type = "image"

        if media_url:
            items.append(MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type=media_type,
                media_url=media_url,
                caption=caption,
                posted_at=posted_at,
                width=mn.get("original_width") or mn.get("dimensions", {}).get("width"),
                height=mn.get("original_height") or mn.get("dimensions", {}).get("height"),
                duration=mn.get("video_duration"),
            ))

    return items


# ---------------------------------------------------------------------------
# TikTok single post
# ---------------------------------------------------------------------------
def _extract_tiktok(url: str, post_id: str) -> list[MediaItemData]:
    """Extract media from a single TikTok video."""
    intercepted: list[dict] = []
    pw_cookies = _load_cookies("tiktok")

    def page_action(page):
        nonlocal intercepted

        # Register response listener BEFORE any reload
        def on_response(response):
            resp_url = response.url
            if "/api/post/item_list" in resp_url or "/api/item/detail" in resp_url:
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        page.on("response", on_response)

        if pw_cookies:
            page.context.add_cookies(pw_cookies)

        # Always reload to trigger API requests while listener is active
        page.reload(wait_until="load")
        page.wait_for_timeout(3000)

    try:
        fetch_kwargs = dict(headless=True, page_action=page_action)
        proxy = get_proxy_for_platform("tiktok")
        if proxy:
            fetch_kwargs["proxy"] = proxy
            fetch_kwargs["geoip"] = True
        adaptor = StealthyFetcher.fetch(url, **fetch_kwargs)
    except Exception as exc:
        logger.error("TikTok fetch failed: {}", exc)
        return []

    # Try embedded JSON first (most reliable for TikTok)
    item_data = None
    try:
        scripts = adaptor.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if scripts:
            blob = json.loads(scripts[0].text)
            # Navigate to item detail
            default_scope = blob.get("__DEFAULT_SCOPE__", {})
            item_module = default_scope.get("webapp.video-detail", {})
            item_data = item_module.get("itemInfo", {}).get("itemStruct")
    except Exception as exc:
        logger.debug("TikTok embedded JSON parse failed: {}", exc)

    # Try API interception
    if not item_data:
        for body in intercepted:
            if isinstance(body, dict):
                item_data = body.get("itemInfo", {}).get("itemStruct")
                if item_data:
                    break
                items = body.get("itemList", [])
                for item in items:
                    if str(item.get("id")) == post_id:
                        item_data = item
                        break

    if not item_data:
        # Fallback: try og:video meta tag
        try:
            og = adaptor.css('meta[property="og:video"]')
            if og:
                return [MediaItemData(
                    post_id=post_id,
                    post_url=url,
                    media_type="video",
                    media_url=og[0].attrib.get("content", ""),
                    caption="",
                )]
        except Exception:
            pass
        return []

    # Parse item data
    caption = item_data.get("desc", "")
    posted_at = None
    create_time = item_data.get("createTime")
    if create_time:
        try:
            posted_at = datetime.fromtimestamp(int(create_time))
        except (ValueError, OSError):
            pass

    # Check for image post
    image_post = item_data.get("imagePost")
    if image_post:
        images = image_post.get("images", [])
        items = []
        for img in images:
            img_url = img.get("imageURL", {}).get("urlList", [None])[0]
            if img_url:
                items.append(MediaItemData(
                    post_id=post_id,
                    post_url=url,
                    media_type="image",
                    media_url=img_url,
                    caption=caption,
                    posted_at=posted_at,
                ))
        return items

    # Video post
    video = item_data.get("video", {})
    video_url = None
    for key in ("playAddr", "downloadAddr"):
        addr = video.get(key)
        if isinstance(addr, dict):
            urls = addr.get("urlList", [])
            if urls:
                video_url = urls[0]
                break
        elif isinstance(addr, str) and addr:
            video_url = addr
            break

    if not video_url:
        # Try direct URL
        video_url = video.get("playAddr") or video.get("downloadAddr")
        if isinstance(video_url, str) and not video_url.startswith("http"):
            video_url = None

    if video_url:
        return [MediaItemData(
            post_id=post_id,
            post_url=url,
            media_type="video",
            media_url=video_url,
            caption=caption,
            posted_at=posted_at,
            width=video.get("width"),
            height=video.get("height"),
            duration=video.get("duration"),
        )]

    return []


# ---------------------------------------------------------------------------
# Twitter/X — syndication API (no auth needed)
# ---------------------------------------------------------------------------
_TWITTER_DEFAULT_IMAGES = (
    "abs.twimg.com/rweb/ssr/default",
    "abs.twimg.com/responsive-web",
    "abs.twimg.com/icons",
)


def _try_twitter_syndication(post_id: str, post_url: str) -> list[MediaItemData]:
    """
    Try the Twitter syndication API to fetch tweet media.
    Works without authentication — returns structured JSON with media URLs.
    """
    import httpx

    syndication_url = f"https://cdn.syndication.twimg.com/tweet-result?id={post_id}&lang=en&token=0"
    try:
        resp = httpx.get(
            syndication_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://platform.twitter.com/",
            },
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.debug("Twitter syndication API returned {}", resp.status_code)
            return []

        data = resp.json()
    except Exception as exc:
        logger.debug("Twitter syndication API failed: {}", exc)
        return []

    caption = data.get("text", "")
    posted_at = None
    created_at = data.get("created_at")
    if created_at:
        try:
            posted_at = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            pass

    items = []

    # Check for media_details (photos, videos, animated_gif)
    media_details = data.get("mediaDetails", [])
    for media in media_details:
        media_type_raw = media.get("type", "photo")

        if media_type_raw in ("video", "animated_gif"):
            variants = media.get("video_info", {}).get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4_variants:
                best = max(mp4_variants, key=lambda v: v.get("bitrate", 0))
                media_url = best.get("url", "")
            else:
                media_url = variants[0]["url"] if variants else ""
            m_type = "video"
            duration = media.get("video_info", {}).get("duration_millis")
            duration = duration / 1000 if duration else None
        else:
            media_url = media.get("media_url_https", media.get("media_url", ""))
            if media_url and ":orig" not in media_url:
                media_url += ":orig"
            m_type = "image"
            duration = None

        if media_url:
            size = media.get("original_info", {})
            items.append(MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type=m_type,
                media_url=media_url,
                caption=caption,
                posted_at=posted_at,
                width=size.get("width"),
                height=size.get("height"),
                duration=duration,
            ))

    # Fallback: check photos array
    if not items:
        for photo in data.get("photos", []):
            photo_url = photo.get("url", "")
            if photo_url:
                if ":orig" not in photo_url:
                    photo_url += ":orig"
                items.append(MediaItemData(
                    post_id=post_id,
                    post_url=post_url,
                    media_type="image",
                    media_url=photo_url,
                    caption=caption,
                    posted_at=posted_at,
                    width=photo.get("width"),
                    height=photo.get("height"),
                ))

    # Fallback: check video object
    if not items and data.get("video"):
        video = data["video"]
        variants = video.get("variants", [])
        mp4s = [v for v in variants if v.get("type") == "video/mp4" or v.get("content_type") == "video/mp4"]
        if mp4s:
            best = max(mp4s, key=lambda v: v.get("bitrate", 0))
            src = best.get("src") or best.get("url", "")
            if src:
                items.append(MediaItemData(
                    post_id=post_id,
                    post_url=post_url,
                    media_type="video",
                    media_url=src,
                    caption=caption,
                    posted_at=posted_at,
                ))

    if items:
        logger.info("Twitter syndication API found {} media items for tweet {}", len(items), post_id)

    return items


# ---------------------------------------------------------------------------
# Twitter/X single post
# ---------------------------------------------------------------------------
def _extract_twitter(url: str, post_id: str) -> list[MediaItemData]:
    """Extract media from a single tweet."""

    # 1) Try syndication API first — fast, no browser, no auth needed
    items = _try_twitter_syndication(post_id, url)
    if items:
        return items

    logger.debug("Syndication API returned nothing, falling back to browser for tweet {}", post_id)

    # 2) Fall back to browser-based extraction
    intercepted: list[dict] = []
    pw_cookies = _load_cookies("twitter")

    def page_action(page):
        nonlocal intercepted

        # Register response listener BEFORE any reload/navigation
        def on_response(response):
            resp_url = response.url
            if "/i/api/graphql/" in resp_url:
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        page.on("response", on_response)

        # Add cookies if available
        if pw_cookies:
            page.context.add_cookies(pw_cookies)

        # Always reload to trigger fresh GraphQL requests while listener is active
        page.reload(wait_until="load")
        page.wait_for_timeout(3000)

    # Normalize URL to x.com
    normalized = re.sub(r"twitter\.com", "x.com", url)
    try:
        fetch_kwargs = dict(headless=True, page_action=page_action)
        proxy = get_proxy_for_platform("twitter")
        if proxy:
            fetch_kwargs["proxy"] = proxy
            fetch_kwargs["geoip"] = True
        adaptor = StealthyFetcher.fetch(normalized, **fetch_kwargs)
    except Exception as exc:
        logger.error("Twitter fetch failed: {}", exc)
        return []

    # Find tweet data in intercepted responses
    tweet_data = None
    for body in intercepted:
        tweet_data = _find_tweet_in_response(body, post_id)
        if tweet_data:
            break

    if tweet_data:
        return _twitter_media_from_tweet(tweet_data, post_id, url)

    # 3) Fallback: try og:image / og:video meta tags (filter out default X images)
    logger.debug("No GraphQL data intercepted for tweet {}, trying meta tags", post_id)
    try:
        og_video = adaptor.css('meta[property="og:video"]')
        og_image = adaptor.css('meta[property="og:image"]')
        caption_tag = adaptor.css('meta[property="og:description"]')
        caption = caption_tag[0].attrib.get("content", "") if caption_tag else ""

        if og_video:
            media_url = og_video[0].attrib.get("content", "")
            if media_url and not any(d in media_url for d in _TWITTER_DEFAULT_IMAGES):
                return [MediaItemData(
                    post_id=post_id, post_url=url, media_type="video",
                    media_url=media_url, caption=caption,
                )]
        if og_image:
            media_url = og_image[0].attrib.get("content", "")
            if media_url and not any(d in media_url for d in _TWITTER_DEFAULT_IMAGES) \
                    and "profile_images" not in media_url:
                return [MediaItemData(
                    post_id=post_id, post_url=url, media_type="image",
                    media_url=media_url, caption=caption,
                )]
    except Exception as exc:
        logger.debug("Twitter meta tag fallback failed: {}", exc)

    return []


def _find_tweet_in_response(data: Any, target_id: str) -> dict | None:
    """Recursively find a tweet object by rest_id."""
    if isinstance(data, dict):
        if data.get("rest_id") == target_id and "legacy" in data:
            return data
        # Check result.legacy too
        if str(data.get("id_str", "")) == target_id and "extended_entities" in data:
            return {"rest_id": target_id, "legacy": data}
        for v in data.values():
            found = _find_tweet_in_response(v, target_id)
            if found:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_tweet_in_response(v, target_id)
            if found:
                return found
    return None


def _twitter_media_from_tweet(tweet: dict, post_id: str, post_url: str) -> list[MediaItemData]:
    """Extract media from a tweet object."""
    legacy = tweet.get("legacy", tweet)
    caption = legacy.get("full_text", "")
    posted_at = None
    created_at = legacy.get("created_at")
    if created_at:
        try:
            posted_at = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            pass

    extended = legacy.get("extended_entities", {})
    media_list = extended.get("media", [])
    if not media_list:
        media_list = legacy.get("entities", {}).get("media", [])

    items = []
    for media in media_list:
        media_type_raw = media.get("type", "photo")

        if media_type_raw in ("video", "animated_gif"):
            variants = media.get("video_info", {}).get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4_variants:
                best = max(mp4_variants, key=lambda v: v.get("bitrate", 0))
                media_url = best.get("url", "")
            else:
                media_url = variants[0]["url"] if variants else ""
            m_type = "video"
            duration = media.get("video_info", {}).get("duration_millis")
            duration = duration / 1000 if duration else None
        else:
            media_url = media.get("media_url_https", media.get("media_url", ""))
            if media_url and ":orig" not in media_url:
                media_url += ":orig"
            m_type = "image"
            duration = None

        if media_url:
            size = media.get("original_info", {})
            items.append(MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type=m_type,
                media_url=media_url,
                caption=caption,
                posted_at=posted_at,
                width=size.get("width"),
                height=size.get("height"),
                duration=duration,
            ))

    return items


# ---------------------------------------------------------------------------
# Reddit single post
# ---------------------------------------------------------------------------
def _extract_reddit(url: str, post_id: str) -> list[MediaItemData]:
    """Extract media from a single Reddit post."""
    intercepted: list[dict] = []
    pw_cookies = _load_cookies("reddit")

    def page_action(page):
        nonlocal intercepted

        # Register response listener BEFORE any reload
        def on_response(response):
            resp_url = response.url
            if any(f in resp_url for f in ("/svc/shreddit/", ".json", "gateway.reddit.com", "oauth.reddit.com")):
                try:
                    intercepted.append(response.json())
                except Exception:
                    pass

        page.on("response", on_response)

        if pw_cookies:
            page.context.add_cookies(pw_cookies)

        # Always reload to trigger API requests while listener is active
        page.reload(wait_until="load")
        page.wait_for_timeout(3000)

    try:
        fetch_kwargs = dict(headless=True, page_action=page_action)
        proxy = get_proxy_for_platform("reddit")
        if proxy:
            fetch_kwargs["proxy"] = proxy
            fetch_kwargs["geoip"] = True
        adaptor = StealthyFetcher.fetch(url, **fetch_kwargs)
    except Exception as exc:
        logger.error("Reddit fetch failed: {}", exc)
        return []

    # Find post data in intercepted responses
    post_data = None
    for body in intercepted:
        post_data = _find_reddit_post(body, post_id)
        if post_data:
            break

    # Try embedded JSON
    if not post_data:
        try:
            for tag in adaptor.css('script[type="application/json"]'):
                try:
                    blob = json.loads(tag.text)
                    post_data = _find_reddit_post(blob, post_id)
                    if post_data:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            pass

    # Try window.__REDDIT_DATA__
    if not post_data:
        try:
            html = adaptor.html
            for pattern in [r'window\.__REDDIT_DATA__\s*=\s*(\{.+\})', r'window\.___r\s*=\s*(\{.+\})']:
                m = re.search(pattern, html)
                if m:
                    blob = json.loads(m.group(1))
                    post_data = _find_reddit_post(blob, post_id)
                    if post_data:
                        break
        except Exception:
            pass

    if not post_data:
        return []

    return _reddit_media_from_post(post_data, post_id, url)


def _find_reddit_post(data: Any, target_id: str) -> dict | None:
    """Recursively find a Reddit post object by ID."""
    if isinstance(data, dict):
        # Direct match
        pid = data.get("id", "")
        name = data.get("name", "")
        if (pid == target_id or pid == f"t3_{target_id}" or name == f"t3_{target_id}"):
            if any(k in data for k in ("url", "media", "is_gallery", "is_video", "preview")):
                return data
        for v in data.values():
            found = _find_reddit_post(v, target_id)
            if found:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_reddit_post(v, target_id)
            if found:
                return found
    return None


def _reddit_media_from_post(post: dict, post_id: str, post_url: str) -> list[MediaItemData]:
    """Extract media items from a Reddit post dict."""
    items = []
    caption = post.get("title", "")
    posted_at = None
    created_utc = post.get("created_utc") or post.get("created")
    if created_utc:
        try:
            posted_at = datetime.fromtimestamp(int(float(created_utc)))
        except (ValueError, OSError):
            pass

    # Gallery
    if post.get("is_gallery"):
        gallery = post.get("gallery_data", {}).get("items", [])
        metadata = post.get("media_metadata", {})
        for gi in gallery:
            media_id = gi.get("media_id", "")
            meta = metadata.get(media_id, {})
            s = meta.get("s", {})
            media_url = unescape(s.get("u", "") or s.get("gif", "") or s.get("mp4", ""))
            if media_url:
                m_type = "video" if s.get("mp4") else "image"
                items.append(MediaItemData(
                    post_id=post_id,
                    post_url=post_url,
                    media_type=m_type,
                    media_url=media_url,
                    caption=caption,
                    posted_at=posted_at,
                    width=s.get("x"),
                    height=s.get("y"),
                ))
        return items

    # Video
    if post.get("is_video"):
        reddit_video = post.get("media", {}).get("reddit_video", {})
        if not reddit_video:
            reddit_video = post.get("secure_media", {}).get("reddit_video", {})
        video_url = reddit_video.get("fallback_url", "")
        if video_url:
            items.append(MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type="video",
                media_url=video_url,
                caption=caption,
                posted_at=posted_at,
                width=reddit_video.get("width"),
                height=reddit_video.get("height"),
                duration=reddit_video.get("duration"),
            ))
            return items

    # Image
    post_url_field = post.get("url", "")
    if post_url_field and ("i.redd.it" in post_url_field or "i.imgur.com" in post_url_field):
        ext = post_url_field.rsplit(".", 1)[-1].lower()
        m_type = "video" if ext in ("mp4", "gif", "gifv") else "image"
        w, h = None, None
        try:
            preview = post.get("preview", {}).get("images", [{}])[0].get("source", {})
            w = preview.get("width")
            h = preview.get("height")
        except (IndexError, AttributeError):
            pass
        items.append(MediaItemData(
            post_id=post_id,
            post_url=post_url,
            media_type=m_type,
            media_url=post_url_field,
            caption=caption,
            posted_at=posted_at,
            width=w,
            height=h,
        ))

    return items


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
_PLATFORM_HANDLERS = {
    "instagram": _extract_instagram,
    "tiktok": _extract_tiktok,
    "twitter": _extract_twitter,
    "reddit": _extract_reddit,
}


def quick_download(url: str) -> QuickDownloadResult:
    """
    Download media from a single post URL.

    Returns a QuickDownloadResult with the list of downloaded media items.
    """
    detection = detect_platform(url)
    if detection is None:
        return QuickDownloadResult(
            platform="unknown", post_id="", post_url=url,
            error="URL non reconnue. Plateformes supportées: Instagram, TikTok, Twitter/X, Reddit",
        )

    platform, post_id = detection
    logger.info("Quick download: platform={}, post_id={}, url={}", platform, post_id, url)

    handler = _PLATFORM_HANDLERS.get(platform)
    if not handler:
        return QuickDownloadResult(
            platform=platform, post_id=post_id, post_url=url,
            error=f"Plateforme {platform} non supportée pour le téléchargement direct",
        )

    # Extract media items
    try:
        media_items = handler(url, post_id)
    except Exception as exc:
        logger.exception("Quick download extraction failed: {}", exc)
        return QuickDownloadResult(
            platform=platform, post_id=post_id, post_url=url,
            error=f"Erreur d'extraction: {exc}",
        )

    if not media_items:
        return QuickDownloadResult(
            platform=platform, post_id=post_id, post_url=url,
            error="Aucun média trouvé sur cette page. Vérifie que le lien est correct et que tu as les cookies de session.",
        )

    # Download each media item
    results = []
    for item in media_items:
        try:
            dl = download_media(item.media_url)
            results.append({
                "post_id": item.post_id,
                "post_url": item.post_url,
                "media_type": item.media_type,
                "media_url": item.media_url,
                "local_path": dl.local_path,
                "file_size": dl.file_size,
                "content_hash": dl.content_hash,
                "caption": item.caption,
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
            })
            logger.info("Downloaded: {} ({} bytes)", dl.local_path, dl.file_size)
        except Exception as exc:
            logger.warning("Download failed for {}: {}", item.media_url[:80], exc)
            results.append({
                "post_id": item.post_id,
                "post_url": item.post_url,
                "media_type": item.media_type,
                "media_url": item.media_url,
                "error": str(exc),
            })

    return QuickDownloadResult(
        platform=platform,
        post_id=post_id,
        post_url=url,
        media_items=results,
    )
