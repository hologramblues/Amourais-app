import logging
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Text, Float, Boolean,
    ForeignKey, UniqueConstraint, Index, event,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from app.config import DB_PATH

logger = logging.getLogger(__name__)

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
    # ---- Empreintes de déduplication (lot A) -------------------------------
    # `md5` : empreinte du FICHIER tel qu'il est sur le disque. Deux médias qui
    # la partagent sont identiques au bit près. (`content_hash` est un SHA-256
    # posé par le téléchargeur ; il n'est pas indexé, n'existe que pour les
    # items passés par `download_media`, et sert au contrôle d'intégrité —
    # on ne le détourne pas.)
    # `phash`  : dhash 64 bits en hexadécimal (16 caractères), calculé sur une
    # vignette 9x8 en niveaux de gris. Il rapproche des images VOISINES, pas
    # identiques. Pour une vidéo il porte sur une image de référence extraite
    # du fichier — l'écran le dit explicitement.
    # Les deux colonnes sont NULLABLES : les 16 médias déjà en base n'en ont
    # aucune, et l'application doit fonctionner ainsi (le calcul différé est
    # exposé par /api/viewer/fingerprints/compute).
    md5 = Column(Text)
    phash = Column(Text)
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
    # Appartenances aux collections. `delete-orphan` : supprimer un média
    # retire ses appartenances, jamais l'inverse (supprimer une collection
    # ne touche AUCUN média — c'est CollectionItem qui disparaît).
    collection_links = relationship(
        "CollectionItem", back_populates="media_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("profile_id", "post_id", "media_url", name="idx_media_dedup"),
        Index("idx_media_status", "status"),
        Index("idx_media_profile", "profile_id"),
        # Les deux index de déduplication. Sur une base EXISTANTE, create_all()
        # saute la table entière (donc ses index) : c'est _migrate_add_columns
        # qui les crée, en CREATE INDEX IF NOT EXISTS.
        Index("idx_media_md5", "md5"),
        Index("idx_media_phash", "phash"),
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


class Collection(Base):
    """Regroupement MANUEL de médias, en travers des profils (critère V20).

    Les dossiers par profil sont imposés par la source ; une collection est
    choisie par le propriétaire et ignore la plateforme d'origine. Un média
    appartient à AUTANT de collections qu'on veut : c'est la table
    d'association `collection_items` qui porte le lien, jamais une colonne
    sur `media_items`.
    """
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))
    updated_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    items = relationship(
        "CollectionItem", back_populates="collection", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("name", name="idx_collections_name"),
    )


