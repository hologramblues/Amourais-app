"""
Tests du SOCLE lui-même (vague 0, lot 0.1).

Ces tests ne testent pas l'application : ils testent le filet.
Si l'un d'eux devient rouge, TOUS les autres modules de test deviennent
suspects — c'est le canari de la mine.

Priorité n°1 : `test_db_path_nest_pas_la_base_de_production`.
"""

from __future__ import annotations

import os
import socket
import sqlite3
from pathlib import Path

import pytest

import app.config as app_config
import app.db as app_db
from conftest import (
    FIXED_NOW,
    PRODUCTION_DATA_DIR,
    PRODUCTION_DB_FINGERPRINT,
    PRODUCTION_DB_PATH,
    TEST_APP_PASSWORD,
    TEST_APP_USERNAME,
    TEST_DATA_DIR,
)

pytestmark = pytest.mark.socle


# ===========================================================================
# 1. ISOLATION — le test le plus important de tout le chantier
# ===========================================================================


def test_db_path_nest_pas_la_base_de_production():
    """`app.config.DB_PATH` ne doit JAMAIS être data/samourais.db."""
    db_path = Path(app_config.DB_PATH).resolve()

    assert db_path != PRODUCTION_DB_PATH.resolve()
    assert PRODUCTION_DATA_DIR.resolve() not in db_path.parents
    assert db_path.parent == TEST_DATA_DIR


def test_engine_sqlalchemy_pointe_sur_la_base_ephemere():
    """L'engine module-level de `app.db` (db.py:237) suit bien DATA_DIR."""
    url = str(app_db.engine.url)

    assert str(TEST_DATA_DIR) in url
    assert "data/samourais.db" not in url
    assert str(PRODUCTION_DATA_DIR) not in url


def test_toutes_les_constantes_de_chemin_sont_sous_le_data_dir_de_test():
    """Aucun chemin dérivé de DATA_DIR ne doit s'échapper de la sandbox.

    Verrouille config.py:58-67 : si quelqu'un ajoute demain un chemin construit
    à partir de BASE_DIR au lieu de DATA_DIR, ce test l'attrape.
    """
    derives = {
        "DATA_DIR": app_config.DATA_DIR,
        "DOWNLOAD_DIR": app_config.DOWNLOAD_DIR,
        "DB_PATH": app_config.DB_PATH,
        "SESSIONS_DIR": app_config.SESSIONS_DIR,
        "COOKIES_DIR": app_config.COOKIES_DIR,
        "CALENDAR_DIR": app_config.CALENDAR_DIR,
        "SETTINGS_ENV": app_config.SETTINGS_ENV,
        "EDITOR_UPLOAD_DIR": app_config.EDITOR_UPLOAD_DIR,
        "EDITOR_OUTPUT_DIR": app_config.EDITOR_OUTPUT_DIR,
    }
    fautifs = {
        name: str(path)
        for name, path in derives.items()
        if not str(Path(path).resolve()).startswith(str(TEST_DATA_DIR))
    }
    assert fautifs == {}, f"chemins hors sandbox : {fautifs}"


def test_la_variable_denvironnement_data_dir_est_bien_posee():
    assert os.environ["DATA_DIR"] == str(TEST_DATA_DIR)


def test_la_base_de_production_na_pas_ete_modifiee():
    """Empreinte (taille, mtime) de data/samourais.db inchangée.

    `os.stat` ne lit aucun octet du fichier : la LOI ABSOLUE n°1 (« ne pas
    toucher à data/ ») est respectée par ce test lui-même.
    """
    if PRODUCTION_DB_FINGERPRINT is None:
        pytest.skip("data/samourais.db absent sur cette machine")

    st = os.stat(PRODUCTION_DB_PATH)
    assert (st.st_size, st.st_mtime_ns) == PRODUCTION_DB_FINGERPRINT


# ===========================================================================
# 2. GARDE-FOU RÉSEAU (LOI ABSOLUE n°3)
# ===========================================================================


