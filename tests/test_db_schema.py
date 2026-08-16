"""
MODULE DE TEST — SCHÉMA, MIGRATIONS, INTÉGRITÉ RÉFÉRENTIELLE (`app/db.py`).

Couvre le test **T1** de §7.4 d'AUDIT.md (volet colonnes ET volet index) ainsi
que le volet « cascades » de **T9**.

────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE ÉTABLIT
────────────────────────────────────────────────────────────────────────────
1. Sur une base NEUVE (celle du socle), `create_all()` produit un schéma
   conforme aux modèles — colonnes ET index. C'est le cas facile, et il est
   vert : il sert de témoin.
2. Sur une base PRÉEXISTANTE (le cas de la production), tout se détraque :
     * `_migrate_add_columns` n'ajoute pas `backfill_from` / `backfill_to`
       (risque #1, corrigé au lot 1.1) ;
     * `_migrate_add_columns` avale silencieusement TOUTE `OperationalError`,
       pas seulement « duplicate column name » ;
     * `create_all()` ne crée AUCUN index sur une table qui existe déjà
       (risque #62, piège du lot 3.8).
3. `PRAGMA foreign_keys` n'étant jamais activé (risque #11), les
   `ondelete="CASCADE"` sont décoratifs : seule la cascade ORM protège les
   enfants (lot 3.7).

────────────────────────────────────────────────────────────────────────────
CONVENTION (LOI ABSOLUE n°4)
────────────────────────────────────────────────────────────────────────────
Chaque bug connu donne DEUX tests :
  * un test VERT qui documente le comportement réel d'aujourd'hui
    (caractérisation — il détecte tout changement, même « accidentellement
    bon ») ;
  * un test `xfail(strict=True)` qui exprime le comportement CORRECT attendu.
    Le jour de la correction il passe XPASS et fait échouer la suite en
    réclamant le retrait du xfail.

Aucun test de ce module ne touche `data/` : les bases « legacy » sont
fabriquées dans `tmp_path`, et `app.db.DB_PATH` / `app.db.engine` sont
monkeypatchés le temps de l'appel.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.db import Base, MediaItem, Profile, SavedMeme, ScheduledPost

pytestmark = pytest.mark.db


# ===========================================================================
# Outillage — fabrication d'une base « legacy » et introspection SQLite
# ===========================================================================

#: `profiles` tel qu'il existe dans la base de PRODUCTION (AUDIT.md ligne 390 :
#: `PRAGMA table_info(profiles)` → 18 colonnes, aucune `backfill_*`).
#: Les 5 colonnes analytics et les 2 colonnes de fenêtre de backfill sont
#: absentes : c'est exactement l'état d'une base antérieure au commit 5a46dc8.
LEGACY_PROFILES_DDL = """
CREATE TABLE profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    is_active BOOLEAN NOT NULL,
    scrape_mode TEXT NOT NULL,
    scrape_interval_minutes INTEGER NOT NULL,
    last_scraped_at INTEGER,
    gdrive_folder_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CONSTRAINT idx_profiles_platform_username UNIQUE (platform, username)
)
"""

#: `media_items` sans les 3 colonnes d'engagement Instagram et — surtout —
#: SANS ses index `idx_media_status` / `idx_media_profile`.
LEGACY_MEDIA_ITEMS_DDL = """
CREATE TABLE media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    post_url TEXT,
    media_type TEXT NOT NULL,
    media_url TEXT NOT NULL,
    content_hash TEXT,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    duration FLOAT,
    caption TEXT,
    posted_at INTEGER,
    status TEXT NOT NULL,
    local_path TEXT,
    gdrive_file_id TEXT,
    gdrive_url TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL,
    discovered_at INTEGER NOT NULL,
    downloaded_at INTEGER,
    uploaded_at INTEGER,
    CONSTRAINT idx_media_dedup UNIQUE (profile_id, post_id, media_url)
)
"""


#: `scheduled_posts` et `saved_memes` TELS QU'ILS SONT EN PRODUCTION (relevé par
#: `SELECT sql FROM sqlite_master` sur une copie de `data/samourais.db`) : leur
#: clé étrangère `source_media_id` ne porte AUCUNE clause `ON DELETE`. Le lot
#: 3.7 ajoute `ondelete="SET NULL"` au modèle, ce qui ne change QUE les bases
#: neuves — SQLite ne sait pas modifier une clé étrangère par `ALTER TABLE`, et
#: reconstruire la table serait destructif. Ces deux DDL servent à prouver que
#: la relation ORM couvre le cas réel.
LEGACY_SCHEDULED_POSTS_DDL = """
CREATE TABLE scheduled_posts (
    id INTEGER NOT NULL,
    title TEXT,
    caption TEXT,
    media_path TEXT,
    media_type TEXT,
    template_format TEXT,
    thumbnail_path TEXT,
    source_media_id INTEGER,
    scheduled_at INTEGER,
    status TEXT NOT NULL,
    platforms TEXT,
    publish_results TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(source_media_id) REFERENCES media_items (id)
)
"""

LEGACY_SAVED_MEMES_DDL = """
CREATE TABLE saved_memes (
    id INTEGER NOT NULL,
    title TEXT,
    caption TEXT,
    media_type TEXT NOT NULL,
    template_format TEXT,
    file_path TEXT NOT NULL,
    thumbnail_path TEXT,
    file_size INTEGER,
    source_media_id INTEGER,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(source_media_id) REFERENCES media_items (id)
)
"""


def _make_legacy_db(path: Path, *, ddls) -> Path:
    """Crée une base SQLite ne contenant que les tables décrites par `ddls`."""
    conn = sqlite3.connect(str(path))
    try:
        for ddl in ddls:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    return path


def _columns_of(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _index_names(path: Path, table: str | None = None) -> set[str]:
    """Index NOMMÉS présents dans la base (les auto-index SQLite sont exclus)."""
    sql = "SELECT name FROM sqlite_master WHERE type='index'"
    params: tuple = ()
    if table is not None:
        sql += " AND tbl_name = ?"
        params = (table,)
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0]
            for row in conn.execute(sql, params)
            if not row[0].startswith("sqlite_autoindex_")
        }
    finally:
        conn.close()


def _declared_index_names(table_name: str) -> set[str]:
    """Index déclarés par `Index(...)` dans le modèle (hors UniqueConstraint)."""
    table = Base.metadata.tables[table_name]
    return {index.name for index in table.indexes}


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """Fabrique une base « legacy » et y branche `app.db` le temps du test.

    Retourne un appelable : `legacy_db(LEGACY_PROFILES_DDL, ...) -> Path`.
    `app.db.DB_PATH` et `app.db.engine` sont repointés dessus, ce qui rend
    `_migrate_add_columns()` et `init_db()` observables SANS modifier `app/`
    (`_migrate_add_columns` relit le global `DB_PATH` à chaque appel —
    `db.py:268` — et `init_db` le global `engine` — `db.py:297`).
    """
    created_engines = []

    def _build(*ddls, rebind_engine: bool = False) -> Path:
        path = _make_legacy_db(tmp_path / "legacy.db", ddls=ddls)
        monkeypatch.setattr(app_db, "DB_PATH", path)
        if rebind_engine:
            engine = create_engine(f"sqlite:///{path}")
            created_engines.append(engine)
            monkeypatch.setattr(app_db, "engine", engine)
        return path

    yield _build

    for engine in created_engines:
        engine.dispose()


# ===========================================================================
# 1. Base NEUVE — `create_all()` est conforme aux modèles (témoin vert)
# ===========================================================================


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_create_all_cree_toutes_les_colonnes_declarees(db_engine, table_name):
    """Sur une base neuve, chaque colonne du modèle existe en base (T1, colonnes)."""
    declared = {col.name for col in Base.metadata.tables[table_name].columns}
    with db_engine.connect() as conn:
        actual = {
            row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
        }
    assert actual, f"table {table_name!r} absente de la base de test"
    assert declared - actual == set(), (
        f"colonnes déclarées mais absentes de {table_name} : {declared - actual}"
    )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_create_all_cree_tous_les_index_declares(db_engine, table_name):
    """Sur une base neuve, chaque `Index(...)` du modèle existe (T1, volet index)."""
    declared = _declared_index_names(table_name)
    with db_engine.connect() as conn:
        # Garde symétrique de celle du test frère sur les colonnes : sans elle,
        # le paramètre [profiles] (aucun Index déclaré, seulement un
        # UniqueConstraint) comparerait set() à set() sur une table absente et
        # ne pourrait structurellement pas échouer.
        tables_en_base = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert table_name in tables_en_base, (
            f"table {table_name!r} absente de la base de test"
        )
        actual = {
            row[1]
            for row in conn.exec_driver_sql(f"PRAGMA index_list({table_name})")
            if not row[1].startswith("sqlite_autoindex_")
        }
    assert declared - actual == set(), (
        f"index déclarés mais absents de {table_name} : {declared - actual}"
    )


def test_profiles_ne_declare_aucun_index_nomme():
    """Fige le seul cas où la paramétrisation ci-dessus n'a rien à comparer :
    `profiles` n'a qu'un `UniqueConstraint` (db.py), aucun `Index(...)`. Si un
    index y est ajouté un jour, ce test rougit et rappelle que le paramètre
    [profiles] du test frère cesse d'être vide."""
    assert _declared_index_names("profiles") == set()


