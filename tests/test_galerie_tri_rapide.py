"""
Refonte UI — la galerie : re-style Modernist du desktop + mode « Tri rapide ».

CE QUE CE MODULE PROTÈGE
------------------------
Le lot n'a RIEN retiré au viewer : il a re-stylé des patrons existants et
ajouté un écran. Le risque n'est donc pas « c'est laid », c'est
« quelque chose a disparu ». Deux disparitions sont possibles, et toutes
les deux sont silencieuses :

  1. Un contrôle du gabarit s'évapore pendant une réécriture de la barre
     d'outils. La page répond toujours 200, mais un réglage n'est plus
     atteignable — et personne ne s'en aperçoit avant l'usage réel.
  2. `viewer.js` demande un `getElementById` que le gabarit ne sert plus.
     Un `null` ne lève rien à l'analyse : le script meurt à la première
     déréférence et l'écran entier cesse de répondre.

§1 est le garde-fou contre (2) : il relit viewer.js, extrait TOUS les
identifiants qu'il demande et vérifie que viewer.html les sert. §5 est le
garde-fou contre (1) : il énumère nommément l'inventaire fonctionnel que
le handoff déclare intouchable.

CE QU'IL NE PROTÈGE PAS
-----------------------
Rien ici n'exécute de JavaScript. Le geste de glissé, la persistance de
la note et de la phrase, et leur survie à un rechargement ont été
vérifiés AU NAVIGATEUR (événements Pointer réels dispatchés sur la
carte, relecture de `/api/viewer/media/<id>` après coup, puis rechargement
complet de la page — cf. rapport de lot). Les tests ci-dessous figent les
invariants STATIQUES qui rendent ces comportements possibles : les
constantes du geste, les deux routes d'écriture, et la présence des
cibles dans le gabarit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.viewer

RACINE = Path(__file__).resolve().parents[1]
VIEWER_HTML = RACINE / "app" / "web" / "templates" / "viewer.html"
VIEWER_CSS = RACINE / "app" / "web" / "static" / "viewer.css"
VIEWER_JS = RACINE / "app" / "web" / "static" / "viewer.js"
EDITOR_CSS = RACINE / "app" / "web" / "static" / "editor.css"
VIEWER_API = RACINE / "app" / "web" / "viewer_api.py"


@pytest.fixture(scope="module")
def html() -> str:
    return VIEWER_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return VIEWER_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return VIEWER_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. LE CONTRAT GABARIT ↔ SCRIPT
# ---------------------------------------------------------------------------

def test_le_gabarit_sert_tous_les_identifiants_demandes_par_le_script(html, js):
    """Chaque `$("x")` de viewer.js doit trouver un `id="x"` dans le gabarit.

    C'est LE test qui attrape un oubli de câblage avant le navigateur : un
    `getElementById` qui renvoie null ne lève rien tant qu'on ne le
    déréférence pas, et quand il le fait, c'est tout le script qui s'arrête.
    """
    servis = set(re.findall(r'id="([^"]+)"', html))
    demandes = set(re.findall(r'\$\("([^"]+)"\)', js))
    # Le raccourci $ n'est pas le seul chemin : les sélecteurs #id aussi.
    demandes |= set(re.findall(r'querySelector(?:All)?\("#([\w-]+)', js))
    manquants = sorted(demandes - servis)
    assert not manquants, f"viewer.js demande des id absents du gabarit : {manquants}"


# ---------------------------------------------------------------------------
# 2. LA STRUCTURE DU TRI RAPIDE (§3 du handoff)
# ---------------------------------------------------------------------------

def test_le_tri_rapide_a_son_entree_en_bouton_primaire(html):
    """« ⇄ Tri rapide » : un bouton, primaire, dans la barre d'outils."""
    assert 'id="btn-tri"' in html
    bloc = html[html.index('id="btn-tri"') - 200:html.index('id="btn-tri"') + 400]
    assert "btn--primary" in bloc
    assert "⇄ Tri rapide" in bloc


def test_le_tri_rapide_est_un_ecran_ferme_par_defaut(html):
    """Écran plein, `hidden` au chargement — jamais ouvert d'office."""
    assert re.search(
        r'<section class="v-tri" id="v-tri" hidden role="dialog" aria-modal="true"',
        html,
    )


