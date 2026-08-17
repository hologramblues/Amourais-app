# CHANGELOG — Refonte UI

Refonte des écrans sur le socle `tokens.css`, menée par quatre lots en parallèle
(Viewer, Calendrier, Analytics, Éditeur), puis jugée par des critics qui ont mesuré,
puis lissée par une passe transversale.

Ce document décrit **ce qui change**, avec les **mesures avant/après** relevées au
navigateur, et l'état du barème de `REFERENCES.md` écran par écran.

---

## 0. Innocuité et suite de tests

| Contrôle | Valeur |
|---|---|
| Empreinte `data/` avant **et** après | `1a0d433713b94b04a3a22aa42acdfb6395b5c2b2` |
| `shasum data/samourais.db` avant **et** après | `b43a7ee25f9bbd28fd180f3857c7f65473555c56` |
| Suite, passe 1 | `499 passed, 30 xfailed in 2.84s` |
| Suite, passe 2 | `499 passed, 30 xfailed in 2.67s` |
| Routes au démarrage | 58 → **61** (+`/favicon.ico`, +`/api/system/status`, +`/api/analytics/profiles`) |
| Pages servies | 8/8 en HTTP 200 |

Aucune écriture dans `data/`. Toutes les vérifications au navigateur ont été menées
sur la copie (`DATA_DIR=…/scratchpad/datacopy`, port 8199).

**Un test a été touché** : `tests/test_web.py::test_les_pages_sont_toutes_servies_par_le_meme_blueprint`.
Il compare l'ensemble des routes à un seul segment avec la liste des 8 pages, en
excluant explicitement les routes techniques (`/health`, `/static`). `/favicon.ico`
a été ajouté à cette liste d'exclusion : ce n'est pas un écran, et le test conserve
exactement son rôle — repérer une **page** ajoutée sans smoke test. Le compte reste
à 499.

---

## 1. Cohérence inter-écrans (passe de lissage)

Les quatre lots ont travaillé en aveugle les uns des autres. Cette passe corrige ce
qu'aucun critic d'écran ne pouvait voir, parce qu'il ne regardait qu'un écran.

### 1.1 Mesures transversales, les 8 pages, les 2 thèmes

Relevé sur `/`, `/profiles`, `/viewer`, `/editor`, `/calendar`, `/analytics`,
`/jobs`, `/settings`, à 1440×900.

| Mesure | Avant | Après |
|---|---|---|
| Polices distinctes rendues | Inter, **Arial**, JetBrains Mono | **Inter + JetBrains Mono** uniquement |
| Éléments hors échelle typo (taille ou police) | Calendrier 22, Analytics 10, Réglages 27, Profils 2, Éditeur 1 | **0 sur les 8 pages** |
| Fond `body`, thème sombre | uniforme `rgb(10,11,13)` | uniforme `rgb(10,11,13)` |
| Fond `body`, thème clair | uniforme `rgb(244,245,247)` | uniforme `rgb(244,245,247)` |
| Rayons distincts par page | jusqu'à 4 (`4 / 8 / 999 / 9999`) | **≤ 3** (`4 / 8 / 9999`) |
| Ombres sur éléments non flottants | 0 | **0** (seule `.cols__menu`, un popover — autorisé G3) |
| Écran courant marqué dans la nav | 8/8 | **8/8** |
| Débordement horizontal | aucun | **aucun** |
| Boîtes système natives (`alert`/`confirm`/`prompt`) | **9** | **0** |

### 1.2 Ce qui a été corrigé, et pourquoi c'était incohérent

**Une même police, deux rendus.** Les écrans Calendrier, Éditeur et Analytics ne
chargent pas `samourais.css` — seul le Viewer le fait. Ils perdaient donc la
normalisation `font: inherit` des contrôles natifs : tout `<button>`, `<input>` ou
`<select>` non stylé explicitement retombait sur la police de l'agent utilisateur.

