"""
Calendar API blueprint — CRUD for scheduled posts and FullCalendar integration.

Endpoints:
    GET    /api/calendar/posts          — list posts (FullCalendar events format)
    POST   /api/calendar/posts          — create a new scheduled post
    PATCH  /api/calendar/posts/<id>     — update (reschedule, edit caption, change status)
    DELETE /api/calendar/posts/<id>     — delete a scheduled post
    POST   /api/calendar/posts/<id>/publish — trigger publish
    GET    /api/calendar/posts/<id>/media   — serve the post's media file
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file
from loguru import logger
from nanoid import generate as nanoid

from app.config import BASE_DIR
from app.db import ScheduledPost, SessionLocal

calendar_api_bp = Blueprint("calendar_api", __name__)

CALENDAR_MEDIA_DIR = BASE_DIR / "data" / "calendar" / "media"


def _now_ts() -> int:
    return int(datetime.now().timestamp())


@calendar_api_bp.route("/calendar/posts", methods=["GET"])
def list_posts():
    """
    List scheduled posts, optionally filtered by date range.

    Query params (FullCalendar compatible):
        start: ISO date string (optional)
        end:   ISO date string (optional)
        status: filter by status (optional)

    Returns an array of FullCalendar-compatible event objects.
    """
    db = SessionLocal()
    try:
        query = db.query(ScheduledPost)

        # Date range filter (FullCalendar sends start/end)
        start_str = request.args.get("start")
        end_str = request.args.get("end")
        if start_str:
            try:
                start_ts = int(datetime.fromisoformat(start_str.replace("Z", "+00:00")).timestamp())
                query = query.filter(ScheduledPost.scheduled_at >= start_ts)
            except (ValueError, TypeError):
                pass
        if end_str:
            try:
                end_ts = int(datetime.fromisoformat(end_str.replace("Z", "+00:00")).timestamp())
                query = query.filter(ScheduledPost.scheduled_at <= end_ts)
            except (ValueError, TypeError):
                pass

        status = request.args.get("status")
        if status:
            query = query.filter(ScheduledPost.status == status)

        posts = query.order_by(ScheduledPost.scheduled_at.asc()).all()

        # Convert to FullCalendar event format
        events = []
        for p in posts:
            platforms = json.loads(p.platforms) if p.platforms else []
            color_map = {
                "draft": "#6b7280",
                "scheduled": "#3b82f6",
                "published": "#22c55e",
                "failed": "#ef4444",
            }
            events.append({
                "id": p.id,
                "title": p.title or p.caption[:50] if p.caption else "Sans titre",
                "start": datetime.fromtimestamp(p.scheduled_at).isoformat() if p.scheduled_at else None,
                "color": color_map.get(p.status, "#6b7280"),
                "extendedProps": {
                    "caption": p.caption,
                    "media_type": p.media_type,
                    "template_format": p.template_format,
                    "status": p.status,
                    "platforms": platforms,
                    "thumbnail_path": p.thumbnail_path,
                    "created_at": p.created_at,
                },
            })

        return jsonify(events)

    except Exception as exc:
        logger.exception("Error listing calendar posts: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@calendar_api_bp.route("/calendar/posts", methods=["POST"])
def create_post():
    """
    Create a new scheduled post.

    Accepts multipart form data or JSON:
        - media: file (optional, for multipart)
        - thumbnail: base64 data URL (optional)
        - caption: text
        - title: text
        - template_format: square | portrait | story
        - media_type: image | video
        - platforms: JSON array ["instagram", "tiktok"]
        - scheduled_at: unix timestamp
        - status: draft | scheduled (default: draft)
    """
    CALENDAR_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        # Handle both multipart and JSON
        if request.content_type and "multipart" in request.content_type:
            data = request.form.to_dict()
        else:
            data = request.get_json() or {}

        media_path = None
        thumbnail_path = None

        # Save media file if uploaded
        if "media" in request.files:
            f = request.files["media"]
            ext = os.path.splitext(f.filename or "file.mp4")[1] or ".mp4"
            filename = f"{nanoid()}{ext}"
            media_path = str(CALENDAR_MEDIA_DIR / filename)
            f.save(media_path)

        # Save thumbnail if provided as base64
        thumb_data = data.get("thumbnail", "")
        if thumb_data and thumb_data.startswith("data:"):
            import base64
            header, b64 = thumb_data.split(",", 1)
            thumb_bytes = base64.b64decode(b64)
            thumb_filename = f"{nanoid()}_thumb.jpg"
            thumbnail_path = str(CALENDAR_MEDIA_DIR / thumb_filename)
            with open(thumbnail_path, "wb") as tf:
                tf.write(thumb_bytes)

        post = ScheduledPost(
            title=data.get("title", ""),
            caption=data.get("caption", ""),
            media_path=media_path,
            media_type=data.get("media_type", "image"),
            template_format=data.get("template_format", "square"),
            thumbnail_path=thumbnail_path,
            source_media_id=data.get("source_media_id"),
            scheduled_at=int(data["scheduled_at"]) if data.get("scheduled_at") else None,
            status=data.get("status", "draft"),
            platforms=data.get("platforms", "[]"),
            created_at=_now_ts(),
            updated_at=_now_ts(),
        )
        db.add(post)
        db.commit()

        logger.info("Created scheduled post {} ({})", post.id, post.status)

        return jsonify({
            "id": post.id,
            "status": post.status,
            "message": "Post created",
        }), 201

    except Exception as exc:
        db.rollback()
        logger.exception("Error creating calendar post: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@calendar_api_bp.route("/calendar/posts/<int:post_id>", methods=["PATCH"])
def update_post(post_id: int):
    """Update a scheduled post (reschedule, edit caption, change status)."""
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter_by(id=post_id).first()
        if not post:
            return jsonify({"error": "Post not found"}), 404

        data = request.get_json() or {}

        if "title" in data:
            post.title = data["title"]
        if "caption" in data:
            post.caption = data["caption"]
        if "scheduled_at" in data:
            post.scheduled_at = int(data["scheduled_at"])
        if "status" in data:
            post.status = data["status"]
        if "platforms" in data:
            post.platforms = json.dumps(data["platforms"]) if isinstance(data["platforms"], list) else data["platforms"]

        post.updated_at = _now_ts()
        db.commit()

        return jsonify({"id": post.id, "status": post.status, "message": "Post updated"})

    except Exception as exc:
        db.rollback()
        logger.exception("Error updating calendar post: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@calendar_api_bp.route("/calendar/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id: int):
    """Delete a scheduled post and its media files."""
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter_by(id=post_id).first()
        if not post:
            return jsonify({"error": "Post not found"}), 404

        # Clean up files
        for path in (post.media_path, post.thumbnail_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

        db.delete(post)
        db.commit()

        return jsonify({"message": "Post deleted"})

    except Exception as exc:
        db.rollback()
        logger.exception("Error deleting calendar post: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@calendar_api_bp.route("/calendar/posts/<int:post_id>/publish", methods=["POST"])
def publish_post(post_id: int):
    """
    Trigger publish for a scheduled post.

    MVP: Returns manual instructions (copy caption + open platform).
    Future: Direct API posting via platform APIs.
    """
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter_by(id=post_id).first()
        if not post:
            return jsonify({"error": "Post not found"}), 404

        platforms = json.loads(post.platforms) if post.platforms else []

        # MVP: manual workflow
        post.status = "published"
        post.updated_at = _now_ts()
        post.publish_results = json.dumps({
            "mode": "manual",
            "platforms": platforms,
            "published_at": _now_ts(),
        })
        db.commit()

        return jsonify({
            "id": post.id,
            "status": "published",
            "mode": "manual",
            "message": "Post marque comme publie. Copie le caption et uploade manuellement.",
            "caption": post.caption,
            "platforms": platforms,
        })

    except Exception as exc:
        db.rollback()
        logger.exception("Error publishing calendar post: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()


@calendar_api_bp.route("/calendar/posts/<int:post_id>/media", methods=["GET"])
def serve_post_media(post_id: int):
    """Serve the media file of a scheduled post."""
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter_by(id=post_id).first()
        if not post or not post.media_path or not os.path.exists(post.media_path):
            return jsonify({"error": "Media not found"}), 404

        mime = "video/mp4" if post.media_type == "video" else "image/jpeg"
        return send_file(post.media_path, mimetype=mime)
    finally:
        db.close()