def test_backfill_from_et_backfill_to_sont_bien_dans_le_modele():
    """Prérequis du risque #1 : les 2 colonnes existent côté modèle (db.py:30-31)."""
    columns = {col.name for col in Base.metadata.tables["profiles"].columns}
    assert {"backfill_from", "backfill_to"} <= columns


# ===========================================================================
# 2. Base PRÉEXISTANTE — `_migrate_add_columns` (risque #1, lot 1.1)
# ===========================================================================


def test_migrate_add_columns_ajoute_bien_les_8_colonnes_codees_en_dur(legacy_db):
    """Témoin : la liste en dur de `db.py:271-282` fait ce qu'elle annonce."""
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)

    app_db._migrate_add_columns()

    assert {
        "biography",
        "is_verified",
        "followers_count",
        "following_count",
        "media_count",
    } <= _columns_of(path, "profiles")
    assert {
        "ig_like_count",
        "ig_comment_count",
        "ig_view_count",
    } <= _columns_of(path, "media_items")


def test_migrate_add_columns_noublie_plus_aucune_colonne_de_profiles(legacy_db):
    """RÉGRESSION du risque #1 (lot 1.1) : la liste en dur de
    `_migrate_add_columns` couvre désormais TOUTES les colonnes du modèle
    `Profile` absentes d'une base legacy — plus seulement les 5 analytics.

    Ce test remplace la caractérisation de l'oubli (`manquantes ==
    {"backfill_from", "backfill_to"}`) : il rougira le jour où une colonne
    sera ajoutée au modèle sans être ajoutée à la migration.
    """
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)

    app_db._migrate_add_columns()

    declared = {col.name for col in Base.metadata.tables["profiles"].columns}
    manquantes = declared - _columns_of(path, "profiles")
    assert manquantes == set(), (
        f"colonnes déclarées dans Profile mais absentes de _migrate_add_columns : "
        f"{manquantes}"
    )


