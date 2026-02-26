from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class MediaItemData:
    post_id: str
    post_url: str
    media_type: str  # "image" | "video"
    media_url: str
    caption: Optional[str] = None
    posted_at: Optional[datetime] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None


@dataclass
class ProfileInfo:
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


@dataclass
class ExtractOptions:
    scrape_mode: str = "daily"  # "backfill" | "daily"
    max_scrolls: int = 30
    backfill_from: Optional[float] = None  # unix timestamp — oldest date
    backfill_to: Optional[float] = None  # unix timestamp — newest date


@dataclass
class ExtractorResult:
    profile_info: ProfileInfo = field(default_factory=ProfileInfo)
    media: list[MediaItemData] = field(default_factory=list)


class PlatformExtractor:
    """Base class for platform-specific extractors using Scrapling."""

    platform: str = ""

    def extract(
        self,
        profile_url: str,
        known_post_ids: set[str],
        options: ExtractOptions | None = None,
    ) -> ExtractorResult:
        raise NotImplementedError