def test_garde_fou_reseau_socket_connect(guard_errors):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(guard_errors.network):
            sock.connect(("93.184.216.34", 80))
    finally:
        sock.close()


def test_garde_fou_reseau_create_connection(guard_errors):
    with pytest.raises(guard_errors.network):
        socket.create_connection(("example.com", 80), timeout=0.1)


def test_garde_fou_reseau_resolution_dns(guard_errors):
    with pytest.raises(guard_errors.network):
        socket.getaddrinfo("example.com", 80)


def test_garde_fou_reseau_httpx(guard_errors):
    import httpx

    with pytest.raises(guard_errors.network):
        httpx.get("https://example.com", timeout=0.1)


def test_garde_fou_reseau_stealthy_fetcher(guard_errors):
    """Le navigateur furtif utilisé par les 4 extracteurs est neutralisé."""
    from scrapling.fetchers import StealthyFetcher

    with pytest.raises(guard_errors.network):
        StealthyFetcher.fetch("https://www.instagram.com/someone/")


def test_garde_fou_reseau_vu_depuis_un_module_applicatif(guard_errors):
    """Les extracteurs importent StealthyFetcher PAR VALEUR (instagram.py:21).

    Comme tous référencent le même objet-classe, patcher la classe suffit :
    ce test verrouille cette propriété, dont dépend tout le module extracteurs.
    """
    from app.scraper import instagram

    with pytest.raises(guard_errors.network):
        instagram.StealthyFetcher.fetch("https://www.instagram.com/someone/")


def test_garde_fou_reseau_nest_pas_avalable_par_un_except_exception(guard_errors):
    """`SocleGuardError` hérite de BaseException, pas de Exception.

    Le code applicatif est truffé de `except Exception:` (scheduler.py:97,
    pipeline.py, web/api.py...). Un garde-fou avalé serait un garde-fou
    inutile : le test passerait au vert en ayant réellement tapé le réseau.
    """
    assert not issubclass(guard_errors.base, Exception)

    with pytest.raises(guard_errors.network):
        try:
            socket.create_connection(("example.com", 80))
        except Exception:  # noqa: BLE001 - c'est précisément ce qu'on teste
            pytest.fail("le garde-fou réseau a été avalé par `except Exception`")


def test_le_client_flask_de_test_ne_declenche_pas_le_garde_fou(client):
    """Le client de test Flask est en-process : aucun socket, aucun faux positif."""
    assert client.get("/health").status_code == 200


# ===========================================================================
# 3. GARDE-FOU data/ (LOI ABSOLUE n°1)
# ===========================================================================


def test_garde_fou_data_open_en_ecriture(guard_errors):
    with pytest.raises(guard_errors.data):
        open(PRODUCTION_DATA_DIR / "poison.txt", "w")


def test_garde_fou_data_open_en_append(guard_errors):
    with pytest.raises(guard_errors.data):
        open(PRODUCTION_DB_PATH, "ab")


def test_garde_fou_data_pathlib_write_text(guard_errors):
    """`Path.write_text` passe par `io.open` et non `builtins.open` (CPython 3.12)."""
    with pytest.raises(guard_errors.data):
        (PRODUCTION_DATA_DIR / "poison.txt").write_text("nope")


def test_garde_fou_data_pathlib_mkdir(guard_errors):
    with pytest.raises(guard_errors.data):
        (PRODUCTION_DATA_DIR / "poison_dir").mkdir()


def test_garde_fou_data_os_remove(guard_errors):
    with pytest.raises(guard_errors.data):
        os.remove(PRODUCTION_DB_PATH)


def test_garde_fou_data_shutil_rmtree(guard_errors):
    with pytest.raises(guard_errors.data):
        import shutil

        shutil.rmtree(PRODUCTION_DATA_DIR / "downloads")