def test_migrate_add_columns_doit_ajouter_backfill_from_et_backfill_to(legacy_db):
    """COMPORTEMENT ATTENDU : après migration, une vieille base porte les 2 colonnes.

    Sans elles, toute requête ORM sur `Profile` lève `no such column` : `/`,
    `/profiles`, `/api/profiles` renvoient 500 et `check_due_profiles` avale
    l'exception, ce qui arrête le scraping DÉFINITIVEMENT (AUDIT.md, risque #1).
    """
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)

    app_db._migrate_add_columns()

    assert {"backfill_from", "backfill_to"} <= _columns_of(path, "profiles")


def test_migrate_add_columns_est_idempotent(legacy_db):
    """Deuxième passage : les `duplicate column name` sont bien absorbés."""
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)

    app_db._migrate_add_columns()
    apres_1 = _columns_of(path, "profiles")
    # Sans cette garde, le test survivrait à un `_migrate_add_columns` réduit à
    # un no-op : deux ensembles VIDES sont égaux. L'idempotence n'a de sens que
    # si le premier passage a réellement travaillé.
    assert "biography" in apres_1, "le premier passage n'a rien migré"
    app_db._migrate_add_columns()  # ne doit pas lever
    apres_2 = _columns_of(path, "profiles")

    assert apres_1 == apres_2


# ---------------------------------------------------------------------------
# `except sqlite3.OperationalError: pass` avale TOUT (db.py:287-288)
# ---------------------------------------------------------------------------


def test_une_alter_table_sur_une_table_absente_leve_bien_une_operationalerror(
    tmp_path,
):
    """Prérequis du test suivant : l'erreur avalée n'est PAS un doublon de colonne."""
    path = _make_legacy_db(tmp_path / "sans-media.db", ddls=[LEGACY_PROFILES_DDL])

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            conn.execute("ALTER TABLE media_items ADD COLUMN ig_like_count INTEGER")
    finally:
        conn.close()

    message = str(excinfo.value).lower()
    assert "no such table" in message
    assert "duplicate column" not in message


def test_migrate_add_columns_propage_une_erreur_qui_nest_pas_un_doublon(
    legacy_db, caplog
):
    """RÉGRESSION (lot 1.1, volet robustesse) : seule « duplicate column name »
    est absorbée ; toute autre `OperationalError` est journalisée en ERROR puis
    propagée.

    Ici `media_items` n'existe pas : la première migration d'engagement échoue
    sur `no such table`. Avant le lot 1.1, `except sqlite3.OperationalError:
    pass` l'avalait et l'application démarrait sur un schéma incomplet, sans
    exception, sans log, sans valeur de retour.
    """
    path = legacy_db(LEGACY_PROFILES_DDL)
    assert "media_items" not in _table_names(path)

    with caplog.at_level("ERROR", logger="app.db"):
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            app_db._migrate_add_columns()

    assert "no such table" in str(excinfo.value)
    assert "duplicate column" not in str(excinfo.value).lower()
    assert any(
        record.levelname == "ERROR" and "media_items" in record.getMessage()
        for record in caplog.records
    ), "la panne de migration doit être journalisée bruyamment"

    # La migration reste NON DESTRUCTIVE : les ALTER déjà appliqués tiennent,
    # ce qui garde le second passage idempotent.
    assert "biography" in _columns_of(path, "profiles")


