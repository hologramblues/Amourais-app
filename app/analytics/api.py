"""
Analytics API blueprint — real metrics from the SAMOURAIS SCRAPPER database.

Endpoints:
    GET /api/analytics/overview               — KPI cards (totals, rates, storage)
    GET /api/analytics/collection-timeline     — media discovered per day
    GET /api/analytics/platform-breakdown      — media count by platform
    GET /api/analytics/top-rated               — top 10 by avg rating
    GET /api/analytics/scrape-activity         — jobs per day with status breakdown
    GET /api/analytics/best-posting-times      — hour distribution of original post times
    GET /api/analytics/content-table           — paginated sortable table of all media

All endpoints accept ?days=7|30|90 for period filtering.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from loguru import logger
from sqlalchemy import func, case, desc

from app.db import (
    MediaItem, MediaComment, MediaRating, Profile, ScrapeJob,
    ScheduledPost, SessionLocal,
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


# ──────────────────────────────────────────────────────────
# KPI Overview
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/overview", methods=["GET"])
def overview():
    """KPI cards: total media, this period, avg rating, active profiles, storage."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        total_media = db.query(func.count(MediaItem.id)).scalar() or 0
        period_media = (
            db.query(func.count(MediaItem.id))
            .filter(MediaItem.discovered_at >= cutoff)
            .scalar() or 0
        )

        avg_rating = (
            db.query(func.avg(MediaRating.rating)).scalar()
        )
        avg_rating = round(avg_rating, 1) if avg_rating else 0

        active_profiles = (
            db.query(func.count(Profile.id))
            .filter(Profile.is_active == True)  # noqa: E712
            .scalar() or 0
        )

        total_storage_bytes = (
            db.query(func.sum(MediaItem.file_size))
            .filter(MediaItem.file_size.isnot(None))
            .scalar() or 0
        )

        total_comments = db.query(func.count(MediaComment.id)).scalar() or 0
        total_ratings = db.query(func.count(MediaRating.id)).scalar() or 0

        total_jobs = db.query(func.count(ScrapeJob.id)).scalar() or 0
        completed_jobs = (
            db.query(func.count(ScrapeJob.id))
            .filter(ScrapeJob.status == "completed")
            .scalar() or 0
        )
        success_rate = round(completed_jobs / total_jobs * 100, 1) if total_jobs > 0 else 0

        scheduled_posts = (
            db.query(func.count(ScheduledPost.id))
            .filter(ScheduledPost.status.in_(["draft", "scheduled"]))
            .scalar() or 0
        )

        return jsonify({
            "total_media": total_media,
            "period_media": period_media,
            "avg_rating": avg_rating,
            "active_profiles": active_profiles,
            "storage_bytes": total_storage_bytes,
            "storage_mb": round(total_storage_bytes / (1024 * 1024), 1),
            "total_comments": total_comments,
            "total_ratings": total_ratings,
            "success_rate": success_rate,
            "scheduled_posts": scheduled_posts,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in analytics overview: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Collection Timeline
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/collection-timeline", methods=["GET"])
def collection_timeline():
    """Media discovered per day, stacked by platform."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        rows = (
            db.query(
                MediaItem.platform,
                MediaItem.discovered_at,
            )
            .filter(MediaItem.discovered_at >= cutoff)
            .all()
        )

        # Bucket by date and platform
        buckets: dict[str, dict[str, int]] = {}
        for platform, ts in rows:
            if ts is None:
                continue
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if day not in buckets:
                buckets[day] = {}
            buckets[day][platform] = buckets[day].get(platform, 0) + 1

        # Build sorted response
        all_dates = sorted(buckets.keys())
        platforms = sorted({platform for platform, _ in rows if platform})

        series = {}
        for p in platforms:
            series[p] = [buckets.get(d, {}).get(p, 0) for d in all_dates]

        return jsonify({
            "labels": all_dates,
            "series": series,
            "platforms": platforms,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in collection timeline: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Platform Breakdown
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/platform-breakdown", methods=["GET"])
def platform_breakdown():
    """Media count by platform (all time)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(MediaItem.platform, func.count(MediaItem.id))
            .group_by(MediaItem.platform)
            .all()
        )
        data = {platform: count for platform, count in rows}
        return jsonify(data)
    except Exception as exc:
        logger.exception("Error in platform breakdown: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Top Rated
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/top-rated", methods=["GET"])
def top_rated():
    """Top 10 media by average rating."""
    db = SessionLocal()
    try:
        rows = (
            db.query(
                MediaItem.id,
                MediaItem.platform,
                MediaItem.media_type,
                MediaItem.post_url,
                MediaItem.caption,
                MediaItem.local_path,
                MediaItem.discovered_at,
                func.avg(MediaRating.rating).label("avg_rating"),
                func.count(MediaRating.id).label("rating_count"),
            )
            .join(MediaRating, MediaRating.media_item_id == MediaItem.id)
            .group_by(MediaItem.id)
            .having(func.count(MediaRating.id) >= 1)
            .order_by(desc("avg_rating"), desc("rating_count"))
            .limit(10)
            .all()
        )

        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "platform": r.platform,
                "media_type": r.media_type,
                "post_url": r.post_url,
                "caption": (r.caption or "")[:100],
                "avg_rating": round(r.avg_rating, 1),
                "rating_count": r.rating_count,
                "discovered_at": r.discovered_at,
            })
        return jsonify(results)
    except Exception as exc:
        logger.exception("Error in top rated: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Scrape Activity
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/scrape-activity", methods=["GET"])
def scrape_activity():
    """Jobs per day with status breakdown."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        rows = (
            db.query(ScrapeJob.status, ScrapeJob.created_at)
            .filter(ScrapeJob.created_at >= cutoff)
            .all()
        )

        buckets: dict[str, dict[str, int]] = {}
        for status, ts in rows:
            if ts is None:
                continue
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if day not in buckets:
                buckets[day] = {}
            buckets[day][status] = buckets[day].get(status, 0) + 1

        all_dates = sorted(buckets.keys())
        statuses = ["completed", "failed", "partial", "running", "queued"]

        series = {}
        for s in statuses:
            vals = [buckets.get(d, {}).get(s, 0) for d in all_dates]
            if any(v > 0 for v in vals):
                series[s] = vals

        return jsonify({
            "labels": all_dates,
            "series": series,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in scrape activity: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Best Posting Times
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/best-posting-times", methods=["GET"])
def best_posting_times():
    """Hour distribution of original post times (from scraped content)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(MediaItem.posted_at)
            .filter(MediaItem.posted_at.isnot(None))
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
# Content Table
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/content-table", methods=["GET"])
def content_table():
    """Paginated sortable table of all media with metrics."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    sort_by = request.args.get("sort", "discovered_at")
    order = request.args.get("order", "desc")
    platform_filter = request.args.get("platform")

    db = SessionLocal()
    try:
        # Base query with aggregates
        query = (
            db.query(
                MediaItem.id,
                MediaItem.platform,
                MediaItem.media_type,
                MediaItem.post_url,
                MediaItem.caption,
                MediaItem.file_size,
                MediaItem.discovered_at,
                MediaItem.posted_at,
                func.coalesce(func.avg(MediaRating.rating), 0).label("avg_rating"),
                func.count(func.distinct(MediaComment.id)).label("comment_count"),
            )
            .outerjoin(MediaRating, MediaRating.media_item_id == MediaItem.id)
            .outerjoin(MediaComment, MediaComment.media_item_id == MediaItem.id)
            .filter(MediaItem.discovered_at >= cutoff)
        )

        if platform_filter:
            query = query.filter(MediaItem.platform == platform_filter)

        query = query.group_by(MediaItem.id)

        # Sorting
        sort_map = {
            "discovered_at": MediaItem.discovered_at,
            "avg_rating": "avg_rating",
            "comment_count": "comment_count",
            "file_size": MediaItem.file_size,
        }
        sort_col = sort_map.get(sort_by, MediaItem.discovered_at)
        if order == "asc":
            query = query.order_by(sort_col)
        else:
            query = query.order_by(desc(sort_col))

        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        items = []
        for r in rows:
            items.append({
                "id": r.id,
                "platform": r.platform,
                "media_type": r.media_type,
                "post_url": r.post_url,
                "caption": (r.caption or "")[:80],
                "file_size": r.file_size,
                "discovered_at": r.discovered_at,
                "posted_at": r.posted_at,
                "avg_rating": round(float(r.avg_rating), 1),
                "comment_count": r.comment_count,
            })

        return jsonify({
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        })
    except Exception as exc:
        logger.exception("Error in content table: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()
