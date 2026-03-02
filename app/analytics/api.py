"""
Analytics API blueprint — Instagram account stats for @samourais_.

Endpoints:
    GET /api/analytics/account-overview      — profile info, followers, engagement rate
    GET /api/analytics/follower-growth       — historical followers/following per day
    GET /api/analytics/engagement            — engagement rate per post over period
    GET /api/analytics/content-breakdown     — images vs videos distribution
    GET /api/analytics/best-posting-times    — hour distribution of original post times
    GET /api/analytics/top-posts             — top 10 posts by engagement (likes + comments)
    GET /api/analytics/posting-frequency     — posts per week

All endpoints accept ?days=7|30|90 for period filtering.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from loguru import logger
from sqlalchemy import func, case, desc

from app.db import (
    MediaItem, Profile, ProfileSnapshot, SessionLocal,
)

analytics_api_bp = Blueprint("analytics_api", __name__)


def _days_param() -> int:
    """Read the ?days query param, default 30."""
    try:
        d = int(request.args.get("days", 30))
        return max(1, min(d, 365))
    except (ValueError, TypeError):
        return 30


def _cutoff_ts(days: int) -> int:
    """Return a unix timestamp for `days` ago."""
    return int((datetime.now() - timedelta(days=days)).timestamp())


def _get_main_profile(db):
    """Get the main Instagram profile (@samourais_) or first active Instagram profile."""
    profile = (
        db.query(Profile)
        .filter(Profile.platform == "instagram", Profile.username == "samourais_")
        .first()
    )
    if not profile:
        profile = (
            db.query(Profile)
            .filter(Profile.platform == "instagram", Profile.is_active == True)  # noqa: E712
            .first()
        )
    return profile


# ──────────────────────────────────────────────────────────
# Account Overview
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/account-overview", methods=["GET"])
def account_overview():
    """Profile info: followers, following, bio, avatar, verified, posts, engagement rate."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        # Average engagement rate from posts in the period
        avg_likes = (
            db.query(func.avg(MediaItem.ig_like_count))
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_like_count.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .scalar() or 0
        )
        avg_comments = (
            db.query(func.avg(MediaItem.ig_comment_count))
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_comment_count.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .scalar() or 0
        )
        followers = profile.followers_count or 0
        engagement_rate = (
            round((avg_likes + avg_comments) / followers * 100, 2)
            if followers > 0 else 0
        )

        total_posts = (
            db.query(func.count(func.distinct(MediaItem.post_url)))
            .filter(MediaItem.profile_id == profile.id)
            .scalar() or 0
        )

        # Follower delta over the period
        oldest_snapshot = (
            db.query(ProfileSnapshot)
            .filter(
                ProfileSnapshot.profile_id == profile.id,
                ProfileSnapshot.snapshot_at >= cutoff,
            )
            .order_by(ProfileSnapshot.snapshot_at.asc())
            .first()
        )
        follower_delta = 0
        if oldest_snapshot and oldest_snapshot.followers_count and profile.followers_count:
            follower_delta = profile.followers_count - oldest_snapshot.followers_count

        return jsonify({
            "username": profile.username,
            "display_name": profile.display_name,
            "avatar_url": profile.avatar_url,
            "biography": profile.biography,
            "is_verified": profile.is_verified or False,
            "followers_count": profile.followers_count or 0,
            "following_count": profile.following_count or 0,
            "media_count": profile.media_count or 0,
            "total_posts_scraped": total_posts,
            "engagement_rate": engagement_rate,
            "avg_likes": round(avg_likes, 1),
            "avg_comments": round(avg_comments, 1),
            "follower_delta": follower_delta,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in account overview: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Follower Growth
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/follower-growth", methods=["GET"])
def follower_growth():
    """Historical followers/following per day from ProfileSnapshot."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        snapshots = (
            db.query(ProfileSnapshot)
            .filter(
                ProfileSnapshot.profile_id == profile.id,
                ProfileSnapshot.snapshot_at >= cutoff,
            )
            .order_by(ProfileSnapshot.snapshot_at.asc())
            .all()
        )

        labels = []
        followers = []
        following = []
        for s in snapshots:
            day = datetime.fromtimestamp(s.snapshot_at).strftime("%Y-%m-%d")
            labels.append(day)
            followers.append(s.followers_count or 0)
            following.append(s.following_count or 0)

        return jsonify({
            "labels": labels,
            "followers": followers,
            "following": following,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in follower growth: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Engagement per post
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/engagement", methods=["GET"])
def engagement():
    """Engagement (likes + comments) per post over the period."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        rows = (
            db.query(
                MediaItem.posted_at,
                MediaItem.ig_like_count,
                MediaItem.ig_comment_count,
                MediaItem.post_url,
            )
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
                MediaItem.posted_at >= cutoff,
                MediaItem.ig_like_count.isnot(None),
            )
            .order_by(MediaItem.posted_at.asc())
            .all()
        )

        # Deduplicate by post_url (carousel items share the same post)
        seen_urls = set()
        labels = []
        likes = []
        comments = []
        for posted_at, lc, cc, url in rows:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            day = datetime.fromtimestamp(posted_at).strftime("%d/%m")
            labels.append(day)
            likes.append(lc or 0)
            comments.append(cc or 0)

        return jsonify({
            "labels": labels,
            "likes": likes,
            "comments": comments,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in engagement: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Content Breakdown
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/content-breakdown", methods=["GET"])
def content_breakdown():
    """Distribution of images vs videos."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        rows = (
            db.query(MediaItem.media_type, func.count(MediaItem.id))
            .filter(MediaItem.profile_id == profile.id)
            .group_by(MediaItem.media_type)
            .all()
        )
        data = {media_type: count for media_type, count in rows}
        return jsonify(data)
    except Exception as exc:
        logger.exception("Error in content breakdown: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Best Posting Times
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/best-posting-times", methods=["GET"])
def best_posting_times():
    """Hour distribution of original post times."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        rows = (
            db.query(MediaItem.posted_at)
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
            )
            .all()
        )

        hours = [0] * 24
        for (ts,) in rows:
            try:
                h = datetime.fromtimestamp(ts).hour
                hours[h] += 1
            except (OSError, ValueError):
                pass

        labels = [f"{h:02d}h" for h in range(24)]
        return jsonify({
            "labels": labels,
            "data": hours,
        })
    except Exception as exc:
        logger.exception("Error in best posting times: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Top Posts
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/top-posts", methods=["GET"])
def top_posts():
    """Top 10 posts by engagement (likes + comments)."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        rows = (
            db.query(
                MediaItem.id,
                MediaItem.post_url,
                MediaItem.media_type,
                MediaItem.caption,
                MediaItem.ig_like_count,
                MediaItem.ig_comment_count,
                MediaItem.ig_view_count,
                MediaItem.posted_at,
                MediaItem.local_path,
            )
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_like_count.isnot(None),
            )
            .order_by(desc(MediaItem.ig_like_count + func.coalesce(MediaItem.ig_comment_count, 0)))
            .limit(10)
            .all()
        )

        results = []
        seen_urls = set()
        for r in rows:
            if r.post_url in seen_urls:
                continue
            seen_urls.add(r.post_url)
            results.append({
                "id": r.id,
                "post_url": r.post_url,
                "media_type": r.media_type,
                "caption": (r.caption or "")[:100],
                "likes": r.ig_like_count or 0,
                "comments": r.ig_comment_count or 0,
                "views": r.ig_view_count,
                "posted_at": r.posted_at,
            })
        return jsonify(results)
    except Exception as exc:
        logger.exception("Error in top posts: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Posting Frequency
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/posting-frequency", methods=["GET"])
def posting_frequency():
    """Number of posts per week."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return jsonify({"error": "No Instagram profile found"}), 404

        rows = (
            db.query(MediaItem.posted_at, MediaItem.post_url)
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .order_by(MediaItem.posted_at.asc())
            .all()
        )

        # Deduplicate by post_url (carousel items)
        seen_urls = set()
        post_dates = []
        for ts, url in rows:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            post_dates.append(ts)

        # Bucket by week
        buckets: dict[str, int] = {}
        for ts in post_dates:
            try:
                dt = datetime.fromtimestamp(ts)
                # ISO week start (Monday)
                week_start = dt - timedelta(days=dt.weekday())
                week_label = week_start.strftime("%d/%m")
                buckets[week_label] = buckets.get(week_label, 0) + 1
            except (OSError, ValueError):
                pass

        labels = list(buckets.keys())
        data = list(buckets.values())

        return jsonify({
            "labels": labels,
            "data": data,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in posting frequency: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()
