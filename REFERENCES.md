# RÉFÉRENCES — barème de design de SAMOURAIS SCRAPPER

> **Usage unique de ce document** : servir de barème à des agents *critics* qui comparent une capture de nos écrans à l'état de l'art, en A/B aveugle.
> Tout ce qui est écrit ici doit être **vérifiable sur une capture** ou **en manipulant l'écran**. Aucun critère de goût.
>
> Références étudiées : **Buffer**, **Later**, **Metricool**, **Eagle**, **Raindrop.io**, **Google Photos**, **Linear**.
> Contrainte technique de notre produit : **Flask + Jinja2 + CSS + JS vanilla + HTMX**. Aucun framework SPA. Tout critère ci-dessous est réalisable dans cette stack.

---

## 1. La barre, en une page

| Écran | Référence qui fait autorité | Les 3 choses qui la rendent supérieure |
|---|---|---|
| **Dashboard** | **Linear** (appoint : Metricool) | 1. Chaque tuile est un **lien vers une vue pré-filtrée**, jamais un chiffre inerte. 2. Tient en un écran à 1440×900, mêmes rayons/filets/hauteurs que le reste de l'app. 3. Les problèmes (comptes déconnectés, jobs échoués) sont **comptés et cliquables** vers l'action corrective (Metricool : pastille verte/ambre/rouge par connexion). |
| **Viewer** | **Eagle** (appoint : Google Photos pour la perf, Raindrop pour l'organisation) | 1. **Slider de densité permanent** dans la barre d'outils + 4 dispositions nommées par usage (justified / waterfall / grid / list). 2. **Filtres persistants en jetons** sous la barre d'outils, jamais une modale, et une combinaison enregistrable en « vue » qui apparaît dans la sidebar au même rang qu'un dossier. 3. **Triage au clavier sans lâcher la grille** : F = classer, T = taguer, 1‑5 = noter, Espace = aperçu — le coût d'inspection d'un média tombe à zéro. Google Photos ajoute la loi physique : layout justifié à ratios préservés, hauteur pré-allouée, ≤50 tuiles dans le DOM. |
| **Éditeur** | **Buffer** (composer + preview), chrome par **Linear** | 1. **Preview au ratio exact de la plateforme cible**, mise à jour pendant la frappe, montrant les 3 choses indevinables : cadrage, point de troncature, place du lien. 2. **Validation dure** : dépassement = texte excédentaire surligné + bouton désactivé + **nom de la plateforme fautive** (correction de leur défaut). 3. **Une seule action finale**, le « quand » relégué dans un `<select>` adjacent — pas 4 boutons concurrents. |
| **Calendrier** | **Later** (bibliothèque ↔ grille), appoint **Buffer** (file, états, erreurs) | 1. **Bibliothèque de médias en colonne permanente à côté de la grille**, filtrée par défaut sur « jamais utilisé » : le média n'est jamais « cherché » depuis un formulaire vide. 2. **Le drop transporte 3 informations d'un coup** (quel média, quel jour, quelle heure) et ouvre un composer déjà à moitié rempli. 3. Buffer : **5 états = 5 emplacements**, l'échec est le seul qui ne déménage pas et porte ses 2 actions de reprise nommées sur la carte. |
| **Analytics** | **Metricool** | 1. **Un seul sélecteur de période**, global, en haut à droite, qui recalcule tout l'écran y compris les comparaisons. 2. **La comparaison période précédente est un défaut, pas une option** — et quand elle est invalide, elle est **absente** plutôt que fausse (jamais « 0 % »). 3. **Agrégat d'abord, tableau ensuite** : 5 chiffres, puis les graphes à sélecteur métrique + breakdown, puis la table triable à miniatures. |
| **Réglages** | **Linear** (forme) + **Metricool** / **Buffer** (contenu) | 1. **Aucun bouton « Enregistrer » global** : chaque rangée persiste au changement, confirmation discrète. 2. Une **ligne par connexion** : nom, pastille d'état, **date absolue de dernière synchro réussie**, bouton « Reconnecter » ; le compte expiré **remonte en tête de liste** (Buffer). 3. Blocs séparés par un filet 1px, libellé à gauche / contrôle aligné à droite, **champ de recherche des réglages** (Eagle). |

---

## 2. Direction esthétique recommandée

**Thèse.** On copie le **système** de Linear (échelle courte, constance, retenue, latence perçue), pas sa direction artistique (fond quasi noir + dégradé violet = cliché SaaS 2024). On s'en écarte sur trois points assumés : (a) **teinte d'accent bleu franc** (~218°) au lieu de l'indigo-violet 245° de Linear — c'est la seule famille chromatique non consommée par la sémantique, puisque rouge/orange/jaune/vert sont réservés aux états ; (b) **densité plus forte** que Linear, parce que notre écran principal est une grille de médias, pas une liste de texte ; (c) **thème clair de premier rang**, pas un sous-produit du thème sombre.

### 2.1 Chiffres directeurs

| Dimension | Valeur | Justification |
|---|---|---|
| Tailles de police UI | **5** : 11 / 12 / 13 / 15 / 18 px | Linear : 5 tailles dans tout le produit. |
| Tailles de titre | **3** : 20 / 24 / 32 px | Linear : title3/title2/title1. |
| Corps de texte | **13 px** (listes, tableaux, panneaux) / **15 px** (texte long) | App d'usage quotidien, pas site vitrine. |
| Graisse du corps | **450** (pas 400) | Compense le petit corps. Linear utilise 450 comme « normal ». |
| Échelle d'espacement | multiples de **4** uniquement : 2, 4, 6, 8, 12, 16, 24, 32, 48 | Aucun 5/7/9/11/14 px dans le CSS. |
| Gap le plus fréquent | **8 px** | Linear : distribution mesurée. |
| Rayons | **4** (contrôles) / **8** (blocs) / **12** (conteneur app) / **999** (pilules) | 4 valeurs max, jamais mélangées dans un même niveau. |
| Bordures | **1 px** (0.5 px si `min-resolution: 2dppx`) | Hiérarchie par surface + filet. |
| Ombres | **interdites** sauf popover / dialog / dropdown | La hiérarchie vient de l'escalier de 4 surfaces. |
| Hauteur de bouton | **32 px**, padding 0 12px, min-width 92px, texte 13/500 | Linear. |
| Hauteur de rangée de menu/popover | **28 px** ; contrôle interne **22 px** ; puce **21 px** | Linear (mesure pixel des Display options). |
| Hauteur de rangée de liste/tableau | **32 px** | |
| Icônes | **2 tailles** : 14 et 16 px | Linear. |
| Sidebar | **240 px** ; conteneur principal marge 8 px, rayon 12 px | Sous 1024 px : sidebar escamotée, marge 0. |
| Cible tactile | **≥ 44×44 px** (icône 20 px + padding, `background-clip: content-box`) | Material 48 dp / WCAG 2.2 AA 24 px — on vise 44. |
| Densité Viewer | 3 paliers de vignette : **120 / 168 / 240 px**, gouttière **4 px** | Objectif mesurable : **≥ 30 vignettes visibles à 1440×900** au palier moyen. |
| Chiffres | `font-variant-numeric: tabular-nums`, alignés à droite | Colonnes de nombres alignées verticalement. |
| Transitions | **2 vitesses** : 100 ms (hover/focus/couleur) et 250 ms (apparition de panneau). Highlight : entrée **0 ms**, sortie **150 ms**. Rien au-dessus de 400 ms. | Linear ; asymétrie volontaire du highlight. |
| Seuil d'indicateur de chargement | **800 ms** avant apparition, puis fondu **200 ms** ; disparition **100 ms** | Linear attend 1000 ms. En local on peut être plus court. |
| Latence de feedback d'une action utilisateur | **< 100 ms**, par mutation optimiste du DOM ; le réseau suit | Aucun spinner sur une action dont l'effet est calculable côté client. |

### 2.2 Tokens CSS — à copier tel quel

```css
/* ============================================================
   SAMOURAIS SCRAPPER — design tokens
   Un seul fichier. Aucune valeur brute ailleurs dans le CSS.
   ============================================================ */

:root {
  /* ---------- Typographie ---------- */
  --font-ui: "Inter", "Inter Variable", -apple-system, BlinkMacSystemFont,
             "Segoe UI", Roboto, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;

  --text-micro:   0.6875rem; /* 11px — badges, légendes de graphe */
  --text-mini:    0.75rem;   /* 12px — métadonnées sous vignette */
  --text-small:   0.8125rem; /* 13px — CORPS PAR DÉFAUT de l'app */
  --text-regular: 0.9375rem; /* 15px — texte long, légendes éditables */
  --text-large:   1.125rem;  /* 18px — sous-titre de section */

  --title-3: 1.25rem;  /* 20px — titre d'état vide / d'erreur */
  --title-2: 1.5rem;   /* 24px — titre d'écran */
  --title-1: 2rem;     /* 32px — valeur KPI hero */

  --weight-light:    300;
  --weight-normal:   450;  /* corps */
  --weight-medium:   500;  /* labels, boutons, en-têtes de colonne */
  --weight-semibold: 600;  /* titres */
  --weight-bold:     700;  /* valeurs KPI */

  --lh-tight: 1.2;   /* titres */
  --lh-ui:    1.4;   /* lignes de liste, boutons */
  --lh-text:  1.55;  /* paragraphes */

  --ls-tight:  -0.014em; /* ≥20px */
  --ls-normal: -0.011em; /* 13–15px */
  --ls-wide:    0.002em; /* 11–12px capitales */

  --numeric: tabular-nums;

  /* ---------- Espacement (multiples de 4) ---------- */
  --sp-1:  2px;
  --sp-2:  4px;
  --sp-3:  6px;
  --sp-4:  8px;   /* défaut */
  --sp-5:  12px;
  --sp-6:  16px;
  --sp-7:  24px;
  --sp-8:  32px;
  --sp-9:  48px;

  /* ---------- Rayons ---------- */
  --radius-control:   4px;
  --radius-block:     8px;
  --radius-container: 12px;
  --radius-pill:      9999px;

  /* ---------- Géométrie ---------- */
  --sidebar-w:       240px;
  --inspector-w:     300px;
  --row-h:           32px;   /* rangée de liste / tableau */
  --menu-row-h:      28px;   /* rangée de popover */
  --control-h:       22px;   /* select dans un popover */
  --chip-h:          21px;
  --btn-h:           32px;
  --btn-min-w:       92px;
  --icon-sm:         14px;
  --icon-md:         16px;
  --tap-min:         44px;
  --border-w:        1px;
  --grid-gap:        4px;
  --thumb-sm:        120px;
  --thumb-md:        168px;  /* défaut Viewer */
  --thumb-lg:        240px;

  /* ---------- Mouvement ---------- */
  --t-highlight-in:  0s;
  --t-highlight-out: 150ms;
  --t-quick:         100ms;
  --t-regular:       250ms;
  --ease:            cubic-bezier(0.2, 0, 0.2, 1);
  --loading-delay:   800ms;
  --loading-fade:    200ms;

  /* ---------- Élévation (popover / dialog UNIQUEMENT) ---------- */
  --shadow-low:    0 2px 4px rgba(0,0,0,.10);
  --shadow-medium: 0 4px 24px rgba(0,0,0,.18);
  --shadow-high:   0 8px 40px rgba(0,0,0,.32);

  /* ---------- Empilement ---------- */
  --z-content: 1;
  --z-sticky:  100;
  --z-overlay: 500;
  --z-popover: 600;
  --z-palette: 650;
  --z-dialog:  700;
  --z-tooltip: 1100;
  --z-toast:   1200;

  /* ============ PALETTE — THÈME CLAIR (défaut) ============ */
  color-scheme: light;

  /* Surfaces : escalier de 4 niveaux */
  --bg-canvas:  #f4f5f7;  /* fond de page + sidebar */
  --bg-1:       #ffffff;  /* conteneur principal, cartes */
  --bg-2:       #f1f2f4;  /* zone en creux, en-tête de tableau, tuile vide */
  --bg-3:       #e8eaed;  /* survol de rangée, piste de slider */
  --bg-inset:   #fbfbfc;  /* fond de champ */

  /* Bordures : 3 niveaux */
  --border-1: #e3e5e8;  /* filet par défaut */
  --border-2: #d2d6dc;  /* séparateur appuyé, bordure de contrôle */
  --border-3: #b7bdc6;  /* bordure au survol */

  /* Texte : 4 niveaux — c'est ce qui permet 8 infos sur une ligne */
  --fg-1: #16181d;  /* primaire */
  --fg-2: #3d434c;  /* secondaire */
  --fg-3: #6b7280;  /* tertiaire — labels, métadonnées */
  --fg-4: #99a0aa;  /* quaternaire — placeholder, désactivé */
  --fg-on-accent: #ffffff;

  /* Accent (bleu 218° — hors des teintes sémantiques) */
  --accent:        #2f6fe0;
  --accent-hover:  #2861cb;
  --accent-active: #2255b3;
  --accent-soft:   #e8f0fe;  /* fond de jeton actif / sélection */
  --accent-border: #a9c6f5;

  /* Sémantique — jamais décorative */
  --success:      #1a7f3c;
  --success-soft: #e3f5e9;
  --warning:      #a86412;
  --warning-soft: #fdf1de;
  --danger:       #c22b30;
  --danger-soft:  #fdeaea;
  --info:         var(--accent);
  --info-soft:    var(--accent-soft);
  --neutral:      var(--fg-3);
  --neutral-soft: var(--bg-3);

  /* Séries de graphe — accent + 4 sémantiques max, dans cet ordre */
  --chart-1: #2f6fe0;
  --chart-2: #1a7f3c;
  --chart-3: #a86412;
  --chart-4: #7c4dbd;
  --chart-5: #0e7f8c;
  --chart-grid: var(--border-1);

  /* Focus */
  --focus-ring: 2px solid var(--accent);
  --focus-offset: 2px;

  /* Scrollbar */
  --scrollbar-track: 12px;
  --scrollbar-thumb: 6px;
  --scrollbar-color: rgba(0,0,0,.16);
  --scrollbar-color-hover: rgba(0,0,0,.28);
}

/* ============ PALETTE — THÈME SOMBRE ============ */
:root[data-theme="dark"] {
  color-scheme: dark;

  --bg-canvas:  #0a0b0d;
  --bg-1:       #101214;
  --bg-2:       #16181b;
  --bg-3:       #1d2024;
  --bg-inset:   #0d0e10;

  --border-1: #23262b;
  --border-2: #31353b;
  --border-3: #414751;

  --fg-1: #f2f3f5;
  --fg-2: #c6ccd4;
  --fg-3: #8b929c;
  --fg-4: #61666e;
  --fg-on-accent: #ffffff;

  --accent:        #4b8dfa;
  --accent-hover:  #6ba0ff;
  --accent-active: #3a7ae8;
  --accent-soft:   #16233a;
  --accent-border: #2f4d80;

  --success:      #35b364;
  --success-soft: #10261a;
  --warning:      #dd9030;
  --warning-soft: #2a1e0c;
  --danger:       #ef5a5f;
  --danger-soft:  #2c1214;
  --info:         var(--accent);
  --info-soft:    var(--accent-soft);
  --neutral:      var(--fg-3);
  --neutral-soft: var(--bg-3);

  --chart-1: #4b8dfa;
  --chart-2: #35b364;
  --chart-3: #dd9030;
  --chart-4: #a279e0;
  --chart-5: #2fb3c2;
  --chart-grid: var(--border-1);

  --shadow-low:    0 2px 4px rgba(0,0,0,.40);
  --shadow-medium: 0 4px 24px rgba(0,0,0,.55);
  --shadow-high:   0 8px 40px rgba(0,0,0,.70);

  --scrollbar-color: rgba(255,255,255,.14);
  --scrollbar-color-hover: rgba(255,255,255,.26);
}

/* Suivi du système quand aucun choix explicite n'est stocké */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    /* dupliquer le bloc [data-theme="dark"] ici, ou poser data-theme
       via le script inline ci-dessous — une seule source de vérité. */
  }
}

/* ---------- Règles non négociables ---------- */
body { background: var(--bg-canvas); color: var(--fg-1);
       font: var(--weight-normal) var(--text-small)/var(--lh-ui) var(--font-ui);
       letter-spacing: var(--ls-normal); }

:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: var(--focus-ring); outline-offset: var(--focus-offset);
}

/* Pas d'ombre pour créer la hiérarchie : surface + filet. */
.panel { background: var(--bg-1); border: var(--border-w) solid var(--border-1);
         border-radius: var(--radius-block); }

/* Hover jamais déclenché au doigt (évite les états collés sur mobile) */
@media (any-hover: hover) and (any-pointer: fine) {
  .row:hover { background: var(--bg-3); }
}

@media (min-resolution: 2dppx) { :root { --border-w: 0.5px; } }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 1ms !important;
                           transition-duration: 1ms !important; }
}

/* Nombres */
.num { font-variant-numeric: var(--numeric); text-align: right; }
```

**Anti-flash de thème** — script inline **synchrone** dans `<head>`, avant tout CSS :

```html
<script>
  (function(){var t=localStorage.getItem('theme');
   if(!t)t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
   document.documentElement.dataset.theme=t;})();
</script>
<style>html[data-theme=dark]{background:#0a0b0d}html[data-theme=light]{background:#f4f5f7}</style>
```

### 2.3 Ce que la palette interdit

- Aucun **dégradé décoratif**, aucun fond coloré pleine surface derrière un logo de plateforme.
- Aucun **bouton coloré** en dehors de l'action primaire (`--accent`) et des actions destructives confirmées (`--danger`).
- Aucune information portée par **la couleur seule** : toujours couleur **+** forme/icône/libellé (variations : flèche ▲▼ en plus du vert/rouge ; statut de post : pastille **+** mot).
- La navigation est **plusieurs crans moins contrastée** que le contenu. Elle ne réclame pas une attention qu'elle n'a pas méritée.
- **Jamais de noir pur ni de blanc pur** en fond.

---

## 3. Flows de référence par écran

Chaque flow : la mécanique de la référence → notre transposition Jinja2 + CSS + JS vanilla + HTMX.

### 3.1 Viewer

**F1 — Balayage à densité réglable** *(Eagle)*
1. Slider de taille de vignette **au centre de la barre d'outils**, visible en permanence, course non linéaire, doublé par `Cmd/Ctrl +` / `-`.
2. Bascule de disposition (justified / grid / list) par un seul bouton.
3. Survol d'une vidéo = lecture d'aperçu **sans clic**, scrubbing à la position X de la souris.
4. `Espace` = lightbox, `←/→` pour enchaîner, `Échap` pour revenir **à la position de scroll exacte**.

> **Transposition.** Grille : `grid-template-columns: repeat(auto-fill, minmax(var(--thumb), 1fr))`. Le `<input type=range>` écrit `--thumb` sur le conteneur en JS pur (0 requête), persisté en `localStorage` **et** en cookie relu par Jinja pour éviter le flash. Progression non linéaire : `Math.round(96 * Math.pow(1.16, v))`. Justified sans lib : `display:flex; flex-wrap:wrap` + `flex-grow: var(--ratio); flex-basis: calc(var(--ratio) * var(--row-h))`, le ratio venant de `width/height` stockés en base au scrape — zéro mesure JS. Hover vidéo : `<video muted preload=none>` superposé au poster, `play()` sur `mouseenter`, `currentTime = offsetX/width*duration` sur `mousemove`. Lightbox : un unique `<dialog>` alimenté par `hx-get /media/{id}/lightbox`, voisins n±1 en `<link rel=prefetch>`.

**F2 — Grille à très grande échelle** *(Google Photos)*
1. Ratios d'aspect **préservés** (une photo portrait est plus haute que large), chaque rangée touche les deux bords.
2. Hauteur de chaque section **pré-allouée** avant chargement → l'ascenseur a sa taille définitive dès la seconde 0 et ne rétrécit jamais.
3. Chargement en 3 paliers : placeholder dimensionné → micro-JPEG (~900 octets) en `image-rendering: pixelated` (**pas de blur**, trop cher sur des centaines d'éléments) → vignette pleine def en crossfade **100 ms**.
4. En-tête de date **sticky**, scrubber latéral affichant **mois + année** pendant le drag.

> **Transposition.** Jinja rend un `<section>` par mois avec `style="height:{{ h }}px"` calculé côté Python (`nb × ratio_moyen`) et un corps vide ; un `IntersectionObserver` déclenche `hx-get /viewer/section/2026-08`, puis on corrige la hauteur mesurée. `content-visibility: auto` + `contain-intrinsic-size` sur chaque tuile = virtualisation offerte par le navigateur. `loading="lazy" decoding="async"` + `width`/`height` écrits par Jinja. Micro-JPEG de 20 px inliné en `data:` URI dans le `background-image` de la tuile — coût réseau nul. Sticky : `position: sticky; top: 0` sur le `<h2>`. Scrubber : `position: fixed; right: 0` avec la carte mois→offset rendue par Jinja, pastille de date décalée **à gauche du doigt**.

**F3 — Sélection multiple sans mode** *(Google Photos + Eagle + Raindrop)*
1. Au survol, un **cercle de sélection** apparaît dans un coin de la tuile ; la tuile et ses voisines ne bougent pas.
2. Clic sur le cercle = sélectionner ; clic sur l'image = ouvrir. **Aucun « mode sélection » à activer.**
3. `Maj+clic` sélectionne la plage, `Ctrl/⌘+clic` picore, `Ctrl/⌘+A` tout, en-tête de date = sélectionne le groupe.
4. Dès ≥1 élément : barre d'actions **affichant le nombre exact** et **≤5 actions**. `Échap` vide.

> **Transposition.** `.tile .check{opacity:0}` + `.tile:hover .check,.tile.is-selected .check{opacity:1}` ; sélection visualisée par `transform: scale(.9)` sur l'`<img>` (l'emplacement ne bouge pas → zéro reflow). Un écouteur **délégué** unique lit `data-index` (index global rendu par Jinja) ; `Maj+clic` = filtre numérique sur `querySelectorAll('[data-index]')`. Les ids vivent dans un `Set` JS sérialisé en `<input type=hidden>`. La barre d'actions est un fragment `hx-swap-oob` en `position: sticky; bottom: 0`, absent du DOM quand `count === 0`. Sous `(pointer: coarse)`, cercles **visibles en permanence** et ≥44 px.

**F4 — Filtres persistants → vue enregistrée** *(Eagle + Raindrop)*
1. Barre de filtres **sous** la barre d'outils, jamais une modale : les conditions actives restent lisibles, chacune retirable d'un clic.
2. ≥6 dimensions : type, ratio/orientation, dimensions, poids, durée, date, tag, note, profil source, présence d'URL, usage (publié / jamais utilisé).
3. Les facettes n'affichent **que les valeurs qui ramènent ≥1 résultat**, avec leur compte, et **se recalculent** après chaque sélection (correction du défaut n°1 de Raindrop).
4. Une combinaison satisfaisante devient une **vue enregistrée** dans la sidebar, au même rang qu'un dossier.

> **Transposition.** **Tout l'état de filtrage vit dans la query string.** Chaque contrôle est dans un `<form id="filter-bar">` avec `hx-get="/viewer/grid" hx-include="#filter-bar" hx-target="#grid" hx-push-url="true"` → bouton retour, URL partageable, rechargement fidèle, gratuitement. Les facettes sont un partial re-rendu à chaque swap depuis un `GROUP BY … WHERE <filtres actifs>`. « Enregistrer cette vue » = insérer `(nom, icône, couleur, query_string)` ; la vue rendue dans la sidebar est alors un simple `<a href="/viewer?{{ qs }}">` — **zéro moteur de règles**. Filtre orientation : colonne calculée `portrait|carré|paysage|large` à partir de `width/height` au scrape.

**F5 — Doublons : deux modes, seuil après scan** *(Eagle)*
1. Deux modes **nommés et séparés** côte à côte : « fichiers identiques » (MD5) et « visuellement similaires » (hash perceptuel).
2. **Portée réglable** : bibliothèque entière / vue courante / sélection.
3. Résultats en **groupes** comparables côte à côte, chaque candidat affichant résolution, poids, format.
4. **Slider de similarité manipulé APRÈS le scan** : il resserre l'affichage sans relancer le calcul.
5. Résolution par **fusion** : on choisit l'exemplaire gardé **et** les métadonnées conservées (tags, collections, notes, URL source).

> **Transposition.** Deux colonnes calculées à l'ingestion : `md5` et `phash` (dhash 64 bits sur un thumbnail 9×8 gris, ~20 lignes avec Pillow). Identiques : `GROUP BY md5 HAVING COUNT(*)>1`. Similaires : bucketing des phash en 4 tranches de 16 bits indexées puis distance de Hamming `bin(a^b).count('1')` — évite le O(n²). La distance de chaque paire est **stockée** : le slider filtre alors purement côté client (`data-dist` + `hidden`), réponse instantanée. La portée « vue courante » réutilise la query string du Viewer.

**F6 — Triage au clavier** *(Eagle)* — `F` classer, `T` taguer, `1‑5` noter, `0` retirer la note, `X` sélectionner, `D` télécharger.
> **Transposition.** Un `keydown` global avec garde `if (e.target.matches('input,textarea,[contenteditable]')) return;`. Les panneaux F et T sont **le même** `<dialog>` non modal chargé une fois puis filtré côté client. **Règle dure : pendant un triage, aucun swap ne vise la grille** — uniquement l'inspector ou la tuile concernée — sinon on perd le scroll et la sélection.

### 3.2 Calendrier

**F1 — Bibliothèque latérale → grille, par glisser-déposer** *(Later)*
1. Grille au centre, **Side Library** en colonne fixe, déjà peuplée et **filtrée par défaut sur « non utilisé »** (filtre visible et désactivable).
2. Cocher 1..N vignettes, glisser le paquet sur une case jour/heure.
3. Au drop, le composer s'ouvre **pré-rempli** : média posé, date/heure = celle de la case, et la **note du média devient l'amorce de la légende**.
4. N médias déposés ensemble = carrousel ; ou répartition sur les N créneaux suivants.

> **Transposition.** HTML5 DnD natif : `<li draggable="true" data-media-id>` côté bibliothèque, cases du calendrier en dropzone (`dragover`/`drop`). Au drop, construire l'URL et déclencher `hx-get /posts/new?media_ids=…&at=2026-08-14T18:00` qui renvoie le composer dans un `<dialog>`, pré-rempli côté Jinja. **Fallback clic obligatoire** : cliquer une case vide ouvre le même composer avec la date pré-remplie (trackpad, tactile). Après création, seule la case concernée est re-swappée.

**F2 — Replanifier par drag, avec rollback** *(Later + Linear)*
1. La carte est à la fois l'affichage et le contrôle : la glisser = la replanifier, l'heure affichée change **immédiatement**, sans formulaire ni confirmation.
2. Vue Mois (vue d'ensemble, compteurs par jour) / Semaine (manipulation horaire) / Liste (file d'attente).

> **Transposition.** Au `drop`, **déplacer le nœud DOM tout de suite**, puis `hx-patch /posts/{id}/schedule`. Sur `htmx:responseError`, remettre le nœud à sa place et afficher un toast. Les 3 vues sont 3 templates servis par le même endpoint avec `?view=`, liés par `hx-get + hx-push-url`.

**F3 — Créneaux récurrents éditables sur la grille** *(Later + Buffer)*
1. Créneaux hebdomadaires matérialisés dans la grille (fond hachuré / contour pointillé), **éditables sur place** : double-clic sur une case vide = créer, croix au survol = supprimer, drag = déplacer. Modification propagée à toutes les semaines suivantes.
2. Deux régimes de programmation explicites : **« prochain créneau »** (flottant, se redistribue si la grille change, réordonnable) vs **« date fixe »** (verrouillé, exclu du drag) — et la distinction est **lisible sur la carte** avant qu'on essaie de la déplacer (correction du défaut de Buffer).

> **Transposition.** `recurring_slots(weekday 0-6, time)`. Chaque cellule reçoit `.slot--recurring` si un slot correspond. Double-clic → `hx-post /slots` ; croix (`.cell:hover .slot__remove`) → `hx-delete` ; drag → `hx-patch`. Le « prochain créneau libre » est **une fonction serveur pure**, recalculée à chaque mutation — aucun état client, donc l'affichage ne peut pas diverger. Sur la carte : badge « créneau » vs « 14 août 18:30 » + icône cadenas et `draggable="false"` sur les posts à date fixe.

**F4 — Cycle de vie et échec** *(Buffer)*
1. 5 états = 5 emplacements : brouillon / en attente / programmé / publié / **échoué**. L'échec **reste dans la file**, il n'est pas archivé silencieusement.
2. La carte échouée porte **2 actions de reprise nommées** : « Réessayer maintenant » et « Remettre en file ».
3. Le message d'erreur est **traduit en cause + geste avec seuils chiffrés** (« Instagram : image > 8 Mo, compresser sous 8 Mo »), jamais un code API brut.
4. Tri « problèmes d'abord » : `ORDER BY (status='failed') DESC, scheduled_at`.

> **Transposition.** `ERROR_MAP = {api_code: (message_fr, action_fr, doc_anchor)}` en Python alimente `_error_banner.html`. Un unique partial `_post_card.html` prend `status` et applique une classe modificatrice (`.card--failed` = liseré gauche 3 px `--danger` + bandeau) : la signalétique est **garantie identique** sur calendrier, dashboard et liste. Les 2 reprises sont deux `<form method=post>` **dans la carte**, pas un menu. Reprise **automatique avec back-off** pour les erreurs transitoires (média pas prêt, timeout) ; reprise manuelle réservée aux erreurs de contenu.

### 3.3 Éditeur

**F1 — Preview par plateforme, en temps réel** *(Buffer)* : chrome de la plateforme, ratio du média (4:5, 1:1, 9:16), point de troncature de la légende, compteur par plateforme, dépassement = surlignage + bouton désactivé + **nom de la plateforme fautive** et pastille d'erreur sur son onglet.
> **Transposition.** Chaque preview est un `<article class="pv pv--ig">` en HTML statique, média en `aspect-ratio: 4/5; object-fit: cover`, troncature en CSS (`-webkit-line-clamp: 2` + « … plus »). Compteur : un handler `input` vanilla qui écrit un `textContent` et fait `toggleAttribute('disabled')` sur le submit. Onglets par plateforme en radios + `:checked ~` — zéro JS.

**F2 — Légende commune puis divergence non destructive** *(Buffer)* : un `<textarea name="caption_base">` + `<details>` « Personnaliser par réseau » contenant **un textarea par TYPE de réseau** (pas par compte : 3 comptes IG = 1 boîte), `placeholder` = texte de base, valeur vide = héritage résolu côté serveur à la publication.

**F3 — Export lourd, progression réelle** *(Linear + Eagle)* : un job FFmpeg n'affiche **jamais** un spinner indéterminé bloquant. Barre de progression chiffrée rafraîchie par `hx-trigger="every 1s"` sur un endpoint de statut, **annulable**, et l'attente est **annoncée à l'avance**.

**F4 — Panneau de propriétés** *(Eagle + Linear)* : sections `<details>` repliables **individuellement**, état mémorisé en `localStorage` par id de section ; rangées à `--menu-row-h`, libellé à gauche / contrôle à droite ; édition au clavier avec application immédiate (`hx-patch` sur `blur, keyup[key=='Enter']`), **aucun bouton de validation**.

### 3.4 Analytics

**F1 — Un sélecteur de période, global** *(Metricool)* : en haut à droite, presets (7 j / 30 j / mois en cours / mois précédent / personnalisé), pousse `?start=&end=` dans l'URL et cible tout le `<main>`.

**F2 — Comparaison par défaut, honnête** *(Metricool)* : composant `kpi_card(label, value, delta_pct, delta_abs)` — valeur en `--title-1`, dessous `▲ 1,8 %` avec `title="+2 026 abonnés vs 16 mai – 14 juin"`. **Règle stricte du template** : `{% if delta_pct is not none %}` sinon `<span class="muted">—</span>`. **Une seule règle de comparaison dans toute l'application**, écrite à l'écran (correction de l'incohérence Analytics/Reports de Metricool).

**F3 — Agrégat → graphe → tableau** *(Metricool)* : 5 chiffres d'abord ; puis un bloc de graphe avec **sélecteur de métrique + sélecteur de breakdown** (`hx-target` sur le seul bloc) plutôt que N graphes empilés ; puis la table à miniatures, en-têtes triables (`hx-get "?sort=saves&dir=desc"`, caret ▲/▼ sur la colonne active, `ORDER BY` en SQL — pas de tri JS), sélecteur de colonnes en `<details>` + checkboxes qui togglent des classes CSS (`.col-saves{display:none}`) — pur CSS, aucune requête.

**F4 — Heatmap actionnable** *(Metricool)* : `<table>` 8 colonnes × 25 lignes, chaque `<td>` contenant un `<a hx-get="/calendrier/nouveau?jour=2&heure=19">`. Intensité **monochrome** : `style="--v:.73"` + `background: color-mix(in oklch, var(--accent) calc(var(--v)*100%), transparent)`. Toggle valeurs = une classe sur le conteneur (`.show-values td::after{content:attr(data-pct)}`). `aria-label` complet sur chaque cellule. Si données insuffisantes : grille en gris + bandeau « estimation générique, pas vos données ».

**F5 — Fraîcheur et pannes** *(Metricool)* : horodatage de dernière synchro en **date absolue** ; si token mort, `<div role="alert">` en haut nommant le compte, la date de dernière donnée valide et un lien direct de reconnexion ; sur un graphe tronqué, une note sous le graphe. **Jamais un zéro à la place d'une donnée absente.**

### 3.5 Dashboard et Réglages

**Dashboard** *(Linear)* : chaque tuile est un `<a>` vers une **vue pré-filtrée** (`/viewer?used=false`, `/calendrier?status=failed`, `/reglages#connexions`). Mêmes hauteurs, rayons et filets que partout. Tient en un écran à 1440×900. Une entrée « inbox » : médias jamais classés / jamais publiés, avec compteur *(Raindrop : « Unsorted »)*.

**Réglages** *(Linear + Metricool + Buffer + Eagle)* : blocs séparés par un filet 1 px, libellé à gauche / contrôle aligné à droite sur chaque rangée ; persistance au changement sans bouton global ; **champ de recherche filtrant les options** ; une ligne par connexion (nom, pastille, dernière synchro absolue, « Reconnecter »), le compte expiré remonté **en tête** ; avant toute déconnexion, dire explicitement **ce qui sera conservé** ; créneaux récurrents configurables **ici ET sur le calendrier** (deux points d'entrée, une seule donnée).

### 3.6 Global — palette de commandes ⌘K *(Linear + Eagle)*

Un unique `<dialog>` dans `layout.html`, ouvert par un `keydown` global sur `(e.metaKey||e.ctrlKey) && e.key==='k'`. Contenu **contextuel** : `hx-get "/palette?ctx=viewer&sel=12,45"` avec `hx-trigger="keyup changed delay:120ms from:#palette-input"`. Chaque `<li>` affiche **le raccourci clavier à droite** — la palette est un tutoriel qui se rend obsolète. Navigation `↑/↓/Entrée/Échap`, `aria-activedescendant`, portion matchée en `<mark>`, **chemin parent affiché en gris** pour les dossiers/vues (c'est ce détail qui fait la différence, pas le fuzzy). Positionnée **près de l'élément invoqué**, pas centrée. La **même table Python de raccourcis** génère la palette, les `title=` des boutons et le panneau `?` — aucune divergence possible entre la doc et le comportement.

---

## 4. GRILLE DE NOTATION

**Mode d'emploi du critic.** Chaque ligne est une question fermée : **oui / non / non applicable**.
Score = `(bloquants OK / bloquants total)` en premier, puis `(importants OK / importants total)`, puis bonus.
**Un seul bloquant manquant = l'écran ne passe pas la barre**, quel que soit le reste.

### 4.1 Viewer — 20 critères

| # | Critère (question fermée) | Poids | Référence |
|---|---|---|---|
| V1 | Un contrôle de densité (slider ou +/−) est-il visible en permanence dans la barre d'outils, sans ouvrir de menu ni de page de réglages ? | bloquant | Eagle |
| V2 | À densité par défaut sur 1440×900, compte-t-on au moins 30 vignettes visibles sans scroller, avec des gouttières ≤ 8 px ? | bloquant | Eagle + Google Photos |
| V3 | Les vignettes conservent-elles leur ratio d'origine dans au moins un mode d'affichage (une image portrait est visiblement plus haute que large) ? | bloquant | Google Photos |
| V4 | La grille s'étend-elle sur toute la largeur utile, sans conteneur centré laissant plus de 5 % de marge vide de chaque côté ? | bloquant | Eagle |
| V5 | Les emplacements de vignettes sont-ils dimensionnés avant l'arrivée des images (aucune tuile déjà affichée ne se déplace pendant le chargement) ? | bloquant | Google Photos |
| V6 | En scrollant une bibliothèque de plusieurs milliers d'éléments, l'ascenseur garde-t-il une taille constante et le nombre de tuiles dans le DOM reste-t-il borné (< 200) ? | bloquant | Google Photos |
| V7 | Le chargement se fait-il par scroll continu ou virtualisation, sans pagination numérotée (page 1 / 2 / 3) ? | bloquant | Eagle |
| V8 | Au survol d'une vignette, un contrôle de sélection apparaît-il dans un coin sans que la tuile ni ses voisines ne changent de position ? | bloquant | Google Photos + Raindrop |
| V9 | Le clic sur le contrôle de sélection et le clic sur l'image ont-ils deux effets distincts (sélectionner vs ouvrir), sans « mode sélection » à activer au préalable ? | bloquant | Google Photos |
| V10 | La sélection multiple supporte-t-elle Maj+clic (plage) ET Ctrl/⌘+clic (picorage) ? | bloquant | Eagle |
| V11 | Dès qu'au moins un média est sélectionné, une barre d'actions apparaît-elle en affichant le **nombre exact** d'éléments sélectionnés et au plus 5 actions ? | bloquant | Raindrop + Google Photos |
| V12 | Les filtres actifs sont-ils visibles en permanence sous forme de jetons retirables d'un clic dans une barre persistante, plutôt qu'enfermés dans une modale ? | bloquant | Eagle |
| V13 | Le panneau de filtres n'affiche-t-il que les valeurs ramenant ≥ 1 résultat, chacune avec son compte, et ces comptes se recalculent-ils après application d'un premier filtre ? | bloquant | Raindrop (corrigé) |
| V14 | L'état complet de la vue (dossier, filtres, tri, densité, recherche) est-il encodé dans l'URL — ouvrir l'URL dans un autre onglet restitue exactement la même vue ? | bloquant | Eagle + Raindrop |
| V15 | La barre d'espace ouvre-t-elle un aperçu agrandi du média surligné, navigable aux flèches ←/→ et refermable par Échap **en restaurant la position de scroll exacte** ? | bloquant | Eagle + Google Photos |
| V16 | Le nombre total d'éléments de la vue courante est-il affiché en chiffre à l'écran ? | bloquant | Eagle |
| V17 | Un panneau d'inspection latéral affiche-t-il pour le média sélectionné au minimum dimensions, poids, format, date, tags et URL source — sans ouvrir de modale ? | important | Eagle |
| V18 | Le filtrage propose-t-il au moins 6 dimensions distinctes parmi format, orientation/ratio, dimensions, poids, durée, date, tag, note, profil source, usage (publié / jamais utilisé) ? | important | Eagle + Raindrop |
| V19 | Chaque vignette porte-t-elle un indicateur d'usage lisible sans survol (déjà publié / programmé vs jamais utilisé) ? | important | Later |
| V20 | Un média peut-il appartenir à plusieurs collections simultanément, et la fiche liste-t-elle toutes ses collections d'appartenance ? | important | Raindrop (corrigé) |
| V21 | Le survol d'une vignette vidéo déclenche-t-il un aperçu animé sans aucun clic ? | important | Eagle |
| V22 | Peut-on parcourir la grille entièrement au clavier (flèches ou J/K/H/L) avec un élément visiblement surligné à tout moment, et une action à une touche (T tag, D télécharger, X sélectionner) s'applique-t-elle à l'élément surligné ? | important | Linear + Eagle |
| V23 | Peut-on enregistrer une combinaison de filtres comme vue réutilisable apparaissant dans la navigation latérale au même rang qu'un dossier, visuellement distinguée des dossiers manuels ? | important | Eagle |
| V24 | Existe-t-il des entrées de navigation dédiées aux médias non classés et non tagués, avec compteur ? | important | Eagle + Raindrop |
| V25 | Existe-t-il un contrôle « Affichage » permettant de basculer individuellement les propriétés montrées sur chaque vignette (source, date, tags, dimensions, durée, statut), appliqué sans bouton « Appliquer » et conservé après rechargement ? | important | Linear |
| V26 | Les en-têtes de groupe (date ou profil) restent-ils collés en haut pendant le scroll, et un contrôle sur l'en-tête sélectionne-t-il d'un clic tout le groupe ? | important | Google Photos |
| V27 | L'écran de doublons présente-t-il les candidats en groupes délimités et comparables côte à côte, chacun affichant résolution, poids et format ? | bloquant | Eagle |
| V28 | L'écran de doublons distingue-t-il deux modes nommés (identiques au bit près vs visuellement similaires) et le seuil de similarité est-il réglable APRÈS le scan sans relancer le calcul ? | important | Eagle |
| V29 | Avant d'exécuter la déduplication, l'interface demande-t-elle explicitement quel exemplaire garder ET quelles métadonnées conserver (tags, collections, notes) ? | important | Eagle |
| V30 | Les couleurs dominantes d'un média sont-elles affichées dans l'inspector et cliquables pour lancer une recherche par couleur ? | bonus | Eagle |
| V31 | Existe-t-il une recherche par image de référence (trouver les médias visuellement proches à partir d'un média de la grille) ? | bonus | Eagle |
| V32 | Peut-on ouvrir un média dans un panneau détaché épinglé pour en comparer deux côte à côte sans quitter la grille ? | bonus | Eagle |

*(16 bloquants, 11 importants, 3 bonus.)*

### 4.2 Calendrier — 15 critères

| # | Critère | Poids | Référence |
|---|---|---|---|
| C1 | Une bibliothèque de médias est-elle affichée en permanence à côté de la grille, sur le même écran, sans navigation vers le Viewer ? | bloquant | Later |
| C2 | Peut-on créer un post en glissant une vignette de cette bibliothèque vers une case du calendrier, le composer s'ouvrant pré-rempli (média + date + heure) ? | bloquant | Later |
| C3 | Existe-t-il un chemin de secours sans drag : un clic sur une case vide ouvre-t-il le composer avec cette date/heure déjà remplies ? | bloquant | Buffer |
| C4 | Une carte de post affiche-t-elle simultanément la miniature du média, l'heure et l'identification de la plateforme cible ? | bloquant | Later + Buffer |
| C5 | Peut-on lire l'état d'un post (brouillon / programmé / publié / échoué) sur la capture par un signal non textuel (liseré, pastille, icône) **doublé d'un libellé**, sans ouvrir le post ni survoler ? | bloquant | Buffer + Linear |
| C6 | Peut-on déplacer un post programmé par glisser-déposer, la nouvelle heure s'affichant immédiatement sans rechargement complet, avec retour arrière + toast si le serveur refuse ? | bloquant | Later + Linear |
| C7 | Une carte de post en échec porte-t-elle, dans la carte elle-même, au moins deux actions de reprise distinctes et nommées (réessayer maintenant / remettre en file) ? | bloquant | Buffer |
| C8 | Le message affiché sur un post en échec nomme-t-il la cause précise et le geste correctif avec un seuil chiffré, plutôt qu'un libellé générique ou un code API brut ? | bloquant | Buffer |
| C9 | Une bascule de granularité (Mois / Semaine / Liste) est-elle visible en permanence en haut de l'écran, sans ouvrir de menu ? | important | Later + Buffer |
| C10 | La bibliothèque latérale est-elle filtrée par défaut sur les médias non encore utilisés, ce filtre étant visible et désactivable ? | important | Later |
| C11 | Les créneaux récurrents vides sont-ils matérialisés dans la grille (fond ou contour dédié), cliquables, et modifiables sans passer par une page de réglages ? | important | Later + Buffer |
| C12 | Le fuseau horaire de référence est-il écrit en clair sur l'écran, sans ouvrir les réglages ? | important | Buffer |
| C13 | La barre d'outils propose-t-elle au moins trois filtres persistants (plateforme, état du post, tag/campagne) dont l'état survit à un rechargement de page ? | important | Buffer |
| C14 | Les posts en échec sont-ils remontés en tête de la vue liste (tri « problèmes d'abord ») ? | important | Buffer |
| C15 | Les posts à date fixe sont-ils visuellement distingués des posts flottants qui suivent la grille de créneaux, **avant** qu'on essaie de les déplacer ? | important | Buffer (corrigé) |
| C16 | Peut-on sélectionner plusieurs posts programmés et leur appliquer une action groupée (décaler, changer de plateforme, supprimer) ? | bonus | Eagle |
| C17 | Une prévisualisation de la grille Instagram en 3 colonnes, mêlant publiés et programmés, est-elle atteignable en 1 clic depuis le calendrier ? | bonus | Later |
| C18 | Les créneaux recommandés par la heatmap sont-ils surlignés directement dans la grille horaire plutôt que listés ailleurs sur la page ? | bonus | Metricool + Later |

*(8 bloquants, 7 importants, 3 bonus.)*

### 4.3 Analytics — 14 critères

| # | Critère | Poids | Référence |
|---|---|---|---|
| A1 | Y a-t-il un sélecteur de plage de dates unique et visible en haut de l'écran, s'appliquant à tous les blocs de la page (et non un filtre par bloc) ? | bloquant | Metricool |
| A2 | Chaque carte KPI affiche-t-elle sous la valeur une variation vs période précédente (flèche + pourcentage), sans que l'utilisateur ait à activer une option ? | bloquant | Metricool |
| A3 | Quand la période précédente n'est pas couverte par les données, la variation est-elle **absente** ou remplacée par un tiret neutre, au lieu d'afficher 0 % ? | bloquant | Metricool |
| A4 | L'écran commence-t-il par un bloc de 5 agrégats maximum AVANT tout tableau détaillé ? | bloquant | Metricool |
| A5 | L'écran affiche-t-il un horodatage de dernière synchronisation réussie en date absolue (« 13 août 2026, 04:12 ») et non « il y a un moment » ? | bloquant | Metricool |
| A6 | Si un token est expiré ou la synchro en échec, un bandeau apparaît-il en haut de l'écran, nommant le compte, la date de dernière donnée valide, et proposant un lien direct de reconnexion — au lieu d'un graphique vide ou d'un toast ? | bloquant | Metricool + Linear |
| A7 | Le tableau des meilleurs posts affiche-t-il une miniature sur chaque ligne, avec width/height fixés (aucun saut de mise en page au chargement) ? | bloquant | Metricool |
| A8 | Les en-têtes de colonnes sont-ils cliquables pour trier, avec un indicateur ▲/▼ sur la colonne active ? | bloquant | Metricool |
| A9 | La période de comparaison est-elle nommée explicitement à l'écran (« vs 16 mai – 14 juin ») et le survol de la variation donne-t-il la valeur absolue en plus du pourcentage ? | important | Metricool |
| A10 | Existe-t-il une heatmap des meilleurs horaires en grille 7 colonnes × 24 lignes, en dégradé **monochrome** (plus foncé = plus élevé), avec un toggle affichant la valeur dans chaque cellule ? | important | Metricool |
| A11 | Cliquer une cellule de la heatmap ouvre-t-il le calendrier avec jour et heure pré-remplis, plutôt que de ne rien faire ? | important | Metricool |
| A12 | Quand les données de la heatmap sont estimées ou insuffisantes, l'écran l'indique-t-il par un texte explicite plutôt que d'afficher une grille silencieusement fausse ? | important | Metricool |
| A13 | Le tableau comporte-t-il au moins 8 colonnes métriques distinctes, défile-t-il horizontalement dans son propre conteneur avec première colonne sticky, et un contrôle permet-il de masquer des colonnes ? | important | Metricool |
| A14 | Les graphes utilisent-ils des courbes pour les séries temporelles et des barres pour les volumes discrets, avec un sélecteur de métrique et/ou de ventilation plutôt que N graphes figés empilés ? | important | Metricool |
| A15 | Les valeurs numériques sont-elles alignées à droite en chiffres tabulaires, et les graphes utilisent-ils la palette sémantique de l'app (accent + 4 couleurs max) plutôt qu'une palette de librairie par défaut ? | important | Linear |
| A16 | Un export CSV est-il accessible en un clic depuis l'écran, et la liste des derniers exports (date, période, lien) est-elle conservée ? | bonus | Metricool |
| A17 | Les limites connues d'une métrique sont-elles expliquées au point d'usage (petit texte ou icône info à côté du chiffre) plutôt que dans une page d'aide séparée ? | bonus | Metricool |

*(8 bloquants, 7 importants, 2 bonus.)*

### 4.4 Éditeur — 12 critères

| # | Critère | Poids | Référence |
|---|---|---|---|
| E1 | La prévisualisation reproduit-elle le rendu propre à chaque plateforme sélectionnée (ratio du média, troncature de la légende, chrome de la plateforme), plutôt qu'une preview unique générique ? | bloquant | Buffer |
| E2 | Un compteur de caractères par plateforme est-il visible, avec surlignage du texte excédentaire en cas de dépassement ? | bloquant | Buffer |
| E3 | Quand une contrainte de plateforme est violée, le bouton de validation est-il désactivé ET le message nomme-t-il explicitement la plateforme fautive ? | bloquant | Buffer (corrigé) |
| E4 | Peut-on passer d'une légende commune à une légende par plateforme sans perdre la légende commune déjà saisie (héritage visible en placeholder) ? | bloquant | Buffer |
| E5 | Une opération lourde (export FFmpeg) affiche-t-elle une progression chiffrée réelle et annulable, au lieu d'un spinner indéterminé bloquant l'écran ? | bloquant | Linear + Eagle |
| E6 | La preview se met-elle à jour pendant la frappe, sans validation intermédiaire ni bouton « Actualiser » ? | important | Buffer |
| E7 | L'action finale se résume-t-elle à un seul bouton de validation, le « quand » étant relégué dans un sélecteur adjacent plutôt qu'en plusieurs boutons concurrents ? | important | Buffer |
| E8 | Le ratio d'export cible (9:16, 4:5, 1:1) est-il affiché et sélectionnable visiblement à l'écran, avec les seuils chiffrés (poids max, durée max) lisibles avant validation ? | important | Buffer + Later |
| E9 | Le panneau de propriétés est-il organisé en sections repliables individuellement, l'état de repli étant mémorisé entre deux visites ? | important | Eagle |
| E10 | Les propriétés d'objet (position, taille, ratio, couleur) sont-elles éditables au clavier avec application immédiate, sans bouton de validation ? | important | Linear |
| E11 | Les raccourcis canvas standards fonctionnent-ils (⌘Z annuler, ⌘D dupliquer, Suppr, flèches pour déplacer de 1 px, Maj+flèches de 10 px) ? | important | Linear |
| E12 | Les panneaux d'outils utilisent-ils la même hauteur de rangée et les mêmes contrôles que les popovers du reste de l'app (28 px / 22 px) ? | important | Linear |
| E13 | Le rendu exporté revient-il dans le Viewer identifié comme dérivé, avec un lien visible vers le média source, plutôt que comme un fichier orphelin ? | bonus | Later |
| E14 | Les raccourcis clavier des opérations principales sont-ils affichés à côté des commandes correspondantes ? | bonus | Eagle + Linear |

*(5 bloquants, 7 importants, 2 bonus.)*

### 4.5 Dashboard — 10 critères

| # | Critère | Poids | Référence |
|---|---|---|---|
| D1 | Chaque bloc chiffré mène-t-il à une vue pré-filtrée précise (cliquer le chiffre ouvre la liste correspondante), plutôt que d'afficher un compteur inerte ? | bloquant | Linear + Eagle |
| D2 | Les posts en échec et les comptes déconnectés sont-ils comptés et visibles depuis le dashboard, avec un lien direct vers l'action corrective ? | bloquant | Buffer + Metricool |
| D3 | Les tuiles utilisent-elles la même hauteur, le même rayon et le même filet que les autres conteneurs de l'app (aucun composant « spécial dashboard ») ? | bloquant | Linear |
| D4 | Le dashboard tient-il en un écran à 1440×900 sans défilement, ou chaque bloc au-delà est-il justifié par une information non disponible ailleurs ? | important | Linear |
| D5 | L'état de connexion de chaque compte est-il visible sans clic (pastille verte / ambre / rouge **+ libellé**) ? | important | Metricool |
| D6 | Une entrée « à traiter » (médias jamais classés ou jamais publiés) est-elle exposée avec son compteur, à la manière d'une boîte de réception ? | important | Raindrop |
| D7 | Les prochains posts programmés sont-ils affichés sous forme de vignettes cliquables menant directement à la case du calendrier correspondante ? | important | Later |
| D8 | Les blocs sont-ils triés par une métrique explicite (ex. urgence, puis date) plutôt que dans un ordre arbitraire ou alphabétique ? | important | Metricool |
| D9 | L'état vide (aucun média scrapé, aucun post programmé) affiche-t-il un titre ~20 px, une description ~14 px limitée à ~360 px de large et au plus 2 boutons, plutôt qu'un texte gris centré ? | important | Linear |
| D10 | Chaque bloc du dashboard est-il masquable depuis les réglages ? | bonus | Linear |

*(3 bloquants, 6 importants, 1 bonus.)*

### 4.6 Réglages — 10 critères

| # | Critère | Poids | Référence |
|---|---|---|---|
| R1 | L'écran liste-t-il une ligne par connexion avec nom du compte, état, **date absolue** de dernière synchro réussie, et un bouton « Reconnecter » ? | bloquant | Metricool |
| R2 | Un compte dont l'autorisation a expiré est-il remonté en tête de la liste et mis en évidence, avec un bouton d'action unique ? | bloquant | Buffer |
| R3 | Peut-on définir des créneaux de publication récurrents par jour de semaine, avec désactivation d'un jour entier en un seul clic ? | bloquant | Buffer |
| R4 | Chaque changement de réglage est-il persisté sans bouton « Enregistrer » global, avec une confirmation discrète et non bloquante ? | bloquant | Linear |
| R5 | Avant une action de déconnexion, l'interface indique-t-elle explicitement ce qui sera conservé ou perdu ? | important | Metricool |
| R6 | Les réglages sont-ils groupés en blocs nommés séparés par un filet, avec libellé à gauche et contrôle aligné à droite sur chaque rangée ? | important | Linear |
| R7 | La page dispose-t-elle d'un champ de recherche filtrant les options ? | important | Eagle |
| R8 | Les réglages d'affichage du Viewer (densité, propriétés affichées, mode par défaut) sont-ils groupés sur cette page plutôt que dispersés dans des menus contextuels ? | important | Google Photos |
| R9 | Le sélecteur de thème propose-t-il clair / sombre / système, et est-il aussi atteignable depuis la palette de commandes ? | important | Linear |
| R10 | Les créneaux récurrents sont-ils modifiables depuis cet écran **ET** directement sur le calendrier (deux points d'entrée pour la même donnée) ? | bonus | Later |
| R11 | Des presets de fréquence (3× / semaine, 5× / semaine) évitent-ils de saisir chaque horaire à la main ? | bonus | Buffer |

*(4 bloquants, 5 importants, 2 bonus.)*

---

## 5. Grille transversale — applicable à tous les écrans

### 5.1 Système de design (Linear)

| # | Critère | Poids |
|---|---|---|
| G1 | Compte-t-on 6 tailles de police ou moins dans l'ensemble de la capture (hors titre de page unique) ? | bloquant |
| G2 | Toutes les valeurs d'espacement visibles (gaps, paddings) sont-elles des multiples de 4 px ? | bloquant |
| G3 | La hiérarchie des panneaux est-elle portée par des filets 1 px et des niveaux de fond, **sans aucune ombre portée** sur les éléments non flottants ? | bloquant |
| G4 | La couleur saturée est-elle réservée aux indicateurs d'état et à l'action primaire, sans dégradé décoratif ni bouton coloré secondaire ? | bloquant |
| G5 | Compte-t-on au plus 3 rayons de bordure différents dans la capture ? | important |
| G6 | Distingue-t-on au moins 3 niveaux de couleur de texte (primaire / secondaire / tertiaire) et non seulement 2 ? | important |
| G7 | Le texte principal est-il à 13–15 px, et non à 16 px+ ? | important |
| G8 | Les icônes se limitent-elles à 2 tailles (14 et 16 px), sans logo de plateforme en couleur pleine surface ? | important |

### 5.2 Thème et couleur

| # | Critère | Poids |
|---|---|---|
| G9 | En basculant clair/sombre, la mise en page reste-t-elle identique au pixel près, sans flash blanc au chargement ? | bloquant |
| G10 | Le fond sombre est-il un gris très foncé (≈ #0a0b0d–#101214) et le fond clair un gris très clair (≈ #f4f5f7), plutôt que noir/blanc purs ? | important |
| G11 | Toute information portée par une couleur est-elle doublée d'une forme, d'une icône ou d'un libellé (lisible en niveaux de gris) ? | bloquant |

### 5.3 Clavier et accessibilité

| # | Critère | Poids |
|---|---|---|
| G12 | Un anneau de focus visible (outline ≥ 2 px, offset ≥ 2 px) apparaît-il en navigation Tab sur tout élément interactif ? | bloquant |
| G13 | ⌘/Ctrl+K ouvre-t-il une palette de commandes filtrable au clavier depuis n'importe quel écran, avec les commandes de l'écran courant en premier et le raccourci affiché à droite de chaque ligne ? | bloquant |
| G14 | La touche `?` ouvre-t-elle un panneau listant les raccourcis actifs dans la vue courante, et les raccourcis apparaissent-ils aussi dans les tooltips et menus contextuels ? | important |
| G15 | Les raccourcis à une touche sont-ils inactifs quand le focus est dans un champ texte (aucun caractère parasite inséré) ? | bloquant |
| G16 | Les actions destructives exigent-elles un modificateur ou une confirmation nommant l'élément concerné, alors que les actions fréquentes et réversibles sont à une touche ? | important |
| G17 | Aucun raccourci ne dépend-il d'une disposition clavier US (testable en AZERTY) ? | bonus |

### 5.4 Vitesse perçue et états

| # | Critère | Poids |
|---|---|---|
| G18 | Aucun spinner n'apparaît-il pour une action dont l'effet est calculable côté client (tag, sélection, filtre, changement de densité) ? | bloquant |
| G19 | Toute action produit-elle un retour visuel en moins de 100 ms (état pressé, mutation optimiste), même si la réponse serveur arrive plus tard ? | bloquant |
| G20 | Pour une opération réellement asynchrone, l'indicateur attend-il ≥ 800 ms avant d'apparaître, puis apparaît-il en fondu ? | important |
| G21 | Le chargement d'une zone utilise-t-il un skeleton aux dimensions exactes du contenu attendu, localisé à cette zone, sans écran de chargement global ni saut de mise en page ? | bloquant |
| G22 | Les transitions se limitent-elles à 2 vitesses (≈ 100 ms hover/focus, ≈ 250 ms panneaux), sans animation > 400 ms ? | important |
| G23 | Les opérations longues (scrape, scan de doublons, export FFmpeg) affichent-elles une progression chiffrée et annoncent-elles à l'avance qu'elles peuvent être longues ? | important |
| G24 | Un échec de mutation optimiste provoque-t-il un retour à l'état antérieur **plus** un message expliquant la cause, jamais un état silencieusement divergent ? | bloquant |
| G25 | Une mise à jour de contenu déclenchée par le système (fin de scrape) est-elle annoncée par un bandeau « N nouveaux médias — afficher » plutôt qu'appliquée d'office sous les yeux de l'utilisateur ? | important |

### 5.5 États vides et erreurs

| # | Critère | Poids |
|---|---|---|
| G26 | L'état vide affiche-t-il un titre ~20 px, une description ~14 px limitée à ~360 px et au plus 2 boutons de 32 px, avec la même échelle typographique que le reste de l'app ? | important |
| G27 | Une erreur est-elle une page ou un bloc composé (titre + cause + geste correctif + action), et non un bandeau rouge ou un toast portant un code brut ? | bloquant |
| G28 | Quand un média ou une donnée est indisponible, un placeholder aux mêmes dimensions occupe-t-il la place, préservant l'alignement de la grille ? | important |
| G29 | Une donnée absente est-elle affichée comme absente (tiret neutre) plutôt que comme un zéro ? | bloquant |

### 5.6 Mobile et densité

| # | Critère | Poids |
|---|---|---|
| G30 | Sur un viewport de 375 px, toutes les cibles tactiles mesurent-elles au moins 44×44 px, et les contrôles normalement révélés au survol sont-ils rendus visibles en permanence ? | bloquant |
| G31 | Sur 375 px, le contenu principal occupe-t-il au moins 80 % de la hauteur (chrome + barres ≤ 20 %) ? | important |
| G32 | Les tableaux larges défilent-ils dans leur propre conteneur `overflow-x`, sans faire déborder la page horizontalement ? | bloquant |
| G33 | Sous 1024 px, les panneaux latéraux sont-ils escamotables, et chaque écran reste-t-il utilisable sans eux ? | important |
| G34 | La navigation latérale persistante liste-t-elle les 6 écrans avec l'écran courant marqué visuellement, en 6 items ou moins au premier niveau ? | bloquant |

---

## 6. Ce qu'on refuse d'imiter

| Défaut relevé | Chez qui | Notre parti pris opposé |
|---|---|---|
| Bouton de validation désactivé sans dire **quelle** plateforme bloque | Buffer | Message nommant la plateforme et le nombre de caractères en trop, + pastille d'erreur sur l'onglet fautif. |
| Aucune reprise automatique après échec de publication : deux boutons manuels uniquement | Buffer, Later | Reprise **automatique avec back-off** pour les erreurs transitoires (média pas prêt, timeout) ; reprise manuelle réservée aux erreurs de contenu. |
| Plafond de vue mois traité comme une contrainte à absorber par l'utilisateur (« passez en vue semaine ») | Buffer | Virtualisation et repli automatique côté produit ; l'utilisateur ne change jamais de vue à cause d'une limite technique. |
| Verrouillage d'un post à date fixe non signalé avant l'échec du geste | Buffer | Icône cadenas + badge de mode sur la carte, `draggable="false"`, curseur `not-allowed`. |
| Échec silencieux du drag quand le filtre est sur « tous les canaux » | Buffer | Aucune contrainte technique remontée en échec muet : soit le geste marche, soit une infobulle explique en une phrase. |
| Créneaux créés en publication par notification **par défaut**, à basculer manuellement ensuite | Later | Le défaut est le comportement attendu (auto-publication) ; le repli manuel est un opt-in explicite. |
| Redimensionnement d'images délégué à l'utilisateur | Later | L'adaptation aux ratios de destination est prise en charge par l'Éditeur, jamais renvoyée à l'utilisateur. |
| Densité de 20–30 éléments chiffrés par écran sans hiérarchie typographique | Metricool | Même densité d'information, mais 4 niveaux de couleur de texte, 5 tailles et des sections repliables. |
| Écarts de données documentés dans une page d'aide lointaine | Metricool | Avertissement **au point d'usage**, à côté du chiffre concerné. |
| Deux règles de comparaison différentes selon l'écran (Analytics vs Reports) | Metricool | **Une seule règle de comparaison** dans toute l'application, et écrite à l'écran. |
| Période de comparaison non modifiable | Metricool | Un second sélecteur de période de référence, optionnel — il coûte peu. |
| Heatmap sans indication de la source ni du volume de données | Metricool | La heatmap affiche le nombre de posts et la fenêtre qui l'alimentent, et se déclare « estimation générique » quand c'est le cas. |
| Double publication d'un même post | Metricool | Jobs **idempotents** (clé d'unicité `post_id + scheduled_at`) et état réel affiché sans ambiguïté. |
| Scan de similarité lancé à l'aveugle, sans durée restante | Eagle | Hash perceptuel **pré-calculé à l'ingestion** ; le scan est instantané et le seuil se règle côté client. |
| Aucune version mobile / tablette | Eagle | Le Viewer reste utilisable à 375 px : grille responsive, panneaux escamotables, contrôles de survol rendus permanents au doigt. |
| Tags plats non hiérarchisables, sidebar de tags non redimensionnable | Eagle | Groupes de tags sur au moins un niveau + panneau latéral redimensionnable, largeur persistée. |
| Rotation et miroir aux conséquences opposées et invisibles | Eagle | Deux actions voisines dans l'UI ont toujours le même modèle de persistance ; sinon, elles sont séparées visuellement. |
| Un élément ne peut vivre que dans **une** collection ; le contournement crée un doublon | Raindrop | Relation **N-N** : un média dans 5 collections, une seule fiche, un seul jeu de tags. |
| Pas de collections dynamiques / recherches sauvegardées | Raindrop | Une vue enregistrée = une query string stockée, rendue exactement comme une collection manuelle. Quasi gratuit puisque l'état vit déjà dans l'URL. |
| Liste des facettes figée après une première sélection | Raindrop | Facettes **recalculées** à chaque sélection (`GROUP BY` sous les filtres actifs). |
| Collection par défaut toujours « Non classé » plutôt que la dernière utilisée | Raindrop | Mémoriser la dernière cible et la proposer en premier. |
| Aucun raccourci clavier applicatif | Raindrop | Jeu complet dès le départ : `j/k`, `x`, `t`, `1‑5`, `/`, `⌘K`, `?`. |
| Scrubber n'affichant que l'année, invisible au repos, recouvert par le pouce | Google Photos | Scrubber affichant **mois + année**, visible dès le premier rendu, pastille décalée à gauche du doigt. |
| En-têtes de date imposés sans possibilité de les couper | Google Photos | Le groupement est un toggle dès la v1. |
| Recherche IA substituée à la recherche classique, plus lente | Google Photos | La recherche par filtres reste le chemin par défaut ; toute aide « intelligente » est un ajout désactivable. |
| Recherche mélangeant texte et contenu, sans moyen de correspondance exacte | Google Photos | Opérateurs documentés dès le départ : `"phrase exacte"`, `-exclusion`, `profil:`, `type:`, `#tag`, `date:`. |
| Éditeur en mode recadrage persistant | Google Photos | Aucun mode d'édition collant : chaque outil est atteignable en un clic depuis l'état par défaut. |
| Outils déplacés lors d'une refonte sans laisser l'ancien chemin actif | Google Photos | Toute refonte préserve la position des actions les plus fréquentes ou garde l'ancien chemin fonctionnel. |
| Réglage quotidien enterré derrière deux niveaux de menu | Google Photos, Raindrop | Ce qui est réglé plusieurs fois par jour (densité, tri) vit dans la barre d'outils, pas dans les réglages. |
| Prolifération d'axes d'organisation concurrents (tags + dossiers + statuts + projets) | Linear | **Un seul axe principal** (les collections), tout le reste en filtres. |
| Raccourci partiel (Markdown incohérent) forçant le retour à la souris | Linear | Si un chemin clavier existe, il couvre 100 % du flow. |
| Blocs IA imposés dans l'espace de travail | Linear | Tout bloc non essentiel du Dashboard est masquable. |
| Esthétique « fond quasi noir + dégradé violet » devenue cliché SaaS | Linear | On copie le **système** (échelle, constance, retenue, latence), pas la direction artistique : accent bleu 218°, thème clair de premier rang, aucun dégradé. |

---

## 7. Sources

### Buffer
- https://buffer.com/publish
- https://support.buffer.com/article/651-how-to-use-the-new-calendar-feature-on-buffer
- https://support.buffer.com/article/642-scheduling-posts
- https://support.buffer.com/article/514-setting-up-your-timezones-and-posting-schedules
- https://support.buffer.com/article/652-re-ordering-posts-in-your-queue
- https://support.buffer.com/article/665-managing-and-approving-draft-posts
- https://support.buffer.com/article/861-how-to-use-the-all-channels-view-in-buffer
- https://support.buffer.com/article/658-using-notification-publishing
- https://support.buffer.com/article/573-refreshing-a-channel-in-buffer
- https://support.buffer.com/article/581-instagram-error-library
- https://support.buffer.com/article/588-character-limits-for-each-social-network
- https://support.buffer.com/article/618-adding-alt-text-to-your-images
- https://support.buffer.com/article/615-attaching-images-videos-and-other-media-to-your-posts
- https://support.buffer.com/article/650-how-to-delete-failed-posts-in-bulk
- https://buffer.com/cdn-cgi/image/width=1920,quality=75,format=auto/img/feature-pages/publish/publish-list-view.webp
- https://buffer.com/cdn-cgi/image/width=1920,quality=75,format=auto/img/feature-pages/publish/publish-calendar-view.webp
- https://buffer.com/cdn-cgi/image/width=1920,quality=75,format=auto/img/feature-pages/publish/customize.webp
- https://zapier.com/blog/how-to-use-buffer/
- https://uploads-ssl.webflow.com/5d60176738c00e7ae2afeba2/6012ae7ab475e299997aab43_brandboard.pdf

### Later
- https://later.com/instagram-scheduler/visual-instagram-planner/
- https://later.com/instagram-scheduler/desktop-posting/
- https://help.later.com/hc/en-us/articles/360043244573-Managing-Your-Media-from-the-Side-Library
- https://help.later.com/hc/en-us/articles/360042771474-Access-Organize-Your-Media-Library
- https://help.later.com/hc/en-us/articles/360043244233-Preview-Your-Feed-With-Your-Visual-Instagram-Planner
- https://help.later.com/hc/en-us/articles/360043243793-How-to-Quick-Schedule-Your-Week-of-Posts
- https://help.later.com/hc/en-us/articles/360042771694-Find-Your-Best-Times-to-Post-for-Instagram-Facebook-TikTok
- https://help.later.com/hc/en-us/articles/360043245093-Using-Saved-Captions-Hashtags
- https://help.later.com/hc/en-us/articles/360043244653-Organize-your-Media-with-Labels
- https://help.later.com/hc/en-us/articles/1500010572781-Schedule-Copy-Posts-With-Later-on-the-Web
- https://help.later.com/hc/en-us/articles/8843980454295-About-Draft-Posts
- https://help.later.com/hc/en-us/articles/360043361213-Uploading-Media-Format-Requirements
- https://images.ctfassets.net/nfpsrlop6sws/3vnYyG6OTfQpWB6TP2UrYZ/4b013d821268b3a41db07ae2c4610516/later-instagram-visual-planner-hero.png
- https://images.ctfassets.net/nfpsrlop6sws/3QS6K42nnpLRI5ozcSLFwF/a083696f216205cc350a5640b1541723/julyw1-dragdrop-feature.png
- https://www.elegantthemes.com/blog/marketing/later-review

### Metricool
- https://help.metricool.com/get-to-know-metricool-p0zdh
- https://help.metricool.com/your-metrics-in-metricool-full-guide-pcwam
- https://help.metricool.com/how-comparison-periods-work-in-your-metrics-yj8tx
- https://help.metricool.com/best-time-to-post-on-social-media-in-metricool-w7ll9
- https://help.metricool.com/instagram-metrics-ght51
- https://help.metricool.com/instagram-account-metrics-breakdowns-and-charts-is1su
- https://help.metricool.com/how-to-generate-reports-szc5k
- https://help.metricool.com/reporting-your-reports-and-analytics-hub-ejidy
- https://help.metricool.com/how-to-use-campaign-dashboards-zgk0b
- https://help.metricool.com/social-networks-got-disconnected-how-to-reconnect-them-in-metricool-thnrd
- https://help.metricool.com/what-to-do-if-your-social-media-data-isnt-updating-in-metricool-4i4p5
- https://metricool.com/metricool-brand-summary/
- https://metricool.com/metricoolanalytics/
- https://metricool.com/best-time-publish-twitter/
- https://metricool.com/wp-content/uploads/analytics_header-1024x678.webp
- https://metricool.com/wp-content/uploads/instagram-analytics.webp
- https://thesmmstack.com/how-to-use-metricool/

### Eagle
- https://en.eagle.cool/
- https://en.eagle.cool/blog/post/eagle4
- https://en.eagle.cool/blog/post/eagle4-build12
- https://en.eagle.cool/blog/post/eagle-plugin-ai-search
- https://en.eagle.cool/blog/post/how-to-organize-files-with-logic
- https://en.eagle.cool/support/article/interface-toolbar
- https://en.eagle.cool/support/article/interface-sidebar
- https://en.eagle.cool/support/article/interface-inspector
- https://en.eagle.cool/support/article/interface-filter
- https://en.eagle.cool/support/article/interface-image-list
- https://en.eagle.cool/support/article/changing-layouts
- https://en.eagle.cool/support/article/smart-folders
- https://en.eagle.cool/support/article/tags
- https://en.eagle.cool/support/article/the-category-tool-f
- https://en.eagle.cool/support/article/quick-search
- https://en.eagle.cool/support/article/how-to-scan-for-similar-and-duplicate-images
- https://en.eagle.cool/support/article/keyboard-shortcuts
- https://r2-web.eagle.cool/media/home-preview-layout-en.png
- https://r2-web.eagle.cool/media/home-search-filters-en.png
- https://r2-web.eagle.cool/media/home-organize-smart-folders-en.png
- https://r2-web.eagle.cool/media/home-organize-tags-en.png
- https://r2-web.eagle.cool/media/home-organize-find-duplicate-en.png
- https://r2-web.eagle.cool/media/home-preview-space-quicklook.png

### Raindrop.io
- https://raindrop.io/
- https://help.raindrop.io/collections
- https://help.raindrop.io/filters
- https://help.raindrop.io/using-search
- https://help.raindrop.io/bookmarks
- https://help.raindrop.io/tags
- https://help.raindrop.io/limitations
- https://help.raindrop.io/changelog/web
- https://raindrop.io/_next/static/media/collections.ec97115a.png
- https://raindrop.io/_next/static/media/view-modes.0907cc9c.png
- https://raindrop.io/_next/static/media/duplicates.37e76711.png
- https://deepwiki.com/raindropio/app/5-search-and-filtering
- https://raindropio.canny.io/feature-requests/p/please-improve-performance
- https://www.xda-developers.com/raindrop-io-visual-research-board/

### Google Photos
- https://medium.com/google-design/google-photos-45b714dfbed1  *(source de référence pour le layout, la virtualisation et les 3 paliers de chargement)*
- https://medium.com/@danrschlosser/building-the-image-grid-from-google-photos-6a09e193c74a
- https://github.com/schlosser/pig.js
- https://9to5google.com/2025/02/04/google-photos-grid-customizations/
- https://9to5google.com/wp-content/uploads/sites/4/2025/02/Google-Photos-grid-customizations-1.jpg
- https://9to5google.com/2026/07/20/google-photos-classic-search-toggle/
- https://www.androidpolice.com/google-photos-floating-bar-update/
- https://support.google.com/photos/answer/6131416
- https://sites.google.com/site/picasaresources/google-photos-1/how-do-i-select-multiple-pictures
- https://developers.google.com/photos/library/guides/apply-filters
- https://m1.material.io/usability/accessibility.html
- https://www.iphoneincanada.ca/2026/07/31/google-photos-fixes-annoying-timeline-details-in-new-ios-and-android-update/

### Linear
- https://linear.app/now/behind-the-latest-design-refresh
- https://linear.app/now/how-we-redesigned-the-linear-ui
- https://linear.app/now/invisible-details
- https://linear.app/docs/display-options
- https://linear.app/docs/select-issues
- https://linear.app/docs/custom-views
- https://linear.app/changelog/2024-03-20-new-linear-ui
- https://linear.app/changelog/2026-03-12-ui-refresh
- https://static.linear.app/client/assets/Root-Dw0WFEWg.css  *(source des échelles typo/espacement/durées)*
- https://linear.app/login  *(source du shell : sidebar 244px, rayons, seuil de chargement 1000 ms)*
- https://performance.dev/how-is-linear-so-fast-a-technical-breakdown
- https://webassets.linear.app/images/ornj730p/production/9723099db6c7913608fcdc59be40a27a33e528a4-1203x939.png  *(Display options — mesure des 28/21 px)*
- https://webassets.linear.app/images/ornj730p/production/f9ebfb3c39a125665370f2c736acf482eaefe0d1-3312x2484.png
- https://fastshortcuts.com/shortcuts/linear/
- https://styles.refero.design/style/90ce5883-bb24-4466-93f7-801cd617b0d1