def test_migrate_add_columns_propage_meme_une_table_devenue_une_vue(legacy_db):
    """Second échantillon d'erreur non-doublon : « Cannot add a column to a view »."""
    path = legacy_db(
        LEGACY_PROFILES_DDL,
        LEGACY_MEDIA_ITEMS_DDL.replace("media_items", "media_items_reelle"),
        "CREATE VIEW media_items AS SELECT * FROM media_items_reelle",
    )

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        app_db._migrate_add_columns()

    assert "column to a view" in str(excinfo.value).lower()
    assert "ig_like_count" not in _columns_of(path, "media_items")


def test_migrate_add_columns_ne_journalise_rien_quand_tout_va_bien(
    legacy_db, caplog
):
    """Contre-épreuve du test précédent : un `duplicate column name` reste
    silencieux. Sans cette garde, un `_migrate_add_columns` qui journaliserait
    à chaque démarrage passerait pour correct."""
    legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)

    app_db._migrate_add_columns()  # premier passage : tout est ajouté
    with caplog.at_level("ERROR", logger="app.db"):
        app_db._migrate_add_columns()  # second passage : que des doublons

    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


# ===========================================================================
# 3. `init_db()` sur une base legacy — T1 de bout en bout
# ===========================================================================


def test_init_db_sur_base_legacy_cree_les_tables_manquantes(legacy_db):
    """Témoin : `create_all()` fait bien son travail sur les tables ABSENTES."""
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL, rebind_engine=True)

    app_db.init_db()

    assert set(Base.metadata.tables) <= _table_names(path)
    # Une table créée par create_all porte, elle, tous ses index.
    assert _declared_index_names("scrape_jobs") <= _index_names(path, "scrape_jobs")


def test_init_db_sur_base_legacy_doit_rendre_le_schema_conforme_aux_modeles(
    legacy_db,
):
    """COMPORTEMENT ATTENDU (T1, volet colonnes) : après `init_db()`, aucune
    colonne déclarée dans `Base.metadata` ne manque, table par table."""
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL, rebind_engine=True)

    app_db.init_db()

    manquantes = {}
    for name, table in Base.metadata.tables.items():
        absentes = {c.name for c in table.columns} - _columns_of(path, name)
        if absentes:
            manquantes[name] = absentes
    assert manquantes == {}


# ===========================================================================
# 4. Risque #62 / lot 3.8 — `create_all()` n'indexe pas une table existante
# ===========================================================================


def test_create_all_cree_les_index_dune_table_quil_cree_lui_meme(tmp_path):
    """Témoin : quand `create_all()` crée la table, il émet bien ses CREATE INDEX."""
    path = tmp_path / "index-neuf.db"
    metadata = MetaData()
    table = Table(
        "widgets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", Text),
    )
    Index("idx_widgets_status", table.c.status)

    engine = create_engine(f"sqlite:///{path}")
    try:
        metadata.create_all(engine)
    finally:
        engine.dispose()

    assert "idx_widgets_status" in _index_names(path, "widgets")


def test_create_all_ne_cree_aucun_index_sur_une_table_preexistante(tmp_path):
    """CARACTÉRISATION du risque #62 (SQLAlchemy 2.0.47).

    `_can_create_table()` écarte en bloc toute table pour laquelle
    `dialect.has_table()` est vrai ; `visit_table` — seul émetteur des
    `CREATE INDEX` — n'est alors jamais appelé. Ajouter un `Index(...)` au
    modèle d'une table déjà en production est donc un **no-op silencieux** :
    aucune exception, les requêtes continuent simplement en scan complet.

    C'est le piège qui rendrait le lot 3.8 inopérant sans qu'on s'en aperçoive.
    """
    path = tmp_path / "index-preexistant.db"

    # Version 1 du modèle : pas d'index. La table est créée.
    metadata_v1 = MetaData()
    Table(
        "widgets",
        metadata_v1,
        Column("id", Integer, primary_key=True),
        Column("status", Text),
    )
    engine = create_engine(f"sqlite:///{path}")
    try:
        metadata_v1.create_all(engine)
        assert _index_names(path, "widgets") == set()

        # Version 2 du modèle : un index est ajouté, puis create_all est relancé
        # — exactement ce que fait `init_db()` (db.py:297).
        metadata_v2 = MetaData()
        table_v2 = Table(
            "widgets",
            metadata_v2,
            Column("id", Integer, primary_key=True),
            Column("status", Text),
        )
        Index("idx_widgets_status", table_v2.c.status)
        metadata_v2.create_all(engine)
    finally:
        engine.dispose()

    assert _index_names(path, "widgets") == set(), (
        "SQLAlchemy aurait créé l'index sur une table préexistante : le "
        "risque #62 serait obsolète, ce test et le lot 3.8 sont à revoir."
    )


