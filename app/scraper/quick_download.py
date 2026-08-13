"""
Quick download — download media from a single URL (post link).

Detects the platform from the URL, navigates to the page using
StealthyFetcher, extracts media links via API interception + DOM parsing,
and downloads the files.

Supports: Instagram, TikTok, Twitter/X, Reddit.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit

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
# URL safety — SSRF (lot 2.4)
# ---------------------------------------------------------------------------
# The URL comes from the user and is opened by a HEADLESS BROWSER running on
# the server, inside the private network of the platform. `detect_platform`
# is NOT a filter: its patterns use `search`, so
# `http://169.254.169.254/x#instagram.com/p/aaa` matches "instagram" while
# pointing at the cloud metadata endpoint.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Host names that always designate the machine itself.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    # Cloud metadata services, reachable by name inside the VPC.
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})


# The browser does NOT read a host the way `ipaddress` does. Per the WHATWG URL
# standard — which Chromium implements — these three code points are all valid
# label separators, and `http://127。0。0。1/` reaches the loopback.
_DOT_CODEPOINTS = ("。", "．", "｡")


def _parse_ipv4_relaxed(host: str) -> str | None:
    """Canonical dotted quad for the SHORTHAND forms a browser accepts.

    `ipaddress.ip_address` only understands the strict dotted quad, so
    `2130706433`, `0x7f000001`, `0177.0.0.1` and `127.1` all slipped through
    the guard while Chromium resolves every one of them to `127.0.0.1`.
    This mirrors the WHATWG IPv4 parser: 1 to 4 parts, each decimal, octal
    (`0` prefix) or hexadecimal (`0x` prefix), the last part filling all the
    remaining bytes. Returns None when the host is not an IPv4 address at all.
    """
    parts = host.split(".")
    if len(parts) > 4:
        return None

    nombres: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            if part[:2] in ("0x", "0X"):
                n = int(part[2:] or "0", 16)
            elif part[0] == "0" and len(part) > 1:
                n = int(part[1:], 8)
            else:
                n = int(part, 10)
        except ValueError:
            return None  # a label with a letter → domain name, not an IPv4
        if n < 0:
            return None
        nombres.append(n)

    # Every part but the last must fit in one byte; the last fills the rest.
    if any(n > 255 for n in nombres[:-1]):
        return None
    if nombres[-1] >= 256 ** (4 - (len(nombres) - 1)):
        return None

    valeur = nombres[-1]
    for i, n in enumerate(nombres[:-1]):
        valeur += n * 256 ** (3 - i)
    return str(ipaddress.IPv4Address(valeur))


def _host_is_internal(hostname: str) -> bool:
    """True for loopback / private / link-local / reserved destinations.

    No DNS resolution is attempted: only literal IPs and well-known names are
    judged (a name lookup here would be a second, TOCTOU-prone network call).
    """
    host = hostname.strip().lower()
    for point in _DOT_CODEPOINTS:
        host = host.replace(point, ".")
    host = host.strip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return True

    # Bracketed IPv6 literal: [::1]
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a strict literal — try the shorthand IPv4 forms the browser
        # still resolves before concluding "domain name".
        quad = _parse_ipv4_relaxed(host)
        if quad is None:
            return False  # a regular domain name
        ip = ipaddress.ip_address(quad)

    # 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16, 169.254/16, 0.0.0.0, …
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    # IPv4 mapped / 6to4 wrappers around a private v4 address.
    mapped = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if mapped is not None:
        return _host_is_internal(str(mapped))
    return False


def validate_public_url(url: str) -> str | None:
    """Return an error message when `url` must NOT be fetched, else None."""
    if not isinstance(url, str) or not url.strip():
        return "URL requise"

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "URL invalide"

    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return "URL invalide : seuls les schemas http et https sont acceptes"

    try:
        hostname = parts.hostname  # lower-cased, brackets stripped, port removed
    except ValueError:  # malformed IPv6 literal
        return "URL invalide : hote illisible"

    if not hostname:
        return "URL invalide : hote manquant"

    if _host_is_internal(hostname):
        return "URL refusee : cette adresse designe le reseau interne du serveur"

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
                expires = float(c["expirationDate"])
                # A past expiry makes Playwright drop the cookie entirely.
                # Send it as a session cookie instead — Instagram pairs
                # sessionid with ds_user_id, so losing one breaks the login.
                if expires > time.time():
                    cookie["expires"] = expires
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
def _try_instagram_embed(post_id: str, post_url: str) -> list[MediaItemData]:
    """
    Try the public Instagram /embed/ page to fetch post media.
    Works without login for public posts — no browser needed.
    """
    import httpx

    embed_url = f"https://www.instagram.com/p/{post_id}/embed/captioned/"
    try:
        resp = httpx.get(
            embed_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html",
            },
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.debug("Instagram embed page returned {}", resp.status_code)
            return []
        raw = resp.text
    except Exception as exc:
        logger.debug("Instagram embed fetch failed: {}", exc)
        return []

    def _unescape_url(u: str) -> str:
        u = re.sub(r"\\+/", "/", u)
        return u.encode("ascii", "ignore").decode("unicode_escape")

    caption = ""
    cap_match = re.search(r'class="Caption"[^>]*>(.*?)</div>', raw, re.DOTALL)
    if cap_match:
        caption = unescape(re.sub(r"<[^>]+>", " ", cap_match.group(1))).strip()[:500]

    # The embed page carries the post JSON double-escaped in a script blob.
    video_match = re.search(r'video_url\\+":\\+"(.*?)\\+"', raw)
    if video_match:
        media_url = _unescape_url(video_match.group(1))
        if media_url.startswith("https://"):
            logger.info("Instagram embed: found video for {}", post_id)
            return [MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type="video",
                media_url=media_url,
                caption=caption,
            )]

    # Image posts: the embed page renders the image directly.
    img_match = (
        re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', raw)
        or re.search(r'display_url\\+":\\+"(.*?)\\+"', raw)
    )
    if img_match:
        media_url = unescape(_unescape_url(img_match.group(1)))
        if media_url.startswith("https://"):
            logger.info("Instagram embed: found image for {}", post_id)
            return [MediaItemData(
                post_id=post_id,
                post_url=post_url,
                media_type="image",
                media_url=media_url,
                caption=caption,
            )]

    logger.debug("Instagram embed page had no usable media for {}", post_id)
    return []


def _extract_instagram(url: str, post_id: str) -> list[MediaItemData]:
    """Extract media from a single Instagram post/reel."""
    # Fast path: the public embed page needs no browser and no cookies.
    items = _try_instagram_embed(post_id, url)
    if items:
        return items

    intercepted: list[dict] = []
    final_url: list[str] = []
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

        # The first (cookie-less) load gets redirected to the homepage/login
        # for anonymous visitors — reload() would just reload THAT page.
        # Navigate back to the post now that cookies are set.
        # Use "load" — Instagram has constant background requests (analytics, tracking)
        # that prevent networkidle from ever being reached, causing timeouts.
        page.goto(url, wait_until="load")
        page.wait_for_timeout(4000)
        final_url.append(page.url)

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
        # Only trust og tags if we actually landed on the post page — after a
        # redirect to the homepage/login they point at Instagram's own logo.
        landed = final_url[0] if final_url else ""
        if post_id not in landed:
            logger.warning(
                "Instagram redirected away from post {} (landed on {}) — "
                "cookies missing or expired?", post_id, landed or "?",
            )
            return []
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

    # Second gate (lot 2.4): the patterns above only `search` the string, so a
    # recognised URL can still point at the server's own network.
    faute = validate_public_url(url)
    if faute:
        logger.warning("Quick download refused for {}: {}", url[:120], faute)
        return QuickDownloadResult(
            platform=detection[0], post_id=detection[1], post_url=url, error=faute,
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
            # Generic on purpose (lot 2.4b): the exception text can carry
            # absolute volume paths or full SQL statements.
            error="Erreur d'extraction (voir les logs serveur)",
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
                # Never the raw exception text: an OSError carries the absolute
                # path of the volume, a SQLAlchemy error the whole statement
                # (lot 2.4b). The detail stays in the log line above.
                "error": "Telechargement impossible (voir les logs serveur)",
            })

    return QuickDownloadResult(
        platform=platform,
        post_id=post_id,
        post_url=url,
        media_items=results,
    )
