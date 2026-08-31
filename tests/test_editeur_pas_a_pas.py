"""
Refonte UI — l'éditeur en quatre étapes (Média → Cadrage → Texte → Export).

CE QUE CE MODULE PROTÈGE
------------------------
Le lot n'a rien réécrit du moteur : il a DÉPLACÉ des contrôles existants
dans quatre panneaux et posé un contrôleur de parcours par-dessus. Le
risque n'est donc pas « le rendu est laid », c'est « editor.js cherche un
élément que le gabarit ne porte plus ». Un `getElementById` qui renvoie
null ne lève rien à l'analyse : la page répond 200, le script meurt à la
première déréférence, et l'écran ne fait plus rien du tout.

D'où la pièce maîtresse de ce module — §2 — qui relit editor.js, extrait
TOUS les identifiants qu'il demande et vérifie que le gabarit les sert.
C'est le garde-fou que le lot lui-même aurait dû avoir : il aurait
attrapé chaque oubli avant le navigateur.

CE QU'IL NE PROTÈGE PAS
-----------------------
Rien ici n'exécute JavaScript. La géométrie du filigrane, la grille des
tiers, le pré-remplissage et l'export double ont été vérifiés AU
NAVIGATEUR (mesures au pixel sur le canvas rendu, cf. rapport de lot) ;
ces tests-là figent les invariants STATIQUES qui les rendent possibles.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.editor

RACINE = Path(__file__).resolve().parents[1]
EDITOR_HTML = RACINE / "app" / "web" / "templates" / "editor.html"
EDITOR_CSS = RACINE / "app" / "web" / "static" / "editor.css"
EDITOR_JS = RACINE / "app" / "web" / "static" / "editor.js"


@pytest.fixture(scope="module")
def html() -> str:
    return EDITOR_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return EDITOR_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return EDITOR_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. La structure du parcours
# ---------------------------------------------------------------------------

def test_le_gabarit_porte_les_quatre_panneaux_detape(html):
    """Un panneau par étape, ni plus ni moins."""
    etapes = re.findall(r'<section class="wiz-step" data-step="(\d)"', html)
    assert etapes == ["1", "2", "3", "4"]


def test_letat_du_parcours_vit_sur_un_seul_attribut(html, css):
    """`.editor-app[data-step]` : un commutateur, pas quatre classes.

    Si l'étape courante était portée par une classe SUR CHAQUE panneau, une
    bascule ratée laisserait deux panneaux ouverts. Avec un attribut unique
    sur le conteneur, l'état est indivisible par construction.
    """
    assert 'class="app editor-app" data-step="1" id="editor-app"' in html
    for n in (1, 2, 3, 4):
        assert f'.editor-app[data-step="{n}"] .wiz-step[data-step="{n}"]' in css


def test_les_quatre_panneaux_restent_dans_le_dom_a_toute_etape(css):
    """`display:none` sur les panneaux inactifs — jamais un retrait du DOM.

    C'est ce qui garantit qu'un `getElementById` d'editor.js trouve son
    élément à n'importe quelle étape. Un panneau détaché rendrait le
    contrôleur dépendant de l'ordre de visite de l'utilisateur.
    """
    assert re.search(r"\.wiz-step\s*\{\s*display:\s*none;", css)


def test_la_barre_detapes_a_quatre_cellules_et_le_rail_quatre_entrees(html):
    assert len(re.findall(r'class="wiz-steps__cell[^"]*" type="button" data-goto="\d"', html)) == 4
    assert len(re.findall(r'class="wiz-rail__item[^"]*" type="button" data-goto="\d"', html)) == 4


def test_les_cibles_tactiles_du_parcours_tiennent_44px(css):
    """Handoff : cibles ≥ 44×44. Les trois familles nées avec ce lot."""
    for bloc in ("wiz-head__back", "stepper__btn", "wiz-steps__cell"):
        motif = re.search(r"\." + bloc + r"\s*\{(.*?)\}", css, re.S)
        assert motif, bloc
        assert "var(--tap-min)" in motif.group(1), bloc


def test_aucun_identifiant_du_gabarit_nest_en_double(html):
    """Deux `id` identiques : `getElementById` en sert un, l'autre est mort."""
    doubles = [k for k, v in Counter(re.findall(r'id="([^"]+)"', html)).items() if v > 1]
    assert doubles == []


