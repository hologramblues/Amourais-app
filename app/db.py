from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Text, Float, Boolean,
    ForeignKey, UniqueConstraint, Index, event,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from app.config import DB_PATH

Base = declarative_base()


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(Text, nullable=False)  # instagram | tiktok | twitter
    username = Column(Text, nullable=False)
    profile_url = Column(Text, nullable=False)
    display_name = Column(Text)
    avatar_url = Column(Text)
    biography = Column(Text)
    is_verified = Column(Boolean)
    followers_count = Column(Integer)
    following_count = Column(Integer)
    media_count = Column(Integer)
    is_active = Column(Boolean, nullable=False, default=True)
    scrape_mode = Column(Text, nullable=False, default="backfill")  # backfill | daily
    scrape_interval_minutes = Column(Integer, nullable=False, default=360)
    last_scraped_at = Column(Integer)  # unix timestamp
    backfill_from = Column(Integer)  # unix timestamp — oldest date to scrape (optional)
    backfill_to = Column(Integer)  # unix timestamp — newest date to scrape (optional, default=now)
    gdrive_folder_id = Column(Text)
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))
    updated_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    media_items = relationship("MediaItem", back_populates="profile", cascade="all, delete-orphan")
    scrape_jobs = relationship("ScrapeJob", back_populates="profile", cascade="all, delete-orphan")
    snapshots = relationship("ProfileSnapshot", back_populates="profile", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("platform", "username", name="idx_profiles_platform_username"),
    )

    @property
    def last_scraped_dt(self):
        if self.last_scraped_at:
            return datetime.fromtimestamp(self.last_scraped_at)
        return None


class MediaItem(Base):
    __tablename__ = "media_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    platform = Column(Text, nullable=False)
    post_id = Column(Text, nullable=False)
    post_url = Column(Text)
    media_type = Column(Text, nullable=False)  # image | video
    media_url = Column(Text, nullable=False)
    content_hash = Column(Text)
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    duration = Column(Float)
    caption = Column(Text)
    ig_like_count = Column(Integer)
    ig_comment_count = Column(Integer)
    ig_view_count = Column(Integer)
    posted_at = Column(Integer)  # unix timestamp
    status = Column(Text, nullable=False, default="pending")
    local_path = Column(Text)
    gdrive_file_id = Column(Text)
    gdrive_url = Column(Text)
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    discovered_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))
    downloaded_at = Column(Integer)
    uploaded_at = Column(Integer)

    profile = relationship("Profile", back_populates="media_items")
    comments = relationship("MediaComment", back_populates="media_item", cascade="all, delete-orphan")
    ratings = relationship("MediaRating", back_populates="media_item", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("profile_id", "post_id", "media_url", name="idx_media_dedup"),
        Index("idx_media_status", "status"),
        Index("idx_media_profile", "profile_id"),
    )


class MediaComment(Base):
    __tablename__ = "media_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_item_id = Column(Integer, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    user_name = Column(Text, nullable=False)
    comment_text = Column(Text, nullable=False)
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    media_item = relationship("MediaItem", back_populates="comments")

    __table_args__ = (
        Index("idx_comments_media", "media_item_id"),
    )


class MediaRating(Base):
    __tablename__ = "media_ratings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    media_item_id = Column(Integer, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    user_name = Column(Text, nullable=False)
    rating = Column(Integer, nullable=False)  # 1-5
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    media_item = relationship("MediaItem", back_populates="ratings")

    __table_args__ = (
        UniqueConstraint("media_item_id", "user_name", name="idx_rating_unique"),
        Index("idx_ratings_media", "media_item_id"),
    )


class ProfileSnapshot(Base):
    """Daily snapshot of profile stats for tracking growth over time."""
    __tablename__ = "profile_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    followers_count = Column(Integer)
    following_count = Column(Integer)
    media_count = Column(Integer)
    snapshot_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    profile = relationship("Profile", back_populates="snapshots")

    __table_args__ = (
        Index("idx_snapshots_profile", "profile_id"),
        Index("idx_snapshots_date", "snapshot_at"),
    )


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    status = Column(Text, nullable=False, default="queued")  # queued | running | completed | failed | partial
    triggered_by = Column(Text, nullable=False)  # scheduler | manual
    media_found = Column(Integer, nullable=False, default=0)
    media_new = Column(Integer, nullable=False, default=0)
    media_downloaded = Column(Integer, nullable=False, default=0)
    media_uploaded = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    started_at = Column(Integer)
    completed_at = Column(Integer)
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    profile = relationship("Profile", back_populates="scrape_jobs")

    __table_args__ = (
        Index("idx_jobs_profile", "profile_id"),
        Index("idx_jobs_status", "status"),
    )


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text)
    caption = Column(Text)
    media_path = Column(Text)              # path to image/video
    media_type = Column(Text)              # image | video
    template_format = Column(Text)         # square | portrait | story
    thumbnail_path = Column(Text)          # small preview
    source_media_id = Column(Integer, ForeignKey("media_items.id"), nullable=True)
    scheduled_at = Column(Integer)         # target unix timestamp
    status = Column(Text, nullable=False, default="draft")  # draft | scheduled | published | failed
    platforms = Column(Text)               # JSON array: ["instagram", "tiktok"]
    publish_results = Column(Text)         # JSON: per-platform results
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))
    updated_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    __table_args__ = (
        Index("idx_scheduled_posts_status", "status"),
        Index("idx_scheduled_posts_scheduled_at", "scheduled_at"),
    )


class SavedMeme(Base):
    """Memes created in the editor and saved to the viewer gallery."""
    __tablename__ = "saved_memes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text)
    caption = Column(Text)
    media_type = Column(Text, nullable=False, default="image")  # image | video
    template_format = Column(Text)  # square | portrait | story
    file_path = Column(Text, nullable=False)  # path to the saved meme file
    thumbnail_path = Column(Text)  # path to thumbnail (for videos)
    file_size = Column(Integer)
    source_media_id = Column(Integer, ForeignKey("media_items.id"), nullable=True)
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    __table_args__ = (
        Index("idx_saved_memes_created", "created_at"),
    )


# ---- Engine & Session ----

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def _migrate_add_columns():
    """Add new columns to existing tables (SQLite ALTER TABLE).

    create_all() only creates NEW tables — it never adds columns to
    existing ones.  We run ALTER TABLE ADD COLUMN for every column that
    may be missing.  SQLite raises an OperationalError if the column
    already exists, which we silently ignore.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    migrations = [
        # Profile — analytics columns
        ("profiles", "biography",        "TEXT"),
        ("profiles", "is_verified",      "BOOLEAN"),
        ("profiles", "followers_count",  "INTEGER"),
        ("profiles", "following_count",  "INTEGER"),
        ("profiles", "media_count",      "INTEGER"),
        # MediaItem — Instagram engagement
        ("media_items", "ig_like_count",    "INTEGER"),
        ("media_items", "ig_comment_count", "INTEGER"),
        ("media_items", "ig_view_count",    "INTEGER"),
    ]

    for table, column, col_type in migrations:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.commit()
    conn.close()


def init_db():
    """Create tables if they don't exist, then run column migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate_add_columns()


def get_db():
    """Yield a DB session (for use as context manager)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
