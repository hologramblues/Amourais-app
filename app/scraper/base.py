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
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    view_count: Optional[int] = None


@dataclass
class ProfileInfo:
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    biography: Optional[str] = None
    is_verified: Optional[bool] = None
    media_count: Optional[int] = None


@dataclass
class ExtractOptions:
    scrape_mode: str = "daily"  # "backfill" | "daily"
    max_scrolls: int = 30
    backfill_from: Optional[float] = None  # unix timestamp — oldest date
    backfill_to: Optional[float] = None  # unix timestamp — newest date
    proxy: Optional[str] = None  # http://user:pass@host:port
    # Borne incrémentale (lot B) : date ISO du post le plus récent DÉJÀ en base
    # pour ce profil. Le backend Apify la passe à l'acteur via
    # `onlyPostsNewerThan` pour ne PAYER que les posts réellement nouveaux ;
    # None (profil neuf / premier run) => scrape complet borné par resultsLimit.
    only_posts_newer_than: Optional[str] = None


@dataclass
class ExtractorResult:
    profile_info: ProfileInfo = field(default_factory=ProfileInfo)
    media: list[MediaItemData] = field(default_factory=list)
    total_seen: int = 0  # total posts found on page (including already-known / skipped)
    # Set by the 4 extractors when StealthyFetcher.fetch raises: the platform was
    # never reached. Consumed by pipeline._plateforme_non_atteinte to mark the job
    # `failed` instead of `empty` (risque #53, AUDIT.md §4.20 / lot 1.4b).
    fetch_error: Optional[str] = None


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
