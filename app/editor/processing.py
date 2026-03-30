"""
FFmpeg video processing for the meme editor.

Port of the Node.js processVideo() function from SAMOURAIS TOOL (server.js).
Uses subprocess.run with raw ffmpeg commands for the complex filter graph,
following the same pattern as app/scraper/downloaders.py (lines 195-216).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from app.config import EDITOR_UPLOAD_DIR, EDITOR_OUTPUT_DIR


def ensure_dirs() -> None:
    """Create editor temp directories if they do not exist."""
    EDITOR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EDITOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def process_video(
    video_path: str,
    template_path: str,
    output_path: str,
    *,
    template_width: int = 1080,
    template_height: int = 1080,
    frame_x: int = 54,
    frame_y: int = 195,
    frame_width: int = 972,
    frame_height: int = 810,
    original_frame_y: int | None = None,
    original_frame_height: int | None = None,
    trim_start: float = 0,
    trim_end: float = 10,
    image_scale: int = 100,
    image_offset_x: int = 0,
    image_offset_y: int = 0,
) -> str:
    """
    Process a video with a template overlay using FFmpeg.

    Replicates the Node.js FFmpeg pipeline exactly:
        1. Create a white background at template dimensions
        2. Scale input video to cover the frame area (maintaining aspect ratio)
        3. Overlay video centered on the frame position (with offset/scale)
        4. Overlay template PNG on top (transparent cutout for the video)
        5. Output MP4: H.264, 30fps, AAC 128k

    Parameters
    ----------
    video_path : str
        Path to the input video file.
    template_path : str
        Path to the template PNG (with transparent cutout for the video frame).
    output_path : str
        Path for the output MP4 file.
    template_width, template_height : int
        Dimensions of the template (e.g. 1080x1080 for square).
    frame_x, frame_y : int
        Position of the video frame within the template.
    frame_width, frame_height : int
        Size of the video frame.
    original_frame_y, original_frame_height : int | None
        Original frame position before slider adjustments (for video centering).
    trim_start, trim_end : float
        Video trim times in seconds.
    image_scale : int
        Scale percentage (100 = no zoom, 150 = 1.5x zoom).
    image_offset_x, image_offset_y : int
        Pixel offset from frame center.

    Returns
    -------
    str
        Path to the output file.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    duration = trim_end - trim_start
    scale_factor = image_scale / 100

    # Use original frame dimensions for video positioning
    # (matches how the frontend positions the video at center of original frame)
    use_frame_y = original_frame_y if original_frame_y is not None else frame_y
    use_frame_height = original_frame_height or frame_height

    # Calculate center of the ORIGINAL frame
    frame_center_x = frame_x + frame_width / 2
    original_frame_center_y = use_frame_y + use_frame_height / 2
    video_x = round(frame_center_x + image_offset_x)
    video_y = round(original_frame_center_y + image_offset_y)

    # Scale video to cover the original frame dimensions
    target_width = round(frame_width * scale_factor)
    target_height = round(use_frame_height * scale_factor)

    filter_complex = ";".join([
        # 1. White background at template size
        f"color=white:s={template_width}x{template_height}:r=30[bg]",
        # 2. Scale input video to COVER the frame (larger than frame, keep aspect ratio)
        f"[0:v]scale=w={target_width}:h={target_height}:force_original_aspect_ratio=increase[scaled]",
        # 3. Overlay video on background, centered at frame position
        f"[bg][scaled]overlay=x={video_x}-overlay_w/2:y={video_y}-overlay_h/2[with_video]",
        # 4. Overlay template PNG on top (has transparent hole for video)
        "[with_video][1:v]overlay=0:0:format=auto[final]",
    ])

    logger.info(
        "FFmpeg processing: {}x{} template, {}x{} target video, scale={}%",
        template_width, template_height, target_width, target_height, image_scale,
    )

    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-ss", str(trim_start),
        "-t", str(duration),
        "-i", video_path,
        "-loop", "1",
        "-i", template_path,
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-map", "0:a?",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-threads", "2",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        output_path,
    ]

    logger.debug("FFmpeg command: {}", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute max
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr_tail}")

        output_size = os.path.getsize(output_path)
        if output_size == 0:
            raise RuntimeError("ffmpeg produced an empty output file")

        logger.info(
            "Video processed successfully: {} ({:.1f} MB)",
            output_path, output_size / (1024 * 1024),
        )
        return output_path

    except subprocess.TimeoutExpired:
        # Clean up partial output
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise RuntimeError("ffmpeg timed out after 10 minutes")


def cleanup_files(*paths: str) -> None:
    """Remove temporary files, ignoring errors."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError as exc:
            logger.warning("Failed to cleanup {}: {}", path, exc)