class CollectionItem(Base):
    """Appartenance d'un média à une collection. N médias × N collections.

    Supprimer la ligne ne supprime QUE l'appartenance : ni le média, ni son
    fichier. C'est la garantie annoncée dans le dialogue de suppression d'une
    collection.
    """
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(
        Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    media_item_id = Column(
        Integer, ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False
    )
    added_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    collection = relationship("Collection", back_populates="items")
    media_item = relationship("MediaItem", back_populates="collection_links")

    __table_args__ = (
        UniqueConstraint("collection_id", "media_item_id", name="idx_collection_item_unique"),
        Index("idx_collection_items_collection", "collection_id"),
        Index("idx_collection_items_media", "media_item_id"),
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


class IgInsightSnapshot(Base):
    """Daily snapshot of Instagram account insights from the Graph API."""
    __tablename__ = "ig_insight_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    followers_count = Column(Integer)
    following_count = Column(Integer)
    media_count = Column(Integer)
    reach = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    accounts_engaged = Column(Integer, default=0)
    profile_views = Column(Integer, default=0)
    snapshot_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    profile = relationship("Profile", backref="ig_insights")

    __table_args__ = (
        Index("idx_ig_insights_profile", "profile_id"),
        Index("idx_ig_insights_date", "snapshot_at"),
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


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    """Enable WAL + a busy timeout on every SQLite connection.

    WAL lets readers and a writer work concurrently (the web app reads while
    scrape threads write), and busy_timeout makes other connections wait for a
    lock instead of failing immediately with "database is locked".
    """
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")  # 15s
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


#: Message exact émis par SQLite quand la colonne visée par un
#: `ALTER TABLE ... ADD COLUMN` existe déjà : « duplicate column name: <col> »
#: (vérifié sur SQLite 3.45.3).  C'est le SEUL échec qu'une migration
#: idempotente a le droit d'ignorer.
_DUPLICATE_COLUMN_PREFIX = "duplicate column name"


def _migrate_add_columns():
    """Add new columns to existing tables (SQLite ALTER TABLE).

    create_all() only creates NEW tables — it never adds columns to
    existing ones.  We run ALTER TABLE ADD COLUMN for every column that
    may be missing.

    The migration is idempotent: re-running it raises « duplicate column
    name: ... » for every column already present, which is the one and only
    error we ignore.  Any OTHER OperationalError (missing table, locked
    database, disk full, target turned into a view…) means the schema is
    left INCOMPLETE — we log it loudly and re-raise instead of booting the
    application on a half-migrated database (AUDIT.md risk #1).

    It is also non-destructive by construction: ALTER TABLE ADD COLUMN only,
    never a DROP, never an UPDATE.
    """
    import sqlite3

    migrations = [
        # Profile — analytics columns
        ("profiles", "biography",        "TEXT"),
        ("profiles", "is_verified",      "BOOLEAN"),
        ("profiles", "followers_count",  "INTEGER"),
        ("profiles", "following_count",  "INTEGER"),
        ("profiles", "media_count",      "INTEGER"),
        # Profile — backfill window (risk #1: present in the model since
        # 5a46dc8 but never migrated, making every pre-5a46dc8 database
        # raise `no such column: profiles.backfill_from` on any ORM query)
        ("profiles", "backfill_from",    "INTEGER"),
        ("profiles", "backfill_to",      "INTEGER"),
        # MediaItem — Instagram engagement
        ("media_items", "ig_like_count",    "INTEGER"),
        ("media_items", "ig_comment_count", "INTEGER"),
        ("media_items", "ig_view_count",    "INTEGER"),
        # MediaItem — empreintes de déduplication (lot A). NULLABLES : la base
        # réelle a 16 médias sans empreinte, et l'application doit fonctionner
        # dans cet état. Le remplissage est différé, jamais bloquant.
        ("media_items", "md5",              "TEXT"),
        ("media_items", "phash",            "TEXT"),
    ]

    # `create_all()` ne pose les index QUE sur les tables qu'il vient de créer.
    # Sur la base du propriétaire, `media_items` existe déjà : sans ces deux
    # lignes, la recherche de doublons balayerait toute la table à chaque appel.
    # `IF NOT EXISTS` rend l'opération idempotente, et un CREATE INDEX ne
    # touche aucune donnée.
    index_migrations = [
        ("idx_media_md5", "CREATE INDEX IF NOT EXISTS idx_media_md5 ON media_items (md5)"),
        ("idx_media_phash", "CREATE INDEX IF NOT EXISTS idx_media_phash ON media_items (phash)"),
    ]

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()

        for table, column, col_type in migrations:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError as exc:
                if str(exc).lower().startswith(_DUPLICATE_COLUMN_PREFIX):
                    continue  # column already exists — migration is idempotent
                logger.error(
                    "Migration de schema echouee: ALTER TABLE %s ADD COLUMN %s %s "
                    "-> %s. Le schema est INCOMPLET, l'application ne peut pas "
                    "demarrer sur cette base.",
                    table, column, col_type, exc,
                )
                raise

        for nom, sql in index_migrations:
            try:
                cur.execute(sql)
            except sqlite3.OperationalError as exc:
                logger.error(
                    "Migration d'index echouee: %s -> %s. Le schema est "
                    "INCOMPLET, l'application ne peut pas demarrer sur cette "
                    "base.",
                    nom, exc,
                )
                raise

        conn.commit()
    finally:
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
