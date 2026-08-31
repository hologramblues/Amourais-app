"""
MODULE DE TEST — SOCLE DE LA REFONTE UI « PAS-À-PAS ».

Ce lot ne dessine aucun écran : il pose les fondations que les deux écrans
(galerie et éditeur) consommeront. Il y en a quatre, et chacune a sa section.

────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE ÉTABLIT
────────────────────────────────────────────────────────────────────────────
1. `tokens.css` porte bien la palette Modernist (fond, surface, texte,
   accent, radius 0, séparateurs 2px) et garde les NOMS de jetons
   historiques — un renommage casserait les 6 écrans qui les consomment.
2. La typographie Archivo est VENDORISÉE : les .woff2 et la licence OFL sont
   sur disque, Flask les sert, `tokens.css` les déclare, et plus AUCUN
   gabarit n'appelle Google Fonts. Ce dépôt a déjà perdu son éditeur à cause
   d'un CDN ; la règle est écrite ici pour qu'on ne la reperde pas.
3. Le thème sombre est complet : tout jeton défini dans un bloc sombre
   existe aussi sur `:root`, et les deux blocs sombres (suivi système et
   choix manuel) sont rigoureusement identiques. Un jeton qui n'existerait
   que sous `@media (prefers-color-scheme: dark)` disparaîtrait dès que
   l'utilisateur bascule le thème à la main.
4. Le CONTRASTE est MESURÉ, pas affirmé : la section 4 relit les vraies
   valeurs de `tokens.css` et calcule les rapports WCAG. C'est le seul test
   du module qui peut rougir sur un simple « on éclaircit un peu le gris ».
5. La colonne `phrase` (seul ajout de données du lot) est migrable sur une
   base préexistante sans perdre une ligne, exposée en lecture par l'API
   viewer et écrivable par un endpoint calqué sur celui de la notation.

Aucun test de ce module ne touche `data/` : la base « legacy » de la section
5 est fabriquée dans `tmp_path`, et `app.db.DB_PATH` est monkeypatché.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import app.db as app_db
from app.db import Base, MediaItem

pytestmark = [pytest.mark.static_check]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "app" / "web" / "static"
TEMPLATES_DIR = PROJECT_ROOT / "app" / "web" / "templates"
FONTS_DIR = STATIC_DIR / "vendor" / "fonts"
TOKENS = STATIC_DIR / "tokens.css"

#: Les 8 pages du produit. Toutes chargent `tokens.css` : c'est ce qui rend
#: le fichier apte à porter les @font-face d'Archivo.
GABARITS = [
    "layout.html",
    "viewer.html",
    "editor.html",
    "calendar.html",
    "analytics.html",
    "dashboard.html",
    "jobs.html",
    "profiles.html",
    "settings.html",
]


# ===========================================================================
# Lecture de tokens.css — un mini-analyseur, pas une regex jetable
# ===========================================================================
#
# Les trois sections qui suivent interrogent le MÊME fichier sous trois angles
# (valeurs, complétude des thèmes, contraste). Elles partagent donc un seul
# analyseur, sinon elles divergeraient à la première retouche.


def _texte_tokens() -> str:
    return TOKENS.read_text(encoding="utf-8")


def _sans_commentaires(css: str) -> str:
    """Retire les /* … */ : un jeton cité en commentaire n'est pas déclaré."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _bloc(css: str, entete: str) -> str:
    """Corps du premier bloc dont l'en-tête est `entete` (accolades équilibrées)."""
    debut = css.index(entete)
    ouvrante = css.index("{", debut)
    profondeur = 0
    for i in range(ouvrante, len(css)):
        if css[i] == "{":
            profondeur += 1
        elif css[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                return css[ouvrante + 1:i]
    raise AssertionError(f"bloc non refermé pour {entete!r}")


def _declarations(corps: str) -> dict[str, str]:
    """{--jeton: valeur} d'un corps de règle, sous-blocs @media exclus."""
    corps = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", corps)
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", corps)
    }


