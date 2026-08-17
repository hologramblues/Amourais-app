"""
MODULE STOCKAGE — vague 0 (filet de sécurité).

Périmètre : `app/storage.py` (Google Drive), `app/scraper/downloaders.py`
(téléchargements HTTP / HLS) et `app.scheduler.cleanup_temp_files` (ménage).

────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE FAIT ET NE FAIT PAS
────────────────────────────────────────────────────────────────────────────
Ces tests sont de la CARACTÉRISATION : ils décrivent le comportement RÉEL
d'aujourd'hui, bugs compris. Chaque bug documenté dans AUDIT.md fait l'objet
d'un test qui exprime le comportement CORRECT, marqué
`@pytest.mark.xfail(strict=True)` avec le numéro de risque et le lot de
correction prévu. Le jour où le lot est livré, le test passe XPASS et fait
échouer la suite en criant « retire ce xfail ».

Aucun réseau (httpx.MockTransport), aucun ffmpeg (faux `subprocess.run`),
aucun Google Drive (faux service), aucun `time.sleep` réel (backoff neutralisé
ET vérifié), aucune écriture hors `tmp_path` / `DATA_DIR` de test.

────────────────────────────────────────────────────────────────────────────
POURQUOI CERTAINS TESTS ÉCRIVENT DANS `test_data_dir` ET NON `tmp_path`
────────────────────────────────────────────────────────────────────────────
Trois xfail portent sur des répertoires que `cleanup_temp_files` ne balaie
PAS aujourd'hui (`EDITOR_UPLOAD_DIR`, `EDITOR_OUTPUT_DIR`, `CALENDAR_DIR`).
Une implémentation corrigée les résoudra depuis `app.config`, c'est-à-dire
sous `DATA_DIR` — qui est ici le répertoire ÉPHÉMÈRE du socle, jamais
`<projet>/data`. Utiliser `tmp_path` pour ceux-là rendrait le xfail incapable
de passer XPASS après correction, donc inutile. Les fichiers créés sont
supprimés par la fixture `orphelin_dans_data_dir`.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.config as app_config
import app.scheduler as scheduler
import app.scraper.downloaders as downloaders
import app.scraper.pipeline as pipeline
import app.storage as storage
from app.db import MediaItem
from app.scraper.base import ExtractorResult, ProfileInfo
from app.scraper.downloaders import DownloadResult
from conftest import FIXED_NOW

pytestmark = pytest.mark.storage


# ===========================================================================
# Outillage commun
# ===========================================================================


@pytest.fixture
def download_dir(tmp_path, monkeypatch) -> Path:
    """`DOWNLOAD_DIR` de `downloaders` redirigé vers tmp_path.

    `downloaders.py:23` importe la constante PAR VALEUR (§7.1 AUDIT.md) :
    patcher `app.config.DOWNLOAD_DIR` n'aurait aucun effet, il faut patcher
    le nom dans le module consommateur.
    """
    d = tmp_path / "downloads"
    d.mkdir()
    monkeypatch.setattr(downloaders, "DOWNLOAD_DIR", d)
    return d


@pytest.fixture
def sleeps(monkeypatch) -> list:
    """Neutralise `time.sleep` ET enregistre le backoff demandé.

    `_download_direct` fait `import time` DANS son `except` (downloaders.py:169)
    : c'est bien le module global `time` qui est utilisé, donc patché ici.
    LOI ABSOLUE n°5 : sans cela, 3 tentatives coûtent 6 secondes réelles.
    """
    recorded: list = []
    monkeypatch.setattr(time, "sleep", lambda seconds: recorded.append(seconds))
    return recorded


@pytest.fixture
def http_mock(monkeypatch):
    """Installe un `httpx.MockTransport` dans `httpx.Client`.

    `_download_direct` instancie `httpx.Client(...)` en dur (downloaders.py:122)
    : on remplace la classe elle-même par une fabrique qui injecte le transport
    simulé. Le garde-fou réseau du socle bloque `HTTPTransport.handle_request`,
    jamais `MockTransport` — c'est la stratégie recommandée par §7.4 (T11).
    """
    real_client = httpx.Client

    def install(handler):
        state = SimpleNamespace(requests=[])

        def recording_handler(request):
            state.requests.append(request)
            return handler(request)

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording_handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)
        return state

    return install


def _files_in(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file())


# ===========================================================================
# 1. app/storage.py — garde-fous de configuration
# ===========================================================================


@pytest.mark.storage
def test_import_de_storage_ne_declenche_aucun_import_google():
    """Les imports Google sont paresseux (§7.2) : importer le module est inerte.

    C'est ce qui rend `app.storage` testable sans réseau ni credentials.
    """
    assert not hasattr(storage, "Flow")
    assert not hasattr(storage, "MediaFileUpload")
    assert storage._SCOPES == ["https://www.googleapis.com/auth/drive.file"]


@pytest.mark.storage
def test_sans_client_id_toutes_les_entrees_gdrive_levent_runtimeerror():
    """Le socle force GOOGLE_CLIENT_ID/SECRET à vide : aucun appel ne part."""
    for fonction in (
        storage.get_gdrive_auth_url,
        storage.get_gdrive_service,
    ):
        with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
            fonction()

    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        storage.exchange_code("un-code")


@pytest.mark.storage
def test_credentials_presentes_mais_refresh_token_absent(monkeypatch):
    """Message d'erreur explicite avant toute tentative réseau (storage.py:125)."""
    monkeypatch.setattr(storage, "GOOGLE_CLIENT_ID", "id-de-test")
    monkeypatch.setattr(storage, "GOOGLE_CLIENT_SECRET", "secret-de-test")
    monkeypatch.setattr(storage, "GOOGLE_REFRESH_TOKEN", "")

    with pytest.raises(RuntimeError, match="GOOGLE_REFRESH_TOKEN"):
        storage.get_gdrive_service()


@pytest.mark.storage
def test_upload_fichier_absent_echoue_avant_tout_appel_reseau(tmp_path, monkeypatch):
    """`upload_to_gdrive` vérifie l'existence du fichier AVANT de bâtir le service."""
    appels = []
    monkeypatch.setattr(
        storage, "get_gdrive_service", lambda: appels.append("service")
    )

    with pytest.raises(FileNotFoundError):
        storage.upload_to_gdrive(
            local_path=str(tmp_path / "inexistant.jpg"),
            platform="instagram",
            username="samourais",
            post_id="p1",
            mime_type="image/jpeg",
        )

    assert appels == []