def test_len_tete_porte_titre_compteur_et_fermeture(html):
    assert 'class="v-tri__titre">Tri rapide<' in html
    assert 'id="tri-compteur"' in html
    assert 'id="btn-tri-close"' in html


def test_la_progression_est_une_vraie_barre_de_progression(html):
    """4px d'accent, mais aussi un role=progressbar : la mesure est
    annoncée, pas seulement dessinée."""
    assert 'id="tri-jauge"' in html
    assert 'role="progressbar"' in html
    assert 'id="tri-jauge-barre"' in html


def test_la_pile_montre_la_carte_suivante_derriere(html, css):
    """Le fantôme est du DÉCOR : aria-hidden, et incliné de 2°."""
    assert 'class="v-tri__fantome" aria-hidden="true"' in html
    assert re.search(r"\.v-tri__fantome\s*\{[^}]*transform:\s*rotate\(2deg\)", css, re.S)


def test_le_fantome_disparait_sur_la_derniere_carte(css, js):
    """Plus rien derrière la dernière : un fantôme y mentirait."""
    assert ".v-tri__pile.is-derniere .v-tri__fantome { display: none; }" in css
    assert 'classList.toggle("is-derniere"' in js


def test_la_carte_porte_source_meta_etoiles_et_phrase(html):
    """Les quatre contenus que la maquette exige SUR la carte."""
    for ident in ("tri-vue", "tri-source", "tri-meta", "tri-etoiles", "tri-phrase"):
        assert f'id="{ident}"' in html, ident


def test_le_champ_phrase_porte_le_label_de_la_maquette(html):
    assert "Ta phrase pour le meme" in html
    assert 'class="v-tri__label" for="tri-phrase"' in html


def test_le_champ_phrase_respecte_le_plafond_du_serveur(html):
    """500 caractères des deux côtés : le client ne peut pas proposer une
    saisie que la route refusera ensuite en 400."""
    assert 'id="tri-phrase" maxlength="500"' in html
    assert "PHRASE_MAX = 500" in VIEWER_API.read_text(encoding="utf-8")


def test_les_deux_etiquettes_de_geste_existent(html):
    assert 'id="tri-indice-pass" aria-hidden="true">← Passer<' in html
    assert 'id="tri-indice-keep" aria-hidden="true">Garder →<' in html


def test_le_pied_offre_les_trois_equivalents_au_clic(html, css):
    """Le geste n'est jamais le SEUL chemin : trois boutons de 46px."""
    for ident in ("btn-tri-undo", "btn-tri-pass", "btn-tri-keep"):
        assert f'id="{ident}"' in html, ident
    assert "--v-tri-bouton: 46px;" in css
    assert re.search(r"\.v-tri__btn\s*\{[^}]*height:\s*var\(--v-tri-bouton\)", css, re.S)


def test_la_fin_de_pile_donne_un_bilan_et_deux_sorties(html, js):
    assert "Tri terminé" in html
    assert 'id="tri-bilan"' in html
    assert 'id="btn-tri-retour"' in html
    assert 'id="btn-tri-restart"' in html
    assert '" gardés · "' in js and '" passés · notes et phrases enregistrées"' in js


# ---------------------------------------------------------------------------
# 3. LE GESTE — le prototype fait autorité
# ---------------------------------------------------------------------------

def test_les_constantes_du_geste_sont_celles_du_prototype(js):
    """80px de seuil, 500px de sortie, 200ms d'animation, pente dx/30.

    Ces quatre nombres SONT le geste. Nommés, ils se relisent ; semés en
    dur dans le code, une modification de l'un aurait laissé les autres
    en arrière.
    """
    assert "var TRI_SEUIL = 80;" in js
    assert "var TRI_SORTIE = 500;" in js
    assert "var TRI_DUREE = 200;" in js
    assert "var TRI_PENTE = 30;" in js


def test_la_carte_suit_le_doigt_avec_sa_rotation(js):
    assert 'carte.style.transform = "translateX(" + x + "px) rotate(" + (x / TRI_PENTE) + "deg)"' in js