# ---------------------------------------------------------------------------
# 2. LE GARDE-FOU : editor.js ne demande rien que le gabarit ne serve
# ---------------------------------------------------------------------------

#: Identifiants CRÉÉS PAR editor.js lui-même (modales d'export et de
#: configuration Drive, construites en `innerHTML` au moment du clic). Ils
#: n'ont rien à faire dans le gabarit ; les lister ici est ce qui rend le
#: test au-dessus utilisable au lieu de le noyer sous les faux positifs.
IDS_CONSTRUITS_EN_JS = {
    "export-modal",
    "progress-fill",
    "progress-text",
    "drive-config-modal",
    "drive-api-key",
    "drive-client-id",
}

#: Préfixes concaténés à une clé de plateau : `'toggle-' + p.key`.
# Préfixes concaténés en JS ('zoom-' + suffixe). Chacun DOIT être couvert
# par `test_les_plateaux_repondent_aux_prefixes_concatenes` : déclarer un
# préfixe ici sans l'y ajouter reviendrait à créer un angle mort.
PREFIXES_DYNAMIQUES = ("toggle-", "export-shot-", "export-tag-",
                       "zoom-", "zoom-readout-")


def test_tout_element_reclame_par_editor_js_existe_dans_le_gabarit(js, html):
    """Le test qui aurait attrapé chaque oubli de ce lot avant l'écran.

    editor.js fait ~90 `getElementById`. Aucun n'est gardé par un `if` dans
    le cas général : le premier absent tue l'initialisation, et la page
    répond quand même 200. Ce test relit le script et confronte sa liste de
    courses au gabarit.
    """
    demandes = set(re.findall(r"getElementById\(\s*'([^']+)'", js))
    demandes |= set(re.findall(r'getElementById\(\s*"([^"]+)"', js))
    demandes -= IDS_CONSTRUITS_EN_JS
    demandes = {i for i in demandes if not any(i == p for p in PREFIXES_DYNAMIQUES)}

    servis = set(re.findall(r'id="([^"]+)"', html))
    manquants = sorted(demandes - servis)
    assert manquants == [], f"editor.js cherche des éléments absents du gabarit : {manquants}"


def test_les_plateaux_repondent_aux_prefixes_concatenes(html):
    """`'toggle-' + p.key`, `'export-tag-' + p.key` : les deux clés existent."""
    for prefixe in ("toggle", "export-shot", "export-tag",
                    "zoom", "zoom-readout"):
        for cle in ("ig", "tt"):
            assert f'id="{prefixe}-{cle}"' in html, f"{prefixe}-{cle}"


def test_aucun_selecteur_de_classe_dediteur_js_nest_orphelin(js, html, css):
    """Les classes que le script interroge doivent exister quelque part.

    Une `querySelectorAll` sur une classe disparue ne lève pas : elle rend
    une liste vide et la fonctionnalité s'évapore en silence — c'est
    exactement ce qui est arrivé à l'accordéon que ce lot a remplacé.
    """
    classes = set()
    for motif in re.findall(r"querySelector(?:All)?\(\s*'([^']+)'", js):
        classes |= set(re.findall(r"\.([a-z][a-z0-9_-]*)", motif))
    # Sélecteurs génériques sur des noeuds construits en JS.
    classes -= {"jpg", "mp4"}
    for classe in sorted(classes):
        assert f'class="{classe}' in html or f" {classe}" in html or f".{classe}" in css, classe


# ---------------------------------------------------------------------------
# 3. « Tout ce qui existe doit survivre »
# ---------------------------------------------------------------------------