# ===========================================================================
# 2. app/storage.py — hiérarchie de dossiers et cache (service simulé)
# ===========================================================================


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeFiles:
    def __init__(self, drive):
        self._drive = drive

    def list(self, **kwargs):
        self._drive.list_queries.append(kwargs["q"])
        for name, folder_id in self._drive.existing.items():
            if f"name = '{name}'" in kwargs["q"]:
                return _FakeExec({"files": [{"id": folder_id, "name": name}]})
        return _FakeExec({"files": []})

    def create(self, **kwargs):
        body = kwargs["body"]
        self._drive.created.append(body)
        if body.get("mimeType") == "application/vnd.google-apps.folder":
            folder_id = f"folder-{len(self._drive.created)}"
            self._drive.existing[body["name"]] = folder_id
            return _FakeExec({"id": folder_id})
        self._drive.uploaded_bodies.append(body)
        return _FakeExec(
            {"id": "file-42", "webViewLink": "https://drive.invalid/file-42"}
        )


class _FakeDrive:
    """Service Drive simulé : aucune requête ne quitte le processus."""

    def __init__(self, existing: dict | None = None):
        self.existing: dict[str, str] = dict(existing or {})
        self.list_queries: list[str] = []
        self.created: list[dict] = []
        self.uploaded_bodies: list[dict] = []

    def files(self):
        return _FakeFiles(self)


@pytest.mark.storage
def test_hierarchie_racine_plateforme_capitalisee_arobase_utilisateur():
    """`GDRIVE_ROOT / {Platform} / @{username}` (storage.py:210-220)."""
    drive = _FakeDrive()

    leaf = storage._get_upload_folder(drive, "instagram", "samourais")

    noms = [body["name"] for body in drive.created]
    assert noms == ["SAMOURAIS SCRAPPER", "Instagram", "@samourais"]
    # Chaque dossier est bien créé DANS le précédent.
    assert "parents" not in drive.created[0]
    assert drive.created[1]["parents"] == ["folder-1"]
    assert drive.created[2]["parents"] == ["folder-2"]
    assert leaf == "folder-3"


@pytest.mark.storage
def test_un_dossier_existant_nest_pas_recree():
    drive = _FakeDrive(existing={"SAMOURAIS SCRAPPER": "racine-existante"})

    storage._get_upload_folder(drive, "tiktok", "samourais")

    noms = [body["name"] for body in drive.created]
    assert noms == ["Tiktok", "@samourais"]
    assert drive.created[0]["parents"] == ["racine-existante"]


@pytest.mark.storage
def test_le_cache_de_dossiers_evite_une_seconde_serie_de_requetes():
    """`_folder_cache` (storage.py:30) : 3 requêtes au 1er appel, 0 au second."""
    drive = _FakeDrive()

    premier = storage._get_upload_folder(drive, "instagram", "samourais")
    requetes_apres_premier = len(drive.list_queries)
    second = storage._get_upload_folder(drive, "instagram", "samourais")

    assert premier == second
    assert requetes_apres_premier == 3
    assert len(drive.list_queries) == 3  # aucune requête supplémentaire


@pytest.mark.storage
def test_clear_folder_cache_reinitialise_bien_le_cache():
    """Le socle appelle `clear_folder_cache()` entre chaque test (conftest:460)."""
    drive = _FakeDrive()
    storage._get_upload_folder(drive, "instagram", "samourais")
    assert storage._folder_cache

    storage.clear_folder_cache()

    assert storage._folder_cache == {}


@pytest.mark.storage
@pytest.mark.security
def test_les_apostrophes_sont_echappees_dans_la_requete_drive():
    """Anti-injection de requête Drive (storage.py:157)."""
    drive = _FakeDrive()

    storage._find_folder(drive, "O'Brien\\x", parent_id="parent-1")

    q = drive.list_queries[0]
    assert "name = 'O\\'Brien\\\\x'" in q
    assert "'parent-1' in parents" in q
    assert "trashed = false" in q


@pytest.mark.storage
def test_upload_prefixe_le_nom_du_fichier_par_le_post_id(tmp_path, monkeypatch):
    """`{post_id}_{nom original}` dans le dossier feuille (storage.py:250-256)."""
    fichier = tmp_path / "media.mp4"
    fichier.write_bytes(b"\x00\x01\x02")

    drive = _FakeDrive()
    monkeypatch.setattr(storage, "get_gdrive_service", lambda: drive)

    medias_construits = []

    class _FakeMediaFileUpload:
        def __init__(self, path, mimetype=None, resumable=None, chunksize=None):
            medias_construits.append(
                {"path": path, "mimetype": mimetype, "resumable": resumable,
                 "chunksize": chunksize}
            )

    import googleapiclient.http as gac_http

    monkeypatch.setattr(gac_http, "MediaFileUpload", _FakeMediaFileUpload)

    resultat = storage.upload_to_gdrive(
        local_path=str(fichier),
        platform="instagram",
        username="samourais",
        post_id="POST123",
        mime_type="video/mp4",
    )

    assert resultat == {
        "file_id": "file-42",
        "web_view_link": "https://drive.invalid/file-42",
    }
    assert drive.uploaded_bodies[0]["name"] == "POST123_media.mp4"
    assert drive.uploaded_bodies[0]["parents"] == ["folder-3"]
    assert medias_construits[0]["resumable"] is True
    assert medias_construits[0]["chunksize"] == 5 * 1024 * 1024
    # Le fichier local n'est PAS supprimé par storage.py — c'est le pipeline
    # qui le fait (pipeline.py:399-407), cf. section 3.
    assert fichier.exists()


@pytest.fixture
def upload_gdrive_simule(tmp_path, monkeypatch):
    """Outillage minimal pour appeler `upload_to_gdrive` N fois sans réseau.

    Renvoie un objet exposant `services` (un enregistrement par appel à
    `get_gdrive_service`) et `televerser()`.
    """
    fichier = tmp_path / "media.jpg"
    fichier.write_bytes(b"\xff\xd8\xff")

    etat = SimpleNamespace(services=[], fichier=fichier)

    def _service():
        drive = _FakeDrive()
        etat.services.append(drive)
        return drive

    monkeypatch.setattr(storage, "get_gdrive_service", _service)

    class _FakeMediaFileUpload:
        def __init__(self, path, mimetype=None, resumable=None, chunksize=None):
            pass

    import googleapiclient.http as gac_http

    monkeypatch.setattr(gac_http, "MediaFileUpload", _FakeMediaFileUpload)

    def _televerser(post_id: str):
        return storage.upload_to_gdrive(
            local_path=str(fichier),
            platform="instagram",
            username="samourais",
            post_id=post_id,
            mime_type="image/jpeg",
        )

    etat.televerser = _televerser
    return etat


