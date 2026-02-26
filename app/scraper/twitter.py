"""Twitter / X extractor using Scrapling StealthyFetcher.

Extracts media (photos, videos) from a Twitter/X profile page by:
1. Loading session cookies from data/sessions/twitter.json
2. Intercepting GraphQL API responses (/i/api/graphql/)
3. Navigating to the /media tab for media-only content
4. DOM scraping as a last-resort fallback
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

from app.config import SESSIONS_DIR

_COOKIE_FILE = SESSIONS_DIR / "twitter.json"
_POST_URL_TEMPLATE = "https://x.com/i/status/{tweet_id}"
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


def _parse_twitter_date(date_str: str | None) -> datetime | None:
    """Parse Twitter's date format like 'Wed Oct 10 20:19:24 +0000 2018'."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except (ValueError, TypeError):
        pass
    # Fallback: manual parse
    try:
        return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None


def _pick_best_video_variant(variants: list[dict]) -> str:
    """Pick the highest-bitrate MP4 variant from Twitter video_info."""
    mp4_variants = [
        v for v in variants
        if v.get("content_type") == "video/mp4" and v.get("url")
    ]
    if not mp4_variants:
        # Fallback: any variant with a URL
        mp4_variants = [v for v in variants if v.get("url")]
    if not mp4_variants:
        return ""
    # Sort by bitrate descending, pick the highest
    mp4_variants.sort(key=lambda v: v.get("bitrate", 0), reverse=True)
    return mp4_variants[0]["url"]


def _extract_media_from_tweet(tweet: dict) -> list[dict]:
    """Extract media items from a Twitter tweet result object.

    The tweet object is expected to have 'rest_id' and 'legacy' keys
    (Twitter GraphQL format).
    """
    items: list[dict] = []
    tweet_id = str(tweet.get("rest_id", ""))
    legacy = tweet.get("legacy", {})
    if not isinstance(legacy, dict):
        return items
    if not tweet_id:
        tweet_id = str(legacy.get("id_str", ""))
    if not tweet_id:
        return items

    post_url = _POST_URL_TEMPLATE.format(tweet_id=tweet_id)
    caption = legacy.get("full_text")
    posted_at = _parse_twitter_date(legacy.get("created_at"))

    # Prefer extended_entities (has video info), fall back to entities
    ext_entities = legacy.get("extended_entities", {})
    entities = legacy.get("entities", {})
    media_list = ext_entities.get("media", []) or entities.get("media", [])

    if not media_list:
        return items

    for idx, media_obj in enumerate(media_list):
        media_type_raw = media_obj.get("type", "photo")  # "photo", "video", "animated_gif"

        if media_type_raw == "photo":
            media_url = media_obj.get("media_url_https", "") or media_obj.get("media_url", "")
            if media_url:
                # Append :orig for full resolution
                if not media_url.endswith(":orig"):
                    media_url = media_url + ":orig"

            original_info = media_obj.get("original_info", {})
            width = original_info.get("width") or media_obj.get("sizes", {}).get("large", {}).get("w")
            height = original_info.get("height") or media_obj.get("sizes", {}).get("large", {}).get("h")

            child_id = f"{tweet_id}_{idx}" if len(media_list) > 1 else tweet_id
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

        elif media_type_raw in ("video", "animated_gif"):
            video_info = media_obj.get("video_info", {})
            variants = video_info.get("variants", [])
            media_url = _pick_best_video_variant(variants)

            duration_ms = video_info.get("duration_millis")
            duration = float(duration_ms) / 1000.0 if duration_ms else None

            aspect_ratio = video_info.get("aspect_ratio", [])
            original_info = media_obj.get("original_info", {})
            width = original_info.get("width")
            height = original_info.get("height")
            if not width and len(aspect_ratio) == 2:
                # Approximate from aspect ratio (not exact)
                width = aspect_ratio[0]
                height = aspect_ratio[1]

            child_id = f"{tweet_id}_{idx}" if len(media_list) > 1 else tweet_id
            items.append({
                "post_id": child_id,
                "post_url": post_url,
                "media_type": "video",
                "media_url": media_url,
                "caption": caption,
                "posted_at": posted_at,
                "width": int(width) if width else None,
                "height": int(height) if height else None,
                "duration": duration,
            })

    return items