def test_garde_fou_data_sqlite_connect(guard_errors):
    """`_migrate_add_columns` (db.py:268) ouvre la base via sqlite3.connect."""
    with pytest.raises(guard_errors.data):
        sqlite3.connect(str(PRODUCTION_DB_PATH))


def test_garde_fou_data_chemin_relatif(guard_errors):
    """Un chemin relatif depuis la racine du projet est résolu, donc attrapé."""
    previous = os.getcwd()
    os.chdir(PRODUCTION_DATA_DIR.parent)
    try:
        with pytest.raises(guard_errors.data):
            open("data/poison.txt", "w")
    finally:
        os.chdir(previous)


def test_garde_fou_data_autorise_la_lecture():
    """Seules les ÉCRITURES sont bloquées : la lecture reste possible."""
    assert PRODUCTION_DB_PATH.exists()
    with open(PRODUCTION_DB_PATH, "rb") as handle:
        assert handle.read(16).startswith(b"SQLite format 3")


def test_garde_fou_data_nempeche_pas_decrire_dans_la_sandbox(tmp_path, test_data_dir):
    (tmp_path / "ok.txt").write_text("ok")
    assert (tmp_path / "ok.txt").read_text() == "ok"

    cible = test_data_dir / "downloads" / "ok.bin"
    cible.write_bytes(b"ok")
    assert cible.read_bytes() == b"ok"
    cible.unlink()


def test_garde_fou_data_nest_pas_avalable_par_un_except_exception(guard_errors):
    assert not issubclass(guard_errors.data, Exception)
    with pytest.raises(guard_errors.data):
        try:
            open(PRODUCTION_DATA_DIR / "poison.txt", "w")
        except Exception:  # noqa: BLE001
            pytest.fail("le garde-fou data/ a été avalé par `except Exception`")


# ===========================================================================
# 4. BASE DE DONNÉES ÉPHÉMÈRE ET ISOLATION ENTRE TESTS
# ===========================================================================


def test_le_schema_complet_existe(db_session):
    from sqlalchemy import inspect

    from app.db import Base

    tables_reelles = set(inspect(app_db.engine).get_table_names())
    tables_declarees = set(Base.metadata.tables)
    assert tables_declarees <= tables_reelles


# Les deux tests suivants sont volontairement identiques : si l'isolation
# fuyait, le second verrait 2 lignes (ou violerait idx_profiles_platform_username).
# Aucun des deux ne dépend de l'ordre d'exécution — LOI ABSOLUE n°5.
def test_isolation_entre_tests_a(db_session, make_profile):
    make_profile(platform="instagram", username="temoin_isolation")
    assert db_session.query(app_db.Profile).count() == 1


def test_isolation_entre_tests_b(db_session, make_profile):
    make_profile(platform="instagram", username="temoin_isolation")
    assert db_session.query(app_db.Profile).count() == 1


def test_isolation_couvre_aussi_les_sessions_ouvertes_par_le_code_applicatif(
    make_profile,
):
    """`SessionLocal` (importé PAR VALEUR par web/api.py:21) voit la même base."""
    from app.db import SessionLocal

    make_profile(username="vue_par_lapplication")
    session = SessionLocal()
    try:
        assert session.query(app_db.Profile).count() == 1
    finally:
        session.close()


# ===========================================================================
# 5. FABRIQUES
# ===========================================================================


def test_fabrique_profile(db_session, make_profile):
    profile = make_profile()
    assert profile.id is not None
    assert profile.platform == "instagram"
    assert profile.is_active is True
    assert profile.created_at == FIXED_NOW
    assert db_session.query(app_db.Profile).count() == 1


def test_fabrique_profile_surchargeable(make_profile):
    profile = make_profile(platform="tiktok", username="samourais", is_active=False)
    assert (profile.platform, profile.username) == ("tiktok", "samourais")
    assert profile.is_active is False
    assert profile.profile_url == "https://tiktok.invalid/samourais/"


