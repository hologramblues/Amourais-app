"""
Media download utilities.

Handles direct HTTP downloads and HLS (m3u8) stream conversion via ffmpeg.
Files are saved to DOWNLOAD_DIR with nanoid-based filenames and SHA-256 hashes.

Garde-fous (lots 2.5 / 3.4 / 3.4b) :
    * contrôle d'espace disque AVANT toute requête et toute écriture ;
    * aucun fichier partiel ne survit à un téléchargement raté ;
    * validation d'intégrité minimale (Content-Length, type de contenu,
      magic bytes) : une page d'erreur HTML n'est plus stockée comme média ;
    * liste blanche d'extensions ;
    * erreurs définitives (403/404/410) non retentées, HLS retenté comme le
      téléchargement direct.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
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

#: Marge d'espace libre exigée avant d'écrire quoi que ce soit (risque #14,
#: §4.10). Remplir le volume corrompt la base SQLite qui vit sur le même
#: montage : mieux vaut refuser proprement le téléchargement.
_MIN_FREE_BYTES = 200 * 1024 * 1024  # 200 MiB

#: Liste blanche d'extensions (lot 2.5, §6.6 / T10). Tout ce qui n'est pas un
#: média reconnu est écrit en `.bin` : un `Content-Type: text/html` distant ne
#: peut plus produire un `.html` servi ensuite same-origin par
#: `/media/file/<nom>` (XSS stockée). `.svg` est volontairement ABSENT : un SVG
#: est un document exécutable, pas une image inerte.
MEDIA_EXTENSIONS: frozenset[str] = frozenset(
    {
        # images
        ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif",
        ".avif", ".tif", ".tiff",
        # vidéos
        ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".ts",
        # audio
        ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac",
    }
)

#: Types de contenu qui ne sont JAMAIS un média : page d'erreur, JSON de refus,
#: challenge anti-bot. Les recevoir en réponse à une URL de média signifie que
#: le CDN a refusé, pas qu'il a livré (§6.6 / T10).
_NON_MEDIA_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
    "application/ecmascript",
)

#: Signatures de début de document texte/HTML — filet de sécurité quand le CDN
#: ment sur son `Content-Type` (magic bytes).
_HTML_SIGNATURES = (
    b"<!doctype",
    b"<html",
    b"<head",
    b"<body",
    b"<?xml",
    b"<script",
)

#: Codes HTTP 4xx qui peuvent encore réussir plus tard. Tous les autres sont
#: définitifs : une URL CDN signée expirée (403/404/410) ne redeviendra jamais
#: valide, la retenter coûte 6 s de backoff et garde le sémaphore de scrape.
_RETRYABLE_CLIENT_ERRORS = frozenset({408, 425, 429})


class PermanentDownloadError(RuntimeError):
    """Échec définitif : le retenter ne peut pas aider.

    Sous-classe de `RuntimeError` pour que les appelants existants
    (`pipeline.py`, qui capture `Exception`) ne changent pas de comportement.
    """


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
    """Determine file extension from content-type header or URL path.

    Le résultat est TOUJOURS filtré par `MEDIA_EXTENSIONS` (lot 2.5) : ni le
    `Content-Type` distant ni le chemin de l'URL ne peuvent imposer une
    extension exécutable (`.html`, `.svg`, `.js`…).
    """
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext and ext.lower() in MEDIA_EXTENSIONS:
            return ext

    parsed_path = urlparse(url).path
    _, ext = os.path.splitext(parsed_path)
    if ext and len(ext) <= 6 and ext.lower() in MEDIA_EXTENSIONS:
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


def _free_bytes(directory: Path) -> int | None:
    """Espace libre (octets) sur le volume de *directory*, None si illisible."""
    try:
        stat = os.statvfs(str(directory))
    except (OSError, AttributeError, ValueError) as exc:
        # statvfs n'existe pas sur Windows et échoue si le chemin a disparu :
        # on ne bloque pas un téléchargement sur une mesure indisponible.
        logger.warning("Espace disque illisible pour {} : {}", directory, exc)
        return None
    return stat.f_frsize * stat.f_bavail


def _assert_enough_free_space(directory: Path | None = None) -> None:
    """Refuse le téléchargement quand le volume est plein (risque #14, §4.10).

    Appelé en TÊTE de `download_media`, donc avant la requête réseau et avant
    toute écriture : sur un volume saturé, mieux vaut un média `failed` avec un
    message clair qu'une base SQLite corrompue sur le même montage.
    """
    target = Path(directory) if directory is not None else DOWNLOAD_DIR
    free = _free_bytes(target)
    if free is None:
        return
    if free < _MIN_FREE_BYTES:
        raise RuntimeError(
            f"Not enough free disk space on {target}: "
            f"{free // (1024 * 1024)} MiB free, "
            f"{_MIN_FREE_BYTES // (1024 * 1024)} MiB required"
        )


def _discard(dest: Path | None) -> None:
    """Supprime un fichier partiel. Ne lève jamais (risque #14)."""
    if dest is None:
        return
    try:
        Path(dest).unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - disque en lecture seule
        logger.warning("Impossible de supprimer le fichier partiel {}: {}", dest, exc)


def _check_status(response: httpx.Response, url: str) -> None:
    """Traduit le code HTTP en exception retentable ou définitive."""
    code = response.status_code
    if code < 400:
        return
    if 400 <= code < 500 and code not in _RETRYABLE_CLIENT_ERRORS:
        raise PermanentDownloadError(
            f"HTTP {code} for {url} — definitive, not retried"
        )
    response.raise_for_status()


def _check_content_type(content_type: str | None, url: str) -> None:
    """Rejette un type de contenu qui ne peut pas être un média (§6.6 / T10)."""
    if not content_type:
        return
    main = content_type.split(";")[0].strip().lower()
    if main.startswith(_NON_MEDIA_CONTENT_TYPES):
        raise PermanentDownloadError(
            f"Refusing non-media content-type {main!r} for {url}"
        )


def _check_magic_bytes(head: bytes, url: str) -> None:
    """Rejette un corps qui commence comme un document HTML/XML (§6.6 / T10).

    Certains CDN servent leur page de refus en 200 avec un `Content-Type`
    d'image : le début du corps est alors le seul indice fiable.
    """
    debut = head[:512].lstrip()[:64].lower()
    if debut.startswith(_HTML_SIGNATURES):
        raise PermanentDownloadError(
            f"Refusing HTML/XML payload served as media for {url}"
        )


def _expected_length(response: httpx.Response) -> int | None:
    """Taille annoncée par `Content-Length`, si elle est comparable au disque.

    Une réponse compressée (`Content-Encoding`) annonce la taille COMPRESSÉE
    alors que httpx écrit le corps décodé : la comparaison n'aurait aucun sens
    et ferait échouer des téléchargements sains.
    """
    encoding = (response.headers.get("content-encoding") or "identity").lower()
    if encoding not in ("identity", ""):
        return None
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Direct HTTP download
# ---------------------------------------------------------------------------
def _download_direct(url: str) -> DownloadResult:
    """Download a file via a streaming HTTP GET request."""
    logger.debug("Direct download: {}", url)

    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        dest: Path | None = None
        succeeded = False
        try:
            with httpx.Client(
                timeout=_HTTPX_TIMEOUT,
                limits=_HTTPX_LIMITS,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    _check_status(resp, url)
                    content_type = resp.headers.get("content-type")
                    _check_content_type(content_type, url)

                    ext = _guess_extension(url, content_type)
                    filename = f"{_nanoid()}{ext}"
                    dest = DOWNLOAD_DIR / filename

                    written = 0
                    premier_bloc = True
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=_CHUNK_SIZE):
                            if premier_bloc and chunk:
                                premier_bloc = False
                                _check_magic_bytes(chunk, url)
                            fh.write(chunk)
                            written += len(chunk)

                    annonce = _expected_length(resp)

            if annonce is not None and written != annonce:
                raise RuntimeError(
                    f"Truncated download for {url}: {written} bytes written, "
                    f"{annonce} announced"
                )

            file_size = dest.stat().st_size
            if file_size == 0:
                raise RuntimeError(f"Downloaded file is empty: {url}")

            content_hash = hash_file(dest)
            mime = _guess_mime(dest)

            logger.info(
                "Downloaded {} ({} bytes, {})",
                dest.name,
                file_size,
                mime,
            )
            succeeded = True
            return DownloadResult(
                local_path=str(dest),
                file_size=file_size,
                mime_type=mime,
                content_hash=content_hash,
            )

        except PermanentDownloadError as exc:
            # 403/404/410, page HTML, type refusé : retenter ne peut pas aider.
            logger.warning("Download refused for {}: {}", url, exc)
            raise

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
                time.sleep(2 ** attempt)

        finally:
            # risque #14 : aucun fichier partiel ne survit à un échec, quelle
            # que soit l'exception — y compris `OSError`/ENOSPC, qui n'est pas
            # retentée et remonte telle quelle jusqu'au pipeline.
            if not succeeded:
                _discard(dest)

    raise RuntimeError(
        f"Failed to download {url} after {_MAX_RETRIES} attempts"
    ) from last_exc


# ---------------------------------------------------------------------------
# HLS / m3u8 download via ffmpeg
# ---------------------------------------------------------------------------
def _download_hls_once(url: str) -> DownloadResult:
    """Une tentative de conversion HLS → MP4 via ffmpeg."""
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
        "-threads", "2",  # cap CPU threads — avoids "Resource temporarily unavailable" on small containers
        "-user_agent", _USER_AGENT,
        "-i", url,
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]

    succeeded = False
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
        succeeded = True
        return DownloadResult(
            local_path=str(dest),
            file_size=file_size,
            mime_type="video/mp4",
            content_hash=content_hash,
        )

    except subprocess.TimeoutExpired:
        # 10 minutes déjà consommées, sémaphore de scrape tenu : on ne retente
        # pas un timeout, on le déclare définitif.
        raise PermanentDownloadError(
            f"ffmpeg timed out downloading HLS stream: {url}"
        )

    finally:
        # Clean up temp file if still present (move failed or error occurred)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if not succeeded:
            _discard(dest)


def _download_hls(url: str) -> DownloadResult:
    """Download an HLS stream by converting it to MP4 with ffmpeg.

    Symétrie avec `_download_direct` (risque #64, lot 3.4b) : une sortie
    ffmpeg non nulle — ou vide — est une panne le plus souvent transitoire
    (segment CDN momentanément absent). Elle est retentée `_MAX_RETRIES` fois
    avec le même backoff `2 ** attempt` avant d'être déclarée en échec, au lieu
    d'attendre le prochain scrape complet AVEC NAVIGATEUR deux heures plus tard.
    """
    logger.debug("HLS download via ffmpeg: {}", url)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return _download_hls_once(url)

        except PermanentDownloadError:
            raise

        except RuntimeError as exc:
            last_exc = exc
            logger.warning(
                "HLS attempt {}/{} failed for {}: {}",
                attempt,
                _MAX_RETRIES,
                url,
                exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"Failed to download HLS stream {url} after {_MAX_RETRIES} attempts"
    ) from last_exc


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

    # Garde d'espace disque AVANT la requête réseau et avant toute écriture.
    _assert_enough_free_space()

    if _is_hls_url(url):
        return _download_hls(url)
    return _download_direct(url)