def _extract_profile_info_from_user(user: dict) -> ProfileInfo:
    """Extract profile info from a Twitter user object."""
    info = ProfileInfo()
    legacy = user.get("legacy", user)
    info.display_name = legacy.get("name")
    avatar = legacy.get("profile_image_url_https", "")
    # Get full-size avatar (remove _normal suffix)
    if avatar:
        avatar = avatar.replace("_normal.", ".")
    info.avatar_url = avatar or None
    return info


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class TwitterExtractor(PlatformExtractor):
    platform = "twitter"

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

        now_ts = time.time()
        cutoff_ts = now_ts - _BACKFILL_MAX_AGE_SECONDS

        # Ensure we navigate to the /media tab
        media_url = self._build_media_tab_url(profile_url)

        # -- Page action callback -------------------------------------------
        def page_action(page):
            nonlocal intercepted_responses

            # 1. Inject cookies
            if pw_cookies:
                logger.info("Adding {} Twitter cookies", len(pw_cookies))
                page.context.add_cookies(pw_cookies)
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(2000)

            # 2. Set up response interception
            def on_response(response):
                url = response.url
                if any(frag in url for frag in (
                    "/i/api/graphql/",
                    "UserMedia",
                    "UserTweets",
                )):
                    try:
                        body = response.json()
                        intercepted_responses.append(body)
                        logger.debug("Intercepted Twitter API response from {}", url[:120])
                    except Exception:
                        pass

            page.on("response", on_response)

            # 3. Navigate to /media tab if not already there
            current = page.url
            if "/media" not in current:
                logger.info("Navigating to /media tab")
                page.goto(media_url, wait_until="networkidle")
                page.wait_for_timeout(3000)

            # 4. Scroll the page
            scroll_count = opts.max_scrolls
            logger.info("Scrolling Twitter media tab (up to {} scrolls)", scroll_count)
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
        logger.info("Fetching Twitter profile: {}", media_url)
        try:
            adaptor = StealthyFetcher.fetch(
                media_url,
                headless=True,
                network_idle=True,
                page_action=page_action,
            )
        except Exception as exc:
            logger.error("StealthyFetcher failed for Twitter: {}", exc)
            return result

        # -- Phase 1: Parse intercepted API responses -----------------------
        tweet_objects: list[dict] = []
        for resp_body in intercepted_responses:
            try:
                self._collect_tweet_results(resp_body, tweet_objects)
                self._extract_profile_from_response(resp_body, result)
            except Exception as exc:
                logger.debug("Error parsing intercepted Twitter response: {}", exc)
        logger.info("Found {} tweet objects from API interception", len(tweet_objects))

        # -- Phase 2: Build media list --------------------------------------
        stop_early = False

        # Deduplicate tweet objects by rest_id
        unique_tweets: dict[str, dict] = {}
        for tw in tweet_objects:
            tid = str(tw.get("rest_id", ""))
            if tid and tid not in unique_tweets:
                unique_tweets[tid] = tw
        deduped_tweets = list(unique_tweets.values())

        for tweet in deduped_tweets:
            if stop_early:
                break

            raw_items = _extract_media_from_tweet(tweet)
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

        # -- Phase 3: DOM fallback ------------------------------------------
        if not result.media and not deduped_tweets:
            logger.info("No media from API, attempting DOM fallback")
            self._dom_fallback(adaptor, result, seen_ids, known_post_ids, opts)

        logger.info(
            "Twitter extraction complete: {} media items, profile={}",
            len(result.media),
            result.profile_info.display_name or "(unknown)",
        )
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_media_tab_url(profile_url: str) -> str:
        """Ensure the URL points to the /media tab."""
        url = profile_url.rstrip("/")
        # Remove existing tab suffix
        url = re.sub(r"/(media|likes|with_replies)$", "", url)
        return url + "/media"

    @staticmethod
    def _collect_tweet_results(blob: Any, out: list[dict]):
        """Walk a JSON blob and collect Twitter tweet result objects.

        Tweet results in GraphQL responses are dicts with 'rest_id' and
        'legacy' containing 'full_text' and 'entities'.
        """
        seen_in_walk: set[str] = set()

        def _visitor(obj: dict):
            rest_id = str(obj.get("rest_id", ""))
            if not rest_id or rest_id in seen_in_walk:
                return
            legacy = obj.get("legacy")
            if not isinstance(legacy, dict):
                return
            # Must have media in entities or extended_entities
            has_media = bool(
                legacy.get("extended_entities", {}).get("media")
                or legacy.get("entities", {}).get("media")
            )
            if has_media:
                seen_in_walk.add(rest_id)
                out.append(obj)

        _walk_json(blob, _visitor)

    @staticmethod
    def _extract_profile_from_response(blob: dict, result: ExtractorResult):
        """Try to pull profile info from a Twitter GraphQL response."""

        def _visitor(obj: dict):
            # User objects in GraphQL have __typename == "User" or "user" key
            # with legacy.name and legacy.profile_image_url_https
            if obj.get("__typename") == "User" or (
                "legacy" in obj
                and isinstance(obj["legacy"], dict)
                and "profile_image_url_https" in obj.get("legacy", {})
            ):
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
    ):
        """Extract minimal data from DOM elements."""
        # Tweet links: /username/status/{id}
        links = adaptor.css('a[href*="/status/"]')
        logger.debug("DOM fallback found {} status links", len(links))
        for link in links:
            href = link.attrib.get("href", "")
            match = re.search(r"/status/(\d+)", href)
            if not match:
                continue
            tweet_id = match.group(1)
            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)

            if opts.scrape_mode == "daily" and tweet_id in known_post_ids:
                logger.info("DOM fallback daily mode: hit known post {}", tweet_id)
                break
            if opts.scrape_mode == "backfill" and tweet_id in known_post_ids:
                continue

            # Try to find images inside tweet elements
            img = link.css("img").first
            media_url = ""
            if img:
                src = img.attrib.get("src", "")
                # Skip profile pics and emoji
                if "pbs.twimg.com/media" in src or "pbs.twimg.com/ext_tw_video_thumb" in src:
                    media_url = src

            if not media_url:
                continue

            post_url = _POST_URL_TEMPLATE.format(tweet_id=tweet_id)
            result.media.append(
                MediaItemData(
                    post_id=tweet_id,
                    post_url=post_url,
                    media_type="image",
                    media_url=media_url,
                )
            )