def _resoudre(jetons: dict[str, str]) -> dict[str, str]:
    """Remplace `var(--x)` par la valeur de `--x`, en chaîne.

    La moitié des alias Modernist sont des renvois (`--color-bg:
    var(--bg-canvas)`) : sans résolution, les sections « palette » et
    « contraste » compareraient des noms au lieu de comparer des couleurs.
    """
    resolus = dict(jetons)
    for _ in range(8):  # profondeur de chaînage largement suffisante
        change = False
        for nom, valeur in list(resolus.items()):
            m = re.fullmatch(r"var\((--[a-zA-Z0-9-]+)\)", valeur.strip())
            if m and m.group(1) in resolus:
                cible = resolus[m.group(1)]
                if cible != valeur:
                    resolus[nom] = cible
                    change = True
        if not change:
            break
    return resolus


def _jetons_clair() -> dict[str, str]:
    css = _sans_commentaires(_texte_tokens())
    return _resoudre(_declarations(_bloc(css, ":root {")))


def _jetons_sombre_media() -> dict[str, str]:
    css = _sans_commentaires(_texte_tokens())
    return _declarations(_bloc(css, ':root:not([data-theme="light"])'))


def _jetons_sombre_manuel() -> dict[str, str]:
    css = _sans_commentaires(_texte_tokens())
    return _declarations(_bloc(css, ':root[data-theme="dark"]'))


# ===========================================================================
# 1. PALETTE MODERNIST — les valeurs du handoff, sous les noms du dépôt
# ===========================================================================


@pytest.mark.parametrize(
    ("jeton", "valeur"),
    [
        # Les quatre couleurs nommées dans le handoff, à la lettre.
        ("--color-bg", "#f3f2f2"),
        ("--color-surface", "#eae9e9"),
        ("--color-accent", "#ec3013"),
        # Bornes de la rampe accent citées par le handoff : « 100 #fff2ef,
        # 700 #ae1800 pour le texte accent ».
        ("--color-accent-100", "#fff2ef"),
        ("--color-accent-700", "#ae1800"),
        # Les rôles historiques portent désormais ces valeurs-là.
        ("--bg-canvas", "#f3f2f2"),
        ("--bg-1", "#eae9e9"),
        ("--fg-1", "#201e1d"),
    ],
)
def test_la_palette_modernist_est_bien_celle_du_handoff(jeton, valeur):
    assert _jetons_clair()[jeton] == valeur


def test_les_noms_de_jetons_historiques_survivent_tous():
    """Le handoff renomme les couleurs ; les 6 écrans, eux, ne sont pas repris.

    Renommer aurait fait tomber chaque `var(--bg-1)` sur son fallback (ou sur
    rien). Les alias Modernist s'AJOUTENT, ils ne remplacent pas.
    """
    jetons = _jetons_clair()
    historiques = {
        "--font-ui", "--font-mono",
        "--bg-canvas", "--bg-1", "--bg-2", "--bg-3", "--bg-inset",
        "--border-1", "--border-2", "--border-3", "--border-w",
        "--fg-1", "--fg-2", "--fg-3", "--fg-4",
        "--fg-on-accent", "--fg-on-solid",
        "--accent", "--accent-hover", "--accent-active",
        "--accent-soft", "--accent-border",
        "--accent-solid", "--accent-solid-hover", "--accent-solid-active",
        "--success", "--warning", "--danger", "--info",
        "--radius-control", "--radius-block", "--radius-container",
        "--radius-pill",
        "--sp-1", "--sp-2", "--sp-4", "--sp-6", "--sp-7", "--sp-8",
        "--header-h", "--tap-min",
        "--shadow-low", "--shadow-medium", "--shadow-high",
        "--nav-bg", "--nav-accent", "--nav-active-fg",
        "--s-bg", "--s-text", "--s-accent",
    }
    assert historiques - set(jetons) == set()