def test_fabriques_sans_argument_ne_se_collisionnent_pas(make_profile, make_media_item):
    """Deux appels nus doivent respecter idx_profiles_platform_username / idx_media_dedup."""
    p1, p2 = make_profile(), make_profile()
    assert p1.username != p2.username

    m1, m2 = make_media_item(p1), make_media_item(p1)
    assert m1.post_id != m2.post_id


def test_fabrique_media_item_cree_son_profil_si_absent(db_session, make_media_item):
    media = make_media_item()
    assert media.profile_id is not None
    assert media.status == "pending"
    assert db_session.query(app_db.Profile).count() == 1


def test_fabrique_scrape_job(make_scrape_job, make_profile):
    profile = make_profile()
    job = make_scrape_job(profile, status="running", triggered_by="scheduler")
    assert job.profile_id == profile.id
    assert (job.status, job.triggered_by) == ("running", "scheduler")


def test_fabrique_scheduled_post(db_session, make_scheduled_post):
    post = make_scheduled_post(status="scheduled")
    assert post.id is not None
    assert post.status == "scheduled"
    assert post.scheduled_at == FIXED_NOW + 3600
    assert str(TEST_DATA_DIR) in post.media_path


def test_fabriques_secondaires(factories):
    media = factories.media_item()
    assert factories.media_comment(media).media_item_id == media.id
    assert factories.media_rating(media).media_item_id == media.id
    assert factories.profile_snapshot().id is not None
    assert factories.ig_insight_snapshot().id is not None
    assert factories.saved_meme().id is not None


# ===========================================================================
# 6. CLIENT FLASK — sans et avec authentification
# ===========================================================================


def test_health_repond_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_sans_mot_de_passe_lauthentification_est_desactivee(client):
    """APP_PASSWORD vide → `_require_auth` laisse tout passer (app.py:79-80)."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 200


def test_avec_mot_de_passe_une_route_protegee_repond_401(auth_client):
    response = auth_client.get("/api/status")
    assert response.status_code == 401
    assert "Basic" in response.headers.get("WWW-Authenticate", "")


def test_avec_mot_de_passe_et_en_tete_valide_la_route_repond(auth_client, auth_header):
    assert auth_client.get("/api/status", headers=auth_header).status_code == 200


def test_health_reste_public_meme_avec_authentification(auth_client):
    """Exemption app.py:84-85 — la sonde Railway ne doit jamais tomber."""
    assert auth_client.get("/health").status_code == 200


def test_make_flask_app_refuse_une_cle_de_config_inexistante(make_flask_app):
    with pytest.raises(AttributeError):
        make_flask_app(APP_PASWORD="typo")


def test_identifiants_de_test_non_vides():
    assert TEST_APP_USERNAME and TEST_APP_PASSWORD


# ===========================================================================
# 7. CONFIGURATION PYTEST
# ===========================================================================


def test_xfail_strict_est_actif(request):
    """Mécanisme central du chantier : un XPASS doit faire échouer la suite."""
    assert request.config.getini("xfail_strict") is True


def test_le_repertoire_de_tests_est_bien_le_testpath(request):
    assert request.config.getini("testpaths") == ["tests"]


def test_les_marqueurs_du_chantier_sont_declares(request):
    declares = {ligne.split(":", 1)[0] for ligne in request.config.getini("markers")}
    attendus = {
        "socle",
        "db",
        "web",
        "scheduler",
        "pipeline",
        "extractor",
        "security",
        "calendar",
        "analytics",
        "editor",
    }
    assert attendus <= declares


def test_le_fuseau_de_la_suite_est_fige():
    """Un décalage de fuseau (risque #46) doit rester visible : TZ != UTC."""
    assert os.environ["TZ"] == "Europe/Paris"


def test_la_fixture_timezone_restaure_le_fuseau(timezone):
    import time as time_module

    timezone("UTC")
    assert time_module.strftime("%Z", time_module.localtime(FIXED_NOW)) in {
        "UTC",
        "GMT",
    }