@pytest.mark.storage
def test_chaque_upload_reconstruit_un_service_drive(upload_gdrive_simule):
    """CARACTÉRISATION du risque #18 (AUDIT.md §3 l.414) : `upload_to_gdrive`
    appelle `get_gdrive_service()` (storage.py:247) à CHAQUE fichier.

    Chaque appel refait un aller-retour OAuth de rafraîchissement : sur un job
    de 200 médias, ce sont 200 refresh au lieu d'un — latence, quota, et un
    point de panne réseau par fichier.
    """
    for i in range(3):
        upload_gdrive_simule.televerser(f"POST{i}")

    assert len(upload_gdrive_simule.services) == 3


@pytest.mark.storage
@pytest.mark.xfail(
    strict=True,
    reason=(
        "risque #18 AUDIT.md (§3 l.414, 🟠 Haut) — `upload_to_gdrive` "
        "reconstruit un service Drive (donc un refresh OAuth) PAR FICHIER "
        "(storage.py:247) ; le service devrait être bâti une fois par job et "
        "réutilisé ; aucun lot dédié à ce jour — à créer"
    ),
)
def test_un_job_ne_devrait_rafraichir_le_jeton_oauth_quune_fois(upload_gdrive_simule):
    """COMPORTEMENT ATTENDU : un service Drive mis en cache pour la session."""
    for i in range(3):
        upload_gdrive_simule.televerser(f"POST{i}")

    assert len(upload_gdrive_simule.services) == 1


# ===========================================================================
# 3. Aiguillage STORAGE_MODE — §4.8 « la bibliothèque devient grise »
# ===========================================================================


class _StubExtractor:
    """Extracteur inerte : aucun navigateur, aucun réseau.

    Le profil est RENSEIGNÉ à dessein (lot 1.4b, risque #53) : depuis que le
    pipeline distingue « rien de neuf » d'« échec total de fetch », un résultat
    TOTALEMENT vide (ni média, ni post vu, ni la moindre bribe de profil) est
    traité comme une plateforme non atteinte → job `failed`, arrêt avant les
    phases download/upload. Ces trois tests portent sur l'aiguillage
    `STORAGE_MODE`, pas sur la détection d'échec : le stub doit donc rendre ce
    que rend un extracteur qui a RÉELLEMENT atteint la page — ici un display_name,
    et toujours zéro nouveau média. Aucune assertion n'est affaiblie.
    """

    platform = "instagram"

    def extract(self, profile_url, known_post_ids, options=None):
        return ExtractorResult(
            profile_info=ProfileInfo(display_name="Compte de test"),
            media=[],
            total_seen=0,
        )


@pytest.fixture
def pipeline_sans_reseau(monkeypatch, tmp_path):
    """Pipeline exécutable en local : extracteur bouchonné, proxy neutralisé.

    `_EXTRACTORS` est un dict de module peuplé paresseusement (pipeline.py:31) :
    le pré-remplir court-circuite entièrement Scrapling/patchright (§7.2).
    Le socle le purge après chaque test (conftest:462-464).
    """
    pipeline._EXTRACTORS["instagram"] = _StubExtractor
    monkeypatch.setattr(pipeline, "get_proxy_for_platform", lambda platform: "")

    fichiers = tmp_path / "medias"
    fichiers.mkdir()
    compteur = {"n": 0}

    def faux_download(url: str) -> DownloadResult:
        compteur["n"] += 1
        chemin = fichiers / f"media{compteur['n']}.jpg"
        contenu = b"JPEG-DE-TEST" * 8
        chemin.write_bytes(contenu)
        return DownloadResult(
            local_path=str(chemin),
            file_size=len(contenu),
            mime_type="image/jpeg",
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )

    monkeypatch.setattr(pipeline, "download_media", faux_download)
    return SimpleNamespace(dossier=fichiers)


@pytest.mark.storage
@pytest.mark.pipeline
def test_mode_local_le_fichier_reste_sur_disque(
    db_session, make_profile, make_media_item, make_scrape_job,
    pipeline_sans_reseau, monkeypatch,
):
    """Mode `local` (défaut) : l'item est `uploaded` et garde son `local_path`."""
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "local")
    profil = make_profile(platform="instagram", scrape_mode="daily")
    media = make_media_item(profil, status="pending")
    job = make_scrape_job(profil, status="queued")

    pipeline._run_scrape_job_inner(db_session, job.id)
    db_session.refresh(media)

    assert media.status == "uploaded"
    assert media.local_path is not None
    assert Path(media.local_path).is_file()


@pytest.mark.storage
@pytest.mark.pipeline
def test_mode_gdrive_le_fichier_local_est_supprime_et_local_path_efface(
    db_session, make_profile, make_media_item, make_scrape_job,
    pipeline_sans_reseau, monkeypatch,
):
    """CARACTÉRISATION §4.8 : après upload, pipeline.py:399-407 détruit la copie locale.

    Le média n'existe plus que sur Drive, et `gdrive_url` est la SEULE façon
    de le retrouver. Le test suivant montre que le viewer ne s'en sert jamais.
    """
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "gdrive")
    monkeypatch.setattr(
        storage,
        "upload_to_gdrive",
        lambda **kw: {
            "file_id": "drive-1",
            "web_view_link": "https://drive.invalid/drive-1",
        },
    )
    profil = make_profile(platform="instagram", scrape_mode="daily")
    media = make_media_item(profil, status="pending")
    job = make_scrape_job(profil, status="queued")

    pipeline._run_scrape_job_inner(db_session, job.id)
    db_session.refresh(media)

    assert media.status == "uploaded"
    assert media.gdrive_file_id == "drive-1"
    assert media.gdrive_url == "https://drive.invalid/drive-1"
    # Le comportement fautif, écrit noir sur blanc :
    assert media.local_path is None
    assert _files_in(pipeline_sans_reseau.dossier) == []


@pytest.mark.storage
@pytest.mark.pipeline
def test_mode_gdrive_un_upload_qui_echoue_conserve_le_fichier_local(
    db_session, make_profile, make_media_item, make_scrape_job,
    pipeline_sans_reseau, monkeypatch,
):
    """En cas d'échec d'upload : statut `upload_failed`, fichier gardé (pipeline.py:355-367)."""
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "gdrive")

    def upload_qui_echoue(**kwargs):
        raise RuntimeError("quota Drive dépassé")

    monkeypatch.setattr(storage, "upload_to_gdrive", upload_qui_echoue)
    profil = make_profile(platform="instagram", scrape_mode="daily")
    media = make_media_item(profil, status="pending")
    job = make_scrape_job(profil, status="queued")

    pipeline._run_scrape_job_inner(db_session, job.id)
    db_session.refresh(media)

    assert media.status == "upload_failed"
    assert "quota Drive" in media.error_message
    assert media.retry_count == 1
    assert Path(media.local_path).is_file()