def test_init_db_sur_base_legacy_indexe_bien_media_items(legacy_db):
    """RÉGRESSION du risque #62 sur le VRAI schéma (lot 3.8).

    `media_items` préexiste SANS ses index. `create_all()` saute la table
    entière, donc ses `CREATE INDEX` : avant le lot 3.8, `idx_media_status` et
    `idx_media_profile` restaient absents d'une base de production. C'est
    désormais `_migrate_add_indexes()` — piloté par les `Index(...)` déclarés,
    sans aucune liste en dur — qui les pose.

    Ce test nomme les index un par un, là où son voisin générique compare des
    ensembles : si quelqu'un retire une déclaration du modèle, le test
    générique reste vert (moins de déclarations, moins d'attentes), celui-ci
    rougit.
    """
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL, rebind_engine=True)
    assert _index_names(path, "media_items") == set(), "la base legacy part sans index"

    app_db.init_db()

    assert {
        "idx_media_status",
        "idx_media_profile",
        "idx_media_md5",
        "idx_media_phash",
        "idx_media_posted_at",
        "idx_media_status_posted",
        "idx_media_date_effective",
    } <= _index_names(path, "media_items")


def test_migrate_add_indexes_est_idempotent(legacy_db):
    """Trois passages de suite ne doivent ni lever ni changer quoi que ce soit.

    `init_db()` est appelé à CHAQUE démarrage (run.py) : une migration d'index
    non idempotente ferait tomber l'application au deuxième boot.
    """
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL, rebind_engine=True)

    app_db.init_db()
    apres_1 = _index_names(path)
    # Sans cette garde, le test survivrait à un `_migrate_add_indexes` réduit à
    # un no-op : deux ensembles VIDES sont égaux.
    assert "idx_media_status" in apres_1, "le premier passage n'a rien indexé"
    app_db.init_db()
    app_db.init_db()

    assert _index_names(path) == apres_1


def test_init_db_sur_base_legacy_doit_creer_les_index_declares(legacy_db):
    """RÉGRESSION (T1, volet index) : après `init_db()`, tout `Index(...)`
    déclaré existe en base, y compris sur une table préexistante (lot 3.8)."""
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL, rebind_engine=True)

    app_db.init_db()

    manquants = {}
    for name in Base.metadata.tables:
        absents = _declared_index_names(name) - _index_names(path, name)
        if absents:
            manquants[name] = absents
    assert manquants == {}


# ---------------------------------------------------------------------------
# Risque #25 — les tris de la bibliothèque cessent de passer par un TEMP B-TREE
# ---------------------------------------------------------------------------

#: Les requêtes de tri RÉELLEMENT émises par l'application, et l'index qui doit
#: les servir. `USE TEMP B-TREE FOR ORDER BY` dans le plan signifie que SQLite
#: matérialise et trie tout l'ensemble en mémoire à chaque page.
_TRIS_DE_PRODUCTION = [
    # analytics/api.py:817
    (
        "SELECT id FROM media_items ORDER BY posted_at DESC, id DESC",
        "idx_media_posted_at",
    ),
    # viewer_api.py:378 — le tri PAR DÉFAUT de la bibliothèque
    (
        "SELECT id FROM media_items "
        "ORDER BY coalesce(posted_at, discovered_at) DESC, id DESC",
        "idx_media_date_effective",
    ),
    # viewer_api.py — bibliothèque filtrée par statut
    (
        "SELECT id FROM media_items WHERE status = 'uploaded' ORDER BY posted_at DESC",
        "idx_media_status_posted",
    ),
    # web/api.py:544 et :586 — l'historique des jobs
    (
        "SELECT id FROM scrape_jobs ORDER BY created_at DESC LIMIT 20",
        "idx_jobs_created",
    ),
]


@pytest.mark.parametrize(
    "requete, index_attendu",
    _TRIS_DE_PRODUCTION,
    ids=[i for _, i in _TRIS_DE_PRODUCTION],
)
def test_les_tris_de_production_nont_plus_de_tri_temporaire(
    db_engine, requete, index_attendu
):
    """Risque #25 / lot 3.8 : preuve par `EXPLAIN QUERY PLAN`.

    Déclarer un index ne prouve rien — c'est le planificateur qui tranche.
    Mesuré identiquement sur une COPIE de la base de production : les quatre
    plans passent de `SCAN ... USE TEMP B-TREE FOR ORDER BY` à un parcours
    d'index.
    """
    with db_engine.connect() as conn:
        plan = " | ".join(
            str(row[3]) for row in conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {requete}")
        )

    assert "TEMP B-TREE" not in plan, f"tri temporaire sur {requete!r} : {plan}"
    assert index_attendu in plan, f"{index_attendu} inutilisé : {plan}"


# ===========================================================================
# 5. Intégrité référentielle — PRAGMA foreign_keys (risques #11/#12, lot 3.7)
# ===========================================================================


def test_pragma_foreign_keys_est_active(db_engine):
    """RÉGRESSION du risque #11 (lot 3.7) : le listener de `app/db.py` pose
    désormais `foreign_keys=ON` en plus de WAL, `busy_timeout` et
    `synchronous`. Sans lui, tous les `ON DELETE CASCADE` du schéma sont
    décoratifs."""
    with db_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        # Les 3 PRAGMA d'origine ne doivent pas avoir été perdus au passage.
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 15000


