"""
LOT A — EXPORT VIDÉO DOUBLE : tests serveur.

« Sauvegarder dans Viewer » avec une vidéo produit UN MP4 PAR plateau actif
(Instagram au format choisi, TikTok 1080×1920 plein cadre) et chacun rejoint
les memes sauvegardés du Viewer. Côté serveur, cela passe par le nouvel
endpoint POST /api/editor/save-video-meme, appelé par le client UNE fois par
plateau, SÉQUENTIELLEMENT (jamais deux ffmpeg concurrents).

Contrats vérifiés ici (FFmpeg SIMULÉ — aucun binaire lancé, aucun réseau) :

  1. Un appel réussi écrit le MP4 dans le répertoire des memes du Viewer
     (suffixe -instagram / -tiktok), crée la ligne SavedMeme, et le meme est
     listé puis servi par l'API Viewer en video/mp4.
  2. La plateforme est une LISTE BLANCHE : toute valeur hors
     {instagram, tiktok} est ignorée (pas d'injection dans le nom de fichier).
  3. Les paramètres JSON du client sont traduits vers process_video() avec le
     MÊME contrat que /api/editor/process-video.
  4. Échec FFmpeg = aucune trace : pas de ligne en base, pas de fichier
     orphelin, uploads nettoyés — et un 500 franc.
  5. Enchaînement séquentiel : si la 2e vidéo échoue, la 1re est DÉJÀ
     sauvegardée et n'est pas perdue.

Le déclenchement côté client (modal, progression « vidéo 1/2… ») est vérifié
au navigateur ; ici on fige seulement les marqueurs statiques d'editor.js.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.config import EDITOR_OUTPUT_DIR, EDITOR_UPLOAD_DIR
from app.db import SavedMeme

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EDITOR_JS = PROJECT_ROOT / "app" / "web" / "static" / "editor.js"

MEMES_DIR = EDITOR_OUTPUT_DIR / "memes"

#: Contenu factice écrit par le faux ffmpeg — la valeur importe peu, seule
#: sa présence et sa taille non nulle comptent.
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42-fake-lot-a"


def _formulaire(platform: str = "instagram", **extra):
    """Multipart minimal tel que l'envoie saveVideoMemesToViewer()."""
    donnees = {
        "video": (io.BytesIO(b"fake-input-video"), "clip.mp4"),
        "template": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "template.png"),
        "params": json.dumps({
            "templateWidth": 1080,
            "templateHeight": 1350,
            "frameX": 54,
            "frameY": 195,
            "frameWidth": 972,
            "frameHeight": 810,
            "originalFrameY": 195,
            "originalFrameHeight": 810,
            "trimStart": 1.5,
            "trimEnd": 7.5,
            "imageScale": 120,
            "imageOffsetX": -10,
            "imageOffsetY": 25,
        }),
        "platform": platform,
        "title": f"Meme vidéo — {platform.capitalize()}",
        "caption": "test lot A",
        "template_format": "portrait" if platform == "instagram" else "story",
    }
    donnees.update(extra)
    return donnees


@pytest.fixture
def faux_ffmpeg(monkeypatch):
    """Remplace process_video par une écriture de fichier instantanée.

    Cible : le nom importé PAR VALEUR dans app.editor.api (cf. conftest §7.1).
    Retourne la liste des appels (kwargs) pour inspection.
    """
    appels = []

    def _fake(video_path, template_path, output_path, **kwargs):
        appels.append({
            "video_path": video_path,
            "template_path": template_path,
            "output_path": output_path,
            **kwargs,
        })
        Path(output_path).write_bytes(FAKE_MP4)
        return output_path

    monkeypatch.setattr("app.editor.api.process_video", _fake)
    return appels


def _fichiers_memes() -> set[str]:
    if not MEMES_DIR.is_dir():
        return set()
    return {p.name for p in MEMES_DIR.iterdir() if p.is_file()}


@pytest.fixture(autouse=True)
def _memes_dir_propre():
    """Chaque test part d'un répertoire de memes vide (et le laisse vide)."""
    MEMES_DIR.mkdir(parents=True, exist_ok=True)
    for f in MEMES_DIR.iterdir():
        if f.is_file():
            f.unlink()
    yield
    for f in MEMES_DIR.iterdir():
        if f.is_file():
            f.unlink()


# ---------------------------------------------------------------------------
# 1. Chemin nominal : un MP4 sauvegardé dans le Viewer
# ---------------------------------------------------------------------------