@pytest.mark.storage
@pytest.mark.viewer
def test_le_viewer_ne_sait_pas_afficher_un_media_stocke_sur_drive(
    client, make_media_item
):
    """CARACTÉRISATION §4.8 : `file_url` est nul et `gdrive_url` n'est PAS sérialisé.

    Le front retombe alors sur `media_url` — l'URL CDN d'origine, signée et
    expirée : toute la bibliothèque devient grise, sans une seule exception
    côté serveur.
    """
    # `media_url` est passée EXPLICITEMENT : l'assertion finale doit porter sur
    # une valeur choisie par le test, pas sur le défaut de la fabrique du socle
    # (sinon elle ne teste que conftest.py, jamais viewer_api.py).
    url_cdn_expiree = "https://cdn.invalid/expire.jpg?token=perime"
    make_media_item(
        status="uploaded",
        local_path=None,
        media_url=url_cdn_expiree,
        gdrive_file_id="drive-1",
        gdrive_url="https://drive.invalid/drive-1",
    )

    payload = client.get("/api/viewer/media").get_json()

    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["file_url"] is None
    assert item["thumb_url"] is None
    assert "gdrive_url" not in item
    # Seul repli laissé au front : l'URL CDN d'origine, signée et expirée.
    assert item["media_url"] == url_cdn_expiree


@pytest.mark.storage
@pytest.mark.viewer
@pytest.mark.xfail(
    strict=True,
    reason="§4.8 AUDIT.md — viewer_api.py ne sérialise jamais gdrive_url "
    "(0 occurrence) ; lot 5.4",
)
def test_le_viewer_doit_exposer_gdrive_url_pour_les_medias_sur_drive(
    client, make_media_item
):
    """COMPORTEMENT ATTENDU : un média sur Drive doit rester atteignable."""
    make_media_item(
        status="uploaded",
        local_path=None,
        gdrive_file_id="drive-1",
        gdrive_url="https://drive.invalid/drive-1",
    )

    item = client.get("/api/viewer/media").get_json()["items"][0]

    assert item.get("gdrive_url") == "https://drive.invalid/drive-1"


# ===========================================================================
# 4. downloaders — succès, routage, boucle de retry et backoff
# ===========================================================================


@pytest.mark.downloader
def test_download_media_refuse_une_url_vide():
    with pytest.raises(ValueError):
        downloaders.download_media("")


@pytest.mark.downloader
def test_telechargement_direct_nominal(download_dir, http_mock, sleeps):
    charge = b"\xff\xd8\xff" + b"pixels" * 100

    http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=charge
        )
    )

    resultat = downloaders.download_media("https://cdn.invalid/photo")

    assert resultat.file_size == len(charge)
    assert resultat.mime_type == "image/jpeg"
    assert resultat.content_hash == hashlib.sha256(charge).hexdigest()
    chemin = Path(resultat.local_path)
    assert chemin.parent == download_dir
    assert chemin.suffix == ".jpg"
    assert len(chemin.stem) == 21  # nanoid (downloaders.py:62)
    assert sleeps == []


@pytest.mark.downloader
def test_trois_tentatives_avec_backoff_exponentiel_puis_runtimeerror(
    download_dir, http_mock, sleeps
):
    """`_MAX_RETRIES = 3`, backoff `2**attempt` → 2 s puis 4 s (downloaders.py:168-171)."""
    etat = http_mock(lambda request: httpx.Response(500, content=b"boom"))

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert len(etat.requests) == 3
    assert sleeps == [2, 4]


@pytest.mark.downloader
def test_le_succes_a_la_seconde_tentative_ne_dort_quune_fois(
    download_dir, http_mock, sleeps
):
    reponses = [
        httpx.Response(503, content=b"indisponible"),
        httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"ok" * 50),
    ]
    http_mock(lambda request: reponses.pop(0))

    resultat = downloaders.download_media("https://cdn.invalid/photo")

    assert resultat.file_size == 100
    assert sleeps == [2]


@pytest.mark.downloader
@pytest.mark.parametrize("code", [403, 404, 410])
def test_une_url_cdn_expiree_nest_plus_retentee(code, download_dir, http_mock, sleeps):
    """NON-RÉGRESSION lot 3.4 (ancienne caractérisation §3 ligne 158).

    Avant le lot 3.4, 403/404/410 passaient 3 fois dans la boucle : 2 tentatives
    et 6 secondes de backoff perdues, sémaphore de scrape tenu pendant ce temps
    (risque #60). Une URL CDN signée expirée ne redevenant jamais valide, ces
    codes sont désormais définitifs.
    """
    etat = http_mock(lambda request: httpx.Response(code, content=b"expire"))

    with pytest.raises(RuntimeError):
        downloaders.download_media("https://cdn.invalid/expire.jpg")

    assert len(etat.requests) == 1
    assert sleeps == []


@pytest.mark.downloader
@pytest.mark.parametrize("code", [429, 500, 503])
def test_une_panne_reellement_transitoire_reste_retentee(
    code, download_dir, http_mock, sleeps
):
    """Contrôle négatif du test précédent : 429/5xx gardent bien leurs 3 essais.

    Sans lui, « ne plus jamais retenter » ferait passer le test ci-dessus.
    """
    etat = http_mock(lambda request: httpx.Response(code, content=b"reessayez"))

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/transitoire.jpg")

    assert len(etat.requests) == 3
    assert sleeps == [2, 4]


@pytest.mark.downloader
def test_une_erreur_definitive_ne_devrait_pas_etre_retentee(
    download_dir, http_mock, sleeps
):
    """NON-RÉGRESSION lot 3.4 : un 403 est définitif, une seule tentative suffit."""
    etat = http_mock(lambda request: httpx.Response(403, content=b"expire"))

    with pytest.raises(RuntimeError):
        downloaders.download_media("https://cdn.invalid/expire.jpg")

    assert len(etat.requests) == 1
    assert sleeps == []


@pytest.mark.downloader
def test_un_corps_vide_est_rejete_et_ne_laisse_aucun_fichier(
    download_dir, http_mock, sleeps
):
    """Seul cas d'`unlink` du code actuel (downloaders.py:138-141)."""
    etat = http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b""
        )
    )

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/vide.mp4")

    assert len(etat.requests) == 3
    assert _files_in(download_dir) == []


# ===========================================================================
# 5. downloaders — aucune validation d'intégrité (§6.6, T10)
# ===========================================================================