def test_le_seuil_commande_la_decision_et_le_fondu_des_etiquettes(js):
    assert 'if (dx > TRI_SEUIL) deciderTri("keep");' in js
    assert 'else if (dx < -TRI_SEUIL) deciderTri("pass");' in js
    # Le fondu est une fraction du MÊME seuil : l'étiquette est pleine
    # exactement quand la décision bascule.
    assert "Math.min(1, Math.max(0, x / TRI_SEUIL))" in js
    assert "Math.min(1, Math.max(0, -x / TRI_SEUIL))" in js


def test_la_sortie_dure_le_temps_de_lanimation(js):
    """La carte suivante n'arrive qu'APRÈS les 200ms, sinon on verrait
    deux cartes se croiser."""
    assert "state.tri.anim = action === \"keep\" ? TRI_SORTIE : -TRI_SORTIE;" in js
    assert "}, TRI_DUREE);" in js


def test_le_geste_est_en_pointer_events(js):
    """pointerdown sur la carte, move/up sur la FENÊTRE : un doigt qui
    sort de la carte ne doit pas figer le glissé en cours."""
    assert 'carte.addEventListener("pointerdown"' in js
    assert 'window.addEventListener("pointermove"' in js
    assert 'window.addEventListener("pointerup", relacher);' in js
    # pointercancel : le navigateur peut confisquer le pointeur (appel,
    # notification). Sans lui, la carte resterait collée en l'air.
    assert 'window.addEventListener("pointercancel", relacher);' in js


def test_la_carte_confisque_le_geste_horizontal_au_navigateur(css):
    """Sans `touch-action: none`, le mobile s'approprie le balayage
    horizontal et `pointermove` n'arrive jamais."""
    assert re.search(r"\.v-tri__carte\s*\{[^}]*touch-action:\s*none", css, re.S)


def test_les_etoiles_et_le_champ_ne_sont_pas_des_poignees(js):
    """Un tap sur une étoile ou dans le champ ne doit jamais devenir un
    glissé — sinon noter la carte la ferait partir."""
    assert 'e.target.closest(".v-tri__etoiles") || e.target.closest(".v-tri__phrase")' in js


def test_annuler_depile_la_derniere_decision(js):
    """`hist` est une PILE : on en retire le dernier élément, on recule
    d'un cran, et rien d'autre ne bouge."""
    bloc = js[js.index("function annulerTri()"):js.index("function recommencerTri()")]
    assert "state.tri.hist.pop();" in bloc
    assert "state.tri.index = Math.max(0, state.tri.index - 1);" in bloc


# ---------------------------------------------------------------------------
# 4. LA PERSISTANCE — deux durées de vie, deux routes
# ---------------------------------------------------------------------------

def test_la_note_passe_par_la_route_de_notation_existante(js):
    """Aucune route inventée : c'est celle des étoiles de l'aperçu, avec
    le même corps (`user_name` + `rating`)."""
    assert 'API + "/media/" + item.id + "/rate"' in js
    assert 'JSON.stringify({ user_name: pseudo, rating: valeur })' in js


def test_la_phrase_passe_par_la_route_ajoutee_au_socle(js):
    assert 'API + "/media/" + item.id + "/phrase"' in js
    assert 'JSON.stringify({ phrase: v })' in js


def test_la_route_de_la_phrase_existe_bien_cote_serveur():
    api = VIEWER_API.read_text(encoding="utf-8")
    assert '@viewer_api_bp.route("/viewer/media/<int:media_id>/phrase", methods=["POST"])' in api


def test_la_liste_transporte_la_phrase_et_pas_seulement_la_fiche():
    """Le Tri rapide empile des dizaines de cartes : il ne peut pas aller
    chercher chaque phrase par une requête de détail. Si la phrase quittait
    la liste, le pré-remplissage de la carte s'éteindrait sans une seule
    erreur visible."""
    api = VIEWER_API.read_text(encoding="utf-8")
    assert api.count('"phrase": item.phrase') >= 2


