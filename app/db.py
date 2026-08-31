import logging
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Text, Float, Boolean,
    ForeignKey, UniqueConstraint, Index, event, text,
)
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import backref, declarative_base, sessionmaker, relationship
from sqlalchemy.schema import CreateIndex
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
    # ---- Phrase du futur meme (refonte UI « PAS-A-PAS ») -------------------
    # Ecrite au Tri rapide de la galerie (une phrase par media, au doigt),
    # relue par l'etape « Texte » de l'editeur pour pre-remplir le bandeau.
    # NULLABLE, et ce n'est pas un detail : les medias deja en base n'en ont
    # aucune, l'application doit fonctionner colonne vide, et une phrase
    # effacee redevient NULL plutot que chaine vide (un seul etat « pas de
    # phrase », donc un seul test cote client).
    # Aucun index : la phrase ne sert ni de filtre ni de tri, elle se lit
    # toujours par l'id du media.
    phrase = Column(Text)
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
    # Références de l'ÉDITEUR (lot 3.7, risques #11/#12). Aucune cascade : un
    # post programmé et un mème sauvegardé ont leur propre fichier, ils
    # survivent à la disparition du média source. La relation existe pour que
    # l'unité de travail SQLAlchemy remette `source_media_id` à NULL AVANT de
    # supprimer le média — c'est ce qui rend la suppression sûre même sur la
    # base de production, dont le DDL de `scheduled_posts` / `saved_memes` ne
    # porte PAS `ON DELETE SET NULL` (les tables préexistent, et SQLite ne sait
    # pas modifier une clé étrangère par ALTER TABLE).
    scheduled_posts = relationship("ScheduledPost", back_populates="source_media")
    saved_memes = relationship("SavedMeme", back_populates="source_media")

    __table_args__ = (
        UniqueConstraint("profile_id", "post_id", "media_url", name="idx_media_dedup"),
        Index("idx_media_status", "status"),
        Index("idx_media_profile", "profile_id"),
        # Les deux index de déduplication. Sur une base EXISTANTE, create_all()
        # saute la table entière (donc ses index) : c'est _migrate_add_indexes
        # qui les crée, en CREATE INDEX IF NOT EXISTS.
        Index("idx_media_md5", "md5"),
        Index("idx_media_phash", "phash"),
        # ---- Lot 3.8 / risque #25 : tris de la bibliothèque et de l'analytics.
        # `posted_at` seul sert `ORDER BY posted_at` (analytics), le composite
        # sert `WHERE status = ? ORDER BY posted_at` (viewer filtré). Les deux
        # suppriment un « USE TEMP B-TREE FOR ORDER BY » mesuré sur la base
        # réelle.
        Index("idx_media_posted_at", "posted_at"),
        Index("idx_media_status_posted", "status", "posted_at"),
        # Le tri PAR DÉFAUT du viewer porte sur `coalesce(posted_at,
        # discovered_at)` (viewer_api.py:238) : un index sur `posted_at` seul
        # ne le sert pas. SQLite sait utiliser un index d'EXPRESSION pour un
        # ORDER BY — vérifié par EXPLAIN QUERY PLAN sur une copie de la base
        # de production.
        Index("idx_media_date_effective", text("coalesce(posted_at, discovered_at)")),
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

    # `cascade="all, delete-orphan"` porté par le BACKREF (lot 3.7, risque #12) :
    # sans lui l'ORM tentait de mettre `profile_id` à NULL en supprimant un
    # profil, ce que `nullable=False` interdit — un profil doté d'un snapshot IG
    # était donc INDÉLETABLE (500 générique sur l'API).
    profile = relationship(
        "Profile",
        backref=backref("ig_insights", cascade="all, delete-orphan"),
    )

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
        # Lot 3.8 : l'historique des jobs est toujours lu en
        # `ORDER BY created_at DESC` (web/api.py:544, :586).
        Index("idx_jobs_created", "created_at"),
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
    # `ondelete="SET NULL"` (lot 3.7) : sur une base NEUVE, le moteur annule la
    # référence quand le média disparaît. Sur la base de production — dont le
    # DDL est antérieur — c'est la relation ORM `MediaItem.scheduled_posts` qui
    # fait le même travail avant la suppression.
    source_media_id = Column(
        Integer, ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at = Column(Integer)         # target unix timestamp
    status = Column(Text, nullable=False, default="draft")  # draft | scheduled | published | failed
    platforms = Column(Text)               # JSON array: ["instagram", "tiktok"]
    publish_results = Column(Text)         # JSON: per-platform results
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))
    updated_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    source_media = relationship("MediaItem", back_populates="scheduled_posts")

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
    # Même traitement que `ScheduledPost.source_media_id` (lot 3.7) : le mème
    # garde son propre fichier, seule la référence est annulée.
    source_media_id = Column(
        Integer, ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(Integer, nullable=False, default=lambda: int(datetime.now().timestamp()))

    source_media = relationship("MediaItem", back_populates="saved_memes")

    __table_args__ = (
        Index("idx_saved_memes_created", "created_at"),
    )


class SessionHealth(Base):
    """Dernier état connu de la session d'une plateforme (lot « santé de session »).

    Une ligne par plateforme, écrasée à chaque évaluation. La table est un
    CACHE d'affichage, jamais une source de vérité : `session_health.py` sait
    recalculer le signal passif sans elle, et l'application fonctionne
    parfaitement quand la table est VIDE (c'est l'état d'une base de production
    au premier démarrage après cette migration).

    TOUTES les colonnes sont NULLABLES sauf `platform` : rien ici ne peut
    empêcher une écriture ni un démarrage.
    """

    __tablename__ = "session_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(Text, nullable=False)  # instagram | reddit | tiktok | twitter
    #: connecté | déconnecté | bloqué | inconnu — jamais un booléen.
    state = Column(Text)
    message = Column(Text)          # explication courte, en français
    remedy = Column(Text)           # geste correctif, quand il y en a un
    source = Column(Text)           # cookies | jobs | sonde | aucune
    details = Column(Text)          # JSON : la liste des indices retenus
    checked_at = Column(Integer)    # dernière évaluation (unix)
    last_probe_at = Column(Integer)  # dernière SONDE ACTIVE (unix)
    last_ok_at = Column(Integer)    # dernière preuve de session vivante (unix)
    cookies_mtime = Column(Integer)  # date du fichier de cookies (unix)
    expires_at = Column(Integer)    # expiration la plus proche d'un cookie critique

    __table_args__ = (
        UniqueConstraint("platform", name="idx_session_health_platform"),
    )


# ---- Engine & Session ----

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    """Enable WAL, a busy timeout and FOREIGN KEYS on every SQLite connection.

    WAL lets readers and a writer work concurrently (the web app reads while
    scrape threads write), and busy_timeout makes other connections wait for a
    lock instead of failing immediately with "database is locked".

    `foreign_keys` (lot 3.7, risque #11) est OFF par défaut dans SQLite : sans
    ce PRAGMA, tous les `ON DELETE CASCADE` déclarés dans le schéma sont
    DÉCORATIFS. Il est posé par connexion (le réglage n'est pas persistant) et
    hors transaction — un `PRAGMA foreign_keys` émis à l'intérieur d'une
    transaction est silencieusement ignoré, d'où sa place ici.

    Contrôle préalable : `PRAGMA foreign_key_check` sur une COPIE de la base de
    production ne remonte AUCUNE violation (0 orphelin, 13 tables). Le
    contrôle est rejoué à chaque `init_db()` — cf. `_detecter_les_orphelins`.
    """
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")  # 15s
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
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
        # MediaItem — phrase du futur meme (refonte UI « PAS-A-PAS »). NULLABLE
        # pour la meme raison que les empreintes : la base reelle n'en a
        # aucune, et l'ecran doit fonctionner ainsi. ADD COLUMN sans DEFAULT
        # ni NOT NULL : SQLite se contente de reecrire l'en-tete de la table,
        # aucune ligne n'est touchee.
        ("media_items", "phrase",           "TEXT"),
        # NOTE — `session_health` (lot « santé de session ») ne figure PAS ici,
        # et c'est volontaire : cette liste ne s'adresse qu'aux tables DÉJÀ
        # PRÉSENTES dans une base ancienne, auxquelles il manque des colonnes.
        # `session_health` est une table ENTIÈREMENT nouvelle : `create_all()`
        # la crée d'un bloc, avec toutes ses colonnes, et son `checkfirst`
        # équivaut à un CREATE TABLE IF NOT EXISTS (aucun DROP, aucun UPDATE).
        # L'y ajouter ferait au contraire ÉCHOUER la migration sur toute base
        # où la table n'existe pas encore — « no such table » n'est pas une
        # erreur ignorable ici, et ne doit pas le devenir.
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

        conn.commit()
    finally:
        conn.close()


def _index_declares():
    """Yield `(nom, DDL)` pour CHAQUE `Index(...)` déclaré dans les modèles.

    Le DDL est produit par le compilateur SQLite de SQLAlchemy, jamais
    concaténé à la main : les index d'EXPRESSION (`coalesce(posted_at,
    discovered_at)`) et un éventuel `unique=True` sortent alors corrects, et la
    liste ne peut pas diverger des modèles — il n'y a pas de liste.
    """
    dialecte = sqlite_dialect.dialect()
    for table in Base.metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda ix: ix.name or ""):
            ddl = str(
                CreateIndex(index, if_not_exists=True).compile(dialect=dialecte)
            ).strip()
            yield index.name, ddl


