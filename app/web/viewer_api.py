"""Viewer API endpoints for media browsing, comments, and ratings."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from loguru import logger
from sqlalchemy import func

from app.db import MediaComment, MediaItem, MediaRating, Profile, SessionLocal

viewer_api_bp = Blueprint("viewer_api", __name__)


# ---------------------------------------------------------------------------
# Media listing
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media")
def list_media():
    """List media items with pagination, filters, and average ratings."""
    db = SessionLocal()
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 60))
        platform = request.args.get("platform", "")
        profile_id = request.args.get("profile_id", "")
        min_rating = request.args.get("min_rating", "")
        sort = request.args.get("sort", "date_desc")
        search = request.args.get("search", "").strip()

        query = db.query(MediaItem).filter(MediaItem.status.in_(("uploaded", "downloaded")))

        if platform:
            query = query.filter(MediaItem.platform == platform)
        if profile_id:
            query = query.filter(MediaItem.profile_id == int(profile_id))
        if search:
            query = query.filter(MediaItem.caption.ilike(f"%{search}%"))

        # Rating filter — need a subquery
        if min_rating:
            min_r = int(min_rating)
            rated_ids = (
                db.query(MediaRating.media_item_id)
                .group_by(MediaRating.media_item_id)
                .having(func.avg(MediaRating.rating) >= min_r)
                .subquery()
            )
            query = query.filter(MediaItem.id.in_(db.query(rated_ids.c.media_item_id)))

        # Sorting
        if sort == "date_asc":
            query = query.order_by(MediaItem.posted_at.asc().nullslast())
        elif sort == "rating_desc":
            # Sort by average rating descending — join with subquery
            avg_sub = (
                db.query(
                    MediaRating.media_item_id,
                    func.avg(MediaRating.rating).label("avg_rating"),
                )
                .group_by(MediaRating.media_item_id)
                .subquery()
            )
            query = (
                query.outerjoin(avg_sub, MediaItem.id == avg_sub.c.media_item_id)
                .order_by(avg_sub.c.avg_rating.desc().nullslast(), MediaItem.posted_at.desc().nullslast())
            )
        else:  # date_desc (default)
            query = query.order_by(MediaItem.posted_at.desc().nullslast())

        total = query.count()
        items = query.offset((page - 1) * per_page).limit(per_page).all()

        # Batch-load average ratings for returned items
        item_ids = [i.id for i in items]
        avg_ratings = {}
        if item_ids:
            rows = (
                db.query(
                    MediaRating.media_item_id,
                    func.avg(MediaRating.rating).label("avg"),
                    func.count(MediaRating.id).label("cnt"),
                )
                .filter(MediaRating.media_item_id.in_(item_ids))
                .group_by(MediaRating.media_item_id)
                .all()
            )
            for row in rows:
                avg_ratings[row.media_item_id] = {
                    "avg": round(float(row.avg), 1),
                    "count": row.cnt,
                }

        # Batch-load comment counts
        comment_counts = {}
        if item_ids:
            rows = (
                db.query(
                    MediaComment.media_item_id,
                    func.count(MediaComment.id).label("cnt"),
                )
                .filter(MediaComment.media_item_id.in_(item_ids))
                .group_by(MediaComment.media_item_id)
                .all()
            )
            for row in rows:
                comment_counts[row.media_item_id] = row.cnt

        result = []
        for item in items:
            # Build media file URL
            file_url = None
            if item.local_path:
                # Extract just the filename from the local path
                filename = item.local_path.split("/")[-1] if "/" in item.local_path else item.local_path
                file_url = f"/media/file/{filename}"

            rating_data = avg_ratings.get(item.id, {"avg": 0, "count": 0})
            result.append({
                "id": item.id,
                "post_id": item.post_id,
                "post_url": item.post_url,
                "media_type": item.media_type,
                "media_url": item.media_url,
                "file_url": file_url,
                "caption": item.caption,
                "platform": item.platform,
                "profile_id": item.profile_id,
                "posted_at": item.posted_at,
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
                "avg_rating": rating_data["avg"],
                "rating_count": rating_data["count"],
                "comment_count": comment_counts.get(item.id, 0),
            })

        return jsonify({
            "items": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })
    except Exception as exc:
        logger.error("Error listing media: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Single media detail
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>")
def get_media(media_id: int):
    """Get a single media item with comments and ratings."""
    db = SessionLocal()
    try:
        item = db.query(MediaItem).get(media_id)
        if not item:
            return jsonify({"error": "Not found"}), 404

        # Get profile info
        profile = db.query(Profile).get(item.profile_id)

        # Comments
        comments = (
            db.query(MediaComment)
            .filter(MediaComment.media_item_id == media_id)
            .order_by(MediaComment.created_at.desc())
            .all()
        )

        # Ratings
        ratings = (
            db.query(MediaRating)
            .filter(MediaRating.media_item_id == media_id)
            .all()
        )

        avg_rating = 0
        if ratings:
            avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1)

        file_url = None
        if item.local_path:
            filename = item.local_path.split("/")[-1] if "/" in item.local_path else item.local_path
            file_url = f"/media/file/{filename}"

        return jsonify({
            "id": item.id,
            "post_id": item.post_id,
            "post_url": item.post_url,
            "media_type": item.media_type,
            "media_url": item.media_url,
            "file_url": file_url,
            "caption": item.caption,
            "platform": item.platform,
            "profile_id": item.profile_id,
            "profile_username": profile.username if profile else None,
            "posted_at": item.posted_at,
            "width": item.width,
            "height": item.height,
            "duration": item.duration,
            "avg_rating": avg_rating,
            "ratings": [
                {"user_name": r.user_name, "rating": r.rating, "created_at": r.created_at}
                for r in ratings
            ],
            "comments": [
                {
                    "id": c.id,
                    "user_name": c.user_name,
                    "text": c.comment_text,
                    "created_at": c.created_at,
                }
                for c in comments
            ],
        })
    except Exception as exc:
        logger.error("Error getting media {}: {}", media_id, exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>/comment", methods=["POST"])
def add_comment(media_id: int):
    """Add a comment to a media item."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        user_name = (data.get("user_name") or "").strip()
        text = (data.get("text") or "").strip()

        if not user_name or not text:
            return jsonify({"error": "user_name and text required"}), 400

        item = db.query(MediaItem).get(media_id)
        if not item:
            return jsonify({"error": "Media not found"}), 404

        comment = MediaComment(
            media_item_id=media_id,
            user_name=user_name,
            comment_text=text,
        )
        db.add(comment)
        db.commit()

        return jsonify({
            "id": comment.id,
            "user_name": comment.user_name,
            "text": comment.comment_text,
            "created_at": comment.created_at,
        }), 201
    except Exception as exc:
        db.rollback()
        logger.error("Error adding comment: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/media/<int:media_id>/comment/<int:comment_id>", methods=["DELETE"])