def test_les_alias_modernist_sont_disponibles_pour_les_ecrans_a_venir():
    """Les maquettes nomment les couleurs ainsi : les deux écrans les copieront."""
    jetons = set(_jetons_clair())
    attendus = {
        "--color-bg", "--color-surface", "--color-text",
        "--color-accent", "--color-accent-2", "--color-divider",
        "--font-heading", "--font-body", "--font-heading-weight",
        "--radius-sm", "--radius-md", "--radius-lg",
        "--shadow-sm", "--shadow-md", "--shadow-lg",
        "--space-1", "--space-2", "--space-3", "--space-4",
        "--space-6", "--space-8",
    }
    attendus |= {f"--color-neutral-{n}" for n in range(100, 1000, 100)}
    attendus |= {f"--color-accent-{n}" for n in range(100, 1000, 100)}
    attendus |= {f"--color-accent-2-{n}" for n in range(100, 1000, 100)}
    assert attendus - jetons == set()


def test_aucun_rayon_nest_arrondi():
    """« Radius 0 partout » — règle explicite du système Modernist.

    Y compris `--radius-pill`, qui vaut encore 9999px dans bien des systèmes :
    30 déclarations le référencent, et chacune doit devenir un angle droit.
    """
    jetons = _jetons_clair()
    rayons = {k: v for k, v in jetons.items() if k.startswith("--radius-")}
    assert len(rayons) >= 7, "les 4 rayons historiques + les 3 alias Modernist"
    for nom, valeur in rayons.items():
        assert valeur in ("0", "0px"), f"{nom} vaut {valeur!r}, pas 0"


