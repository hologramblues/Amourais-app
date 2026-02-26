"""TikTok extractor using Scrapling StealthyFetcher.

Extracts media (videos, photo posts) from a TikTok profile page by:
1. Loading session cookies from data/sessions/tiktok.json
2. Intercepting /api/post/item_list and /api/user/detail responses
3. Parsing the #__UNIVERSAL_DATA_FOR_REHYDRATION__ embedded script
4. DOM scraping as a last-resort fallback
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from scrapling.fetchers import StealthyFetcher

from app.scraper.base import (
    ExtractOptions,
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSIONS_DIR = Path("data/sessions")
_COOKIE_FILE = _SESSIONS_DIR / "tiktok.json"
_POST_URL_TEMPLATE = "https://www.tiktok.com/@{author}/video/{video_id}"
_BACKFILL_MAX_AGE_SECONDS = 2 * 365 * 24 * 3600  # ~2 years


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_cookies_raw(path: Path) -> list[dict]:
    """Load Cookie Editor JSON and return raw list."""
    if not path.exists():
        logger.warning("Cookie file not found: {}", path)
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        logger.error("Expected a JSON array in {}", path)
        return []
    return data


def _to_playwright_cookies(raw: list[dict]) -> list[dict]:
    """Convert Cookie Editor format to Playwright cookie format."""
    pw_cookies: list[dict] = []
    for c in raw:
        cookie: dict[str, Any] = {
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


def _walk_json(obj: Any, visitor, *, depth: int = 0, max_depth: int = 40):
    """Recursively walk a JSON tree, calling *visitor(obj)* on every dict."""
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        visitor(obj)
        for v in obj.values():
            _walk_json(v, visitor, depth=depth + 1, max_depth=max_depth)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, visitor, depth=depth + 1, max_depth=max_depth)


def _unix_to_datetime(ts: int | float | None) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def _extract_username_from_url(url: str) -> str:
    """Extract @username from a TikTok profile URL."""
    match = re.search(r"tiktok\.com/@([^/?#]+)", url)
    return match.group(1) if match else "unknown"


def _safe_get(d: dict, *keys, default=None):
    """Safely traverse nested dicts."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k)
        elif isinstance(current, list) and isinstance(k, int) and k < len(current):
            current = current[k]
        else:
            return default
        if current is None:
            return default
    return current


def _extract_video_url(video_obj: dict) -> str:
    """Get the best video URL from a TikTok video object."""
    # playAddr can be a string or an object with urlList
    play_addr = video_obj.get("playAddr", "")
    if isinstance(play_addr, dict):
        url_list = play_addr.get("urlList", [])
        play_addr = url_list[0] if url_list else ""
    elif isinstance(play_addr, str):
        pass

    download_addr = video_obj.get("downloadAddr", "")
    if isinstance(download_addr, dict):
        url_list = download_addr.get("urlList", [])
        download_addr = url_list[0] if url_list else ""
    elif isinstance(download_addr, str):
        pass

    return play_addr or download_addr or ""


def _extract_cover_url(video_obj: dict) -> str:
    """Get the best cover/thumbnail URL from a TikTok video object."""
    for key in ("originCover", "cover", "dynamicCover"):
        val = video_obj.get(key, "")
        if isinstance(val, dict):
            url_list = val.get("urlList", [])
            if url_list:
                return url_list[0]
        elif isinstance(val, str) and val:
            return val
    return ""


def _media_items_from_node(node: dict, author: str) -> list[dict]:
    """Return a list of raw media dicts from a TikTok item node."""
    items: list[dict] = []
    video_id = str(node.get("id", ""))
    if not video_id:
        return items

    post_url = _POST_URL_TEMPLATE.format(author=author, video_id=video_id)
    caption = node.get("desc", "") or None
    timestamp = node.get("createTime")
    posted_at = _unix_to_datetime(timestamp)

    # Check for photo/image post first
    image_post = node.get("imagePost")
    if image_post and isinstance(image_post, dict):
        images = image_post.get("images", [])
        for idx, img in enumerate(images):
            image_url_obj = img.get("imageURL", {})
            url_list = image_url_obj.get("urlList", []) if isinstance(image_url_obj, dict) else []
            media_url = url_list[0] if url_list else ""
            if not media_url:
                continue

            width = img.get("imageWidth")
            height = img.get("imageHeight")
            child_id = f"{video_id}_{idx}" if len(images) > 1 else video_id

            items.append({
                "post_id": child_id,
                "post_url": post_url,
                "media_type": "image",
                "media_url": media_url,
                "caption": caption,
                "posted_at": posted_at,
                "width": int(width) if width else None,
                "height": int(height) if height else None,
                "duration": None,
            })
        return items

    # Regular video post
    video_obj = node.get("video", {})
    if not isinstance(video_obj, dict):
        return items

    media_url = _extract_video_url(video_obj)
    if not media_url:
        # Fall back to cover image if video URL is unavailable
        media_url = _extract_cover_url(video_obj)
        if media_url:
            items.append({
                "post_id": video_id,
                "post_url": post_url,
                "media_type": "image",
                "media_url": media_url,
                "caption": caption,
                "posted_at": posted_at,
                "width": int(video_obj.get("width", 0)) or None,
                "height": int(video_obj.get("height", 0)) or None,
                "duration": None,
            })
        return items

    duration = video_obj.get("duration")
    width = video_obj.get("width")
    height = video_obj.get("height")

    items.append({
        "post_id": video_id,
        "post_url": post_url,
        "media_type": "video",
        "media_url": media_url,
        "caption": caption,
        "posted_at": posted_at,
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "duration": float(duration) if duration else None,
    })
    return items