def test_une_ligne_orpheline_est_desormais_refusee(db_session):
    """RÉGRESSION (lot 3.7) : insérer un enfant sans parent lève.

    C'est la contrepartie visible du PRAGMA : `media_items.profile_id` est
    enfin contrôlé par le moteur, plus seulement par la bonne volonté du code
    appelant.
    """
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(
            text(
                "INSERT INTO media_items "
                "(profile_id, platform, post_id, media_type, media_url, status, "
                " retry_count, discovered_at) "
                "VALUES (424242, 'instagram', 'orphelin', 'image', "
                "'https://cdn.invalid/x.jpg', 'pending', 0, 0)"
            )
        )
        db_session.commit()
    db_session.rollback()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value)
    reste = db_session.execute(
        text("SELECT COUNT(*) FROM media_items WHERE profile_id = 424242")
    ).scalar()
    assert reste == 0


def test_detecter_les_orphelins_compte_sans_rien_supprimer(legacy_db):
    """Lot 3.7 : le contrôle de boot COMPTE les orphelins, il ne les touche pas.

    Activer `foreign_keys=ON` ne revalide PAS les lignes déjà en base : un
    orphelin hérité survit silencieusement à la migration. `init_db()` le
    signale donc à chaque démarrage — sans jamais décider à la place du
    propriétaire (AUDIT.md §9, question ouverte n°5).
    """
    path = legacy_db(LEGACY_PROFILES_DDL, LEGACY_MEDIA_ITEMS_DDL)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO media_items "
            "(profile_id, platform, post_id, media_type, media_url, status, "
            " retry_count, discovered_at) "
            "VALUES (424242, 'instagram', 'orphelin', 'image', "
            "'https://cdn.invalid/x.jpg', 'pending', 0, 0)"
        )
        conn.commit()
    finally:
        conn.close()

    violations = app_db._detecter_les_orphelins()

    assert violations == {"media_items -> profiles": 1}
    # NON DESTRUCTIF : la ligne est toujours là après le diagnostic.
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 1
    finally:
        conn.close()


def test_detecter_les_orphelins_ne_signale_rien_sur_une_base_saine(db_session, factories):
    """Contre-épreuve du test précédent : sur une base saine, le diagnostic est
    muet. Sans cette garde, un `_detecter_les_orphelins` qui crierait au loup à
    chaque démarrage passerait pour correct."""
    profil = factories.profile()
    factories.media_item(profil)
    db_session.commit()

    assert app_db._detecter_les_orphelins() == {}


def test_la_cascade_orm_supprime_bien_les_enfants(db_session, factories):
    """Ce qui protège RÉELLEMENT les enfants aujourd'hui : `cascade=
    "all, delete-orphan"` côté ORM (db.py:36-38), pas le moteur SQLite."""
    profile = factories.profile()
    media = factories.media_item(profile)
    factories.media_comment(media)
    factories.media_rating(media)
    factories.profile_snapshot(profile)
    factories.scrape_job(profile)
    media_id = media.id
    profile_id = profile.id

    db_session.delete(profile)
    db_session.commit()

    for table, colonne, valeur in (
        ("media_items", "id", media_id),
        ("media_comments", "media_item_id", media_id),
        ("media_ratings", "media_item_id", media_id),
        ("profile_snapshots", "profile_id", profile_id),
        ("scrape_jobs", "profile_id", profile_id),
    ):
        reste = db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {colonne} = :v"), {"v": valeur}
        ).scalar()
        assert reste == 0, f"{table} : orphelin laissé par la cascade ORM"


def _arbre_complet_avec_references_editeur(db_session, factories):
    """Arbre T9 COMPLET : profil -> média -> post programmé + mème sauvegardé.

    `scheduled_posts.source_media_id` (db.py:201) et `saved_memes.
    source_media_id` (db.py:227) référencent `media_items.id` SANS `ondelete`
    et SANS `relationship` ORM : ils échappent donc aux DEUX mécanismes de
    cascade (moteur et ORM). C'est le troisième volet du lot 3.7.
    """
    profile = factories.profile()
    media = factories.media_item(profile)
    post = factories.scheduled_post(source_media_id=media.id)
    meme = factories.saved_meme(source_media_id=media.id)
    identifiants = (profile.id, media.id, post.id, meme.id)

    db_session.delete(profile)
    db_session.commit()
    return identifiants


