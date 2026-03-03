"""Instagram extractor using Scrapling StealthyFetcher.

Extracts media (images, videos, carousels) from an Instagram profile page by:
1. Loading session cookies from data/sessions/instagram.json
2. Intercepting GraphQL / API responses for structured JSON data
3. Parsing embedded <script type="application/json"> tags as fallback
4. DOM scraping as a last-resort fallback
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
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

_COOKIE_FILE = SESSIONS_DIR / "instagram.json"
_POST_URL_TEMPLATE = "https://www.instagram.com/p/{shortcode}/"
_REEL_URL_TEMPLATE = "https://www.instagram.com/reel/{shortcode}/"
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


def _extract_caption(node: dict) -> str | None:
    """Pull caption text from an Instagram media node."""
    # GraphQL format
    edges = (
        node.get("edge_media_to_caption", {}).get("edges")
        or node.get("caption", {}).get("edges") if isinstance(node.get("caption"), dict) else None
    )
    if edges and isinstance(edges, list) and len(edges) > 0:
        text = edges[0].get("node", {}).get("text")
        if text:
            return text
    # API v1 format
    cap = node.get("caption")
    if isinstance(cap, dict):
        return cap.get("text")
    if isinstance(cap, str):
        return cap
    return None


def _media_items_from_node(node: dict) -> list[dict]:
    """Return a list of raw media dicts from a single Instagram post node.

    Handles single image/video and carousel (sidecar) posts.
    """
    items: list[dict] = []
    shortcode = node.get("shortcode") or node.get("code") or ""
    post_id = node.get("pk") or node.get("id") or shortcode
    if not post_id:
        return items

    post_id = str(post_id)
    post_url = _POST_URL_TEMPLATE.format(shortcode=shortcode) if shortcode else ""
    caption = _extract_caption(node)
    timestamp = node.get("taken_at_timestamp") or node.get("taken_at")
    posted_at = _unix_to_datetime(timestamp)

    # Sidecar / carousel
    sidecar_edges = (
        node.get("edge_sidecar_to_children", {}).get("edges", [])
        or node.get("carousel_media", [])
    )

    children: list[dict] = []
    if sidecar_edges:
        for edge in sidecar_edges:
            child = edge.get("node", edge) if isinstance(edge, dict) else edge
            if isinstance(child, dict):
                children.append(child)
    else:
        children.append(node)

    for idx, child in enumerate(children):
        is_video = (
            child.get("is_video", False)
            or child.get("media_type") == 2
            or "Video" in str(child.get("__typename", ""))
            or child.get("product_type") in ("clips", "igtv")
            or "video_url" in child
            or "video_versions" in child
        )
        if is_video:
            video_versions = child.get("video_versions") or []
            media_url = (
                child.get("video_url")
                or (video_versions[0].get("url", "") if video_versions else "")
            )
            media_type = "video"
        else:
            media_url = (
                child.get("display_url")
                or child.get("display_src")
                or child.get("thumbnail_src")
                or ""
            )
            # Try image_versions2 (API v1 format)
            if not media_url:
                candidates = (child.get("image_versions2") or {}).get("candidates") or []
                if candidates:
                    media_url = candidates[0].get("url", "")
            media_type = "image"

        if not media_url:
            continue

        width = child.get("dimensions", {}).get("width") or child.get("original_width")
        height = child.get("dimensions", {}).get("height") or child.get("original_height")
        duration = child.get("video_duration") if is_video else None

        # Engagement counts (from post-level node, not per-child)
        like_count = (
            node.get("like_count")
            or node.get("edge_media_preview_like", {}).get("count")
            or node.get("edge_liked_by", {}).get("count")
        )
        comment_count_ig = (
            node.get("comment_count")
            or node.get("edge_media_to_comment", {}).get("count")
            or node.get("edge_media_preview_comment", {}).get("count")
        )
        view_count = (
            node.get("video_view_count")
            or node.get("play_count")
            or node.get("view_count")
        ) if is_video else None

        child_id = f"{post_id}_{idx}" if len(children) > 1 else post_id

        items.append({
            "post_id": child_id,
            "post_url": post_url,
            "media_type": media_type,
            "media_url": media_url,
            "caption": caption,
            "posted_at": posted_at,
            "width": int(width) if width else None,
            "height": int(height) if height else None,
            "duration": float(duration) if duration else None,
            "like_count": int(like_count) if like_count else None,
            "comment_count": int(comment_count_ig) if comment_count_ig else None,
            "view_count": int(view_count) if view_count else None,
        })

    return items


def _extract_profile_info(data: Any) -> ProfileInfo | None:
    """Try to pull display_name, avatar_url, and account stats from any JSON blob."""
    info = ProfileInfo()
    if not isinstance(data, dict):
        return None

    def _visitor(obj: dict):
        if obj.get("full_name") and not info.display_name:
            info.display_name = obj["full_name"]
        if obj.get("profile_pic_url_hd") and not info.avatar_url:
            info.avatar_url = obj["profile_pic_url_hd"]
        elif obj.get("profile_pic_url") and not info.avatar_url:
            info.avatar_url = obj["profile_pic_url"]
        # Biography
        if obj.get("biography") and not info.biography:
            info.biography = obj["biography"]
        # Verified
        if "is_verified" in obj and info.is_verified is None:
            info.is_verified = bool(obj["is_verified"])
        # Followers count (GraphQL: edge_followed_by.count, API v1: follower_count)
        if info.followers_count is None:
            fc = obj.get("edge_followed_by", {}).get("count") if isinstance(obj.get("edge_followed_by"), dict) else None
            if fc is None:
                fc = obj.get("follower_count")
            if fc is not None:
                info.followers_count = int(fc)
        # Following count
        if info.following_count is None:
            fg = obj.get("edge_follow", {}).get("count") if isinstance(obj.get("edge_follow"), dict) else None
            if fg is None:
                fg = obj.get("following_count")
            if fg is not None:
                info.following_count = int(fg)
        # Media count (total posts)
        if info.media_count is None:
            mc = obj.get("edge_owner_to_timeline_media", {}).get("count") if isinstance(obj.get("edge_owner_to_timeline_media"), dict) else None
            if mc is None:
                mc = obj.get("media_count")
            if mc is not None:
                info.media_count = int(mc)

    _walk_json(data, _visitor)
    if info.display_name or info.avatar_url or info.followers_count is not None:
        return info
    return None


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class InstagramExtractor(PlatformExtractor):
    platform = "instagram"

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

        # -- Page action callback -------------------------------------------
        def page_action(page):
            nonlocal intercepted_responses

            # 1. Inject cookies and reload
            if pw_cookies:
                logger.info("Adding {} Instagram cookies", len(pw_cookies))
                page.context.add_cookies(pw_cookies)
                try:
                    # Use "load" instead of "networkidle" — Instagram has constant
                    # background requests (analytics, tracking) that prevent
                    # networkidle from ever being reached, causing a 30s timeout.
                    page.reload(wait_until="load")
                    page.wait_for_timeout(3000)
                except Exception as reload_exc:
                    logger.warning("Page reload after cookies failed: {}. Continuing anyway.", reload_exc)
                    page.wait_for_timeout(2000)

            # 1b. Dismiss consent / GDPR / ad-free subscription popups
            try:
                current_url = page.url
                if "/consent" in current_url or "/challenge" in current_url:
                    logger.info("Detected consent/challenge page: {}", current_url[:120])
                    for selector in [
                        'button:has-text("Not now")',
                        'button:has-text("Pas maintenant")',
                        'button:has-text("Decline optional cookies")',
                        'button:has-text("Refuser")',
                        'a:has-text("Not now")',
                        'a:has-text("Pas maintenant")',
                    ]:
                        try:
                            btn = page.locator(selector).first
                            if btn.is_visible(timeout=1000):
                                btn.click()
                                logger.info("Clicked dismiss button: {}", selector)
                                page.wait_for_timeout(2000)
                                break
                        except Exception:
                            continue

                    # If still on consent page, navigate directly to the profile
                    if "/consent" in page.url or "/challenge" in page.url:
                        logger.info("Still on consent page, navigating directly to profile")
                        try:
                            page.goto(profile_url, wait_until="load")
                            page.wait_for_timeout(3000)
                        except Exception:
                            logger.warning("Direct navigation after consent failed, continuing")
            except Exception as consent_exc:
                logger.debug("Consent page handling: {}", consent_exc)

            # Also dismiss any overlay/modal popups on the profile page
            try:
                for selector in [
                    'button:has-text("Not Now")',
                    'button:has-text("Pas maintenant")',
                    'button:has-text("Not now")',
                    'button:has-text("Decline optional cookies")',
                    '[role="dialog"] button:has-text("Not Now")',
                    '[role="dialog"] button:has-text("Pas maintenant")',
                ]:
                    try:
                        btn = page.locator(selector).first
                        if btn.is_visible(timeout=500):
                            btn.click()
                            logger.info("Dismissed overlay popup: {}", selector)
                            page.wait_for_timeout(1000)
                    except Exception:
                        continue
            except Exception:
                pass

            # 2. Set up response interception BEFORE scrolling
            def on_response(response):
                url = response.url
                if any(frag in url for frag in ("/graphql", "/api/v1/users/", "/api/v1/feed/", "/api/v1/clips/")):
                    try:
                        body = response.json()
                        intercepted_responses.append(body)
                        logger.debug("Intercepted Instagram API response from {}", url[:120])
                    except Exception:
                        pass

            page.on("response", on_response)

            # 3. Scroll the page
            scroll_count = opts.max_scrolls
            is_backfill = opts.scrape_mode == "backfill"
            # Backfill: wait longer between scrolls, check less often, allow stalls
            scroll_pause = 2500 if is_backfill else 1800
            check_interval = 10 if is_backfill else 8
            max_stalls = 3 if is_backfill else 2  # allow stalls before stopping (IG loads async)
            stall_count = 0

            logger.info("Scrolling Instagram profile (up to {} scrolls, mode={})", scroll_count, opts.scrape_mode)
            for i in range(scroll_count):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(scroll_pause)

                # Early-stop heuristic: check if new content appeared
                if i > 0 and i % check_interval == 0:
                    current_height = page.evaluate("document.body.scrollHeight")
                    page.wait_for_timeout(1000)
                    new_height = page.evaluate("document.body.scrollHeight")
                    if new_height == current_height:
                        stall_count += 1
                        logger.info(
                            "No new content after scroll {} (stall {}/{})",
                            i, stall_count, max_stalls,
                        )
                        if stall_count >= max_stalls:
                            logger.info("Max stalls reached at scroll {}, stopping", i)
                            break
                        # Wait a bit longer before retrying
                        page.wait_for_timeout(2000)
                    else:
                        stall_count = 0  # reset on new content

            logger.info("Scrolling done after {} iterations (stalls={})", min(i + 1, scroll_count), stall_count)

        # -- Fetch ----------------------------------------------------------
        logger.info("Fetching Instagram profile: {}", profile_url)
        try:
            fetch_kwargs = dict(
                headless=True,
                network_idle=True,
                page_action=page_action,
            )
            if opts.proxy:
                fetch_kwargs["proxy"] = opts.proxy
                fetch_kwargs["geoip"] = True  # Spoof location to match proxy IP
                logger.info("Instagram fetch with proxy + geoip enabled")
            adaptor = StealthyFetcher.fetch(profile_url, **fetch_kwargs)
        except Exception as exc:
            logger.error("StealthyFetcher failed for Instagram: {}", exc)
            return result

        # -- Debug: log page status ------------------------------------------
        try:
            page_title = adaptor.css("title").first
            title_text = page_title.text if page_title else "(no title)"
            page_text = adaptor.get_all_text()[:500] if hasattr(adaptor, 'get_all_text') else ""
            logger.info("Instagram page title: {}", title_text)
            if page_text:
                logger.info("Instagram page text preview: {}", page_text[:300])
            # Detect login/challenge page
            page_html = str(adaptor.html)[:2000] if hasattr(adaptor, 'html') else ""
            if "login" in page_html.lower() or "challenge" in page_html.lower():
                logger.warning("Instagram returned a login/challenge page — proxy or cookies may be blocked")
            if "suspicious" in page_html.lower() or "automated" in page_html.lower():
                logger.warning("Instagram detected automation — bot detection triggered")
        except Exception as dbg_exc:
            logger.debug("Debug page inspection failed: {}", dbg_exc)

        # -- Phase 1: Parse embedded JSON -----------------------------------
        embedded_nodes: list[dict] = []
        try:
            script_tags = adaptor.css('script[type="application/json"]')
            for tag in script_tags:
                try:
                    blob = json.loads(tag.text)
                    self._collect_media_nodes(blob, embedded_nodes)
                except (json.JSONDecodeError, TypeError):
                    continue
            logger.info("Found {} media nodes from embedded JSON", len(embedded_nodes))
        except Exception as exc:
            logger.warning("Embedded JSON parsing failed: {}", exc)

        # -- Phase 2: Parse intercepted API responses -----------------------
        api_nodes: list[dict] = []
        for resp_body in intercepted_responses:
            try:
                self._collect_media_nodes(resp_body, api_nodes)
            except Exception as exc:
                logger.debug("Error parsing intercepted response: {}", exc)
        logger.info("Found {} media nodes from API interception", len(api_nodes))

        # -- Phase 3: Profile info ------------------------------------------
        for blob_set in [intercepted_responses, embedded_nodes]:
            for blob in blob_set:
                pinfo = _extract_profile_info(blob)
                if pinfo:
                    if pinfo.display_name and not result.profile_info.display_name:
                        result.profile_info.display_name = pinfo.display_name
                    if pinfo.avatar_url and not result.profile_info.avatar_url:
                        result.profile_info.avatar_url = pinfo.avatar_url

        # -- Phase 4: Build media list from JSON ----------------------------
        all_nodes = embedded_nodes + api_nodes
        stop_early = False

        for node in all_nodes:
            if stop_early:
                break
            raw_items = _media_items_from_node(node)
            for item in raw_items:
                pid = item["post_id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                result.total_seen += 1

                # Daily mode: stop when we hit already-known content
                if opts.scrape_mode == "daily" and pid in known_post_ids:
                    logger.info("Daily mode: hit known post {}, stopping", pid)
                    stop_early = True
                    break

                # Backfill mode: skip known but stop if too old
                if opts.scrape_mode == "backfill":
                    if item["posted_at"]:
                        if item["posted_at"].timestamp() < cutoff_ts:
                            logger.info("Backfill mode: post {} older than 2 years, stopping", pid)
                            stop_early = True
                            break
                    if pid in known_post_ids:
                        logger.debug("Backfill: skipping known post {}", pid)
                        continue

                result.media.append(MediaItemData(**item))

        # -- Phase 5: DOM fallback ------------------------------------------
        if not result.media and not all_nodes:
            logger.info("No media from JSON, attempting DOM fallback")
            self._dom_fallback(adaptor, result, seen_ids, known_post_ids, opts, cutoff_ts)

        logger.info(
            "Instagram extraction complete: {} media items, profile={}",
            len(result.media),
            result.profile_info.display_name or "(unknown)",
        )
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _collect_media_nodes(blob: Any, out: list[dict]):
        """Walk a JSON blob and collect objects that look like IG media nodes."""

        def _visitor(obj: dict):
            # Must have a shortcode (or code) AND some media URL
            has_shortcode = "shortcode" in obj or "code" in obj
            has_media = (
                "display_url" in obj
                or "video_url" in obj
                or "image_versions2" in obj
                or "video_versions" in obj
                or "carousel_media" in obj
            )
            if has_shortcode and has_media:
                out.append(obj)

        _walk_json(blob, _visitor)

    @staticmethod
    def _dom_fallback(
        adaptor,
        result: ExtractorResult,
        seen_ids: set[str],
        known_post_ids: set[str],
        opts: ExtractOptions,
        cutoff_ts: float,
    ):
        """Extract minimal data from DOM links and images."""
        links = adaptor.css('a[href*="/p/"], a[href*="/reel/"]')
        logger.debug("DOM fallback found {} post/reel links", len(links))
        for link in links:
            href = link.attrib.get("href", "")
            shortcode = ""
            match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
            if match:
                shortcode = match.group(1)
            if not shortcode or shortcode in seen_ids:
                continue
            seen_ids.add(shortcode)
            result.total_seen += 1

            if opts.scrape_mode == "daily" and shortcode in known_post_ids:
                logger.info("DOM fallback daily mode: hit known post {}", shortcode)
                break
            if opts.scrape_mode == "backfill" and shortcode in known_post_ids:
                logger.debug("DOM fallback: skipping known post {}", shortcode)
                continue

            # Try to find an image inside the link
            img = link.css("img").first
            media_url = ""
            if img:
                media_url = img.attrib.get("src", "")

            if not media_url:
                continue

            post_url = f"https://www.instagram.com{href}" if href.startswith("/") else href
            result.media.append(
                MediaItemData(
                    post_id=shortcode,
                    post_url=post_url,
                    media_type="video" if "/reel/" in href else "image",
                    media_url=media_url,
                )
            )
