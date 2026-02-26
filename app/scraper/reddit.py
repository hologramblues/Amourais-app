"""Reddit extractor using Scrapling StealthyFetcher.

Extracts media (images, videos, galleries) from a Reddit user's submitted posts by:
1. Loading session cookies from data/sessions/reddit.json
2. Intercepting Reddit API responses during page scrolling
3. Parsing embedded JSON (window.__REDDIT_DATA__, application/json scripts)
4. DOM scraping as a last-resort fallback
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html import unescape
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
_COOKIE_FILE = _SESSIONS_DIR / "reddit.json"
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


def _unix_to_datetime(ts) -> datetime | None:
    """Convert a Unix timestamp to a timezone-aware datetime."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _extract_username_from_url(url: str) -> str:
    """Extract username from a Reddit user URL."""
    match = re.search(r"reddit\.com/user/([^/?#]+)", url)
    return match.group(1) if match else "unknown"


def _strip_t3_prefix(post_id: str) -> str:
    """Strip the 't3_' prefix from a Reddit fullname."""
    if post_id.startswith("t3_"):
        return post_id[3:]
    return post_id


def _extract_media_from_post(post_data: dict) -> list[dict]:
    """Extract media items from a single Reddit post data dict.

    Handles:
    - Image posts (post_hint == "image"): data.url -> i.redd.it URL
    - Video posts (is_video == True): data.media.reddit_video.fallback_url
    - Gallery posts (is_gallery == True): gallery_data + media_metadata
    - Fallback: check if data.url contains i.redd.it or i.imgur.com
    """
    items: list[dict] = []

    # Extract post identifiers
    post_id = str(post_data.get("id", "") or post_data.get("name", ""))
    post_id = _strip_t3_prefix(post_id)
    if not post_id:
        return items

    permalink = post_data.get("permalink", "")
    post_url = f"https://www.reddit.com{permalink}" if permalink else ""
    caption = post_data.get("title")
    created_utc = post_data.get("created_utc")
    posted_at = _unix_to_datetime(created_utc)

    is_video = post_data.get("is_video", False)
    is_gallery = post_data.get("is_gallery", False)
    post_hint = post_data.get("post_hint", "")

    # --- Gallery posts (multiple images) ---
    if is_gallery:
        gallery_data = post_data.get("gallery_data", {})
        media_metadata = post_data.get("media_metadata", {})
        gallery_items = (
            gallery_data.get("items", []) if isinstance(gallery_data, dict) else []
        )

        for idx, gallery_item in enumerate(gallery_items):
            media_id = gallery_item.get("media_id", "")
            meta = (
                media_metadata.get(media_id, {})
                if isinstance(media_metadata, dict)
                else {}
            )
            if not meta:
                continue

            status = meta.get("status", "")
            if status != "valid":
                continue

            source = meta.get("s", {})
            # 'u' is URL-encoded, 'gif' for animated, 'mp4' for video
            media_url = (
                source.get("u", "") or source.get("gif", "") or source.get("mp4", "")
            )
            # Reddit HTML-encodes the URL in media_metadata
            if media_url:
                media_url = unescape(media_url)

            if not media_url:
                continue

            media_type = "video" if source.get("mp4") else "image"
            width = source.get("x")
            height = source.get("y")

            child_id = f"{post_id}_{idx}"
            items.append(
                {
                    "post_id": child_id,
                    "post_url": post_url,
                    "media_type": media_type,
                    "media_url": media_url,
                    "caption": caption,
                    "posted_at": posted_at,
                    "width": int(width) if width else None,
                    "height": int(height) if height else None,
                    "duration": None,
                }
            )
        return items

    # --- Video posts ---
    if is_video:
        reddit_video = (post_data.get("media") or {}).get("reddit_video", {})
        fallback_url = reddit_video.get("fallback_url", "")
        if not fallback_url:
            # Try secure_media as alternative
            reddit_video = (post_data.get("secure_media") or {}).get(
                "reddit_video", {}
            )
            fallback_url = reddit_video.get("fallback_url", "")

        if fallback_url:
            duration = reddit_video.get("duration")
            width = reddit_video.get("width")
            height = reddit_video.get("height")

            items.append(
                {
                    "post_id": post_id,
                    "post_url": post_url,
                    "media_type": "video",
                    "media_url": fallback_url,
                    "caption": caption,
                    "posted_at": posted_at,
                    "width": int(width) if width else None,
                    "height": int(height) if height else None,
                    "duration": float(duration) if duration else None,
                }
            )
        return items

    # --- Image posts ---
    if post_hint == "image" or (
        post_data.get("url", "").startswith("https://i.redd.it/")
    ):
        media_url = post_data.get("url", "")
        if media_url:
            # Get dimensions from preview if available
            preview = post_data.get("preview", {})
            images = preview.get("images", [])
            width = None
            height = None
            if images:
                source = images[0].get("source", {})
                width = source.get("width")
                height = source.get("height")

            items.append(
                {
                    "post_id": post_id,
                    "post_url": post_url,
                    "media_type": "image",
                    "media_url": media_url,
                    "caption": caption,
                    "posted_at": posted_at,
                    "width": int(width) if width else None,
                    "height": int(height) if height else None,
                    "duration": None,
                }
            )
        return items

    # --- Fallback: check if URL points to i.redd.it or i.imgur.com ---
    url = post_data.get("url", "")
    if url and any(domain in url for domain in ("i.redd.it", "i.imgur.com")):
        ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
        media_type = "video" if ext in ("mp4", "gif", "gifv") else "image"
        items.append(
            {
                "post_id": post_id,
                "post_url": post_url,
                "media_type": media_type,
                "media_url": url,
                "caption": caption,
                "posted_at": posted_at,
                "width": None,
                "height": None,
                "duration": None,
            }
        )

    return items


