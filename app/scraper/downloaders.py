"""
Media download utilities.

Handles direct HTTP downloads and HLS (m3u8) stream conversion via ffmpeg.
Files are saved to DOWNLOAD_DIR with nanoid-based filenames and SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import DOWNLOAD_DIR

# ---------------------------------------------------------------------------
# Ensure download directory exists on import
# ---------------------------------------------------------------------------
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HTTPX_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
_HTTPX_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)
_CHUNK_SIZE = 64 * 1024  # 64 KiB
_MAX_RETRIES = 3

# Common user-agent to avoid bot-detection on CDN hosts
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class DownloadResult:
    """Outcome of a successful media download."""

    local_path: str
    file_size: int
    mime_type: str
    content_hash: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _nanoid(size: int = 21) -> str:
    """Generate a URL-safe nanoid-style random string."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(secrets.choice(alphabet) for _ in range(size))


def hash_file(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _guess_extension(url: str, content_type: str | None) -> str:
    """Determine file extension from content-type header or URL path."""
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext

    parsed_path = urlparse(url).path
    _, ext = os.path.splitext(parsed_path)
    if ext and len(ext) <= 6:
        return ext

    return ".bin"


def _guess_mime(path: str | Path) -> str:
    """Guess MIME type from file extension, default to application/octet-stream."""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _is_hls_url(url: str) -> bool:
    """Check whether a URL points to an HLS manifest."""
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".m3u8")


# ---------------------------------------------------------------------------
# Direct HTTP download
# ---------------------------------------------------------------------------
def _download_direct(url: str) -> DownloadResult:
    """Download a file via a streaming HTTP GET request."""
    logger.debug("Direct download: {}", url)

    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(
                timeout=_HTTPX_TIMEOUT,
                limits=_HTTPX_LIMITS,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type")
                    ext = _guess_extension(url, content_type)
                    filename = f"{_nanoid()}{ext}"
                    dest = DOWNLOAD_DIR / filename

                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=_CHUNK_SIZE):
                            fh.write(chunk)

            file_size = dest.stat().st_size
            if file_size == 0:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"Downloaded file is empty: {url}")

            content_hash = hash_file(dest)
            mime = _guess_mime(dest)

            logger.info(
                "Downloaded {} ({} bytes, {})",
                dest.name,
                file_size,
                mime,
            )
            return DownloadResult(
                local_path=str(dest),
                file_size=file_size,
                mime_type=mime,
                content_hash=content_hash,
            )

        except (httpx.HTTPStatusError, httpx.TransportError, RuntimeError) as exc:
            last_exc = exc
            logger.warning(
                "Download attempt {}/{} failed for {}: {}",
                attempt,
                _MAX_RETRIES,
                url,
                exc,
            )
            if attempt < _MAX_RETRIES:
                import time

                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"Failed to download {url} after {_MAX_RETRIES} attempts"
    ) from last_exc


# ---------------------------------------------------------------------------
# HLS / m3u8 download via ffmpeg
# ---------------------------------------------------------------------------
def _download_hls(url: str) -> DownloadResult:
    """Download an HLS stream by converting it to MP4 with ffmpeg."""
    logger.debug("HLS download via ffmpeg: {}", url)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    filename = f"{_nanoid()}.mp4"
    dest = DOWNLOAD_DIR / filename

    # Use a temp file so a partial write does not leave a broken file behind
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=str(DOWNLOAD_DIR))
    os.close(tmp_fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-user_agent", _USER_AGENT,
        "-i", url,
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute max for large streams
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr_tail}")

        tmp_size = os.path.getsize(tmp_path)
        if tmp_size == 0:
            raise RuntimeError("ffmpeg produced an empty file")

        # Atomically move into place
        shutil.move(tmp_path, str(dest))

        file_size = dest.stat().st_size
        content_hash = hash_file(dest)

        logger.info("HLS downloaded {} ({} bytes)", dest.name, file_size)
        return DownloadResult(
            local_path=str(dest),
            file_size=file_size,
            mime_type="video/mp4",
            content_hash=content_hash,
        )

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg timed out downloading HLS stream: {url}")

    finally:
        # Clean up temp file if still present (move failed or error occurred)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def download_media(url: str) -> DownloadResult:
    """
    Download a media file from *url* and return a DownloadResult.

    Automatically routes HLS (.m3u8) URLs through ffmpeg and everything
    else through a direct HTTP download.
    """
    if not url:
        raise ValueError("download_media called with empty URL")

    if _is_hls_url(url):
        return _download_hls(url)
    return _download_direct(url)