#: Les contrôles de l'éditeur d'avant le lot, un par réglage. La refonte
#: n'avait le droit de changer QUE leur organisation : si l'un d'eux
#: disparaît, un réglage a été perdu en route.
REGLAGES_HISTORIQUES = [
    # Format et cadrage
    "frame-height", "image-scale", "crop-group", "crop-readout",
    "rotate-left", "rotate-right", "flip-h", "flip-v", "orient-readout",
    "adj-brightness", "adj-contrast", "adj-saturation", "image-reset-btn",
    "image-tools", "image-scale-section", "frame-height-section",
    # Média
    "upload-zone", "file-input", "drive-zone", "connect-drive-btn",
    "library-zone", "library-grid", "select-image-btn", "media-type-badge",
    # Vidéo
    "timeline-container", "timeline-wrapper", "timeline-thumbnails",
    "timeline-selection", "handle-start", "handle-end", "timeline-playhead",
    "time-start", "time-end", "trim-duration", "btn-play", "btn-preview",
    "video-source",
    # Texte
    "meme-text", "text-size", "text-size-value", "line-height",
    "line-height-value", "overlay-toggle", "overlay-switch", "overlay-text",
    "pov-text", "pov-style-group",
    # Sortie
    "output-tools", "imgformat-group", "quality-ctl", "export-quality",
    "export-quality-value", "size-group", "export-dims", "export-format-label",
    # Plateaux et actions
    "meme-canvas-ig", "meme-canvas-tt", "toggle-ig", "toggle-tt",
    "stage-ig", "stage-tt", "stage-ig-dims", "stage-switch", "stages",
    "reset-btn", "export-btn", "export-notice", "save-meme-btn", "schedule-btn",
    # Planification double
    "schedule-dialog", "schedule-form", "schedule-datetime",
    "schedule-check-ig", "schedule-check-tt", "schedule-ig-dims",
]


@pytest.mark.parametrize("identifiant", REGLAGES_HISTORIQUES)
def test_chaque_reglage_dorigine_est_toujours_la(html, identifiant):
    assert f'id="{identifiant}"' in html


def test_les_trois_sources_dimport_sont_conservees(html):
    for source in ("library", "local", "drive"):
        assert f'data-source="{source}"' in html


def test_les_trois_formats_instagram_sont_conserves(html):
    for fmt, w, h in (("square", 1080, 1080), ("portrait", 1080, 1350), ("story", 1080, 1920)):
        assert f'data-format="{fmt}" data-width="{w}" data-height="{h}"' in html


def test_les_quatre_multiplicateurs_de_taille_sont_conserves(html):
    for scale in ("0.5", "1", "1.5", "2"):
        assert f'data-scale="{scale}"' in html


def test_les_trois_styles_de_bloc_pov_sont_conserves(html):
    for style in ("outline", "light", "dark"):
        assert f'data-povstyle="{style}"' in html


def test_lavertissement_dexport_video_est_conserve(html):
    assert 'id="export-notice"' in html
    assert "FFmpeg" in html


# ---------------------------------------------------------------------------
# 4. Le filigrane : posé, jamais réglé
# ---------------------------------------------------------------------------

def test_le_filigrane_est_calcule_sur_le_cadre(js):
    """Largeur 70 % du cadre, débord 3,5 % — deux constantes, pas deux
    coordonnées écrites en dur par gabarit."""
    assert "const WATERMARK_FRAME_RATIO = 0.32;" in js, (
        "Instagram : le filigrane doit rester a 0.32 — il etait a 0.70, juge "
        "trop gros par le proprietaire et reduit de plus de moitie."
    )
    assert "const WATERMARK_TIKTOK_RATIO  = 0.22;" in js, (
        "TikTok : filigrane encore plus discret que sur Instagram."
    )
    # TikTok l ancre au bord DROIT, CENTRE EN HAUTEUR (le bas de l ecran y est
    # mange par les libelles de l application).
    assert "originY: 'center'" in js, "TikTok : filigrane centre en hauteur"
    assert "const WATERMARK_BLEED_RATIO = 0.035;" in js
    assert "function placeWatermark(p)" in js
    assert "function effectiveFrame(p)" in js


def test_le_filigrane_nest_plus_manipulable(js):
    """Le geste qui produisait les « positions et tailles hasardeuses ».

    L'objet portait `selectable: true, hasControls: true` : un doigt posé
    sur le canvas l'attrapait et le déplaçait. Il est désormais inerte, et
    la fonction qui le pose est la SEULE à écrire sa géométrie.
    """
    bloc = js[js.index("function placeWatermark(p)"):]
    bloc = bloc[:bloc.index("\n        }")]
    for propriete in ("selectable: false", "evented: false",
                      "hasControls: false", "hasBorders: false"):
        assert propriete in bloc, propriete


def test_aucun_reglage_de_filigrane_nest_expose(html):
    """Ni position, ni taille, ni opacité : rien à dérégler."""
    assert "watermark" not in html.lower()
    assert 'id="watermark-opacity"' not in html