@pytest.mark.downloader
def test_une_page_derreur_html_doit_etre_rejetee(download_dir, http_mock, sleeps):
    """NON-RÉGRESSION lot 2.5 : une page HTML n'est pas un média, on refuse.

    Avant le lot 2.5, ces 2 Ko de HTML devenaient un « média » de la
    bibliothèque : écrit en `.html` (`mimetypes.guess_extension('text/html')`)
    puis servi INLINE par `/media/file/<nom>.html` sur l'origine de
    l'application — XSS stockée (§6.6 / T10).
    """
    page = ("<html><body>" + "Access denied. " * 130 + "</body></html>").encode()
    assert 1500 < len(page) < 3000  # ~2 Ko, comme une vraie page d'erreur

    http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=page
        )
    )

    with pytest.raises((RuntimeError, ValueError)):
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_un_refus_json_est_rejete_sur_son_content_type(
    download_dir, http_mock, sleeps
):
    """Le volet « Content-Type » du lot 2.5, isolé de celui des magic bytes.

    Un JSON de refus (`{"error": …}`) ne commence par aucune signature HTML :
    seul le contrôle du type de contenu peut l'arrêter. Sans ce test, retirer
    ce contrôle laisserait la suite verte — la page HTML du test voisin étant
    déjà rattrapée par ses premiers octets.
    """
    http_mock(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"error": "rate limited", "retry_after": 900}',
        )
    )

    with pytest.raises((RuntimeError, ValueError)):
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_une_page_html_deguisee_en_image_est_rejetee_sur_ses_magic_bytes(
    download_dir, http_mock, sleeps
):
    """Second volet du lot 2.5 : le CDN peut mentir sur son `Content-Type`.

    Sans le contrôle des premiers octets, il suffirait d'annoncer `image/jpeg`
    pour faire écrire la page de refus en `.jpg`.
    """
    page = b"\n  <!DOCTYPE html><html><body>Access denied</body></html>"
    http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=page
        )
    )

    with pytest.raises((RuntimeError, ValueError)):
        downloaders.download_media("https://cdn.invalid/photo.jpg")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_une_reponse_tronquee_est_rejetee_et_retentee(
    download_dir, http_mock, sleeps
):
    """Troisième volet du lot 2.5 : `Content-Length` non tenu = média incomplet.

    Un JPEG amputé s'affiche à moitié dans la bibliothèque et n'est jamais
    retéléchargé, faute d'être détecté.
    """

    def _tronque(request):
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg", "content-length": "5000"},
            content=b"\xff\xd8\xff" + b"pixels" * 100,  # 603 octets seulement
        )

    etat = http_mock(_tronque)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/photo.jpg")

    assert len(etat.requests) == 3  # une troncature est transitoire : on retente
    assert _files_in(download_dir) == []


@pytest.mark.downloader
@pytest.mark.security
def test_guess_extension_devrait_appliquer_une_liste_blanche():
    """NON-RÉGRESSION lot 2.5 : hors médias reconnus, `.bin`.

    Avant le lot 2.5, `text/html` donnait `.html` et `image/svg+xml` donnait
    `.svg` — deux documents exécutables servis same-origin (§6.6 / T10).
    """
    assert downloaders._guess_extension("https://x.invalid/a", "text/html") == ".bin"
    assert downloaders._guess_extension("https://x.invalid/a", "image/svg+xml") == ".bin"
    # Le chemin de l'URL est filtré par la même liste blanche.
    assert downloaders._guess_extension("https://x.invalid/a.html", None) == ".bin"
    assert downloaders._guess_extension("https://x.invalid/a.svg", None) == ".bin"


@pytest.mark.downloader
@pytest.mark.security
def test_guess_extension_laisse_passer_les_vrais_medias():
    """Contrôle négatif : la liste blanche ne dégrade pas les cas légitimes.

    Sans lui, un `return ".bin"` inconditionnel ferait passer le test ci-dessus.
    """
    assert downloaders._guess_extension("https://x.invalid/a", "image/jpeg") == ".jpg"
    assert downloaders._guess_extension("https://x.invalid/a", "video/mp4") == ".mp4"
    # Sans content-type, l'extension vient du chemin de l'URL.
    assert downloaders._guess_extension("https://x.invalid/a.mp4", None) == ".mp4"
    assert downloaders._guess_extension("https://x.invalid/a", None) == ".bin"


# ===========================================================================
# 6. downloaders — fichiers partiels (risque #14)
# ===========================================================================


def _reponse_qui_coupe_en_plein_flux(request):
    """200 OK, puis coupure CDN au milieu du corps (le cas le plus fréquent).

    Le corps émis dépasse `_CHUNK_SIZE` pour que des octets soient réellement
    écrits sur disque avant la coupure : sous ce seuil, httpx tamponne et le
    fichier abandonné ferait 0 octet.
    """

    def flux():
        yield b"\x00" * (3 * downloaders._CHUNK_SIZE)
        raise httpx.ReadError("connexion CDN coupée")

    return httpx.Response(
        200, headers={"content-type": "video/mp4"}, content=flux()
    )


@pytest.mark.downloader
def test_aucun_fichier_partiel_ne_doit_survivre_a_trois_tentatives_ratees(
    download_dir, http_mock, sleeps
):
    """NON-RÉGRESSION lot 3.4 (risque #14) : critère de succès, mot pour mot.

    Avant le lot 3.4, chaque tentative ouvrait un NOUVEAU nom nanoid et rien ne
    supprimait le fichier commencé : une seule URL coupée laissait TROIS
    orphelins sans ligne `MediaItem`, récupérés au mieux 24 h plus tard par
    `cleanup_temp_files`. Le compte de tentatives est vérifié ici pour que la
    disparition des résidus ne puisse pas venir d'une boucle qui ne tourne plus.
    """
    etat = http_mock(_reponse_qui_coupe_en_plein_flux)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert len(etat.requests) == 3
    assert _files_in(download_dir) == []


# ===========================================================================
# 7. downloaders — disque plein (§4.10, risque #14)
# ===========================================================================


@pytest.fixture
def disque_plein(monkeypatch):
    """Fait lever ENOSPC à la première écriture, en laissant le fichier créé.

    Reproduit fidèlement le disque plein : `open()` réussit (l'inode existe),
    c'est `write()` qui échoue.
    """
    vrai_open = open

    class _FichierSature:
        def __init__(self, chemin):
            self.chemin = chemin

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, _donnees):
            raise OSError(errno.ENOSPC, "No space left on device")

    def open_sature(fichier, mode="r", *args, **kwargs):
        if "w" in mode:
            vrai_open(fichier, "wb").close()  # le fichier est bien créé
            return _FichierSature(fichier)
        return vrai_open(fichier, mode, *args, **kwargs)

    monkeypatch.setattr(downloaders, "open", open_sature, raising=False)


