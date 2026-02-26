"""
Google Drive storage integration.

Provides OAuth2 authentication and file upload with automatic folder
hierarchy creation: GDRIVE_ROOT_FOLDER_NAME / {Platform} / @{username}.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from loguru import logger

from app.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_REFRESH_TOKEN,
    GDRIVE_ROOT_FOLDER_NAME,
)

# ---------------------------------------------------------------------------
# Lazy imports for google libs (heavy; only loaded when actually needed)
# ---------------------------------------------------------------------------
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# In-memory cache: (parent_folder_id, folder_name) -> folder_id
_folder_cache: dict[tuple[str, str], str] = {}


def _require_credentials() -> None:
    """Raise early if Google Drive credentials are not configured."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "Google Drive credentials (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) "
            "are not configured. Set them in your .env file."
        )


# ---------------------------------------------------------------------------
# OAuth2 helpers
# ---------------------------------------------------------------------------
def get_gdrive_auth_url() -> str:
    """
    Generate the Google OAuth2 authorization URL.

    The user should visit this URL, grant access, and be redirected back
    to GOOGLE_REDIRECT_URI with an authorization code.
    """
    _require_credentials()

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        client_config={
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return auth_url


def exchange_code(code: str) -> str:
    """
    Exchange an authorization code for an OAuth2 refresh token.

    Returns the refresh token string which should be stored in the
    GOOGLE_REFRESH_TOKEN environment variable / .env file.
    """
    _require_credentials()

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        client_config={
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.fetch_token(code=code)

    credentials = flow.credentials
    if not credentials.refresh_token:
        raise RuntimeError(
            "No refresh token received. Make sure you set "
            "access_type='offline' and prompt='consent'."
        )

    return credentials.refresh_token


# ---------------------------------------------------------------------------
# Drive service
# ---------------------------------------------------------------------------
def get_gdrive_service():
    """
    Build and return an authorized Google Drive API service object.

    Uses the stored refresh token to obtain fresh access tokens automatically.
    """
    _require_credentials()

    if not GOOGLE_REFRESH_TOKEN:
        raise RuntimeError(
            "GOOGLE_REFRESH_TOKEN is not set. Complete the OAuth flow first "
            "via /auth/google/connect."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=_SCOPES,
    )

    # Force an immediate token refresh so we fail fast on bad credentials
    creds.refresh(Request())

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return service


# ---------------------------------------------------------------------------
# Folder management
# ---------------------------------------------------------------------------
def _find_folder(service, name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """Search for a folder by name under an optional parent. Returns folder ID or None."""
    query_parts = [
        f"name = '{name}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    result = (
        service.files()
        .list(
            q=" and ".join(query_parts),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
    )

    files = result.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Create a Drive folder and return its ID."""
    metadata: dict = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]
    logger.debug("Created Drive folder '{}' ({})", name, folder_id)
    return folder_id


def _ensure_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Get or create a folder, using an in-memory cache to reduce API calls."""
    cache_key = (parent_id or "root", name)
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    folder_id = _find_folder(service, name, parent_id)
    if not folder_id:
        folder_id = _create_folder(service, name, parent_id)

    _folder_cache[cache_key] = folder_id
    return folder_id


def _get_upload_folder(service, platform: str, username: str) -> str:
    """
    Ensure the full folder hierarchy exists and return the leaf folder ID.

    Hierarchy: GDRIVE_ROOT_FOLDER_NAME / {Platform} / @{username}
    """
    root_id = _ensure_folder(service, GDRIVE_ROOT_FOLDER_NAME)
    platform_id = _ensure_folder(service, platform.capitalize(), root_id)
    username_folder = f"@{username}"
    user_id = _ensure_folder(service, username_folder, platform_id)
    return user_id


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def upload_to_gdrive(
    local_path: str,
    platform: str,
    username: str,
    post_id: str,
    mime_type: str,
) -> dict:
    """
    Upload a local file to Google Drive.

    The file is placed under:
        GDRIVE_ROOT_FOLDER_NAME / {Platform} / @{username} / {post_id}_{original_filename}

    Returns:
        dict with keys ``file_id`` and ``web_view_link``.
    """
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"Local file not found: {local_path}")

    from googleapiclient.http import MediaFileUpload

    service = get_gdrive_service()
    folder_id = _get_upload_folder(service, platform, username)

    original_name = os.path.basename(local_path)
    drive_filename = f"{post_id}_{original_name}"

    file_metadata = {
        "name": drive_filename,
        "parents": [folder_id],
    }

    media = MediaFileUpload(
        local_path,
        mimetype=mime_type,
        resumable=True,
        chunksize=5 * 1024 * 1024,  # 5 MiB chunks
    )

    logger.debug(
        "Uploading {} to Drive folder {} as '{}'",
        local_path,
        folder_id,
        drive_filename,
    )

    uploaded = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        )
        .execute()
    )

    file_id = uploaded["id"]
    web_view_link = uploaded.get("webViewLink", "")

    logger.info(
        "Uploaded to Drive: {} -> {} ({})",
        original_name,
        file_id,
        web_view_link,
    )

    return {
        "file_id": file_id,
        "web_view_link": web_view_link,
    }


def clear_folder_cache() -> None:
    """Clear the in-memory folder ID cache (useful for testing)."""
    _folder_cache.clear()