def test_lopacite_du_filigrane_reste_la_constante_existante(js):
    assert "const WATERMARK_OPACITY = 50;" in js
    assert "watermarkOpacity: WATERMARK_OPACITY" in js


def test_le_filigrane_suit_la_hauteur_du_cadre(js):
    """Le seul cas où sa position redevenait « hasardeuse » : un cadre
    rétréci laissait le logo à l'ancienne hauteur."""
    bloc = js[js.index("function updateFrameHeight(percentage)"):]
    bloc = bloc[:bloc.index("\n        function ")]
    assert "placeWatermark(p);" in bloc


# ---------------------------------------------------------------------------
# 5. La grille des tiers est un repère, jamais du contenu
# ---------------------------------------------------------------------------

def test_la_grille_des_tiers_est_exclue_de_lexport(js):
    """Deux garde-fous indépendants : `excludeFromExport` sur l'objet ET un
    masquage explicite dans le rendu d'export. Un export ne doit jamais
    dépendre d'un seul verrou."""
    bloc = js[js.index("function buildThirdsGrid(p)"):]
    bloc = bloc[:bloc.index("function placeThirdsGrid(p)")]
    assert "excludeFromExport: true" in bloc
    # La grille renaît dans l'état de l'étape courante : reconstruire le
    # plateau (changement de format, « Réinitialiser ») ne doit pas
    # l'éteindre au milieu du cadrage.
    assert "const visible = (wizStep === 2);" in bloc
    assert "visible: visible" in bloc

    export = js[js.index("function renderCanvasToDataURL(p"):]
    export = export[:export.index("\n        /** Poids d'une data URL")]
    assert "thirdsWereVisible" in export


def test_la_grille_nexiste_qua_letape_de_cadrage(js):
    assert "showThirdsGrid(wizStep === 2);" in js


def test_la_grille_est_inerte_au_pointeur(js):
    bloc = js[js.index("function buildThirdsGrid(p)"):]
    bloc = bloc[:bloc.index("function placeThirdsGrid(p)")]
    assert "selectable: false" in bloc
    assert "evented: false" in bloc


# ---------------------------------------------------------------------------
# 6. Les steppers écrivent dans les curseurs d'origine
# ---------------------------------------------------------------------------

def test_les_steppers_bornent_comme_le_handoff(js, html):
    """Zoom 100→200 pas de 10 ; taille 24→72 pas de 2 ; interligne
    0,8→2,0 pas de 0,1."""
    # Le zoom n est plus un stepper partage : c est UN CURSEUR PAR PLATEAU
    # (un 4:5 et un 9:16 ne se cadrent pas au meme zoom).
    assert "appliquerZoom(pane, v)" in js, (
        "chaque curseur de zoom ne doit toucher QUE son plateau"
    )
    assert 'id="zoom-ig"' in html and 'id="zoom-tt"' in html, (
        "un curseur de zoom par plateau doit exister dans le gabarit"
    )
    # (le sens inverse du zoom n a plus de bouton : le curseur couvre les
    #  deux sens d un seul geste)
    assert "nudgeRange(textSizeSlider, 2, 24, 72)" in js
    assert "nudgeRange(textSizeSlider, -2, 24, 72)" in js
    assert "nudgeRange(lineHeightSlider, 10, 80, 200)" in js
    assert "nudgeRange(lineHeightSlider, -10, 80, 200)" in js


def test_un_stepper_repasse_par_levenement_du_curseur(js):
    """Aucune logique de rendu n'est dupliquée : le stepper écrit la valeur
    puis laisse le gestionnaire d'origine faire son travail."""
    bloc = js[js.index("function nudgeRange(input, delta, min, max)"):]
    bloc = bloc[:bloc.index("\n        /** Interligne")]
    assert "input.dispatchEvent(new Event('input', { bubbles: true }));" in bloc


def test_les_curseurs_dorigine_restent_focalisables(html, css):
    """`clip` et pas `display:none` : un `input[type=range]` masqué par
    `display` ne reçoit ni focus clavier ni évènement `input`."""
    assert 'class="wiz-hidden-range"' in html
    bloc = re.search(r"\.wiz-hidden-range\s*\{(.*?)\}", css, re.S).group(1)
    assert "clip-path: inset(50%)" in bloc
    assert "display: none" not in bloc


