"""
Editor API blueprint — handles video processing and media serving for the meme editor.

Endpoints:
    POST /api/editor/process-video  — FFmpeg video processing (port of Node.js server.js)
    GET  /api/editor/media/<id>     — serve a scraped media file for use in the editor
    GET  /api/editor/health         — check FFmpeg availability
"""

from __future__ import annotations

import json
import os
import shutil

from flask import Blueprint, jsonify, request, send_file
from loguru import logger
from nanoid import generate as nanoid

from app.config import DOWNLOAD_DIR, EDITOR_OUTPUT_DIR, EDITOR_UPLOAD_DIR
from app.db import MediaItem, SessionLocal
from app.editor.processing import cleanup_files, ensure_dirs, process_video

editor_api_bp = Blueprint("editor_api", __name__)


@editor_api_bp.route("/editor/health", methods=["GET"])
def editor_health():
    """Check if FFmpeg is available."""
    has_ffmpeg = shutil.which("ffmpeg") is not None
    return jsonify({"status": "ok", "ffmpeg": has_ffmpeg})


@editor_api_bp.route("/editor/process-video", methods=["POST"])
def process_video_endpoint():
    """
    Process a video with a template overlay using FFmpeg.

    Accepts multipart form data:
        - video: the input video file
        - template: the template PNG (with transparent cutout)
        - params: JSON string with processing parameters

    Returns the processed MP4 file as a download.
    """
    ensure_dirs()

    # Validate uploads
    if "video" not in request.files:
        return jsonify({"error": "No video file uploaded"}), 400
    if "template" not in request.files:
        return jsonify({"error": "No template file uploaded"}), 400

    video_file = request.files["video"]
    template_file = request.files["template"]

    # Save uploaded files with unique names
    vid_ext = os.path.splitext(video_file.filename or "video.mp4")[1] or ".mp4"
    tpl_ext = os.path.splitext(template_file.filename or "template.png")[1] or ".png"

    vid_id = nanoid()
    video_path = str(EDITOR_UPLOAD_DIR / f"{vid_id}{vid_ext}")
    template_path = str(EDITOR_UPLOAD_DIR / f"{vid_id}_tpl{tpl_ext}")
    output_id = nanoid()
    output_path = str(EDITOR_OUTPUT_DIR / f"{output_id}.mp4")

    video_file.save(video_path)
    template_file.save(template_path)

    try:
        # Parse processing parameters
        raw_params = request.form.get("params", "{}")
        try:
            params = json.loads(raw_params)
        except json.JSONDecodeError:
            params = {}

        logger.info("Processing video with params: {}", params)

        process_video(
            video_path=video_path,
            template_path=template_path,
            output_path=output_path,
            template_width=int(params.get("templateWidth", 1080)),
            template_height=int(params.get("templateHeight", 1080)),
            frame_x=int(params.get("frameX", 54)),
            frame_y=int(params.get("frameY", 195)),
            frame_width=int(params.get("frameWidth", 972)),
            frame_height=int(params.get("frameHeight", 810)),
            original_frame_y=(
                int(params["originalFrameY"])
                if "originalFrameY" in params and params["originalFrameY"] is not None
                else None
            ),
            original_frame_height=(
                int(params["originalFrameHeight"])
                if "originalFrameHeight" in params and params["originalFrameHeight"] is not None
                else None
            ),
            trim_start=float(params.get("trimStart", 0)),
            trim_end=float(params.get("trimEnd", 10)),
            image_scale=int(params.get("imageScale", 100)),
            image_offset_x=int(params.get("imageOffsetX", 0)),
            image_offset_y=int(params.get("imageOffsetY", 0)),
        )

        # Send the output file, then clean up everything
        response = send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"samourais_meme_{output_id}.mp4",
        )

        # Schedule cleanup after response is sent
        @response.call_on_close
        def _cleanup():
            cleanup_files(video_path, template_path, output_path)

        return response

    except Exception as exc:
        logger.exception("Video processing failed: {}", exc)
        cleanup_files(video_path, template_path, output_path)
        return jsonify({"error": str(exc)}), 500


@editor_api_bp.route("/editor/media/<int:media_id>", methods=["GET"])
def serve_editor_media(media_id: int):
    """
    Serve a scraped media file for use in the editor.

    This allows users to import scraped media directly into the meme editor
    from the "Library" tab or via the viewer's "Edit" button.
    """
    db = SessionLocal()
    try:
        item = db.query(MediaItem).filter_by(id=media_id).first()
        if not item:
            return jsonify({"error": "Media not found"}), 404

        if not item.local_path or not os.path.exists(item.local_path):
            # Try to serve from DOWNLOAD_DIR using filename from media_url
            return jsonify({"error": "Media file not available locally"}), 404

        mime = "video/mp4" if item.media_type == "video" else "image/jpeg"
        return send_file(item.local_path, mimetype=mime)

    except Exception as exc:
        logger.exception("Failed to serve editor media: {}", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()
