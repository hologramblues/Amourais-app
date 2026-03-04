"""
Instagram Graph API client for @samourais_.

Provides access to real account insights via the official Meta Graph API.
Requires:
    - Instagram Business/Creator account
    - Facebook App with instagram_basic + instagram_manage_insights permissions
    - Long-lived access token (auto-refreshed every ~55 days)

Environment variables (stored in DATA_DIR/.env via Settings UI):
    IG_ACCESS_TOKEN   — long-lived user access token
    IG_USER_ID        — Instagram Business Account ID (numeric)
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from loguru import logger

from app.config import SETTINGS_ENV

GRAPH_API_VERSION = "v22.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# ── Token helpers ──────────────────────────────────────────


def _read_env_value(key: str) -> str:
    """Read a value from the persistent .env (re-reads every time)."""
    from dotenv import load_dotenv
    load_dotenv(SETTINGS_ENV, override=True)
    return os.getenv(key, "")


def get_access_token() -> str:
    return _read_env_value("IG_ACCESS_TOKEN")


def get_ig_user_id() -> str:
    return _read_env_value("IG_USER_ID")


def is_configured() -> bool:
    """Check if IG Graph API credentials are set."""
    return bool(get_access_token() and get_ig_user_id())


# ── Low-level API call ─────────────────────────────────────


def _api_get(endpoint: str, params: dict | None = None) -> dict[str, Any]:
    """Make a GET request to the Graph API."""
    token = get_access_token()
    if not token:
        raise RuntimeError("IG_ACCESS_TOKEN is not set")

    url = f"{GRAPH_BASE}/{endpoint}"
    p = dict(params or {})
    p["access_token"] = token

    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=p)

    data = resp.json()
    if "error" in data:
        err = data["error"]
        code = err.get("code", "?")
        msg = err.get("message", "Unknown error")
        raise RuntimeError(f"Graph API error {code}: {msg}")

    return data


# ── Token management ───────────────────────────────────────


def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    """Exchange a short-lived token for a long-lived one (~60 days).

    Returns {"access_token": "...", "token_type": "bearer", "expires_in": seconds}.
    """
    app_id = _read_env_value("FB_APP_ID")
    app_secret = _read_env_value("FB_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("FB_APP_ID and FB_APP_SECRET must be set")

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token exchange failed: {data['error'].get('message')}")
    return data


def refresh_long_lived_token() -> dict:
    """Refresh an existing long-lived token (valid if not yet expired).

    Returns {"access_token": "...", "token_type": "bearer", "expires_in": seconds}.
    """
    token = get_access_token()
    if not token:
        raise RuntimeError("No existing token to refresh")

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": _read_env_value("FB_APP_ID"),
                "client_secret": _read_env_value("FB_APP_SECRET"),
                "fb_exchange_token": token,
            },
        )
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data['error'].get('message')}")
    return data


def discover_ig_user_id() -> dict:
    """Auto-discover the Instagram Business Account ID from connected Facebook Pages.

    Returns {"ig_user_id": "...", "page_name": "...", "page_id": "..."}.
    """
    # Step 1: get user's pages
    pages_data = _api_get("me/accounts", {"fields": "id,name,instagram_business_account"})
    pages = pages_data.get("data", [])

    for page in pages:
        ig_account = page.get("instagram_business_account")
        if ig_account:
            return {
                "ig_user_id": ig_account["id"],
                "page_name": page.get("name", ""),
                "page_id": page["id"],
            }

    raise RuntimeError(
        "Aucun compte Instagram Business/Creator trouvé lié à tes Pages Facebook. "
        "Vérifie que @samourais_ est bien un compte Pro lié à une Page."
    )


# ── Profile data ───────────────────────────────────────────


def fetch_profile() -> dict:
    """Fetch basic profile info for the IG Business Account.

    Returns: {id, username, name, biography, followers_count, follows_count,
              media_count, profile_picture_url, website}
    """
    ig_id = get_ig_user_id()
    fields = (
        "id,username,name,biography,followers_count,follows_count,"
        "media_count,profile_picture_url,website"
    )
    return _api_get(ig_id, {"fields": fields})


# ── Account insights ───────────────────────────────────────


def fetch_account_insights(period: str = "day", days: int = 30) -> dict:
    """Fetch account-level insights.

    Available metrics (period=day):
        reach, impressions, accounts_engaged, profile_views,
        follower_count (lifetime)

    Returns raw API response with data[].values[].
    """
    ig_id = get_ig_user_id()

    # Calculate time range
    now = int(time.time())
    since = now - (days * 86400)

    metrics = "reach,impressions,accounts_engaged,profile_views"

    return _api_get(
        f"{ig_id}/insights",
        {
            "metric": metrics,
            "period": period,
            "since": since,
            "until": now,
        },
    )


def fetch_follower_count_insights(days: int = 30) -> dict:
    """Fetch follower_count as a lifetime metric (returns daily values).

    This is separate because follower_count uses period=day but metric_type=total_value
    in newer API versions, or we can use the time_series approach.
    """
    ig_id = get_ig_user_id()
    now = int(time.time())
    since = now - (days * 86400)

    return _api_get(
        f"{ig_id}/insights",
        {
            "metric": "follower_count",
            "period": "day",
            "since": since,
            "until": now,
        },
    )


# ── Media insights ─────────────────────────────────────────


def fetch_recent_media(limit: int = 50) -> list[dict]:
    """Fetch recent media posts with basic fields.

    Returns list of {id, caption, media_type, timestamp, like_count, comments_count,
                     permalink, thumbnail_url, media_url}.
    """
    ig_id = get_ig_user_id()
    fields = (
        "id,caption,media_type,timestamp,like_count,comments_count,"
        "permalink,thumbnail_url,media_url"
    )

    all_media = []
    result = _api_get(f"{ig_id}/media", {"fields": fields, "limit": min(limit, 50)})
    all_media.extend(result.get("data", []))

    # Paginate if needed
    while len(all_media) < limit:
        paging = result.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break
        with httpx.Client(timeout=30) as client:
            resp = client.get(next_url)
        result = resp.json()
        if "error" in result:
            break
        all_media.extend(result.get("data", []))

    return all_media[:limit]


def fetch_media_insights(media_id: str) -> dict:
    """Fetch insights for a single media post.

    Returns: {reach, impressions, saved, shares, likes, comments, ...}
    """
    # Different metrics for different media types
    metrics = "reach,impressions,saved,shares,likes,comments,plays"

    try:
        data = _api_get(
            f"{media_id}/insights",
            {"metric": metrics},
        )
        # Flatten into a simple dict
        result = {}
        for item in data.get("data", []):
            name = item["name"]
            values = item.get("values", [{}])
            result[name] = values[0].get("value", 0) if values else 0
        return result
    except RuntimeError as e:
        # Some metrics aren't available for all media types
        logger.debug("Could not fetch insights for media {}: {}", media_id, e)
        return {}


# ── Demographics ───────────────────────────────────────────


def fetch_audience_demographics() -> dict:
    """Fetch audience demographics (requires 100+ followers).

    Returns: {cities: [...], countries: [...], age_gender: [...]}
    """
    ig_id = get_ig_user_id()

    result = {}

    # These are lifetime metrics
    for metric in ["follower_demographics"]:
        try:
            data = _api_get(
                f"{ig_id}/insights",
                {
                    "metric": metric,
                    "period": "lifetime",
                    "metric_type": "total_value",
                    "timeframe": "this_month",
                },
            )
            for item in data.get("data", []):
                breakdown = item.get("total_value", {}).get("breakdowns", [])
                if breakdown:
                    result[item["name"]] = breakdown
        except RuntimeError as e:
            logger.debug("Could not fetch {}: {}", metric, e)

    return result