- Calendrier : 11 éléments en **Arial 13,333 px**, dont les cartes de post du tiroir
  et de la file, rendues en `<button>`. La **même** carte s'affichait en Inter dans
  la grille et en Arial dans le tiroir.
- FullCalendar impose en plus `font-size: .85em` sur `.fc-event`, ce qui donnait
  **11,05 px** — une taille hors échelle — à 11 éléments de plus.
- Éditeur : 1 bouton en Arial 13,333 px. Analytics : 10 `<input>` idem.
- Réglages : 26 éléments à **13,92 px** (`0.87rem` écrit en style inline), un champ
  en `monospace` brut. Profils : 2 éléments à **12,8 px** (`0.8rem`).

→ Bloc `:where(button, input, select, textarea) { font: inherit }` ajouté à
`calendar.css`, `editor.css` et `analytics.css` ; `.pcard` et `.fc-event` épinglés
sur `--text-small` ; styles inline des Réglages et des Profils passés aux jetons.
**Résultat mesuré : 0 élément hors échelle sur les 8 pages, dans les 2 thèmes.**

**Un même concept, deux pastilles d'état.** Le Calendrier rend ses états
« ✓ Publié / ! Échoué / ◷ Programmé », le Viewer « ● Utilisé / ○ Inédit », les
Profils « ● Actif ». Les écrans **Jobs et Dashboard** affichaient la valeur brute
de la base : `failed`, `completed` — en anglais, en minuscules, sans glyphe.

→ `_status_badge()` dans `app/web/api.py` rend désormais le motif commun
glyphe + mot français (`! Échoué`, `✓ Terminé`, `◌ En cours`, `◐ Partiel`,
`◷ En file`, `○ Vide`), avec `.s-badge__glyph` ajouté à `samourais.css`. Lisible
en niveaux de gris (G11). Les deux écrans qui affichaient l'anomalie utilisent le
même helper.

**Un logo de plateforme en couleur pleine.** Le Calendrier rend les plateformes par
une marque monochrome (`IG / TT / X / RD`), le Viewer par un libellé texte. L'écran
Profils était le seul à afficher des **emoji en couleur** (📷 🐦 👽 🎵), ce que G8
interdit explicitement.

→ Le filtre Jinja `platformicon` rend maintenant la même marque monochrome que le
Calendrier, dans un `.s-plat` ajouté à `samourais.css`.

**Trois titres d'écran en anglais.** `Settings`, `Profiles`, `Jobs`, plus les
en-têtes de colonnes (`Status`, `Trigger`, `Download`, `Upload`, `Media`,
`Username`) et les boutons (`Retry`, `Resume`, `Scrape`, `Suppr`) — au milieu d'une
application entièrement en français.

→ Traduits. Les colonnes chiffrées portent en outre `class="num"`, donc l'alignement
à droite et `tabular-nums` du socle, comme sur les autres écrans.

**Une police téléchargée pour rien sur les 8 pages.** `partials/nav.html` importait
la famille **DM Sans** complète, en `@import` depuis un `<style>` situé dans le
`<body>` — donc bloquant. Sa propre note disait « repli pour les 4 écrans
historiques, à retirer avec le dernier ». Les quatre sont migrés et plus aucune
règle du projet ne demande DM Sans.

→ `@import` retiré. `tokens.css` conserve `"DM Sans"` en 3ᵉ position de `--font-ui`,
donc la pile dégrade toujours proprement si Inter tombe.

**Un rayon hors échelle.** `.s-sys-dot` (nav, donc les 8 pages) posait
`border-radius: 999px` au lieu du jeton `--radius-pill` (9999px), ce qui faisait une
4ᵉ valeur de rayon sur chaque page. → passé au jeton.

**Des dialogues centrés en haut à gauche.** Le reset universel de `samourais.css`
(`* { margin: 0 }`) écrase le `margin: auto` que la feuille de l'agent utilisateur
pose sur `dialog:modal`. → `margin: auto` explicite sur les dialogues partagés.
Vérifié : boîte de 420 px à x=510 sur 1440 (510 + 420 + 510).