def test_supprimer_un_profil_conserve_les_lignes_de_lediteur(db_session, factories):
    """RÉGRESSION (lot 3.7) : le post programmé et le mème SURVIVENT.

    La contrepartie du volet « SET NULL » : on annule la référence, on ne
    supprime pas la ligne. Le mème et le post programmé ont leur propre fichier
    (`file_path`, `media_path`) — les effacer avec le média source détruirait un
    travail d'édition que le propriétaire n'a pas demandé de jeter.
    """
    _profile_id, media_id, post_id, meme_id = _arbre_complet_avec_references_editeur(
        db_session, factories
    )

    restants_media = db_session.execute(
        text("SELECT COUNT(*) FROM media_items WHERE id = :v"), {"v": media_id}
    ).scalar()
    assert restants_media == 0, "la cascade ORM doit bien supprimer le média"

    for table, ligne_id in (("scheduled_posts", post_id), ("saved_memes", meme_id)):
        survivante = db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE id = :v"), {"v": ligne_id}
        ).scalar()
        assert survivante == 1, f"{table} : la ligne éditeur a été emportée à tort"


def test_supprimer_un_profil_doit_annuler_les_references_de_lediteur(
    db_session, factories
):
    """RÉGRESSION (lot 3.7) : `source_media_id` vaut NULL après la suppression.

    Deux mécanismes se recouvrent, à dessein : `ondelete="SET NULL"` sur les 2
    clés étrangères (base NEUVE) et la relation ORM `MediaItem.scheduled_posts`
    / `.saved_memes` (base de PRODUCTION, dont le DDL est antérieur et que
    SQLite ne sait pas modifier par ALTER TABLE).
    """
    _profile_id, _media_id, post_id, meme_id = _arbre_complet_avec_references_editeur(
        db_session, factories
    )

    for table, ligne_id in (("scheduled_posts", post_id), ("saved_memes", meme_id)):
        pendant = db_session.execute(
            text(f"SELECT source_media_id FROM {table} WHERE id = :v"), {"v": ligne_id}
        ).scalar()
        assert pendant is None, f"{table} : référence pendante vers un média supprimé"


def test_supprimer_un_profil_doit_supprimer_ses_snapshots_ig(db_session, factories):
    """RÉGRESSION du risque #12 (lot 3.7) : supprimer un profil supprime ses
    snapshots IG, exactement comme ses médias, ses jobs et ses
    `profile_snapshots` — et surtout, ne lève plus.

    `IgInsightSnapshot` utilisait un `backref` SANS `cascade` : l'ORM tentait de
    mettre `profile_id` à NULL au lieu de supprimer la ligne, ce que
    `nullable=False` interdit. Un profil doté d'un snapshot IG était donc
    INDÉLETABLE et l'API renvoyait un 500 générique. Le `backref(...,
    cascade="all, delete-orphan")` clôt le cas.
    """
    profile = factories.profile()
    factories.ig_insight_snapshot(profile)
    profile_id = profile.id

    db_session.delete(profile)
    db_session.commit()

    reste = db_session.execute(
        text("SELECT COUNT(*) FROM ig_insight_snapshots WHERE profile_id = :id"),
        {"id": profile_id},
    ).scalar()
    assert reste == 0


def test_sur_le_ddl_de_production_lorm_annule_seul_les_references(tmp_path, monkeypatch):
    """LE cas que la base de test ne peut PAS reproduire (lot 3.7).

    La base de test est fabriquée par `create_all()` : elle porte donc le DDL
    NEUF, avec `ON DELETE SET NULL`, et c'est le moteur qui annule la
    référence. La base de PRODUCTION, elle, a un DDL antérieur sans clause
    `ON DELETE` — et SQLite ne sait pas la modifier par `ALTER TABLE`.

    Avec `PRAGMA foreign_keys=ON` (lot 3.7), supprimer un média référencé y
    lèverait « FOREIGN KEY constraint failed » : la suppression d'un média
    depuis le viewer, ou d'un profil entier, tomberait en 500. Ce qui l'en
    empêche est la relation ORM `MediaItem.scheduled_posts` / `.saved_memes` :
    l'unité de travail émet un `UPDATE ... SET source_media_id = NULL` AVANT le
    `DELETE`.

    Ce test rejoue exactement cette situation, sur le DDL réel.
    """
    path = _make_legacy_db(
        tmp_path / "ddl-de-production.db",
        ddls=[
            LEGACY_PROFILES_DDL,
            LEGACY_MEDIA_ITEMS_DDL,
            LEGACY_SCHEDULED_POSTS_DDL,
            LEGACY_SAVED_MEMES_DDL,
        ],
    )
    moteur = create_engine(f"sqlite:///{path}")
    event.listen(moteur, "connect", app_db._set_sqlite_pragma)
    monkeypatch.setattr(app_db, "DB_PATH", path)
    monkeypatch.setattr(app_db, "engine", moteur)
    app_db.init_db()  # complète les colonnes et les index, JAMAIS les tables existantes

    # Garde : sans elle, ce test se contenterait de re-tester le DDL neuf.
    conn = sqlite3.connect(str(path))
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'scheduled_posts'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "SET NULL" not in ddl.upper(), "le DDL n'est plus celui de la production"

    Session = sessionmaker(bind=moteur)
    db = Session()
    try:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1

        profil = Profile(
            platform="instagram",
            username="prod",
            profile_url="https://instagram.com/prod",
            is_active=True,
            scrape_mode="daily",
            scrape_interval_minutes=360,
            created_at=0,
            updated_at=0,
        )
        db.add(profil)
        db.flush()
        media = MediaItem(
            profile_id=profil.id,
            platform="instagram",
            post_id="P1",
            media_type="image",
            media_url="https://cdn.invalid/1.jpg",
            status="pending",
            retry_count=0,
            discovered_at=0,
        )
        db.add(media)
        db.flush()
        post = ScheduledPost(
            source_media_id=media.id, status="draft", created_at=0, updated_at=0
        )
        meme = SavedMeme(
            source_media_id=media.id,
            media_type="image",
            file_path="/tmp/meme.png",
            created_at=0,
        )
        db.add_all([post, meme])
        db.commit()
        post_id, meme_id = post.id, meme.id

        db.delete(media)
        db.commit()  # ne doit PAS lever « FOREIGN KEY constraint failed »

        for table, ligne_id in (("scheduled_posts", post_id), ("saved_memes", meme_id)):
            ligne = db.execute(
                text(f"SELECT source_media_id FROM {table} WHERE id = :v"),
                {"v": ligne_id},
            ).fetchone()
            assert ligne is not None, f"{table} : la ligne éditeur a été emportée"
            assert ligne[0] is None, f"{table} : référence pendante"
    finally:
        db.close()
        moteur.dispose()