def test_le_pseudo_est_demande_au_moment_de_noter(js):
    """Jamais au chargement de l'écran : c'est la règle de tout le viewer,
    et le Tri rapide ne s'en exempte pas."""
    bloc = js[js.index("function noterTri("):js.index("function enregistrerPhrase(")]
    assert "assurerPseudo().then(" in bloc


def test_un_echec_denregistrement_revient_en_arriere(js):
    """Une étoile allumée qui n'est PAS en base est un mensonge que
    l'éditeur paierait plus tard : l'échec restaure la valeur d'avant."""
    bloc = js[js.index("function noterTri("):js.index("function enregistrerPhrase(")]
    assert 'if (typeof avant === "number") state.tri.notes[item.id] = avant;' in bloc
    bloc2 = js[js.index("function enregistrerPhrase("):js.index("function enregistrerPhraseCourante(")]
    assert "item.phrase = avant || null;" in bloc2


def test_un_echec_ne_part_jamais_en_toast(js):
    """Le toast s'efface en 1,6s. Annoncer « ça n'a pas été enregistré »
    par un message qui disparaît tout seul serait pire que se taire :
    les échecs vont dans notifier(), où ils attendent d'être lus."""
    for fn in ("noterTri", "enregistrerPhrase"):
        debut = js.index(f"function {fn}(")
        bloc = js[debut:debut + 2200]
        assert 'notifier(' in bloc and '"danger"' in bloc, fn


def test_la_phrase_ne_part_pas_a_chaque_frappe(js):
    """Un POST par lettre saturerait le réseau au doigt. C'est `change`
    (sortie du champ ou Entrée) qui déclenche, et la sortie de carte qui
    rattrape ce qui n'a pas encore été validé."""
    assert '$("tri-phrase").addEventListener("change"' in js
    assert '$("tri-phrase").addEventListener("input"' not in js
    # ... et la décision pousse ce qui reste avant de quitter la carte.
    bloc = js[js.index("function deciderTri("):js.index("function annulerTri(")]
    assert "enregistrerPhraseCourante();" in bloc


def test_fermer_le_tri_ne_perd_pas_la_phrase_en_cours(js):
    bloc = js[js.index("function fermerTri()"):js.index("function majCompteurTri()")]
    assert "enregistrerPhraseCourante();" in bloc


def test_annuler_ne_perd_pas_la_phrase_en_cours(js):
    """« Annuler » quitte la carte courante pour revenir sur la
    précédente : c'est une SORTIE, au même titre que le glissé et la
    croix. Sans cette poussée, taper une phrase puis se raviser était le
    seul geste de l'écran qui jetait une saisie sans rien dire — dans le
    mode dont la phrase est justement la raison d'être."""
    bloc = js[js.index("function annulerTri()"):js.index("function recommencerTri()")]
    assert "enregistrerPhraseCourante();" in bloc


def test_le_compteur_annonce_le_total_de_la_vue_pas_la_page_chargee(js):
    """« 12 / 60 » sur une vue qui compte 312 médias serait un chiffre
    FAUX. Le dénominateur est `state.total`, celui du serveur."""
    bloc = js[js.index("function majCompteurTri()"):js.index("function reapprovisionnerTri()")]
    assert "var total = state.total || charges;" in bloc


def test_la_pile_se_reapprovisionne_avant_de_se_vider(js):
    """Le corps est gelé pendant le tri : le chargement continu de la
    grille ne se déclenche plus. Sans ce réapprovisionnement, le tri
    annoncerait « terminé » au 60e média d'une vue qui en compte 300."""
    bloc = js[js.index("function reapprovisionnerTri()"):js.index("/** Rendu complet")]
    assert "if (state.chargement || state.page >= state.pages) return;" in bloc
    assert "if (state.tri.index < charges - 3) return;" in bloc
    assert "chargerMedias(true)" in bloc


def test_le_reapprovisionnement_nefface_pas_une_phrase_en_cours(js):
    """Repeindre la carte remettrait `tri-phrase` à la valeur du serveur.
    On ne redessine QUE si la pile était vide — donc si aucune saisie
    n'est en cours."""
    bloc = js[js.index("function reapprovisionnerTri()"):js.index("/** Rendu complet")]
    assert "if (state.tri.index >= charges) rendreTri();" in bloc
    assert "else majCompteurTri();" in bloc