---

## 2. Besoins hors périmètre repris

Les quatre builders ont signalé des manques qu'ils n'avaient pas le droit de traiter.
Cette passe les traite.

### 2.1 Palette de commandes ⌘K — G13, bloquant, signalé par 3 critics

`partials/nav.html` portait depuis le début un bouton `.s-cmdk` avec
`aria-keyshortcuts="Meta+K Control+K"`… masqué par `.s-cmdk { display: none }` tant
que `:root[data-cmdk]` n'était pas posé. **Aucun écran ne le posait, et aucun
écouteur `keydown` Meta+K n'existait nulle part.** Le contrôle était mort sur les 8
pages.

→ Nouveau fichier **`app/web/static/samourais-app.js`**, chargé par `nav.html` — le
seul point commun aux 8 écrans, puisque 4 d'entre eux n'étendent pas `layout.html`.

| Mesure | Avant | Après |
|---|---|---|
| `document.documentElement.dataset.cmdk` | `null` (8/8 pages) | `"on"` (**8/8 pages**) |
| `getComputedStyle('.s-cmdk').display` | `none` | `flex` |
| ⌘K ouvre une palette | non | **oui**, 10 commandes, filtrable |
| Raccourci affiché à droite de la ligne | — | oui (`kbd` par commande) |
| Commandes de l'écran courant en premier | — | oui, groupe « Cet écran » en tête |

La palette est filtrable par sous-séquence (« acal » trouve « Aller au Calendrier »),
navigable ↑/↓, `Entrée` exécute, `Échap` referme et rend le focus. Les commandes
d'écran **cliquent les contrôles réels** déjà présents dans le DOM : la palette ne
peut donc pas diverger de la barre d'outils, et une commande n'apparaît que si son
contrôle existe.

### 2.2 Panneau des raccourcis « ? » — G14

Absent partout. → Ajouté au même fichier. Mesuré : `?` ouvre un `<dialog>` centré
(560 px à x=440 sur 1440) listant `⌘K`, `T`, `?`.

**Garde de saisie (G15)** vérifiée : frappe de `that?` dans le champ *username* des
Profils → valeur intacte `"that?"`, thème inchangé (`dark` → `dark`), panneau non
ouvert.

### 2.3 Aucun sélecteur de thème dans toute l'application

Les 8 écrans **lisaient** `localStorage.theme` (script anti-flash dupliqué dans
`layout.html` et les 4 gabarits autonomes), mais **aucun ne l'écrivait**. Le thème
clair n'était atteignable qu'en changeant la préférence du système d'exploitation.

→ Bouton de bascule ajouté à la zone utilitaire de la nav, présent sur les 8 pages,
plus la commande palette « Basculer le thème clair / sombre » et le raccourci `T`.
Le glyphe hérite de la taille de son voisin ⚙ (12 px) pour ne pas introduire une
7ᵉ taille de police.

### 2.4 Indicateur d'état système figé — les 8 pages

`nav.html` portait `<a data-system-status data-state="idle">` depuis le début.
**Rien n'a jamais alimenté cet attribut** : la pastille restait grise en permanence,
y compris avec 84 jobs en échec dans la base.

→ Nouvel endpoint `GET /api/system/status` (JSON) + polling 15 s dans
`samourais-app.js`. Un job en cours prime sur un job échoué (l'état vivant est plus
utile que le passé). Le **libellé texte** est mis à jour en même temps que la
pastille, donc le sens ne repose jamais sur la couleur seule (G11).

Mesuré : `{"state":"error","label":"51 échecs (24 h)","running":0,"failed":51}`,
rendu dans la nav en « ● 51 échecs (24 h) ».