def test_supprimer_un_profil_en_sql_doit_supprimer_ses_medias_en_cascade(
    db_session, factories
):
    """RÉGRESSION (lot 3.7) : une suppression au niveau SQL — celle que fait un
    script de maintenance, une purge ou un `DELETE` en masse — entraîne la
    suppression des médias liés, comme le déclare `ondelete="CASCADE"`. Avant
    le PRAGMA, la contrainte n'était pas appliquée par le moteur.
    """
    profile = factories.profile()
    factories.media_item(profile)
    factories.media_item(profile)
    profile_id = profile.id

    db_session.execute(
        text("DELETE FROM profiles WHERE id = :id"), {"id": profile_id}
    )
    db_session.commit()

    reste = db_session.execute(
        text("SELECT COUNT(*) FROM media_items WHERE profile_id = :id"),
        {"id": profile_id},
    ).scalar()
    assert reste == 0


# ===========================================================================
# 6. Contraintes UNIQUE déclarées
# ===========================================================================


def test_unicite_platform_username_sur_profiles(db_session, factories):
    """`idx_profiles_platform_username` (db.py:41)."""
    factories.profile(platform="instagram", username="samourais")

    with pytest.raises(IntegrityError):
        factories.profile(platform="instagram", username="samourais")
    db_session.rollback()


def test_meme_username_sur_deux_plateformes_est_autorise(db_session, factories):
    """La contrainte est bien COMPOSITE : le username seul n'est pas unique."""
    a = factories.profile(platform="instagram", username="samourais")
    b = factories.profile(platform="tiktok", username="samourais")
    assert a.id != b.id


def test_unicite_dedup_sur_media_items(db_session, factories):
    """`idx_media_dedup` = (profile_id, post_id, media_url) (db.py:86)."""
    profile = factories.profile()
    factories.media_item(
        profile, post_id="P1", media_url="https://cdn.invalid/a.jpg"
    )

    with pytest.raises(IntegrityError):
        factories.media_item(
            profile, post_id="P1", media_url="https://cdn.invalid/a.jpg"
        )
    db_session.rollback()


def test_deux_medias_du_meme_post_avec_des_url_differentes_sont_acceptes(
    db_session, factories
):
    """Cas du carrousel : même `post_id`, `media_url` distinctes → 2 lignes."""
    profile = factories.profile()
    a = factories.media_item(
        profile, post_id="P1", media_url="https://cdn.invalid/a.jpg"
    )
    b = factories.media_item(
        profile, post_id="P1", media_url="https://cdn.invalid/b.jpg"
    )
    assert a.id != b.id


def test_unicite_rating_par_utilisateur(db_session, factories):
    """`idx_rating_unique` = (media_item_id, user_name) (db.py:120)."""
    media = factories.media_item()
    factories.media_rating(media, user_name="jeremie")

    with pytest.raises(IntegrityError):
        factories.media_rating(media, user_name="jeremie")
    db_session.rollback()


def test_aucune_contrainte_unique_sur_les_commentaires(db_session, factories):
    """CARACTÉRISATION : `media_comments` n'a qu'un index, pas de contrainte —
    deux commentaires identiques du même auteur sont acceptés (db.py:103-105)."""
    media = factories.media_item()
    a = factories.media_comment(media, user_name="jeremie", comment_text="idem")
    b = factories.media_comment(media, user_name="jeremie", comment_text="idem")
    assert a.id != b.id