def test_le_separateur_fait_deux_pixels_et_ne_saffine_jamais():
    """« Règles de séparation fortes : 2px --color-divider ».

    `--border-w` reste à 1px (et descend même à 0.5px sur écran dense) parce
    qu'il habille les bordures de CONTRÔLE. Le séparateur de STRUCTURE est un
    autre jeton, et lui ne bouge sous aucune media query — sinon la règle
    Modernist s'évaporerait sur tout écran retina, c'est-à-dire partout.
    """
    assert _jetons_clair()["--divider-w"] == "2px"
    css = _sans_commentaires(_texte_tokens())
    for media in re.findall(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", css):
        assert "--divider-w" not in media, (
            "--divider-w redéfini dans une media query : le séparateur "
            "Modernist doit faire 2px sur TOUS les écrans"
        )


def test_les_libelles_de_boutons_sont_alignes_a_gauche():
    """Règle de système explicite du handoff, pas une préférence.

    Elle est portée par un JETON pour être appliquée mécaniquement par les
    deux écrans à venir, plutôt que retenue de mémoire écran par écran.
    """
    assert _jetons_clair()["--btn-label-align"] == "left"


# ===========================================================================
# 2. ARCHIVO VENDORISÉE — plus aucune dépendance à un CDN de polices
# ===========================================================================
#
# Même exigence que pour Fabric.js et Montserrat avant elle : une panne de CDN
# a déjà tué l'éditeur une fois. La police est sur disque, sa licence à côté.

FICHIERS_ARCHIVO = ["Archivo-latin.woff2", "Archivo-latin-ext.woff2"]


@pytest.mark.parametrize("nom", FICHIERS_ARCHIVO)
def test_la_police_archivo_est_vendorisee(nom):
    fichier = FONTS_DIR / nom
    assert fichier.is_file(), f"{nom} manquant dans vendor/fonts/"
    # En-tête WOFF2 : 'wOF2'. Un HTML d'erreur enregistré à la place du
    # binaire passerait tous les autres tests de ce module.
    assert fichier.read_bytes()[:4] == b"wOF2", f"{nom} n'est pas un WOFF2"
    assert fichier.stat().st_size > 10_000


def test_la_licence_ofl_darchivo_accompagne_la_police():
    """L'OFL exige que son texte accompagne la police distribuée.

    Fichier SÉPARÉ de `OFL.txt` : celui-ci est la licence de Montserrat, et
    deux polices distinctes ne partagent pas une notice de copyright.
    """
    licence = FONTS_DIR / "OFL-Archivo.txt"
    assert licence.is_file(), "OFL-Archivo.txt manquant à côté de la police"
    texte = licence.read_text(encoding="utf-8")
    assert "SIL Open Font License" in texte
    assert "Archivo" in texte


@pytest.mark.parametrize("nom", FICHIERS_ARCHIVO + ["OFL-Archivo.txt"])
def test_flask_sert_bien_les_fichiers_darchivo(client, nom):
    """Les @font-face pointent sur /static/vendor/fonts/… : Flask doit suivre."""
    reponse = client.get(f"/static/vendor/fonts/{nom}")
    try:
        # send_file laisse le descripteur ouvert : la suite convertit les
        # ResourceWarning en échecs, on ferme donc explicitement.
        assert reponse.status_code == 200
        assert len(reponse.data) > 1000
    finally:
        reponse.close()


def test_tokens_css_declare_les_font_face_darchivo():
    """Sans @font-face, la police vendorisée ne serait jamais chargée."""
    css = _sans_commentaires(_texte_tokens())
    assert css.count("@font-face") == 2
    for nom in FICHIERS_ARCHIVO:
        assert f"/static/vendor/fonts/{nom}" in css
    # Archivo est VARIABLE : un seul fichier par sous-ensemble couvre 400 à
    # 800. Sans la plage, le navigateur synthétiserait le gras.
    assert css.count("font-weight: 400 800") == 2


def test_la_pile_de_polices_commence_par_archivo():
    jetons = _jetons_clair()
    assert jetons["--font-ui"].startswith('"Archivo"')
    # Une seule famille dans tout le produit : les alias Modernist renvoient
    # sur la pile UI, ils n'en ouvrent pas une seconde.
    for alias in ("--font-heading", "--font-body"):
        assert jetons[alias] == jetons["--font-ui"]
    assert jetons["--font-heading-weight"] == "800"


@pytest.mark.parametrize("gabarit", GABARITS)
def test_plus_aucun_gabarit_nappelle_google_fonts(gabarit):
    """Une feuille de style tierce dans le <head> bloque le rendu de la page.

    Elle le bloquait ici pour Inter et JetBrains Mono, dont plus une seule
    règle du projet ne veut depuis le passage à Archivo.
    """
    texte = (TEMPLATES_DIR / gabarit).read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in texte
    assert "fonts.gstatic.com" not in texte


@pytest.mark.parametrize("gabarit", GABARITS)
def test_chaque_page_charge_tokens_css(gabarit):
    """C'est ce qui rend `tokens.css` apte à porter les @font-face.

    Les 4 pages du gabarit commun héritent du <head> de `layout.html` ; les 5
    autres ont leur propre <head> et doivent le déclarer elles-mêmes.
    """
    texte = (TEMPLATES_DIR / gabarit).read_text(encoding="utf-8")
    if "{% extends" in texte:
        # Ces pages n'ont pas de <head> propre : elles héritent de celui de
        # `layout.html`, dont le test frère vérifie qu'il charge tokens.css.
        assert 'extends "layout.html"' in texte
        return
    assert "tokens.css" in texte


# ===========================================================================
# 3. THÈME SOMBRE — dérivé, complet, et identique dans ses deux blocs
# ===========================================================================


def test_aucune_couleur_nexiste_uniquement_en_theme_sombre():
    """Un jeton défini SEULEMENT sous @media disparaît dès la bascule manuelle.

    `[data-theme="light"]` désactive le bloc @media ; si une couleur n'était
    déclarée que là, elle vaudrait alors « rien » et la règle qui la consomme
    tomberait sur son fallback — ou sur du noir.
    """
    clair = set(_jetons_clair())
    for nom, sombre in (
        ("suivi système", _jetons_sombre_media()),
        ("choix manuel", _jetons_sombre_manuel()),
    ):
        orphelins = set(sombre) - clair
        assert orphelins == set(), (
            f"jetons définis uniquement dans le bloc sombre « {nom} » : {orphelins}"
        )


def test_les_deux_blocs_sombres_sont_rigoureusement_identiques():
    """Le suivi système et le choix manuel doivent produire le MÊME thème.

    C'est le piège classique : on retouche un bloc, on oublie l'autre, et le
    thème change selon qu'on l'a choisi ou subi.
    """
    assert _jetons_sombre_media() == _jetons_sombre_manuel()


def test_le_theme_sombre_garde_la_meme_geometrie_et_le_meme_accent():
    """Le handoff ne traite que le clair : le sombre est DÉRIVÉ, pas réinventé.

    Rayons, épaisseur de séparateur, typographie et espacements sont communs
    aux deux thèmes — seules les couleurs basculent.
    """
    sombre = _jetons_sombre_media()
    interdits = {
        k for k in sombre
        if k.startswith(("--radius-", "--sp-", "--space-", "--font-", "--text-"))
        or k in ("--divider-w", "--border-w", "--header-h", "--tap-min")
    }
    assert interdits == set(), f"géométrie redéfinie en thème sombre : {interdits}"
    # Le rouge de marque ne bouge pas d'un thème à l'autre.
    assert sombre["--color-accent"] == _jetons_clair()["--color-accent"]


def test_le_theme_sombre_couvre_tous_les_roles_de_couleur():
    """Un rôle oublié en sombre garderait sa valeur CLAIRE sur fond noir."""
    sombre = set(_jetons_sombre_media())
    obligatoires = {
        "--bg-canvas", "--bg-1", "--bg-2", "--bg-3", "--bg-inset",
        "--border-1", "--border-2", "--border-3",
        "--fg-1", "--fg-2", "--fg-3", "--fg-4",
        "--fg-on-accent", "--fg-on-solid",
        "--accent", "--accent-hover", "--accent-active",
        "--accent-soft", "--accent-border",
        "--accent-solid", "--accent-solid-hover", "--accent-solid-active",
        "--success", "--success-soft", "--warning", "--warning-soft",
        "--danger", "--danger-hover", "--danger-soft",
        "--info", "--info-soft",
    }
    obligatoires |= {f"--color-neutral-{n}" for n in range(100, 1000, 100)}
    obligatoires |= {f"--color-accent-{n}" for n in range(100, 1000, 100)}
    assert obligatoires - sombre == set()


# ===========================================================================
# 4. CONTRASTE — mesuré sur les vraies valeurs du fichier
# ===========================================================================
#
# Le seul test du module qui rougit sur « on éclaircit un peu le gris ». Les
# rapports sont recalculés depuis tokens.css à chaque exécution : aucune
# valeur n'est recopiée ici, donc rien ne peut se désynchroniser.

AA_TEXTE = 4.5   # WCAG 1.4.3, texte normal
AA_GRAPHIQUE = 3.0  # WCAG 1.4.11, composants non textuels

#: Le fond le plus DÉFAVORABLE de chaque thème n'est pas le fond de page :
#: c'est `--bg-3`, le survol de rangée, sur lequel se lisent précisément les
#: métadonnées sous vignette et les en-têtes de colonne.
FONDS = ["--bg-canvas", "--bg-1", "--bg-2", "--bg-3", "--bg-inset"]
TEXTES = ["--fg-1", "--fg-2", "--fg-3", "--accent",
          "--success", "--warning", "--danger", "--info"]


def _canal(v: int) -> float:
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hexa: str) -> float:
    h = hexa.strip().lstrip("#")
    assert re.fullmatch(r"[0-9a-fA-F]{6}", h), f"couleur non littérale : {hexa!r}"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _canal(r) + 0.7152 * _canal(g) + 0.0722 * _canal(b)