@pytest.mark.downloader
def test_enospc_remonte_brut_sans_retry(
    download_dir, http_mock, sleeps, disque_plein
):
    """§4.10 : un disque plein ne se retente pas et reste lisible en base.

    `OSError` n'est volontairement pas dans le tuple retenté : l'exception
    traverse la boucle sans être traduite, le pipeline la rattrape en
    `except Exception` (pipeline.py:295) et écrit « No space left on device »
    dans `error_message` — c'est le seul endroit de l'application où le disque
    plein est visible. Le fichier abandonné, lui, a disparu avec le lot 3.4
    (cf. `test_enospc_ne_doit_laisser_aucun_fichier_derriere_lui`).
    """
    etat = http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"x" * 1024
        )
    )

    with pytest.raises(OSError) as exc_info:
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert exc_info.value.errno == errno.ENOSPC
    assert len(etat.requests) == 1  # aucun retry
    assert sleeps == []


@pytest.mark.downloader
def test_enospc_ne_doit_laisser_aucun_fichier_derriere_lui(
    download_dir, http_mock, sleeps, disque_plein
):
    """NON-RÉGRESSION lot 3.4 : sur disque plein, surtout ne rien laisser.

    Avant le lot 3.4, `ENOSPC` abandonnait un orphelin de 0 octet sur un volume
    déjà saturé (risque #14 / §4.10).
    """
    http_mock(
        lambda request: httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"x" * 1024
        )
    )

    with pytest.raises(OSError):
        downloaders.download_media("https://cdn.invalid/video.mp4")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_download_media_doit_verifier_lespace_disque_avant_decrire(
    download_dir, http_mock, sleeps, monkeypatch
):
    """NON-RÉGRESSION lot 3.4 : un garde `os.statvfs` en tête de `download_media`.

    On n'assère PAS « statvfs a été appelé quelque part » : un correctif qui
    interrogerait le disque APRÈS l'écriture, ou sans jamais refuser, ferait
    passer ce xfail en XPASS sans apporter la propriété utile. On simule donc
    un volume PLEIN et on exige que rien ne soit ni demandé au réseau ni écrit
    sur disque.
    """
    volume_plein = os.statvfs_result(
        (4096, 4096, 1_000_000, 1, 1, 100_000, 99_999, 99_999, 0, 255)
    )
    monkeypatch.setattr(os, "statvfs", lambda _chemin: volume_plein)

    requetes = []

    def _repondre(request):
        requetes.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"ok" * 100
        )

    http_mock(_repondre)

    with pytest.raises((OSError, RuntimeError)):
        downloaders.download_media("https://cdn.invalid/photo.jpg")

    assert _files_in(download_dir) == [], "un fichier a été écrit malgré le disque plein"
    assert requetes == [], "le contrôle d'espace doit précéder la requête réseau"


# ===========================================================================
# 8. downloaders — HLS / ffmpeg (risque #64, symétrie avec _download_direct)
# ===========================================================================