def test_le_curseur_de_zoom_garde_sa_course_complete(html):
    """Le stepper s'arrête à 100 % (en dessous, le média ne couvre plus le
    cadre) ; le curseur fin, lui, descend toujours à 50 % — aucune valeur
    atteignable avant ce lot ne devient inatteignable."""
    assert 'id="image-scale" min="50" max="200"' in html


# ---------------------------------------------------------------------------
# 7. La phrase du Tri rapide
# ---------------------------------------------------------------------------

def test_la_phrase_du_tri_rapide_preremplit_le_bandeau(js, html):
    assert 'id="wiz-prefill-note"' in html
    assert "Pr&eacute;-rempli avec ta phrase du Tri rapide" in html
    assert "state.phrase && !state.phraseUsed && !state.text" in js


def test_le_preremplissage_necrase_jamais_une_saisie(js):
    """`!state.text` dans la garde : un bandeau déjà écrit reste intact."""
    bloc = js[js.index("function goStep(n)"):]
    bloc = bloc[:bloc.index("function setupWizard()")]
    assert "!state.text" in bloc
    assert "state.phraseUsed = true;" in bloc


def test_un_media_ouvert_depuis_la_galerie_apporte_sa_phrase(js):
    """`?media_id=` ne portait que l'identifiant : sans la fiche, le même
    média arrivait à l'étape Texte sans sa phrase selon la porte d'entrée."""
    bloc = js[js.index("function checkMediaParam()"):]
    bloc = bloc[:bloc.index("\n        // ====")]
    assert "/api/viewer/media/" in bloc


# ---------------------------------------------------------------------------
# 8. Régressions de comportement corrigées en passant
# ---------------------------------------------------------------------------

def test_le_style_pov_contour_noir_est_atteignable(js):
    """Bug préexistant : le ternaire `=== 'dark' ? 'dark' : 'light'` rendait
    'outline' — le style natif TikTok le plus courant — inatteignable dès le
    premier clic dans le groupe."""
    assert "=== 'dark' ? 'dark' : 'light'" not in js
    assert "POV_STYLES[btn.dataset.povstyle] ? btn.dataset.povstyle : 'outline'" in js


def test_le_restylage_pov_repose_le_contour(js):
    bloc = js[js.index("function restylePovObject()"):]
    bloc = bloc[:bloc.index("\n        // ====")]
    assert "povStrokeProps(style, p.povObj.fontSize)" in bloc


# ---------------------------------------------------------------------------
# 9. Hygiène
# ---------------------------------------------------------------------------

def test_laccordeon_remplace_ne_laisse_aucun_reste(html, css, js):
    """L'ancien accordéon d'outils : gabarit, styles ET script."""
    assert "group__fold" not in html
    assert "group__fold" not in css
    assert "setupMobileUx" not in js
    assert "sidebar__scroll" not in css
    assert "sidebar__actions" not in css


def test_aucune_boite_systeme(js):
    """Ni alert, ni confirm, ni prompt — la règle de l'écran depuis le lot
    précédent, que ce lot ne doit pas défaire."""
    for boite in ("alert(", "confirm(", "prompt("):
        occurrences = [
            ligne for ligne in js.splitlines()
            if boite in ligne and not ligne.strip().startswith("//")
        ]
        assert occurrences == [], (boite, occurrences)


def test_aucun_appel_a_un_cdn(html):
    """Reprise du garde-fou du lot D : ce gabarit a été réécrit en entier.

    On regarde les URL RÉELLEMENT CHARGÉES (`src` / `href`) et non le texte
    brut : les commentaires Jinja du gabarit citent nommément le CDN qu'on a
    quitté, et cette mémoire-là doit pouvoir rester.
    """
    charges = re.findall(r'(?:src|href)="([^"]+)"', html)
    for url in charges:
        for hote in ("cdnjs.cloudflare.com", "fonts.googleapis.com",
                     "fonts.gstatic.com", "unpkg.com", "jsdelivr"):
            assert hote not in url, (hote, url)
    # Seul appel distant admis : l'API Google, non rapatriable (Picker OAuth).
    distants = [u for u in charges if u.startswith("http")]
    assert distants == ["https://apis.google.com/js/api.js"], distants


def test_le_gabarit_sert_toujours_fabric_en_local(html):
    assert "/static/vendor/fabric-5.3.1.min.js" in html