# ---------------------------------------------------------------------------
# 5. RIEN N'A DISPARU — l'inventaire déclaré intouchable
# ---------------------------------------------------------------------------

CONTROLES_INTOUCHABLES = [
    # Onglets Médias / Memes
    ('data-tab="media"', "onglet Médias"),
    ('data-tab="memes"', "onglet Memes"),
    # Recherche, filtres avancés, jetons retirables
    ('id="f-q"', "recherche"),
    ('id="pop-filters"', "panneau de filtres"),
    ('id="filters-badge"', "badge compteur de filtres"),
    ('id="v-facets"', "facettes"),
    ('id="f-from"', "date de début"),
    ('id="f-to"', "date de fin"),
    ('id="v-chips"', "barre des jetons actifs"),
    ('id="v-chips-list"', "liste des jetons"),
    ('id="btn-clear-filters"', "tout effacer"),
    # Tri, groupement, disposition, densité, propriétés
    ('id="f-sort"', "tri"),
    ('id="f-group"', "groupement"),
    ('data-layout="justified"', "disposition Ratios"),
    ('data-layout="grid"', "disposition Carrés"),
    ('id="f-density"', "densité"),
    ('id="pop-display"', "propriétés affichées"),
    # Inspection, collections, doublons, total
    ('id="btn-inspector"', "panneau d'inspection"),
    ('id="v-inspector"', "fiche"),
    ('id="btn-collections"', "colonne des collections"),
    ('id="collections-list"', "liste des collections"),
    ('id="btn-collection-new"', "nouvelle collection"),
    ('id="btn-doublons"', "écran des doublons"),
    ('data-dupmode="exact"', "mode fichiers identiques"),
    ('data-dupmode="similar"', "mode visuellement similaires"),
    ('id="dup-distance"', "curseur de tolérance"),
    ('id="v-total"', "total de la vue"),
    # Sélection multiple et actions par lot
    ('id="v-selbar"', "barre de sélection"),
    ('id="sel-count"', "compteur de sélection"),
    ('id="btn-sel-all"', "tout sélectionner"),
    ('id="btn-sel-none"', "désélectionner"),
    ('id="pop-addto"', "ajouter à une collection"),
    ('id="btn-sel-download"', "télécharger la sélection"),
    ('id="btn-sel-delete"', "supprimer la sélection"),
    # Aperçu et dialogues
    ('id="lightbox"', "aperçu"),
    ('id="lb-stars"', "notation dans l'aperçu"),
    ('id="dlg-confirm"', "dialogue de confirmation"),
    ('id="dlg-user"', "dialogue de pseudo"),
    ('id="dlg-saisie"', "dialogue de saisie"),
    ('id="dlg-dedup"', "dialogue de déduplication"),
]


@pytest.mark.parametrize("motif,nom", CONTROLES_INTOUCHABLES, ids=[n for _, n in CONTROLES_INTOUCHABLES])
def test_aucun_controle_existant_na_ete_retire(html, motif, nom):
    """« On ne touche pas aux réglages existants — uniquement à
    l'interface. » Une disparition est un défaut bloquant, et c'est le
    genre de défaut qu'aucun autre test n'attraperait."""
    assert motif in html, f"contrôle disparu du gabarit : {nom} ({motif})"


def test_la_barre_de_selection_a_quitte_le_chrome_sans_rien_perdre(html):
    """Elle est passée en bas d'écran (§4). Le déplacement ne doit pas
    avoir coûté un seul bouton — ni sortir le panneau « Ajouter à… » de
    la barre, qui n'aurait alors plus d'ancre."""
    debut = html.index('<div class="v-selbar" id="v-selbar"')
    bloc = html[debut:html.index("</div>", html.index('id="btn-sel-delete"'))]
    for ident in ("sel-count", "sel-label", "btn-sel-all", "btn-sel-none",
                  "pop-addto", "addto-list", "btn-addto-new",
                  "btn-sel-download", "btn-sel-delete"):
        assert ident in bloc, ident
    # Elle vit désormais HORS de #v-chrome.
    assert html.index('id="v-chrome"') < html.index("</div>\n\n<!-- Zone de messages")
    assert debut > html.index('id="v-workspace"')


