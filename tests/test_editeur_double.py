"""
ÉDITEUR DOUBLE — tests serveur.

Périmètre testable côté serveur du chantier « side by side » :

  1. La typo POV (Montserrat SemiBold, alternative OFL à Proxima Nova
     SemiBold qui est commerciale) est VENDORISÉE avec sa licence — l'OFL
     exige que le texte de licence accompagne le fichier de police — et
     servie par Flask.
  2. La page /editor porte bien les DEUX plateaux (canvas Instagram et
     canvas TikTok), le champ POV et le dialogue de planification double.
  3. La planification double réutilise POST /api/calendar/posts tel quel :
     un post `platforms=["instagram"]` + un post `platforms=["tiktok"]`,
     même date/heure — la colonne `platforms` existait déjà, AUCUNE
     migration.
  4. GET /api/calendar/posts/<id>/media sert désormais la vignette (l'export
     de l'Éditeur) quand le post n'a pas de fichier média — avant ce repli,
     le calendrier recevait 404 et affichait « Sans média ».

Le rendu canvas lui-même (Fabric.js) n'est pas testable ici : il est
vérifié au navigateur.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXED_NOW

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = PROJECT_ROOT / "app" / "web" / "static" / "vendor" / "fonts"

# JPEG 8×8 valide (généré par ffmpeg) — sert de vignette d'export factice.
TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjExLjEwMAD/2wBDAAgEBAQEBAUFBQUF"
    "BQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/"
    "xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAA"
    "AAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAAIAAgDASIAAhEAAxEA/9oADAMBAAIRAxEA"
    "PwCLAE1/f//Z"
)
TINY_JPEG_DATA_URL = "data:image/jpeg;base64," + TINY_JPEG_B64


def _creer_post(client, *, platform: str, template_format: str, scheduled_at: int):
    """POST /api/calendar/posts comme le fait editor.js (createCalendarPost)."""
    return client.post(
        "/api/calendar/posts",
        json={
            "title": f"Meme — {platform.capitalize()}",
            "caption": "test éditeur double",
            "media_type": "image",
            "template_format": template_format,
            "thumbnail": TINY_JPEG_DATA_URL,
            "status": "scheduled",
            "scheduled_at": scheduled_at,
            "platforms": json.dumps([platform]),
        },
    )


# ---------------------------------------------------------------------------
# 1. Police POV vendorisée + licence OFL
# ---------------------------------------------------------------------------

def test_la_police_pov_est_vendorisee():
    """Montserrat-SemiBold.woff2 est présent, non tronqué, et bien du WOFF2."""
    woff2 = FONTS_DIR / "Montserrat-SemiBold.woff2"
    assert woff2.is_file(), "Montserrat-SemiBold.woff2 manquant dans vendor/fonts/"
    data = woff2.read_bytes()
    # La signature WOFF2 est 'wOF2' ; un HTML d'erreur GitHub ne l'a pas.
    assert data[:4] == b"wOF2", f"signature inattendue : {data[:4]!r}"
    assert len(data) > 50_000, f"fichier suspect ({len(data)} octets)"


def test_la_licence_ofl_accompagne_la_police():
    """L'OFL exige que son texte accompagne la police distribuée."""
    ofl = FONTS_DIR / "OFL.txt"
    assert ofl.is_file(), "OFL.txt manquant à côté de la police"
    text = ofl.read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in text
    assert "Montserrat" in text


def test_la_police_et_sa_licence_sont_servies_par_flask(client):
    """Le @font-face pointe sur /static/vendor/fonts/… : Flask doit le servir."""
    reponse = client.get("/static/vendor/fonts/Montserrat-SemiBold.woff2")
    try:
        assert reponse.status_code == 200
        assert reponse.data[:4] == b"wOF2"
    finally:
        # send_file laisse le descripteur ouvert : la suite convertit les
        # ResourceWarning en échecs, on ferme donc explicitement.
        reponse.close()

    licence = client.get("/static/vendor/fonts/OFL.txt")
    try:
        assert licence.status_code == 200
        assert b"SIL OPEN FONT LICENSE" in licence.data
    finally:
        licence.close()


def test_le_css_declare_le_font_face_montserrat(client):
    """Sans @font-face, la police vendorisée ne serait jamais chargée."""
    reponse = client.get("/static/editor.css")
    try:
        assert reponse.status_code == 200
        css = reponse.data.decode("utf-8")
    finally:
        reponse.close()
    assert "@font-face" in css
    assert "Montserrat" in css
    assert "/static/vendor/fonts/Montserrat-SemiBold.woff2" in css