def test_la_sauvegarde_instagram_cree_un_meme_video(client, db_session, faux_ffmpeg):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("instagram"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201, reponse.data
    corps = reponse.get_json()
    assert corps["id"]
    assert corps["file_url"] == f"/api/viewer/memes/{corps['id']}/file"

    # La ligne SavedMeme est complète.
    meme = db_session.query(SavedMeme).filter_by(id=corps["id"]).one()
    assert meme.media_type == "video"
    assert meme.template_format == "portrait"
    assert meme.title == "Meme vidéo — Instagram"
    assert meme.caption == "test lot A"
    assert meme.file_size == len(FAKE_MP4)

    # Le fichier vit dans le répertoire des memes, suffixé -instagram.
    chemin = Path(meme.file_path)
    assert chemin.parent == MEMES_DIR
    assert chemin.name.endswith("-instagram.mp4")
    assert chemin.read_bytes() == FAKE_MP4

    # Les uploads temporaires sont nettoyés, le meme reste.
    restes = [p.name for p in EDITOR_UPLOAD_DIR.iterdir() if p.is_file()]
    assert restes == [], f"uploads non nettoyés : {restes}"


def test_le_meme_video_est_liste_et_servi_par_le_viewer(client, faux_ffmpeg):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("tiktok"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    meme_id = reponse.get_json()["id"]

    liste = client.get("/api/viewer/memes").get_json()
    ids = {m["id"]: m for m in liste["items"]}
    assert meme_id in ids
    assert ids[meme_id]["media_type"] == "video"

    fichier = client.get(f"/api/viewer/memes/{meme_id}/file")
    try:
        assert fichier.status_code == 200
        assert fichier.mimetype == "video/mp4"
        assert fichier.data == FAKE_MP4
    finally:
        fichier.close()


def test_le_suffixe_tiktok_est_applique(client, db_session, faux_ffmpeg):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("tiktok"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    meme = db_session.query(SavedMeme).one()
    assert Path(meme.file_path).name.endswith("-tiktok.mp4")
    assert meme.template_format == "story"


# ---------------------------------------------------------------------------
# 2. Liste blanche de plateforme (le nom de fichier n'est pas injectable)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plateforme", ["", "youtube", "../../evil", "insta gram"])
def test_une_plateforme_hors_liste_blanche_est_ignoree(client, db_session, faux_ffmpeg, plateforme):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire(plateforme, title="t", template_format="square"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    meme = db_session.query(SavedMeme).one()
    chemin = Path(meme.file_path)
    # Pas de suffixe, et surtout : le fichier reste DANS le répertoire memes.
    assert chemin.parent == MEMES_DIR
    assert chemin.name.endswith(".mp4")
    assert "-instagram" not in chemin.name and "-tiktok" not in chemin.name
    assert "/" not in chemin.name and ".." not in chemin.name


def test_la_casse_de_la_plateforme_est_normalisee(client, db_session, faux_ffmpeg):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("  Instagram "),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    meme = db_session.query(SavedMeme).one()
    assert Path(meme.file_path).name.endswith("-instagram.mp4")


# ---------------------------------------------------------------------------
# 3. Contrat des paramètres FFmpeg (identique à /process-video)
# ---------------------------------------------------------------------------

def test_les_params_json_sont_transmis_a_ffmpeg(client, faux_ffmpeg):
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("instagram"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    assert len(faux_ffmpeg) == 1
    appel = faux_ffmpeg[0]
    assert appel["template_width"] == 1080
    assert appel["template_height"] == 1350
    assert appel["frame_x"] == 54
    assert appel["frame_y"] == 195
    assert appel["frame_width"] == 972
    assert appel["frame_height"] == 810
    assert appel["original_frame_y"] == 195
    assert appel["original_frame_height"] == 810
    assert appel["trim_start"] == 1.5
    assert appel["trim_end"] == 7.5
    assert appel["image_scale"] == 120
    assert appel["image_offset_x"] == -10
    assert appel["image_offset_y"] == 25


def test_des_params_illisibles_retombent_sur_les_defauts(client, faux_ffmpeg):
    """Même tolérance que /process-video : JSON cassé → défauts, pas un 500."""
    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("instagram", params="{pas-du-json"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 201
    assert faux_ffmpeg[0]["template_width"] == 1080
    assert faux_ffmpeg[0]["trim_end"] == 10.0


# ---------------------------------------------------------------------------
# 4. Validation des uploads
# ---------------------------------------------------------------------------

def test_sans_video_le_serveur_repond_400(client, faux_ffmpeg):
    donnees = _formulaire("instagram")
    donnees.pop("video")
    reponse = client.post(
        "/api/editor/save-video-meme", data=donnees, content_type="multipart/form-data"
    )
    assert reponse.status_code == 400
    assert faux_ffmpeg == []


def test_sans_template_le_serveur_repond_400(client, faux_ffmpeg):
    donnees = _formulaire("instagram")
    donnees.pop("template")
    reponse = client.post(
        "/api/editor/save-video-meme", data=donnees, content_type="multipart/form-data"
    )
    assert reponse.status_code == 400
    assert faux_ffmpeg == []


# ---------------------------------------------------------------------------
# 5. Échec FFmpeg : aucune trace, 500 franc
# ---------------------------------------------------------------------------

def test_un_echec_ffmpeg_ne_laisse_aucune_trace(client, db_session, monkeypatch):
    def _explose(video_path, template_path, output_path, **kwargs):
        # Simule un ffmpeg qui a commencé à écrire avant d'échouer.
        Path(output_path).write_bytes(b"partiel")
        raise RuntimeError("ffmpeg exited 1: boom")

    monkeypatch.setattr("app.editor.api.process_video", _explose)

    reponse = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("instagram"),
        content_type="multipart/form-data",
    )
    assert reponse.status_code == 500
    assert reponse.get_json()["error"]

    # Pas de ligne en base, pas de fichier orphelin, uploads nettoyés.
    assert db_session.query(SavedMeme).count() == 0
    assert _fichiers_memes() == set()
    restes = [p.name for p in EDITOR_UPLOAD_DIR.iterdir() if p.is_file()]
    assert restes == []


def test_la_premiere_video_survit_a_l_echec_de_la_seconde(client, db_session, monkeypatch):
    """LOT A point 2 : un échec sur la 2e ne doit PAS perdre la 1re.

    Reproduit l'enchaînement séquentiel du client : appel Instagram (réussit),
    puis appel TikTok (ffmpeg échoue). Le meme Instagram doit rester en base
    ET sur disque.
    """
    appels = {"n": 0}

    def _un_sur_deux(video_path, template_path, output_path, **kwargs):
        appels["n"] += 1
        if appels["n"] == 2:
            raise RuntimeError("ffmpeg timed out after 10 minutes")
        Path(output_path).write_bytes(FAKE_MP4)
        return output_path

    monkeypatch.setattr("app.editor.api.process_video", _un_sur_deux)

    premiere = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("instagram"),
        content_type="multipart/form-data",
    )
    assert premiere.status_code == 201

    seconde = client.post(
        "/api/editor/save-video-meme",
        data=_formulaire("tiktok"),
        content_type="multipart/form-data",
    )
    assert seconde.status_code == 500

    survivants = db_session.query(SavedMeme).all()
    assert len(survivants) == 1
    assert Path(survivants[0].file_path).name.endswith("-instagram.mp4")
    assert Path(survivants[0].file_path).read_bytes() == FAKE_MP4
    # Aucun fichier orphelin de la seconde tentative.
    assert _fichiers_memes() == {Path(survivants[0].file_path).name}


def test_les_deux_plateaux_donnent_deux_memes(client, db_session, faux_ffmpeg):
    """Le chemin double nominal : deux appels séquentiels, deux memes."""
    for plateforme in ("instagram", "tiktok"):
        reponse = client.post(
            "/api/editor/save-video-meme",
            data=_formulaire(plateforme),
            content_type="multipart/form-data",
        )
        assert reponse.status_code == 201

    noms = sorted(Path(m.file_path).name for m in db_session.query(SavedMeme).all())
    assert len(noms) == 2
    assert noms[0].endswith("-instagram.mp4") or noms[1].endswith("-instagram.mp4")
    assert noms[0].endswith("-tiktok.mp4") or noms[1].endswith("-tiktok.mp4")
    assert _fichiers_memes() == set(noms)
    # Deux rendus, jamais concurrents ici : le client attend chaque réponse.
    assert len(faux_ffmpeg) == 2


# ---------------------------------------------------------------------------
# 6. Marqueurs statiques d'editor.js (le détail est vérifié au navigateur)
# ---------------------------------------------------------------------------

def test_editor_js_appelle_le_nouvel_endpoint():
    js = EDITOR_JS.read_text(encoding="utf-8")
    assert "/api/editor/save-video-meme" in js
    assert "saveVideoMemesToViewer" in js
    # L'ancien refus « pas encore les vidéos » a disparu du bouton Sauvegarder.
    assert "ne prend pas encore les vidéos" not in js
    # Progression honnête : le libellé « vidéo i/n » existe.
    assert "'vidéo ' + (i + 1) + '/' + targets.length" in js