def test_la_barre_de_selection_souvre_vers_le_haut(css):
    """Une barre au ras du bas ouvre ses panneaux VERS LE HAUT, sinon
    « Ajouter à… » tombe hors de l'écran."""
    assert re.search(
        r"\.v-selbar \.v-pop__panel\s*\{[^}]*bottom:\s*calc\(100% \+ var\(--sp-2\)\)",
        css, re.S,
    )


def test_la_grille_reserve_la_place_de_la_barre_du_bas(css, js):
    """Fixe, elle recouvrirait la dernière rangée de vignettes."""
    assert ".v-body.has-selbar .v-workspace { padding-bottom:" in css
    assert 'document.body.classList.toggle("has-selbar", n > 0);' in js


def test_le_total_de_la_vue_reste_visible_quoi_quil_arrive(css):
    """V16 : le total est le seul chiffre exigé SOUS LES YEUX en
    permanence. La barre défilant désormais à l'horizontale, il faut le
    coller au bord droit — sinon il sort du champ sur une fenêtre étroite."""
    assert re.search(
        r"\.v-total\s*\{[^}]*position:\s*sticky;[^}]*right:\s*0;", css, re.S,
    )


def test_la_barre_doutils_tient_sur_une_seule_rangee(css):
    """§4 : une rangée. Elle défile plutôt que de s'empiler — c'est ce qui
    permet de n'avoir RETIRÉ aucun contrôle pour y arriver."""
    bloc = css[css.index(".v-toolbar {"):css.index(".v-toolbar::-webkit-scrollbar")]
    assert "flex-wrap: nowrap;" in bloc
    assert "overflow-x: auto;" in bloc


# ---------------------------------------------------------------------------
# 6. LE SYSTÈME MODERNIST
# ---------------------------------------------------------------------------

def test_les_segments_actifs_sont_un_aplat_accent_comme_dans_leditor(css):
    """COHÉRENCE : `.seg__btn` / `.import-tab` de editor.css posent un
    aplat --accent-solid à texte --fg-on-accent sur le segment actif. Deux
    écrans voisins ne peuvent pas traiter le même contrôle autrement."""
    editor = EDITOR_CSS.read_text(encoding="utf-8")
    reference = re.search(
        r"\.seg__btn\.active,\s*\.seg__btn\[aria-pressed=\"true\"\]\s*\{([^}]*)\}",
        editor, re.S,
    )
    assert reference, "la référence .seg__btn.active a disparu de editor.css"
    assert "--accent-solid" in reference.group(1)
    assert "--fg-on-accent" in reference.group(1)

    for selecteur in (r"\.v-tab\.is-active", r"\.v-seg__btn\.is-active"):
        bloc = re.search(selecteur + r"\s*\{([^}]*)\}", css, re.S)
        assert bloc, selecteur
        assert "var(--accent-solid)" in bloc.group(1), selecteur
        assert "var(--fg-on-accent)" in bloc.group(1), selecteur


def test_la_colonne_des_collections_fait_210px(css):
    assert "--v-collections-w: 210px;" in css


def test_la_collection_active_porte_sa_barre_interieure(css):
    """Fond accent-100 ET barre de 3px : la couleur seule ne se repère pas
    en balayant la colonne du regard."""
    bloc = re.search(
        r"\.v-col-row\.is-on \.v-col-row__pick,\s*\.v-col-row__pick--all\.is-on\s*\{([^}]*)\}",
        css, re.S,
    )
    assert bloc
    assert "var(--accent-soft)" in bloc.group(1)
    assert "inset 3px 0 0 var(--color-accent)" in bloc.group(1)


def test_la_legende_de_vignette_porte_la_date_et_la_note(css):
    """Date à gauche, note ★ à droite en accent — la lecture de la maquette."""
    bloc = re.search(r"\.v-tile__rating\s*\{([^}]*)\}", css, re.S)
    assert bloc
    assert "margin-left: auto;" in bloc.group(1)
    assert "color: var(--accent);" in bloc.group(1)