def delete_comment(media_id: int, comment_id: int):
    """Delete a comment (only the author can delete)."""
    db = SessionLocal()
    try:
        user_name = request.args.get("user_name", "").strip()
        comment = db.query(MediaComment).get(comment_id)
        if not comment or comment.media_item_id != media_id:
            return jsonify({"error": "Comment not found"}), 404
        if comment.user_name != user_name:
            return jsonify({"error": "Not authorized"}), 403
        db.delete(comment)
        db.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        db.rollback()
        logger.error("Error deleting comment: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>/rate", methods=["POST"])
def rate_media(media_id: int):
    """Add or update a rating for a media item (one per user)."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        user_name = (data.get("user_name") or "").strip()
        rating = data.get("rating")

        if not user_name or rating is None:
            return jsonify({"error": "user_name and rating required"}), 400

        rating = int(rating)
        if rating < 1 or rating > 5:
            return jsonify({"error": "Rating must be 1-5"}), 400

        item = db.query(MediaItem).get(media_id)
        if not item:
            return jsonify({"error": "Media not found"}), 404

        # Upsert: update existing or create new
        existing = (
            db.query(MediaRating)
            .filter(
                MediaRating.media_item_id == media_id,
                MediaRating.user_name == user_name,
            )
            .first()
        )

        if existing:
            existing.rating = rating
            existing.created_at = int(datetime.now().timestamp())
        else:
            existing = MediaRating(
                media_item_id=media_id,
                user_name=user_name,
                rating=rating,
            )
            db.add(existing)

        db.commit()

        # Return updated average
        all_ratings = (
            db.query(MediaRating)
            .filter(MediaRating.media_item_id == media_id)
            .all()
        )
        avg = round(sum(r.rating for r in all_ratings) / len(all_ratings), 1) if all_ratings else 0

        return jsonify({
            "user_name": user_name,
            "rating": rating,
            "avg_rating": avg,
            "rating_count": len(all_ratings),
        })
    except Exception as exc:
        db.rollback()
        logger.error("Error rating media: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Profiles list (for filter dropdown)
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/profiles")
def list_profiles():
    """List profiles for the viewer filter dropdown."""
    db = SessionLocal()
    try:
        profiles = db.query(Profile).filter(Profile.is_active == True).order_by(Profile.platform, Profile.username).all()
        return jsonify([
            {
                "id": p.id,
                "platform": p.platform,
                "username": p.username,
                "display_name": p.display_name,
            }
            for p in profiles
        ])
    except Exception as exc:
        logger.error("Error listing profiles: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()