@pytest.fixture
def ffmpeg_simule(monkeypatch):
    """Remplace `shutil.which` et `subprocess.run` du module downloaders.

    LOI ABSOLUE n°3 : aucun ffmpeg réel, aucun flux réseau. On substitue un
    objet `subprocess` complet au module pour ne pas toucher au `subprocess`
    global utilisé par pytest lui-même.
    """
    monkeypatch.setattr(downloaders.shutil, "which", lambda nom: f"/usr/bin/{nom}")
    etat = SimpleNamespace(commandes=[], returncode=0, ecrit=b"MP4-DE-TEST" * 16)

    def faux_run(cmd, capture_output=False, text=False, timeout=None):
        etat.commandes.append(cmd)
        if etat.returncode == 0:
            with open(cmd[-1], "wb") as fh:
                fh.write(etat.ecrit)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=etat.returncode, stdout="", stderr="ffmpeg: 404 Not Found"
        )

    monkeypatch.setattr(
        downloaders,
        "subprocess",
        SimpleNamespace(run=faux_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return etat


@pytest.mark.downloader
def test_une_url_m3u8_est_routee_vers_ffmpeg(download_dir, ffmpeg_simule):
    """`_is_hls_url` route sur `_download_hls` (downloaders.py:259-260)."""
    resultat = downloaders.download_media("https://cdn.invalid/master.m3u8?token=abc")

    assert resultat.mime_type == "video/mp4"
    assert resultat.local_path.endswith(".mp4")
    assert resultat.file_size == len(ffmpeg_simule.ecrit)
    assert resultat.content_hash == hashlib.sha256(ffmpeg_simule.ecrit).hexdigest()
    cmd = ffmpeg_simule.commandes[0]
    assert cmd[0] == "ffmpeg"
    assert "-threads" in cmd and cmd[cmd.index("-threads") + 1] == "2"
    assert cmd[cmd.index("-i") + 1] == "https://cdn.invalid/master.m3u8?token=abc"
    # Le temporaire a bien été déplacé, pas copié : il ne reste que le final.
    assert _files_in(download_dir) == [Path(resultat.local_path)]


@pytest.mark.downloader
def test_hls_un_timeout_ffmpeg_devient_une_runtimeerror_sans_residu(
    download_dir, ffmpeg_simule, monkeypatch
):
    """Chemin `subprocess.TimeoutExpired` (downloaders.py:237-238) et son
    `finally` de nettoyage : un ffmpeg qui pend ne doit laisser ni fichier
    temporaire ni fichier final derrière lui."""

    def run_qui_pend(cmd, capture_output=False, text=False, timeout=None):
        ffmpeg_simule.commandes.append(cmd)
        # Le temporaire existe déjà quand ffmpeg est tué : c'est tout l'objet
        # du `finally`.
        with open(cmd[-1], "wb") as fh:
            fh.write(b"fragment-partiel")
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(downloaders.subprocess, "run", run_qui_pend)

    with pytest.raises(RuntimeError, match="timed out"):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_hls_sans_ffmpeg_installe_leve_immediatement(download_dir, monkeypatch):
    monkeypatch.setattr(downloaders.shutil, "which", lambda nom: None)

    with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_hls_un_echec_ffmpeg_ne_laisse_aucun_residu(
    download_dir, ffmpeg_simule, sleeps
):
    """Le `finally` de `_download_hls_once` nettoie le temporaire à CHAQUE passe.

    C'est ce qui rend la boucle de reprise du lot 3.4b sûre : trois tentatives
    ratées ne laissent pas trois `.mp4` temporaires derrière elles.
    """
    ffmpeg_simule.returncode = 1

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert _files_in(download_dir) == []


@pytest.mark.downloader
def test_hls_doit_retenter_comme_le_telechargement_direct(
    download_dir, ffmpeg_simule, sleeps
):
    """NON-RÉGRESSION lot 3.4b (risque #64) : symétrie avec `_download_direct`.

    Avant le lot 3.4b, une sortie ffmpeg non nulle marquait l'item
    `download_failed` (pipeline.py:302) dès la première tentative, et le seul
    rattrapage était un scrape complet AVEC NAVIGATEUR 2 h plus tard
    (scheduler.py:304-318) au lieu d'un retry en processus de 2 secondes.
    """
    ffmpeg_simule.returncode = 1

    with pytest.raises(RuntimeError):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert len(ffmpeg_simule.commandes) == downloaders._MAX_RETRIES
    assert sleeps == [2, 4]


@pytest.mark.downloader
def test_hls_un_fichier_vide_doit_aussi_etre_retente(
    download_dir, ffmpeg_simule, sleeps
):
    """NON-RÉGRESSION lot 3.4b : la sortie vide est transitoire, elle se retente."""
    ffmpeg_simule.ecrit = b""

    with pytest.raises(RuntimeError):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert len(ffmpeg_simule.commandes) == downloaders._MAX_RETRIES


@pytest.mark.downloader
def test_hls_un_timeout_ffmpeg_nest_jamais_retente(
    download_dir, ffmpeg_simule, monkeypatch
):
    """Limite explicite de la boucle du lot 3.4b : le timeout reste définitif.

    Dix minutes ont déjà été consommées et le sémaphore de scrape est tenu
    pendant ce temps : retenter coûterait une demi-heure par média. Ce test n'a
    volontairement PAS la fixture `sleeps` — s'il devenait retenté, le backoff
    serait un vrai `time.sleep` et la suite ralentirait de six secondes.
    """

    def run_qui_pend(cmd, capture_output=False, text=False, timeout=None):
        ffmpeg_simule.commandes.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout or 1)

    monkeypatch.setattr(downloaders.subprocess, "run", run_qui_pend)

    with pytest.raises(RuntimeError, match="timed out"):
        downloaders.download_media("https://cdn.invalid/master.m3u8")

    assert len(ffmpeg_simule.commandes) == 1


# ===========================================================================
# 9. cleanup_temp_files — ce qu'il nettoie, ce qu'il oublie
# ===========================================================================


@pytest.fixture
def menage(tmp_path, monkeypatch):
    """Prépare `cleanup_temp_files` : DOWNLOAD_DIR isolé et horloge figée.

    `scheduler.py:23` importe `DOWNLOAD_DIR` par valeur ; `now = time.time()`
    (`:393`) est neutralisé pour que l'âge des fichiers soit calculé par rapport
    à `FIXED_NOW` et non à l'horloge murale (LOI ABSOLUE n°5).
    """
    dossier = tmp_path / "downloads"
    dossier.mkdir()
    monkeypatch.setattr(scheduler, "DOWNLOAD_DIR", dossier)
    monkeypatch.setattr(scheduler, "time", SimpleNamespace(time=lambda: FIXED_NOW))

    def fichier(chemin: Path, *, age_heures: float) -> Path:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(b"orphelin")
        horodatage = FIXED_NOW - age_heures * 3600
        os.utime(chemin, (horodatage, horodatage))
        return chemin

    return SimpleNamespace(dir=dossier, fichier=fichier)


@pytest.fixture
def orphelin_dans_data_dir(test_data_dir):
    """Crée un orphelin dans un sous-répertoire RÉEL de `DATA_DIR` (éphémère).

    Nécessaire pour les répertoires que `cleanup_temp_files` devra balayer
    après le lot 3.4 : une implémentation corrigée les résoudra depuis
    `app.config`, pas depuis une constante patchable. Voir l'en-tête du module.
    Les fichiers créés sont supprimés en fin de test, réussi ou non.
    """
    crees: list[Path] = []

    def creer(repertoire: Path, nom: str, *, age_heures: float) -> Path:
        repertoire.mkdir(parents=True, exist_ok=True)
        chemin = repertoire / nom
        chemin.write_bytes(b"orphelin")
        horodatage = FIXED_NOW - age_heures * 3600
        os.utime(chemin, (horodatage, horodatage))
        crees.append(chemin)
        return chemin

    yield creer

    for chemin in crees:
        if chemin.exists():
            chemin.unlink()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_supprime_un_orphelin_de_plus_de_24h(menage):
    vieux = menage.fichier(menage.dir / "vieux.mp4", age_heures=25)

    scheduler.cleanup_temp_files()

    assert not vieux.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_epargne_un_fichier_de_moins_de_24h(menage):
    """Un téléchargement en cours ne doit jamais être effacé sous les pieds."""
    recent = menage.fichier(menage.dir / "recent.mp4", age_heures=23)

    scheduler.cleanup_temp_files()

    assert recent.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_epargne_un_fichier_reference_en_base(menage, make_media_item):
    """Le filtre est `local_path`, quel que soit le statut de l'item."""
    reference = menage.fichier(menage.dir / "reference.mp4", age_heures=100)
    make_media_item(status="uploaded", local_path=str(reference))
    orphelin = menage.fichier(menage.dir / "orphelin.mp4", age_heures=100)

    scheduler.cleanup_temp_files()

    assert reference.exists()
    assert not orphelin.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_compare_les_chemins_en_absolu(menage, make_media_item):
    """`os.path.abspath` des deux côtés (scheduler.py:391, :401).

    Le détour est construit avec `os.path.join`, qui NE normalise PAS, et non
    avec `pathlib` — `Path(x).parent / "." / x.name` vaut exactement `Path(x)`
    (pathlib supprime le composant "." à la construction), ce qui rendait ce
    test tautologique : il survivait à la suppression des deux `abspath`.
    Le garde-fou en tête interdit qu'il le redevienne.
    """
    reference = menage.fichier(menage.dir / "reference.mp4", age_heures=100)
    chemin_avec_detour = os.path.join(str(reference.parent), ".", reference.name)
    assert chemin_avec_detour != str(reference), (
        "le chemin de test doit être NON normalisé, sinon le test ne prouve rien"
    )
    assert os.path.abspath(chemin_avec_detour) == str(reference)
    make_media_item(status="uploaded", local_path=chemin_avec_detour)

    scheduler.cleanup_temp_files()

    assert reference.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_ne_leve_pas_si_le_repertoire_nexiste_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "DOWNLOAD_DIR", tmp_path / "absent")

    scheduler.cleanup_temp_files()  # ne doit rien lever


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_ne_supprime_jamais_un_repertoire(menage):
    """Le balayage récursif du lot 3.4 ne s'en prend qu'aux FICHIERS.

    `.thumbs` reste un répertoire même vidé de son contenu : `serve_media_thumbnail`
    le recrée au besoin, mais un `os.unlink` sur un répertoire ferait remonter
    une `OSError` à chaque passage du ménage.
    """
    thumbs = menage.dir / ".thumbs"
    vignette = menage.fichier(thumbs / "abc.jpg", age_heures=200)

    scheduler.cleanup_temp_files()

    assert thumbs.is_dir()
    assert not vignette.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_epargne_les_fichiers_de_service(menage):
    """`.gitkeep` (versionné) et consorts ne sont jamais balayés.

    Les supprimer ne libère rien et fait disparaître l'arborescence du dépôt.
    """
    garde = menage.fichier(menage.dir / ".gitkeep", age_heures=10_000)

    scheduler.cleanup_temp_files()

    assert garde.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_doit_purger_les_vignettes_orphelines(menage):
    """NON-RÉGRESSION lot 3.4 : `.thumbs` est balayé comme le reste.

    Avant le lot 3.4, `if not entry.is_file(): continue` empêchait d'entrer
    dans le répertoire `.thumbs` : les vignettes de médias supprimés y
    restaient indéfiniment (risque #51).
    """
    vignette = menage.fichier(menage.dir / ".thumbs" / "abc.jpg", age_heures=200)

    scheduler.cleanup_temp_files()

    assert not vignette.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_epargne_la_vignette_dun_media_vivant(menage, make_media_item):
    """Contrôle négatif : purger `.thumbs` ne doit pas purger la bibliothèque.

    `.thumbs/<nanoid>.jpg` n'est jamais référencé en base — seul le média
    `<nanoid>.mp4` l'est. Sans la garde sur le radical, le ménage détruirait
    chaque nuit les vignettes de TOUS les médias vivants, à regénérer une par
    une à coups de ffmpeg au prochain affichage.
    """
    media = menage.fichier(menage.dir / "vivant.mp4", age_heures=200)
    make_media_item(status="uploaded", local_path=str(media))
    vignette = menage.fichier(menage.dir / ".thumbs" / "vivant.jpg", age_heures=200)

    scheduler.cleanup_temp_files()

    assert media.exists()
    assert vignette.exists()