def test_aucun_media_nest_desature(css):
    """Règle explicite du système : les médias restent EN COULEUR. Aucun
    filtre de désaturation, nulle part."""
    interdits = re.findall(r"filter:\s*[^;]*(?:grayscale|saturate\(0)", css)
    assert not interdits, f"filtre de désaturation trouvé : {interdits}"


def test_aucune_couleur_brute_dans_la_feuille(css):
    """La règle d'en-tête du fichier : tout passe par var(--…), donc les
    deux thèmes suivent sans une seule règle dupliquée."""
    brutes = [
        ligne for ligne in css.splitlines()
        if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", ligne)
    ]
    assert not brutes, f"couleurs brutes : {brutes[:5]}"


def test_aucune_boite_systeme_dans_le_script(js):
    """alert / confirm / prompt sont bloquants, non stylables, et un
    navigateur a le droit de les refuser."""
    # Les commentaires du fichier NOMMENT ces trois-là pour dire qu'ils
    # sont bannis : on ne cherche que dans le code exécutable.
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    for interdit in ("alert(", "confirm(", "prompt("):
        # `confirmer(` et `demanderPseudo` sont les remplaçants maison :
        # on cherche l'appel global, pas les noms qui le contiennent.
        trouve = re.findall(r"(?<![\w.])" + re.escape(interdit), code)
        assert not trouve, f"{interdit} appelé dans viewer.js"


def test_le_toast_est_un_accuse_de_reception_de_16s(html, js, css):
    """Bandeau encré en bas d'écran, 1,6 s, role=status SUR LE CONTENEUR
    (le poser sur chaque toast rejouerait l'annonce à chaque insertion)."""
    assert '<div class="v-toasts" id="v-toasts" role="status" aria-live="polite">' in html
    assert "setTimeout(function () { el.remove(); }, 1600);" in js
    bloc = re.search(r"\n\.v-toast\s*\{([^}]*)\}", css, re.S)
    assert bloc
    assert "background: var(--fg-1);" in bloc.group(1)
    assert "color: var(--bg-canvas);" in bloc.group(1)


def test_le_toast_ne_se_pose_pas_sur_les_barres_du_bas(css):
    """Le bas de l'écran est occupé : par la barre de sélection dans la
    galerie, par le pied des trois boutons dans le Tri rapide."""
    assert ".v-body.has-selbar .v-toasts { bottom:" in css
    assert ".v-body.tri-ouvert .v-toasts { bottom:" in css


def test_les_cibles_du_tri_sont_tactiles(css):
    """≥ 44×44 : la croix de fermeture, les étoiles, le champ."""
    for selecteur in (r"\.v-tri__fermer", r"\.v-tri__etoile", r"\.v-tri__phrase"):
        bloc = re.search(selecteur + r"\s*\{([^}]*)\}", css, re.S)
        assert bloc, selecteur
        assert "var(--tap-min)" in bloc.group(1), selecteur


# ---------------------------------------------------------------------------
# 7. LE CLAVIER
# ---------------------------------------------------------------------------

def test_le_tri_ouvert_capte_le_clavier_avant_tout_le_reste(js):
    """Il recouvre l'écran, donc il capte les touches : Échap ferme, les
    flèches valent les deux gestes, 1–5 valent les étoiles. Ce bloc doit
    passer AVANT celui de l'aperçu, sinon les flèches y navigueraient."""
    debut = js.index('document.addEventListener("keydown"')
    bloc = js[debut:debut + 1000]
    assert bloc.index('!$("v-tri").hidden') < bloc.index("!lightbox.hidden")
    assert 'if (e.key === "Escape") { e.preventDefault(); fermerTri(); }' in bloc
    assert 'deciderTri("keep")' in bloc and 'deciderTri("pass")' in bloc


def test_aucun_raccourci_a_une_touche_ne_part_dun_champ(js):
    """Taper « 3 » dans la phrase ne doit pas noter le média."""
    debut = js.index('document.addEventListener("keydown"')
    bloc = js[debut:debut + 600]
    assert "if (dansUnChamp(e)) {" in bloc