def _rapport(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _palette(theme: str) -> dict[str, str]:
    """Palette EFFECTIVE d'un thème : le clair, écrasé par le bloc sombre."""
    css = _sans_commentaires(_texte_tokens())
    palette = _declarations(_bloc(css, ":root {"))
    if theme == "sombre":
        palette.update(_declarations(_bloc(css, ':root:not([data-theme="light"])')))
    return _resoudre(palette)


@pytest.mark.parametrize("theme", ["clair", "sombre"])
@pytest.mark.parametrize("texte", TEXTES)
@pytest.mark.parametrize("fond", FONDS)
def test_tout_texte_tient_le_seuil_aa_sur_tous_les_fonds(theme, texte, fond):
    palette = _palette(theme)
    rapport = _rapport(palette[texte], palette[fond])
    assert rapport >= AA_TEXTE, (
        f"{texte} sur {fond} en thème {theme} : {rapport:.2f}:1 < {AA_TEXTE}"
    )


@pytest.mark.parametrize("theme", ["clair", "sombre"])
@pytest.mark.parametrize(
    ("texte", "fond"),
    [
        ("--fg-on-accent", "--accent-solid"),
        ("--fg-on-accent", "--accent-solid-hover"),
        ("--fg-on-accent", "--accent-solid-active"),
        ("--fg-on-solid", "--success"),
        ("--fg-on-solid", "--warning"),
        ("--fg-on-solid", "--danger"),
        ("--fg-on-solid", "--info"),
        ("--accent", "--accent-soft"),
        ("--success", "--success-soft"),
        ("--warning", "--warning-soft"),
        ("--danger", "--danger-soft"),
        ("--info", "--info-soft"),
    ],
)
def test_tout_texte_pose_sur_un_aplat_tient_le_seuil_aa(theme, texte, fond):
    palette = _palette(theme)
    rapport = _rapport(palette[texte], palette[fond])
    assert rapport >= AA_TEXTE, (
        f"{texte} sur {fond} en thème {theme} : {rapport:.2f}:1 < {AA_TEXTE}"
    )


@pytest.mark.parametrize("theme", ["clair", "sombre"])
@pytest.mark.parametrize("fond", ["--bg-canvas", "--bg-1", "--bg-2", "--bg-3"])
def test_le_separateur_de_structure_reste_visible(theme, fond):
    """Une règle de 2px qu'on ne voit pas ne sépare rien (WCAG 1.4.11)."""
    palette = _palette(theme)
    rapport = _rapport(palette["--border-2"], palette[fond])
    assert rapport >= AA_GRAPHIQUE, (
        f"--border-2 sur {fond} en thème {theme} : {rapport:.2f}:1 < {AA_GRAPHIQUE}"
    )


@pytest.mark.parametrize("theme", ["clair", "sombre"])
@pytest.mark.parametrize("jeton", ["--color-accent", "--accent-solid"])
def test_le_rouge_de_marque_reste_perceptible_comme_graphique(theme, jeton):
    """Anneau de sélection, soulignement d'étape, remplissage d'étoile.

    Ce rouge ne porte JAMAIS de texte — il n'a donc à tenir que le seuil des
    composants non textuels. C'est exactement pourquoi il est séparé de
    `--accent` (texte) et de `--accent-solid` (aplat porteur de libellé).
    `--accent-border` n'est PAS de la partie : c'est la teinte compagnon de
    `--accent-soft`, décorative, qui cerne des surfaces déjà teintées.
    """
    palette = _palette(theme)
    rapport = _rapport(palette[jeton], palette["--bg-canvas"])
    assert rapport >= AA_GRAPHIQUE, (
        f"{jeton} sur --bg-canvas en thème {theme} : {rapport:.2f}:1"
    )


# ===========================================================================
# 5. COLONNE `phrase` — seul ajout de données du lot
# ===========================================================================


#: `profiles` doit exister : `_migrate_add_columns` migre les DEUX tables et
#: relaie toute erreur qui n'est pas un doublon de colonne — « no such table »
#: en est une, et c'est voulu (une base à moitié migrée ne doit pas démarrer).
LEGACY_PROFILES = """
CREATE TABLE profiles (
    id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (id)
)
"""

LEGACY_MEDIA_SANS_PHRASE = """
CREATE TABLE media_items (
    id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
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
    PRIMARY KEY (id)
)
"""


@pytest.fixture
def base_legacy_peuplee(tmp_path, monkeypatch):
    """Base préexistante SANS `phrase`, contenant 3 médias déjà en place.

    C'est la forme de la base de production : `create_all()` saute une table
    qui existe, donc seule `_migrate_add_columns` peut y ajouter la colonne.
    """
    chemin = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(chemin))
    try:
        conn.execute(LEGACY_PROFILES)
        conn.execute(LEGACY_MEDIA_SANS_PHRASE)
        conn.executemany(
            "INSERT INTO media_items (id, profile_id, platform, post_id, "
            "media_type, media_url, caption, status, retry_count, discovered_at) "
            "VALUES (?, 1, 'reddit', ?, 'image', ?, ?, 'downloaded', 0, 1700000000)",
            [
                (1, "p1", "https://x.invalid/1.jpg", "légende accentuée é à ç"),
                (2, "p2", "https://x.invalid/2.jpg", None),
                (3, "p3", "https://x.invalid/3.jpg", "troisième"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(app_db, "DB_PATH", chemin)
    return chemin


def _colonnes(chemin: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(chemin))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_phrase_est_declaree_nullable_sur_le_modele():
    """Les médias déjà en base n'ont aucune phrase : la colonne doit l'admettre."""
    colonne = Base.metadata.tables["media_items"].columns["phrase"]
    assert colonne.nullable is True
    assert colonne.default is None and colonne.server_default is None


def test_la_migration_ajoute_phrase_a_une_base_preexistante(base_legacy_peuplee):
    assert "phrase" not in _colonnes(base_legacy_peuplee, "media_items")
    app_db._migrate_add_columns()
    assert "phrase" in _colonnes(base_legacy_peuplee, "media_items")


def test_la_migration_ne_perd_aucune_ligne_et_laisse_la_base_saine(
    base_legacy_peuplee,
):
    """Non destructive par construction — encore faut-il le vérifier.

    `ALTER TABLE ADD COLUMN` sans DEFAULT ni NOT NULL ne réécrit que l'en-tête
    de la table. On contrôle donc les trois choses qui pourraient démentir
    cette théorie : le compte de lignes, le contenu, et `integrity_check`.
    """
    conn = sqlite3.connect(str(base_legacy_peuplee))
    try:
        avant = conn.execute(
            "SELECT id, post_id, caption FROM media_items ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    app_db._migrate_add_columns()

    conn = sqlite3.connect(str(base_legacy_peuplee))
    try:
        apres = conn.execute(
            "SELECT id, post_id, caption FROM media_items ORDER BY id"
        ).fetchall()
        phrases = conn.execute("SELECT phrase FROM media_items").fetchall()
        integrite = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    assert apres == avant, "la migration a modifié des lignes existantes"
    assert len(apres) == 3
    assert phrases == [(None,), (None,), (None,)], "la colonne doit naître vide"
    assert integrite == "ok"


def test_la_migration_de_phrase_est_idempotente(base_legacy_peuplee):
    """Deux boots d'affilée : le second ne doit pas lever sur « duplicate column »."""
    app_db._migrate_add_columns()
    app_db._migrate_add_columns()
    assert "phrase" in _colonnes(base_legacy_peuplee, "media_items")


def test_la_migration_couvre_toutes_les_colonnes_du_modele_media_items():
    """Garde-fou général, pas seulement pour `phrase`.

    Une colonne ajoutée au modèle et oubliée dans `_migrate_add_columns` ne
    casse RIEN sur une base neuve — et fait exploser toute requête ORM sur la
    base de production. C'est le risque #1 d'AUDIT.md, reproduit à l'identique
    à chaque nouvelle colonne tant qu'aucun test ne le tient.
    """
    import inspect

    source = inspect.getsource(app_db._migrate_add_columns)
    migrees = set(re.findall(r'\("media_items",\s*"([a-z_0-9]+)"', source))
    declarees = {c.name for c in Base.metadata.tables["media_items"].columns}
    # Les colonnes du DDL d'origine (antérieures à toute migration) n'ont
    # jamais eu à être ajoutées : on ne vérifie que celles que la migration
    # connaît déjà, plus `phrase`.
    assert "phrase" in migrees
    assert migrees <= declarees, (
        f"colonnes migrées mais absentes du modèle : {migrees - declarees}"
    )


# --- API viewer -------------------------------------------------------------


def _poster_phrase(client, media_id, charge):
    return client.post(
        f"/api/viewer/media/{media_id}/phrase",
        data=json.dumps(charge),
        content_type="application/json",
    )


def test_la_liste_expose_la_phrase(client, make_media_item):
    """Le Tri rapide empile des dizaines de cartes : il lit la LISTE, pas les
    fiches une par une."""
    make_media_item(status="downloaded", phrase="quand tu crois que c'est fini")
    item = client.get("/api/viewer/media").get_json()["items"][0]
    assert item["phrase"] == "quand tu crois que c'est fini"


def test_la_liste_expose_une_phrase_nulle_quand_personne_na_trie(
    client, make_media_item
):
    """L'application doit fonctionner colonne vide — c'est l'état de départ."""
    make_media_item(status="downloaded")
    item = client.get("/api/viewer/media").get_json()["items"][0]
    assert "phrase" in item and item["phrase"] is None


def test_la_fiche_expose_la_phrase(client, make_media_item):
    """L'étape « Texte » de l'éditeur la relit pour pré-remplir le bandeau."""
    media = make_media_item(phrase="pov : tu lis les tests")
    fiche = client.get(f"/api/viewer/media/{media.id}").get_json()
    assert fiche["phrase"] == "pov : tu lis les tests"


def test_ecrire_une_phrase_la_persiste_et_la_renvoie(
    client, make_media_item, db_session
):
    media = make_media_item()
    reponse = _poster_phrase(client, media.id, {"phrase": "  ça passe crème  "})

    assert reponse.status_code == 200
    assert reponse.get_json() == {"id": media.id, "phrase": "ça passe crème"}

    db_session.expire_all()
    assert db_session.get(MediaItem, media.id).phrase == "ça passe crème"


def test_reecrire_une_phrase_remplace_la_precedente(client, make_media_item):
    media = make_media_item(phrase="première")
    _poster_phrase(client, media.id, {"phrase": "seconde"})
    fiche = client.get(f"/api/viewer/media/{media.id}").get_json()
    assert fiche["phrase"] == "seconde"


@pytest.mark.parametrize("vide", ["", "   ", None])
def test_effacer_une_phrase_la_ramene_a_null_et_pas_a_une_chaine_vide(
    client, make_media_item, vide
):
    """Un seul état « pas de phrase » en base, donc un seul test côté client."""
    media = make_media_item(phrase="à effacer")
    reponse = _poster_phrase(client, media.id, {"phrase": vide})
    assert reponse.status_code == 200
    assert reponse.get_json()["phrase"] is None
    assert client.get(f"/api/viewer/media/{media.id}").get_json()["phrase"] is None


def test_une_phrase_trop_longue_est_refusee(client, make_media_item):
    """Un bandeau de meme tient en deux lignes ; la borne protège aussi la base."""
    from app.web.viewer_api import PHRASE_MAX

    media = make_media_item()
    reponse = _poster_phrase(client, media.id, {"phrase": "x" * (PHRASE_MAX + 1)})
    assert reponse.status_code == 400
    assert client.get(f"/api/viewer/media/{media.id}").get_json()["phrase"] is None


def test_une_phrase_exactement_a_la_borne_est_acceptee(client, make_media_item):
    from app.web.viewer_api import PHRASE_MAX

    media = make_media_item()
    reponse = _poster_phrase(client, media.id, {"phrase": "x" * PHRASE_MAX})
    assert reponse.status_code == 200
    assert len(reponse.get_json()["phrase"]) == PHRASE_MAX


def test_une_phrase_qui_nest_pas_une_chaine_est_refusee(client, make_media_item):
    media = make_media_item()
    assert _poster_phrase(client, media.id, {"phrase": 42}).status_code == 400


def test_un_corps_sans_champ_phrase_est_refuse(client, make_media_item):
    """Distinct de `{"phrase": null}`, qui EFFACE volontairement."""
    media = make_media_item()
    assert _poster_phrase(client, media.id, {"note": 5}).status_code == 400


def test_ecrire_une_phrase_sur_un_media_inconnu_renvoie_404(client):
    """Même contrat que la notation, à la ligne près."""
    assert _poster_phrase(client, 999_999, {"phrase": "fantôme"}).status_code == 404


def test_la_phrase_ne_touche_pas_a_la_notation(client, make_media_item):
    """Deux durées de vie, deux tables, deux routes : écrire l'une ne doit rien
    faire à l'autre."""
    media = make_media_item()
    client.post(
        f"/api/viewer/media/{media.id}/rate",
        data=json.dumps({"user_name": "jeremie", "rating": 4}),
        content_type="application/json",
    )
    _poster_phrase(client, media.id, {"phrase": "les deux à la fois"})

    fiche = client.get(f"/api/viewer/media/{media.id}").get_json()
    assert fiche["phrase"] == "les deux à la fois"
    assert fiche["avg_rating"] == 4
    assert len(fiche["ratings"]) == 1