def _extract_profile_info_from_data(data: dict) -> ProfileInfo:
    """Extract profile info from Reddit user data."""
    info = ProfileInfo()
    # Reddit user objects have "name" (username) and "subreddit.title" (display name)
    info.display_name = data.get("name") or data.get("author")
    subreddit = data.get("subreddit", {})
    if isinstance(subreddit, dict):
        display = subreddit.get("title") or subreddit.get("display_name")
        if display:
            info.display_name = display
    # Avatar
    icon_img = data.get("icon_img") or data.get("snoovatar_img", "")
    if icon_img:
        info.avatar_url = unescape(icon_img)
    return info


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class RedditExtractor(PlatformExtractor):
    platform = "reddit"

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
        username = _extract_username_from_url(profile_url)

        now_ts = time.time()
        cutoff_ts = now_ts - _BACKFILL_MAX_AGE_SECONDS

        # Ensure we navigate to /submitted (posts only, not comments)
        submitted_url = self._build_submitted_url(profile_url)

        # -- Page action callback -------------------------------------------
        def page_action(page):
            nonlocal intercepted_responses

            # 1. Inject cookies
            if pw_cookies:
                logger.info("Adding {} Reddit cookies", len(pw_cookies))
                page.context.add_cookies(pw_cookies)
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(2000)

            # 2. Set up response interception
            def on_response(response):
                url = response.url
                # Intercept Reddit API calls, .json responses, and GraphQL
                if any(
                    frag in url
                    for frag in (
                        "/svc/shreddit/",
                        "/api/v1/",
                        ".json",
                        "gateway.reddit.com",
                        "oauth.reddit.com",
                    )
                ):
                    try:
                        body = response.json()
                        intercepted_responses.append(body)
                        logger.debug(
                            "Intercepted Reddit API response from {}", url[:120]
                        )
                    except Exception:
                        pass

            page.on("response", on_response)

            # 3. Scroll the page
            scroll_count = opts.max_scrolls
            logger.info("Scrolling Reddit profile (up to {} scrolls)", scroll_count)
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
        logger.info("Fetching Reddit profile: {}", submitted_url)
        try:
            adaptor = StealthyFetcher.fetch(
                submitted_url,
                headless=True,
                network_idle=True,
                page_action=page_action,
            )
        except Exception as exc:
            logger.error("StealthyFetcher failed for Reddit: {}", exc)
            return result

        # -- Phase 1: Parse intercepted API responses -----------------------
        api_posts: list[dict] = []
        for resp_body in intercepted_responses:
            try:
                self._collect_post_data(resp_body, api_posts)
                self._extract_profile_from_response(resp_body, result)
            except Exception as exc:
                logger.debug("Error parsing intercepted Reddit response: {}", exc)

        # Also check for standard Reddit listing format in intercepted responses
        for resp_body in intercepted_responses:
            if isinstance(resp_body, dict) and "data" in resp_body:
                children = resp_body.get("data", {}).get("children", [])
                for child in children:
                    if isinstance(child, dict) and child.get("kind") == "t3":
                        child_data = child.get("data", {})
                        if child_data and child_data.get("id"):
                            api_posts.append(child_data)

        logger.info("Found {} posts from API interception", len(api_posts))

        # -- Phase 2: Parse embedded JSON from DOM --------------------------
        embedded_posts: list[dict] = []
        try:
            script_tags = adaptor.css("script")
            for tag in script_tags:
                text = tag.text or ""
                # Look for window.__REDDIT_DATA__ or window.___r
                for pattern in (
                    r"window\.__REDDIT_DATA__\s*=\s*(\{.+\})",
                    r"window\.___r\s*=\s*(\{.+\})",
                ):
                    match = re.search(pattern, text, re.DOTALL)
                    if match:
                        try:
                            blob = json.loads(match.group(1))
                            self._collect_post_data(blob, embedded_posts)
                            self._extract_profile_from_response(blob, result)
                        except json.JSONDecodeError:
                            pass
                # Also try <script type="application/json"> tags
                if tag.attrib.get("type") == "application/json":
                    try:
                        blob = json.loads(text)
                        self._collect_post_data(blob, embedded_posts)
                    except (json.JSONDecodeError, TypeError):
                        pass
            logger.info("Found {} posts from embedded JSON", len(embedded_posts))
        except Exception as exc:
            logger.warning("Embedded JSON parsing failed: {}", exc)

        # -- Phase 3: Build media list --------------------------------------
        all_posts = api_posts + embedded_posts
        stop_early = False

        # Deduplicate posts by id
        unique_posts: dict[str, dict] = {}
        for post in all_posts:
            pid = str(post.get("id", ""))
            pid = _strip_t3_prefix(pid)
            if pid and pid not in unique_posts:
                unique_posts[pid] = post
        deduped_posts = list(unique_posts.values())

        for post in deduped_posts:
            if stop_early:
                break

            raw_items = _extract_media_from_post(post)
            for item in raw_items:
                pid = item["post_id"]
                # For gallery child items, check the base post_id too
                base_pid = pid.split("_")[0] if "_" in pid else pid
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

                if opts.scrape_mode == "daily" and (
                    pid in known_post_ids or base_pid in known_post_ids
                ):
                    logger.info("Daily mode: hit known post {}, stopping", pid)
                    stop_early = True
                    break

                if opts.scrape_mode == "backfill":
                    if item["posted_at"]:
                        if item["posted_at"].timestamp() < cutoff_ts:
                            logger.info(
                                "Backfill mode: post {} older than 2 years, stopping",
                                pid,
                            )
                            stop_early = True
                            break
                    if pid in known_post_ids or base_pid in known_post_ids:
                        continue

                result.media.append(MediaItemData(**item))

        # -- Phase 4: DOM fallback ------------------------------------------
        if not result.media and not deduped_posts:
            logger.info("No media from API/JSON, attempting DOM fallback")
            self._dom_fallback(adaptor, result, seen_ids, known_post_ids, opts)

        logger.info(
            "Reddit extraction complete: {} media items, profile={}",
            len(result.media),
            result.profile_info.display_name or username,
        )
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_submitted_url(profile_url: str) -> str:
        """Ensure the URL points to the /submitted tab."""
        url = profile_url.rstrip("/")
        # Remove existing tab suffix
        url = re.sub(r"/(submitted|comments|gilded|overview)$", "", url)
        return url + "/submitted"

    @staticmethod
    def _collect_post_data(blob: Any, out: list[dict]):
        """Walk a JSON blob and collect Reddit post data objects."""
        seen_in_walk: set[str] = set()

        def _visitor(obj: dict):
            pid = str(obj.get("id", ""))
            pid = _strip_t3_prefix(pid)
            if not pid or pid in seen_in_walk:
                return
            has_permalink = "permalink" in obj
            has_media_indicator = (
                obj.get("is_video")
                or obj.get("is_gallery")
                or obj.get("post_hint") in ("image", "hosted:video", "rich:video")
                or (
                    isinstance(obj.get("url"), str)
                    and "i.redd.it" in obj.get("url", "")
                )
                or obj.get("media_metadata")
            )
            if has_permalink and has_media_indicator:
                seen_in_walk.add(pid)
                out.append(obj)

        _walk_json(blob, _visitor)

    @staticmethod
    def _extract_profile_from_response(blob: dict, result: ExtractorResult):
        """Try to pull profile info from Reddit API response."""

        def _visitor(obj: dict):
            if ("icon_img" in obj or "snoovatar_img" in obj) and "name" in obj:
                pinfo = _extract_profile_info_from_data(obj)
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
    ):
        """Extract minimal data from DOM elements."""
        # Reddit post links contain /comments/{id}/
        links = adaptor.css('a[href*="/comments/"]')
        logger.debug("DOM fallback found {} comment links", len(links))
        for link in links:
            href = link.attrib.get("href", "")
            match = re.search(r"/comments/([a-z0-9]+)", href)
            if not match:
                continue
            post_id = match.group(1)
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            if opts.scrape_mode == "daily" and post_id in known_post_ids:
                logger.info("DOM fallback daily mode: hit known post {}", post_id)
                break
            if opts.scrape_mode == "backfill" and post_id in known_post_ids:
                continue

            # Try to find thumbnail image
            img = link.css("img").first
            media_url = ""
            if img:
                src = img.attrib.get("src", "")
                # Filter out Reddit UI icons; keep actual thumbnails
                if any(
                    domain in src
                    for domain in ("preview.redd.it", "i.redd.it", "external-preview")
                ):
                    media_url = src

            if not media_url:
                continue

            post_url = (
                f"https://www.reddit.com{href}" if href.startswith("/") else href
            )
            result.media.append(
                MediaItemData(
                    post_id=post_id,
                    post_url=post_url,
                    media_type="image",
                    media_url=media_url,
                )
            )