def _migrate_add_indexes():
    """Create every declared index on a PREEXISTING table (SQLite DDL).

    Le piège du risque #62, vérifié par exécution (SQLAlchemy 2.0.47) :
    `create_all()` écarte en bloc toute table déjà présente
    (`_can_create_table()`) et n'appelle donc jamais `visit_table`, SEUL
    émetteur des `CREATE INDEX`. Ajouter un `Index(...)` au modèle d'une table
    en production est un **no-op silencieux** — aucune exception, les requêtes
    continuent simplement en balayage complet.

    Cette migration comble le trou, exactement comme `_migrate_add_columns`
    comble celui des colonnes : `CREATE INDEX IF NOT EXISTS`, donc idempotente,
    et non destructive par construction (jamais de DROP, jamais d'UPDATE).

    Elle s'exécute APRÈS `_migrate_add_columns` : un index sur une colonne
    fraîchement ajoutée (`md5`, `phash`) a besoin que la colonne existe.

    Comme pour les colonnes, un échec est journalisé bruyamment puis propagé :
    un index déclaré et jamais créé est précisément ce que ce lot corrige, il
    ne doit pas pouvoir passer inaperçu une seconde fois.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        for nom, sql in _index_declares():
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


def _detecter_les_orphelins():
    """Report (never delete) rows that violate a declared foreign key.

    `PRAGMA foreign_keys=ON` n'invalide PAS rétroactivement les lignes déjà en
    base : SQLite ne contrôle une contrainte qu'au moment où une écriture la
    touche. Une base contenant des orphelins ne « casse » donc pas au
    démarrage — elle casse plus tard, sur une écriture, loin de la cause.

    D'où ce contrôle au boot : il COMPTE et JOURNALISE, il ne supprime rien.
    Décider du sort de lignes orphelines est une décision du propriétaire, pas
    d'une migration automatique (AUDIT.md §9, question ouverte n°5).

    Renvoie le nombre de violations, `{}` quand la base est saine.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    try:
        try:
            lignes = conn.execute("PRAGMA foreign_key_check").fetchall()
        except sqlite3.DatabaseError as exc:
            # Un contrôle de diagnostic ne doit JAMAIS empêcher le démarrage.
            logger.warning("PRAGMA foreign_key_check indisponible: %s", exc)
            return {}
    finally:
        conn.close()

    violations: dict[str, int] = {}
    for table, _rowid, table_parente, _fk_id in lignes:
        cle = f"{table} -> {table_parente}"
        violations[cle] = violations.get(cle, 0) + 1

    if violations:
        logger.error(
            "INTEGRITE REFERENTIELLE: %d ligne(s) orpheline(s) detectee(s) dans "
            "%s. PRAGMA foreign_keys=ON est actif : SQLite ne revalide pas les "
            "lignes existantes, elles restent donc lisibles et modifiables, mais "
            "toute ecriture qui TOUCHE la cle etrangere echouera. Aucune "
            "suppression automatique n'est faite — le nettoyage est une decision "
            "du proprietaire.",
            sum(violations.values()),
            ", ".join(f"{cle} ({n})" for cle, n in sorted(violations.items())),
        )
    return violations


def init_db():
    """Create tables if they don't exist, then run column and index migrations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate_add_columns()
    _migrate_add_indexes()
    _detecter_les_orphelins()


def get_db():
    """Yield a DB session (for use as context manager)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