@pytest.mark.storage
@pytest.mark.scheduler
@pytest.mark.editor
def test_le_menage_doit_purger_les_repertoires_de_lediteur(
    menage, orphelin_dans_data_dir
):
    """NON-RÉGRESSION lot 3.4 (T13) : les répertoires de l'éditeur sont balayés.

    Avant le lot 3.4, le ménage ne connaissait que `DOWNLOAD_DIR` :
    `EDITOR_UPLOAD_DIR` et `EDITOR_OUTPUT_DIR` grossissaient sans limite.
    """
    entree = orphelin_dans_data_dir(
        app_config.EDITOR_UPLOAD_DIR, "upload-mort.mp4", age_heures=200
    )
    sortie = orphelin_dans_data_dir(
        app_config.EDITOR_OUTPUT_DIR, "sortie-morte.mp4", age_heures=200
    )

    scheduler.cleanup_temp_files()

    assert not entree.exists()
    assert not sortie.exists()


@pytest.mark.storage
@pytest.mark.scheduler
@pytest.mark.calendar
def test_le_menage_doit_purger_les_medias_du_calendrier(
    menage, orphelin_dans_data_dir
):
    """NON-RÉGRESSION lot 3.4 (risque #56) : `DATA_DIR/calendar` est balayé.

    Ces fichiers sont écrits AVANT le commit (`calendar/api.py:144`) et ne sont
    pas supprimés sur rollback — d'où des orphelins définitifs, qu'AUCUN chemin
    de code ne ramassait avant ce lot.
    """
    media = orphelin_dans_data_dir(
        app_config.CALENDAR_DIR / "media", "post-annule.jpg", age_heures=200
    )

    scheduler.cleanup_temp_files()

    assert not media.exists()


@pytest.mark.storage
@pytest.mark.scheduler
@pytest.mark.calendar
def test_le_menage_epargne_le_visuel_dun_post_programme(
    menage, orphelin_dans_data_dir, make_scheduled_post
):
    """Contrôle négatif : un visuel encore référencé par un post survit.

    Étendre le balayage au calendrier et à l'éditeur sans lire
    `ScheduledPost.media_path` / `SavedMeme.file_path` détruirait des fichiers
    ENCORE UTILISÉS — un post programmé sans son image se publierait vide.
    """
    visuel = orphelin_dans_data_dir(
        app_config.CALENDAR_DIR / "media", "post-vivant.jpg", age_heures=200
    )
    make_scheduled_post(media_path=str(visuel))

    scheduler.cleanup_temp_files()

    assert visuel.exists()


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_survit_a_un_fichier_impossible_a_supprimer(menage, monkeypatch):
    """`OSError` sur un `unlink` est journalisée, pas propagée (scheduler.py:418)."""
    premier = menage.fichier(menage.dir / "a-verrouille.mp4", age_heures=100)
    second = menage.fichier(menage.dir / "b-normal.mp4", age_heures=100)
    vrai_unlink = os.unlink

    def unlink_capricieux(chemin, *args, **kwargs):
        if str(chemin) == str(premier):
            raise OSError(errno.EACCES, "Permission denied")
        return vrai_unlink(chemin, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink_capricieux)

    scheduler.cleanup_temp_files()

    assert premier.exists()
    assert not second.exists()  # le balayage continue malgré l'échec


@pytest.mark.storage
@pytest.mark.scheduler
def test_le_menage_ne_touche_pas_aux_lignes_de_la_base(menage, db_session, make_media_item):
    """Le ménage est purement disque : aucune ligne `MediaItem` n'est modifiée.

    Un item dont le fichier a disparu reste donc `uploaded` avec un `local_path`
    pointant dans le vide (risque #24, symétrique du mode gdrive).
    """
    orphelin = menage.fichier(menage.dir / "orphelin.mp4", age_heures=100)
    item = make_media_item(status="uploaded", local_path=str(orphelin))
    # On coupe la référence pour que le fichier soit bien considéré orphelin.
    item.local_path = None
    db_session.commit()

    scheduler.cleanup_temp_files()

    assert not orphelin.exists()
    assert db_session.query(MediaItem).count() == 1