> Bogue attrapé au passage : `ScrapeJob.created_at` est un **entier Unix**
> (`app/db.py:184`), pas un `datetime`. La première version comparait à un objet
> `datetime` et ne filtrait donc rien — `failed: 0` avec 84 échecs à l'écran.
> Corrigé, puis remesuré à 51.

### 2.5 `GET /favicon.ico` 404 — seule ligne d'erreur console, sur les 8 pages

Relevée par les critics du Viewer et du Calendrier, hors périmètre pour les deux.

→ Route `/favicon.ico` servant un SVG inline (le glyphe ⚔ de la marque, sur l'aplat
d'accent). Aucun binaire à servir. Mesuré : **200**, et **0 réponse 4xx/5xx** sur
les 131 requêtes de la campagne de vérification finale.

### 2.6 Analytics câblé sur un profil inexistant — bloquant

`app/analytics/api.py:47` filtrait **les 9 endpoints** sur `@samourais_`, un compte
absent de la base de production. Sur les vraies données, l'écran n'affichait qu'un
bandeau rouge et cinq tirets — aucun chiffre réel n'était atteignable.

| Mesure | Avant | Après |
|---|---|---|
| `GET /api/analytics/account-overview?days=30` | **404** « Profil @samourais_ introuvable » | **200**, `"username": "yugnat999"` |
| Compte affiché à l'écran | `@samourais_` (inexistant) | **`@yugnat999`** (réel) |
| Bandeau | « aucun compte Instagram analysable » | « API Instagram non connectée (@yugnat999) » — la vraie cause |
| Sélecteur de compte | absent | présent, 2 comptes |

→ `_get_main_profile()` résout dans l'ordre : `?profile_id=` → `@samourais_` s'il
existe vraiment (compatibilité) → premier Instagram actif → premier profil actif.
Nouveau `GET /api/analytics/profiles` alimentant un sélecteur ajouté dans
`.top__controls`, à gauche du sélecteur de période. Il pousse `&profile_id=` dans
l'URL exactement comme `&days=`, donc l'état complet de la vue reste dans l'URL
(A14). Masqué tant qu'il n'y a qu'un compte.

Le message d'erreur restant est composé (cause + geste + comptes disponibles nommés),
plus jamais un code brut (G27).

Vérifié au navigateur : bascule `@yugnat999` → `@FAFO_TV` → en-tête, bandeau **et**
URL suivent (`?profile_id=2`). La chaîne de rechargement complète a dû être extraite
(`rechargerTout()`), sinon l'écran affichait les chiffres du nouveau compte sous le
nom de l'ancien.

### 2.7 Lien profond de la heatmap vers le Calendrier — A11

L'`aria-label` de chaque case promettait « Ouvrir le calendrier sur ce créneau » et
le lien `/calendar?jour=0&heure=19` était correct — mais `calendar.js` ne lisait
jamais ces deux paramètres, et son `persistState()` les effaçait de l'URL par
`history.replaceState` juste après le chargement.

→ Les paramètres sont capturés **avant** la réécriture d'URL, puis consommés une
fois la grille prête : le calendrier va à la prochaine occurrence de ce jour de
semaine à cette heure et ouvre le composer.

Vérifié : `/calendar?jour=0&heure=19` ouvre « Nouveau post — Créneau retenu :
**lundi 17 août 2026 à 19:00** », champ date/heure `17/08/2026 19:00`.

### 2.8 Les 9 dernières boîtes système natives

Le Viewer et le Calendrier avaient remplacé les leurs par un `<dialog>` local. Il
restait **8 `alert()` dans `editor.js`** et **1 `hx-confirm`** (qui appelle
`window.confirm`) sur l'écran Profils.

→ Implémentation partagée dans `samourais-app.js` (`samourais.notify()` non
bloquante en `role=status`, `samourais.confirm()` en `<dialog>`), plutôt qu'une 5ᵉ
copie. Un écouteur `htmx:confirm` détourne `hx-confirm` vers le dialogue de l'app.

La confirmation **nomme l'élément** (G16). Mesuré sur la suppression d'un profil :

- `window.confirm` appelé : **false**
- titre : « Supprimer @FAFO_TV ? »
- texte : « Le profil @FAFO_TV (twitter) et le suivi de ses 4 média(s) seront
  retirés. Les fichiers déjà téléchargés sont conservés. »
- boutons : « Annuler » / « Supprimer le profil » (un verbe, pas « OK »)
- focus par défaut : **Annuler** (l'option non destructive)

**Comptage final : 0 appel réel à `alert()`, `confirm()` ou `prompt()` dans
l'ensemble des JS de l'application.** Les seules occurrences restantes sont des
commentaires qui documentent leur propre suppression.

### 2.9 Éditeur : plan de travail noir en thème clair

`editor.js` peignait le fond du canvas Fabric en `#2a2a2a` écrit en dur. Fabric
peint en JS et ne voit pas le CSS : en thème **clair**, cet écran affichait un pavé
quasi noir de 712×712 px au milieu d'une page à `rgb(244,245,247)` — le seul écran
de l'application dans ce cas.

→ Le fond lit le jeton `--bg-2`, défini dans les deux thèmes, et se repeint à chaque
bascule (trois sources : `data-theme`, l'évènement `samourais:themechange`, la
préférence système).

Mesuré en direct sur la bascule : `#f1f2f4` (clair) ↔ `#16181b` (sombre), le fond de
page passant de `rgb(244,245,247)` à `rgb(10,11,13)`.

---

## 3. État du barème, écran par écran

### 3.1 Viewer — `A_REPRENDRE`

**Tenu** (mesuré par le critic) : V1 densité permanente (120/165/215 px) · V2 **41
vignettes à 1440×900, gouttière 4 px** (exigence : ≥30) · V3 **0 tuile carrée sur 41**
en mode Ratios · V4 grille pleine largeur (0,28 % de marge perdue) · V5 aucun
décalage (0/76 tuiles déplacées) · V7 défilement continu, progression monotone
(41 252 px en 40 crans contre 8 202 avant) · V8–V14, V16, V17 · G1/G3/G5 · G9/G10
(30 tuiles au rectangle identique dans les 2 thèmes) · G11 · G12/G15 · G30/G32
(0 cible < 44×44 après correctif) · contraste minimum 5,01.

**Non tenu :**
- **V27 doublons — bloquant.** L'écran n'existe pas. Ni MD5, ni hash perceptuel, ni
  groupes comparables. `media_items` n'a aucune des deux colonnes.
- **V6 ascenseur constant — bloquant.** `scrollHeight` passe de 3 021 à 42 152 px
  pendant la descente : le pouce rétrécit en continu. Le second volet du critère
  (DOM borné) est tenu — 157 tuiles au maximum.
- Groupement par mois par défaut : gâche 2 rangées du premier écran (28 vignettes
  entièrement visibles au lieu de 41 qui touchent l'écran).
- Libellé de groupe fusionné à l'envers du tri (« mars 2025 – mai 2024 »).

**Passe de lissage :** `999px` → jeton ; palette ⌘K, panneau `?`, bascule de thème,
état système et favicon désormais présents ; 0 élément hors échelle typo.

### 3.2 Calendrier — `A_REPRENDRE`

**Tenu :** C1 bibliothèque permanente (264 px, 16 vignettes) · C2 glisser-déposer
créant le post (vérifié en Mois **et** en Semaine à l'heure exacte) · C3 clic sur
case vide · C4 miniature + heure + plateforme · C5 liseré + glyphe + libellé ·
C6 replanification optimiste avec rollback + toast · C7 deux reprises nommées dans
la carte · C8 cause chiffrée (« 2 480 caractères, soit 280 de trop ») · G9 (106
rectangles identiques entre les 2 thèmes) · G12 · G30 · G32 · 0 couleur brute.

**Non tenu :**
- **C11 créneaux récurrents.** `const SLOTS = [[9,0],[12,30],[18,0]]` en dur
  (`calendar.js:66`), rendus en vue Semaine seulement, ni cliquables, ni déplaçables,
  ni persistés.
- **C15** régime « date fixe » vs « prochain créneau » inexistant : le cadenas ne
  code que `status=published`.
- **G21 skeleton** absent (atténué : grille 7×6 rendue avant le fetch, CLS mesuré
  0,0000 — mais la question fermée du barème appelle un skeleton).
- **G31** à 375 px : la grille commence vers y≈780 sur 812.
- Contrastes `.tray__hint` (2,35:1 clair) et jours hors-mois (2,42:1) : viennent de
  `--fg-4` dans `tokens.css`, socle fermé.

**Passe de lissage :** **G13 ⌘K désormais tenu** (il était relevé non tenu et hors
périmètre) ; Arial et 11,05 px éliminés (22 éléments → **0**) ; lien profond
`?jour=&heure=` de la heatmap désormais fonctionnel.

### 3.3 Analytics — `A_REPRENDRE`

**Tenu :** A1 sélecteur de période unique et global · A2 variation par défaut sur
5/5 cartes · A3 **absence** de comparaison quand invalide, jamais « 0 % » · A4 5
agrégats avant tout tableau · A5 date absolue · A6 bandeau composé · A7 miniatures
44×44 sans saut · A8 tri avec indicateur · A9 · A10/A12 heatmap 7×24 · A13 9 colonnes,
1ʳᵉ sticky, masquables · A14 · A15 · A16/A17 · G1/G3/G5/G6/G7 · G9/G10 (12
conteneurs à la géométrie identique) · G11 · G12 · G18/G19/G21 (CLS 0,0000) ·
G24 · G26–G29 · 0 couleur brute.

**Non tenu :**
- G2 (2 px et 6 px : `--sp-1` et `--sp-3` du socle validé).
- G30 partiel : case de heatmap 38 px de large à 375 px (compromis assumé — 7×44
  imposerait un défilement horizontal de la grille).
- 2 requêtes dupliquées sur la période « 1 an ».

**Passe de lissage :** **le blocage principal est levé** — l'écran n'est plus câblé
sur `@samourais_` et affiche les chiffres d'un compte réel, avec sélecteur ; **A11
désormais tenu** (le lien heatmap → calendrier ouvre le créneau) ; **G13 ⌘K tenu** ;
10 `<input>` en Arial ramenés sur Inter.

### 3.4 Éditeur — `A_REPRENDRE`

**Tenu :** E6 preview mise à jour pendant la frappe (33 454 → 36 138 caractères de
`toDataURL()` sans clic) · E8 ratios sélectionnables avec dimensions lisibles
(contraste 2,35 → **4,88** en clair) · G1 (4 tailles) · G2 · **G3 ombres
supprimées** (2 → 0) · G9 (le choix de thème explicite était **ignoré** sur ce seul
écran ; corrigé) · G10 · G12 (14/14 tabulations avec anneau).

**Non tenu :**
- **E1–E4 — la moitié « composer » n'existe pas.** Aucune preview par plateforme,
  aucune légende, aucun compteur de caractères, aucune validation nommant la
  plateforme fautive. C'est un éditeur de meme, pas un composeur de publication.
- **E5** progression FFmpeg factice (pilotée par le client à 5/15/20/60/90/100 %) et
  non annulable.
- **E7** trois actions finales concurrentes au lieu d'une.
- **E9/E10/E11/E14** aucune section repliable, aucun état mémorisé, **aucun raccourci
  clavier** (`keydown` : 0 occurrence dans `editor.js`).
- **E12** hauteurs de rangée incohérentes (28 / 32 / 44 / 60 px).
- Artboard à **19 %** de l'aire qui lui est réservée (`state.scale` plafonné à 0.4,
  `CANVAS_PADDING = 350`).
- 28 couleurs brutes subsistent dans `editor.js` ; `editor.css` les rattrape par des
  sélecteurs d'attribut `[style*="#888"]` (12 occurrences) — **0 déclaration** de
  couleur brute, mais la dette est masquée, pas soldée.

**Passe de lissage :** **8 `alert()` supprimés** (les derniers de l'application) ;
**plan de travail noir en thème clair corrigé** ; **G13/G14 désormais tenus** (⌘K et
`?` hérités de la couche partagée, alors que cet écran n'étend pas `layout.html`).

### 3.5 Dashboard, Profils, Jobs, Réglages

Ces quatre écrans n'avaient pas de lot dédié. Corrigés dans cette passe : titres et
colonnes en français, pastilles d'état alignées sur le motif commun, marques de
plateforme monochromes, tailles de police ramenées sur les jetons, palette ⌘K,
bascule de thème, indicateur système vivant, confirmation de suppression nommant
le profil.

Non traité : le Dashboard n'a pas été audité contre les critères Linear de
`REFERENCES.md` §1 (tuiles = liens vers vues pré-filtrées, problèmes comptés et
cliquables). L'indicateur d'état système de la nav en est une première brique.

---

## 4. Ce qui reste à faire

### 4.1 Fonctionnalités reportées (par ordre d'écart au barème)

**Écran de doublons — Viewer, V27, bloquant.** Le seul flow de référence entièrement
à zéro, et précisément ce qu'un utilisateur d'Eagle ouvre après un import massif.
Chemin actionnable :
1. deux colonnes calculées à l'ingestion, `md5` et `phash` (dhash 64 bits sur une
   vignette 9×8 en gris, ~20 lignes avec Pillow) — la seule partie hors de
   `viewer_api.py` ;
2. deux endpoints séparés et nommés : « fichiers identiques »
   (`GROUP BY md5 HAVING COUNT(*)>1`) et « visuellement similaires » (bucketing des
   phash en 4 tranches de 16 bits puis distance de Hamming, **distance stockée**) ;
3. groupes rendus côte à côte, chaque candidat affichant résolution, poids et format
   (l'inspecteur prouve que les trois champs sont déjà disponibles) ;
4. le curseur de similarité filtre alors côté client sur `data-dist`, donc **après**
   le scan et sans relancer le calcul — ce qui décroche V28 au passage.

**Ascenseur de taille constante — Viewer, V6, bloquant.** Implémenter la
pré-allocation par section décrite dans `REFERENCES.md` §3.1 F2 : une section par
mois, `height` calculée côté Python à partir du compte, avant tout chargement. Le
DOM borné (157 tuiles) est déjà acquis.

**Créneaux récurrents — Calendrier, C11 + C15.** Sortir `SLOTS` du JS vers une table
`recurring_slots(weekday 0-6, time)` avec endpoints POST/PATCH/DELETE ; exposer
« prochain créneau libre » comme fonction serveur pure recalculée à chaque mutation
(aucun état client, donc l'affichage ne peut pas diverger) ; matérialiser les
créneaux vides dans les **deux** vues (double-clic pour créer, croix au survol pour
supprimer, drag pour déplacer) ; ajouter au post un booléen « date fixe » rendu par
un badge « créneau » vs « 14 août 18:30 » + `draggable="false"` sur les verrouillés.
**C11 et C15 tombent ensemble avec cette seule pièce.**

**Composer de publication — Éditeur, E1–E4, 4 bloquants.** Sous « Texte du bandeau » :
un `<textarea name="caption_base">` puis un `<details>` « Personnaliser par réseau »
avec un textarea par type de réseau dont le placeholder reprend la légende de base ;
à droite du canvas, une colonne d'onglets plateforme en radios + `:checked ~` rendant
un `<article class="pv pv--ig">` avec chrome de plateforme, média en
`aspect-ratio: 4/5` `object-fit: cover` et troncature en `-webkit-line-clamp: 2` ;
un compteur par onglet qui fait `toggleAttribute('disabled')` sur `#export-btn` en
affichant « Instagram : 2 340 / 2 200 ». C'est ce qui transforme un générateur de
memes en outil de publication.

**Progression FFmpeg réelle — Éditeur, E5.** Remplacer la barre pilotée par le client
par une progression serveur (parsing de la sortie `-progress` de ffmpeg) et un bouton
d'annulation dans l'état « Processing ».

**Collections — transversal.** `REFERENCES.md` §6 pose une relation **N-N**
(un média dans 5 collections, une seule fiche, un seul jeu de tags) et des vues
enregistrées (une query string stockée, rendue comme une collection manuelle —
quasi gratuit puisque l'état vit déjà dans l'URL). Rien n'existe aujourd'hui.

**Édition d'images — Éditeur.** Recadrage aux ratios de destination pris en charge
par le produit plutôt que renvoyé à l'utilisateur (`REFERENCES.md` §6, reproche
adressé à Later). Aujourd'hui l'écran ne fait que composer un bandeau + un cadre.

### 4.2 Dette technique repérée, non traitée

- **28 couleurs brutes dans `editor.js`**, rattrapées par des sélecteurs d'attribut
  dans `editor.css`. Le rendu est correct aujourd'hui, mais toute retouche du JS
  fait retomber le rhabillage **silencieusement**. À solder en passant les modales
  d'export au CSS.
- **Iconographie de l'Éditeur en emoji couleur** (💻 ☁ 📚 🖼 🎬 🚀 ✅ ❌), dont une
  icône de 48 px dans la modale d'export alors que G8 n'autorise que 14 et 16 px.
- **G2 : 2 px et 6 px** (`--sp-1`, `--sp-3`) hors de l'échelle de 4. Décision de
  socle, validée et fermée — constatée, non rouverte.
- **Contrastes sous AA** portés par `--fg-4` (`.tray__hint` 2,35:1 en clair, jours
  hors-mois 2,42:1). Appartient à `tokens.css`.
- **Le scheduler lance au démarrage un scrape qui échoue** : Playwright/Chromium
  n'est pas installé sur cette machine. 4 lignes `ERROR` par démarrage, sans rapport
  avec l'interface. C'est aussi ce qui remplit la file d'échecs que l'indicateur
  système compte honnêtement.
- **Dashboard non audité** contre les critères Linear de `REFERENCES.md` §1.

---

## 5. Fichiers touchés par la passe de lissage

| Fichier | Nature |
|---|---|
| `app/web/static/samourais-app.js` | **nouveau** — palette ⌘K, panneau `?`, thème, état système, confirm/notify partagés |
| `app/web/templates/partials/nav.html` | styles de la palette et des dialogues, bouton de thème, chargement de la couche partagée, retrait de l'`@import` DM Sans, rayon au jeton |
| `app/web/api.py` | `_status_badge()` + libellés français, `/api/system/status` |
| `app/web/routes.py` | route `/favicon.ico` |
| `app/web/app.py` | filtre `platformicon` monochrome |
| `app/analytics/api.py` | résolution dynamique du profil, `/api/analytics/profiles`, erreurs composées |
| `app/web/static/analytics.js` / `.css`, `app/web/templates/analytics.html` | sélecteur de compte, `rechargerTout()`, normalisation des contrôles |
| `app/web/static/calendar.js` / `.css` | lien profond `?jour=&heure=`, normalisation des contrôles, `.pcard`/`.fc-event` sur jetons |
| `app/web/static/editor.js` / `.css` | 8 `alert()` → `notify()`, fond du canvas suivant le thème, normalisation des contrôles |
| `app/web/static/samourais.css` | `.s-badge__glyph`, `.s-plat` |
| `app/web/templates/jobs.html`, `profiles.html`, `partials/profile_list.html`, `settings.html` | français, jetons, confirmation nommée |
| `tests/test_web.py` | `/favicon.ico` ajouté à la liste d'exclusion documentée des routes techniques |