def _extract_profile_info_from_user(user: dict) -> ProfileInfo:
    """Extract profile info from a TikTok user object."""
    info = ProfileInfo()
    info.display_name = user.get("nickname") or user.get("uniqueId")
    avatar = user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb", "")
    if isinstance(avatar, dict):
        url_list = avatar.get("urlList", [])
        avatar = url_list[0] if url_list else ""
    info.avatar_url = avatar or None
    return info


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class TikTokExtractor(PlatformExtractor):
    platform = "tiktok"

    def extract(
        self,
        profile_url: str,
        known_post_ids: set[str],
        options: ExtractOptions | None = None,
    ) -> ExtractorResult:
        opts = options or ExtractOptions()
        result = ExtractorResult()
        seen_ids: set[str] = set()
        intercepted_responses: list[dict] = []
        raw_cookies = _load_cookies_raw(_COOKIE_FILE)
        pw_cookies = _to_playwright_cookies(raw_cookies)
        author = _extract_username_from_url(profile_url)

        now_ts = time.time()
        cutoff_ts = now_ts - _BACKFILL_MAX_AGE_SECONDS

        # -- Page action callback -------------------------------------------
        def page_action(page):
            nonlocal intercepted_responses

            # 1. Inject cookies
            if pw_cookies:
                logger.info("Adding {} TikTok cookies", len(pw_cookies))
                page.context.add_cookies(pw_cookies)
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(2000)

            # 2. Set up response interception
            def on_response(response):
                url = response.url
                if any(frag in url for frag in (
                    "/api/post/item_list",
                    "/api/user/detail",
                    "/api/comment/list",
                )):
                    try:
                        body = response.json()
                        intercepted_responses.append(body)
                        logger.debug("Intercepted TikTok API response from {}", url[:120])
                    except Exception:
                        pass

            page.on("response", on_response)

            # 3. Scroll the page
            scroll_count = opts.max_scrolls
            logger.info("Scrolling TikTok profile (up to {} scrolls)", scroll_count)
            for i in range(scroll_count):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

                if i > 0 and i % 5 == 0:
                    current_height = page.evaluate("document.body.scrollHeight")
                    page.wait_for_timeout(500)
                    new_height = page.evaluate("document.body.scrollHeight")
                    if new_height == current_height:
                        logger.info("No new content after scroll {}, stopping", i)
                        break

        # -- Fetch ----------------------------------------------------------
        logger.info("Fetching TikTok profile: {}", profile_url)
        try:
            adaptor = StealthyFetcher.fetch(
                profile_url,
                headless=True,
                network_idle=True,
                page_action=page_action,
            )
        except Exception as exc:
            logger.error("StealthyFetcher failed for TikTok: {}", exc)
            return result

        # -- Phase 1: Parse __UNIVERSAL_DATA_FOR_REHYDRATION__ --------------
        rehydration_nodes: list[dict] = []
        try:
            script_el = adaptor.css("script#__UNIVERSAL_DATA_FOR_REHYDRATION__").first
            if script_el:
                blob = json.loads(script_el.text)
                self._collect_item_nodes(blob, rehydration_nodes)
                self._extract_profile_from_blob(blob, result)
                logger.info(
                    "Found {} media nodes from rehydration data", len(rehydration_nodes)
                )
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Rehydration JSON parse failed: {}", exc)

        # -- Phase 2: Parse intercepted API responses -----------------------
        api_nodes: list[dict] = []
        for resp_body in intercepted_responses:
            try:
                # item_list responses have an "itemList" key
                item_list = resp_body.get("itemList", [])
                if item_list:
                    api_nodes.extend(item_list)
                # Also try user detail for profile info
                user_info = _safe_get(resp_body, "userInfo", "user")
                if user_info and isinstance(user_info, dict):
                    pinfo = _extract_profile_info_from_user(user_info)
                    if pinfo.display_name and not result.profile_info.display_name:
                        result.profile_info.display_name = pinfo.display_name
                    if pinfo.avatar_url and not result.profile_info.avatar_url:
                        result.profile_info.avatar_url = pinfo.avatar_url
                # Generic walk for anything else
                self._collect_item_nodes(resp_body, api_nodes)
            except Exception as exc:
                logger.debug("Error parsing intercepted TikTok response: {}", exc)
        logger.info("Found {} media nodes from API interception", len(api_nodes))

        # -- Phase 3: Build media list from all nodes -----------------------
        all_nodes = rehydration_nodes + api_nodes
        stop_early = False

        # Deduplicate nodes by id before processing
        unique_nodes: dict[str, dict] = {}
        for node in all_nodes:
            nid = str(node.get("id", ""))
            if nid and nid not in unique_nodes:
                unique_nodes[nid] = node
        deduped_nodes = list(unique_nodes.values())

        for node in deduped_nodes:
            if stop_early:
                break

            # Update author from the node if available
            node_author = _safe_get(node, "author", "uniqueId") or author
            raw_items = _media_items_from_node(node, node_author)

            for item in raw_items:
                pid = item["post_id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

                if opts.scrape_mode == "daily" and pid in known_post_ids:
                    logger.info("Daily mode: hit known post {}, stopping", pid)
                    stop_early = True
                    break

                if opts.scrape_mode == "backfill":
                    if item["posted_at"]:
                        if item["posted_at"].timestamp() < cutoff_ts:
                            logger.info(
                                "Backfill mode: post {} older than 2 years, stopping", pid
                            )
                            stop_early = True
                            break
                    if pid in known_post_ids:
                        continue

                result.media.append(MediaItemData(**item))

        # -- Phase 4: DOM fallback ------------------------------------------
        if not result.media and not deduped_nodes:
            logger.info("No media from JSON, attempting DOM fallback")
            self._dom_fallback(adaptor, result, seen_ids, known_post_ids, opts, author)

        logger.info(
            "TikTok extraction complete: {} media items, profile={}",
            len(result.media),
            result.profile_info.display_name or "(unknown)",
        )
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _collect_item_nodes(blob: Any, out: list[dict]):
        """Walk a JSON blob and collect objects that look like TikTok item nodes."""
        seen_in_walk: set[str] = set()

        def _visitor(obj: dict):
            nid = str(obj.get("id", ""))
            if not nid or nid in seen_in_walk:
                return
            has_video = "video" in obj and isinstance(obj.get("video"), dict)
            has_image_post = "imagePost" in obj and isinstance(obj.get("imagePost"), dict)
            has_create_time = "createTime" in obj
            if (has_video or has_image_post) and has_create_time:
                seen_in_walk.add(nid)
                out.append(obj)

        _walk_json(blob, _visitor)

    @staticmethod
    def _extract_profile_from_blob(blob: dict, result: ExtractorResult):
        """Try to pull profile info from the rehydration blob."""

        def _visitor(obj: dict):
            # Look for user info structures
            if "uniqueId" in obj and "nickname" in obj:
                pinfo = _extract_profile_info_from_user(obj)
                if pinfo.display_name and not result.profile_info.display_name:
                    result.profile_info.display_name = pinfo.display_name
                if pinfo.avatar_url and not result.profile_info.avatar_url:
                    result.profile_info.avatar_url = pinfo.avatar_url

        _walk_json(blob, _visitor)

    @staticmethod
    def _dom_fallback(
        adaptor,
        result: ExtractorResult,
        seen_ids: set[str],
        known_post_ids: set[str],
        opts: ExtractOptions,
        author: str,
    ):
        """Extract minimal data from DOM elements."""
        # TikTok video links typically match /video/{id}
        links = adaptor.css('a[href*="/video/"]')
        logger.debug("DOM fallback found {} video links", len(links))
        for link in links:
            href = link.attrib.get("href", "")
            match = re.search(r"/video/(\d+)", href)
            if not match:
                continue
            video_id = match.group(1)
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            if opts.scrape_mode == "daily" and video_id in known_post_ids:
                logger.info("DOM fallback daily mode: hit known post {}", video_id)
                break
            if opts.scrape_mode == "backfill" and video_id in known_post_ids:
                continue

            # Try to find a thumbnail/cover image
            img = link.css("img").first
            media_url = ""
            if img:
                media_url = img.attrib.get("src", "")

            if not media_url:
                continue

            post_url = _POST_URL_TEMPLATE.format(author=author, video_id=video_id)
            result.media.append(
                MediaItemData(
                    post_id=video_id,
                    post_url=post_url,
                    media_type="image",  # Only thumbnail available from DOM
                    media_url=media_url,
                )
            )
