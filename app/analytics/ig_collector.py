"""
Instagram Graph API data collector.

Runs as a scheduled job to fetch real account stats from the official
Instagram Graph API and store them in the database.

Called by the scheduler every 6 hours (and once at startup).
"""

from __future__ import annotations

import time
from datetime import datetime

from loguru import logger

from app.db import (
    IgInsightSnapshot,
    Profile,
    ProfileSnapshot,
    SessionLocal,
)


def collect_ig_stats() -> None:
    """Main collection job: fetch profile + insights from IG Graph API.

    Stores data in:
        - Profile (current followers/following/bio)
        - ProfileSnapshot (historical followers/following point-in-time)
        - IgInsightSnapshot (reach, impressions, profile_views, engagement)
    """
    from app.instagram_api import is_configured, fetch_profile, fetch_account_insights

    if not is_configured():
        logger.debug("IG Graph API not configured — skipping stats collection")
        return

    logger.info("Collecting Instagram stats via Graph API...")
    db = SessionLocal()
    now_ts = int(time.time())

    try:
        # ── 1. Fetch profile data ──────────────────────────────
        try:
            profile_data = fetch_profile()
        except RuntimeError as e:
            logger.error("Failed to fetch IG profile: {}", e)
            return

        username = profile_data.get("username", "samourais_")
        followers = profile_data.get("followers_count", 0)
        following = profile_data.get("follows_count", 0)
        media_count = profile_data.get("media_count", 0)
        bio = profile_data.get("biography", "")
        display_name = profile_data.get("name", "")
        avatar_url = profile_data.get("profile_picture_url", "")

        logger.info(
            "IG Profile @{}: {} followers, {} following, {} posts",
            username, followers, following, media_count,
        )

        # ── 2. Update or create Profile row ────────────────────
        profile = (
            db.query(Profile)
            .filter(Profile.platform == "instagram", Profile.username == username)
            .first()
        )

        if not profile:
            # Auto-create the profile (not for scraping, just for analytics)
            profile = Profile(
                platform="instagram",
                username=username,
                profile_url=f"https://www.instagram.com/{username}/",
                display_name=display_name,
                avatar_url=avatar_url,
                biography=bio,
                followers_count=followers,
                following_count=following,
                media_count=media_count,
                is_active=False,  # not active for scraping
                scrape_mode="daily",
                scrape_interval_minutes=99999,  # never auto-scrape
            )
            db.add(profile)
            db.flush()
            logger.info("Auto-created profile for @{} (id={})", username, profile.id)
        else:
            profile.display_name = display_name
            profile.avatar_url = avatar_url
            profile.biography = bio
            profile.followers_count = followers
            profile.following_count = following
            profile.media_count = media_count
            profile.updated_at = now_ts

        # ── 3. Create ProfileSnapshot ──────────────────────────
        # Check if we already have a snapshot today
        today_start = int(datetime(
            datetime.now().year, datetime.now().month, datetime.now().day
        ).timestamp())

        existing_snap = (
            db.query(ProfileSnapshot)
            .filter(
                ProfileSnapshot.profile_id == profile.id,
                ProfileSnapshot.snapshot_at >= today_start,
            )
            .first()
        )

        if existing_snap:
            # Update existing snapshot
            existing_snap.followers_count = followers
            existing_snap.following_count = following
            existing_snap.media_count = media_count
            logger.debug("Updated today's ProfileSnapshot")
        else:
            snap = ProfileSnapshot(
                profile_id=profile.id,
                followers_count=followers,
                following_count=following,
                media_count=media_count,
                snapshot_at=now_ts,
            )
            db.add(snap)
            logger.info("Created new ProfileSnapshot: {} followers", followers)

        # ── 4. Fetch account insights ──────────────────────────
        reach = 0
        impressions = 0
        accounts_engaged = 0
        profile_views = 0

        try:
            insights_data = fetch_account_insights(period="day", days=1)
            for item in insights_data.get("data", []):
                name = item.get("name", "")
                values = item.get("values", [])
                # Get the most recent value
                val = values[-1].get("value", 0) if values else 0
                if name == "reach":
                    reach = val
                elif name == "impressions":
                    impressions = val
                elif name == "accounts_engaged":
                    accounts_engaged = val
                elif name == "profile_views":
                    profile_views = val

            logger.info(
                "IG Insights: reach={}, impressions={}, engaged={}, profile_views={}",
                reach, impressions, accounts_engaged, profile_views,
            )
        except RuntimeError as e:
            logger.warning("Could not fetch account insights: {}", e)

        # ── 5. Store IgInsightSnapshot ─────────────────────────
        existing_insight = (
            db.query(IgInsightSnapshot)
            .filter(
                IgInsightSnapshot.profile_id == profile.id,
                IgInsightSnapshot.snapshot_at >= today_start,
            )
            .first()
        )

        if existing_insight:
            existing_insight.followers_count = followers
            existing_insight.following_count = following
            existing_insight.media_count = media_count
            existing_insight.reach = reach
            existing_insight.impressions = impressions
            existing_insight.accounts_engaged = accounts_engaged
            existing_insight.profile_views = profile_views
            logger.debug("Updated today's IgInsightSnapshot")
        else:
            insight = IgInsightSnapshot(
                profile_id=profile.id,
                followers_count=followers,
                following_count=following,
                media_count=media_count,
                reach=reach,
                impressions=impressions,
                accounts_engaged=accounts_engaged,
                profile_views=profile_views,
                snapshot_at=now_ts,
            )
            db.add(insight)
            logger.info("Created new IgInsightSnapshot")

        db.commit()
        logger.info("Instagram stats collection complete for @{}", username)

    except Exception as exc:
        logger.exception("Error collecting IG stats: {}", exc)
        db.rollback()
    finally:
        db.close()


def collect_media_insights() -> None:
    """Fetch insights for recent media posts and update the database.

    Enriches existing MediaItem rows with reach, saves, shares data.
    """
    from app.instagram_api import is_configured, fetch_recent_media, fetch_media_insights
    from app.db import MediaItem

    if not is_configured():
        return

    logger.info("Collecting media insights via Graph API...")
    db = SessionLocal()

    try:
        media_list = fetch_recent_media(limit=25)

        for m in media_list:
            permalink = m.get("permalink", "")
            ig_media_id = m.get("id", "")

            if not permalink:
                continue

            # Find matching MediaItem in our DB
            item = (
                db.query(MediaItem)
                .filter(MediaItem.post_url == permalink)
                .first()
            )

            if not item:
                continue

            # Fetch detailed insights
            insights = fetch_media_insights(ig_media_id)
            if not insights:
                continue

            # Update the media item with real insights
            if "likes" in insights:
                item.ig_like_count = insights["likes"]
            if "comments" in insights:
                item.ig_comment_count = insights["comments"]
            if "plays" in insights:
                item.ig_view_count = insights["plays"]

            logger.debug(
                "Updated insights for {}: likes={}, comments={}, views={}",
                permalink,
                insights.get("likes"),
                insights.get("comments"),
                insights.get("plays"),
            )

        db.commit()
        logger.info("Media insights collection complete ({} posts checked)", len(media_list))

    except Exception as exc:
        logger.exception("Error collecting media insights: {}", exc)
        db.rollback()
    finally:
        db.close()