# ---------------------------------------------------------------------------
# 2. La page /editor porte les deux plateaux
# ---------------------------------------------------------------------------

def test_la_page_editeur_porte_les_deux_plateaux(client):
    reponse = client.get("/editor")
    assert reponse.status_code == 200
    html = reponse.data.decode("utf-8")

    # Deux canvas, un par plateau — c'est LE contrat de l'éditeur double.
    assert 'id="meme-canvas-ig"' in html
    assert 'id="meme-canvas-tt"' in html
    # L'ancien canvas unique a disparu (sinon editor.js viserait un fantôme).
    assert 'id="meme-canvas"' not in html

    # Interrupteurs de plateau.
    assert 'id="toggle-ig"' in html
    assert 'id="toggle-tt"' in html

    # Champ POV + styles de bloc.
    assert 'id="pov-text"' in html
    assert 'id="pov-style-group"' in html

    # Dialogue de planification double : date + une case par plateforme.
    assert 'id="schedule-dialog"' in html
    assert 'id="schedule-datetime"' in html
    assert 'id="schedule-check-ig"' in html
    assert 'id="schedule-check-tt"' in html


# ---------------------------------------------------------------------------
# 3. Planification double via l'API calendrier existante
# ---------------------------------------------------------------------------

def test_un_post_tiktok_planifie_est_cree_et_liste(client):
    quand = FIXED_NOW + 3600
    reponse = _creer_post(
        client, platform="tiktok", template_format="story", scheduled_at=quand
    )
    assert reponse.status_code == 201
    corps = reponse.get_json()
    assert corps["status"] == "scheduled"

    events = client.get("/api/calendar/posts").get_json()
    assert len(events) == 1
    event = events[0]
    assert event["title"] == "Meme — Tiktok"
    props = event["extendedProps"]
    assert props["platforms"] == ["tiktok"]
    assert props["template_format"] == "story"
    assert props["status"] == "scheduled"
    # La vignette (l'export du canvas) est bien écrite sur le disque.
    assert props["thumbnail_path"] and Path(props["thumbnail_path"]).is_file()


def test_la_planification_double_cree_deux_posts_a_la_meme_heure(client):
    """Un post Instagram (canvas gauche) + un post TikTok (canvas droit)."""
    quand = FIXED_NOW + 7200
    rep_ig = _creer_post(
        client, platform="instagram", template_format="portrait", scheduled_at=quand
    )
    rep_tt = _creer_post(
        client, platform="tiktok", template_format="story", scheduled_at=quand
    )
    assert rep_ig.status_code == 201
    assert rep_tt.status_code == 201

    events = client.get("/api/calendar/posts").get_json()
    assert len(events) == 2

    par_plateforme = {e["extendedProps"]["platforms"][0]: e for e in events}
    assert set(par_plateforme) == {"instagram", "tiktok"}

    # Même date/heure proposée pour les deux : le POST la conserve telle quelle.
    assert (
        par_plateforme["instagram"]["start"] == par_plateforme["tiktok"]["start"]
    )
    # Chaque plateau garde SON format : 4:5 à gauche, 9:16 à droite.
    assert par_plateforme["instagram"]["extendedProps"]["template_format"] == "portrait"
    assert par_plateforme["tiktok"]["extendedProps"]["template_format"] == "story"

    # Deux vignettes distinctes sur le disque (une par canvas).
    chemins = {e["extendedProps"]["thumbnail_path"] for e in events}
    assert len(chemins) == 2
    for chemin in chemins:
        assert Path(chemin).is_file()


# ---------------------------------------------------------------------------
# 4. Le média d'un post de l'Éditeur = sa vignette (repli)
# ---------------------------------------------------------------------------

def test_le_media_d_un_post_sans_fichier_sert_la_vignette(client):
    reponse = _creer_post(
        client, platform="instagram", template_format="portrait",
        scheduled_at=FIXED_NOW + 3600,
    )
    post_id = reponse.get_json()["id"]

    media = client.get(f"/api/calendar/posts/{post_id}/media")
    try:
        assert media.status_code == 200
        assert media.mimetype == "image/jpeg"
        # C'est bien un JPEG, pas un corps d'erreur retitré.
        assert media.data[:3] == b"\xff\xd8\xff"
    finally:
        media.close()


def test_le_media_d_un_post_sans_rien_repond_404(client, make_scheduled_post):
    post = make_scheduled_post(media_path=None, thumbnail_path=None)
    reponse = client.get(f"/api/calendar/posts/{post.id}/media")
    assert reponse.status_code == 404
