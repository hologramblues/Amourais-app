# CHANGELOG — VAGUE 2

Deux chantiers ont été menés en parallèle sur des fichiers disjoints — le **socle
du design system** et la **fin de la sécurité** — puis lissés par une passe de
cohérence qui a repris les besoins qu'aucun des deux ne pouvait traiter depuis
son propre périmètre.

État de la suite de tests : **499 passed, 30 xfailed, 0 failed**.
Les 30 `xfail` sont ceux du départ, inchangés : ils décrivent des bugs des lots
3.x, non traités par cette vague. Le nombre de tests réussis est passé de 416 à
499 parce que les trois agents ont ajouté des tests de non-régression ; aucun
test n'a été supprimé ni assoupli.

---

## 1. Le socle du design system

### Ce qui change

L'application avait ses couleurs, ses espacements et ses tailles écrits en dur,
répétés d'un fichier à l'autre. Ils vivent désormais dans un jeu de **jetons CSS**
(`app/web/static/tokens.css`) que toute l'interface consomme.

**AVANT** — 32 couleurs hexadécimales et 9 `rgba()` dispersées dans la feuille de
style principale ; changer une teinte demandait de la traquer partout.
**APRÈS** — 0 couleur brute, 0 `rgba()`. Les seules valeurs en pixels qui
subsistent sont défendables une par une (mesure de lecture, bornes de media query
— une media query ne sait pas lire un jeton).

### Lisibilité : 8 défauts de contraste corrigés

Huit paires de couleurs échouaient au seuil AA de la norme WCAG. **Il n'en reste
aucune.**

| | AVANT | APRÈS |
|---|---|---|
| Bouton principal, thème sombre | 3,24:1 | **5,05:1** |
| Bouton principal, thème clair | 4,31:1 | **4,70:1** (aplat inchangé au pixel près) |
| Texte tertiaire sur fond de page | 4,43:1 | **5,01:1** |
| Texte tertiaire sur en-tête de tableau | 4,32:1 | **4,88:1** |
| Jeton de filtre actif / badge info | 4,10:1 | **5,00:1** |
| Pastille d'avertissement | 4,18:1 | **5,19:1** |
| Pastille de succès | 4,46:1 | **5,09:1** |

**Ce que le propriétaire voit** : le bouton bleu le plus cliqué de l'application
était, en thème sombre, du blanc sur un bleu trop clair — pénible à lire de nuit.
Il ne l'est plus. Les textes secondaires (en-têtes de colonnes, mentions sous les
champs, méta-données de lignes) étaient d'un gris trop pâle ; ils sont désormais
lisibles sans effort dans les deux thèmes.

### Thème clair / sombre

**AVANT** — le passage d'un thème à l'autre provoquait un flash blanc.
**APRÈS** — un script synchrone applique le thème avant le moindre CSS ; le fond
est peint avant tout. Les deux blocs de définition (préférence système et choix
explicite) déclarent strictement les mêmes 43 jetons, donc aucune couleur ne
peut exister dans un thème et manquer dans l'autre.

### Mobile

**AVANT** — la page débordait horizontalement sur un écran de 375 px, et un
bouton de fermeture de jeton (`.chip`) de 16 px se voyait imposer une hauteur de
44 px, ce qui faisait éclater le composant sur tout écran tactile.
**APRÈS** — plus aucun débordement (largeur du document = largeur de la fenêtre),
les cibles tactiles font 44 × 44 px, les tableaux défilent dans leur propre
conteneur, et le panneau de navigation se replie sous 1024 px (il s'ouvre, Échap
le referme, un clic à l'extérieur aussi).

---

## 2. La sécurité

### Une URL de téléchargement ne peut plus viser le serveur lui-même

Le contrôle qui empêche de faire télécharger au serveur une adresse **interne**
ne connaissait que la notation classique `127.0.0.1`. Or le navigateur qui
exécute réellement la requête accepte bien d'autres écritures de la même adresse.

**AVANT** — toutes ces adresses passaient le contrôle et atteignaient le réseau
interne :

| Ce qui était collé | Ce que ça visait réellement |
|---|---|
| `http://2130706433/` | `127.0.0.1` (décimal) |
| `http://0x7f000001/` | `127.0.0.1` (hexadécimal) |
| `http://0177.0.0.1/` | `127.0.0.1` (octal) |
| `http://127.1/` | `127.0.0.1` (abrégé) |
| `http://127。0。0。1/` | `127.0.0.1` (point idéographique) |
| `http://2852039166/` | `169.254.169.254`, les **métadonnées d'hébergement** |

**APRÈS** — les 14 formes détournées sont refusées avec le message « URL refusée :
cette adresse désigne le réseau interne du serveur ». Les URL normales
(Instagram, X, Reddit, TikTok) passent exactement comme avant.

### Un site tiers ne peut plus agir à la place du propriétaire

C'est le trou le plus grave de la vague, et il était **encore ouvert** après les
deux lots : la protection avait été écrite mais jamais branchée — du code mort.

**AVANT** — une page piégée ouverte dans le navigateur du propriétaire pouvait
envoyer un formulaire vers `/api/settings/env` ou `/api/settings/session`. Le
navigateur y joignait automatiquement le mot de passe de l'application. Le site
tiers pouvait donc modifier les réglages, ou remplacer les cookies de session.

**APRÈS** — la protection est enregistrée dans la fabrique de l'application. Les
**21 routes qui modifient quelque chose** refusent toute requête qui vient
visiblement d'un autre site (403). Vérifié en conditions réelles, serveur HTTP
lancé :

```
POST /api/settings/env  depuis https://pirate.example
  -> 403 {"error":"Requête inter-site refusée"}
POST /api/settings/env  depuis l'application elle-même
  -> 200 <small class="text-success">Sauvegarde OK</small>
```

**Aucun formulaire de l'application n'a été cassé.** Les 21 routes ont été
éprouvées avec les en-têtes que posent réellement HTMX, un `fetch()` classique,
un navigateur ancien, un sous-domaine et `curl` : **0 requête légitime bloquée,
0 attaque passée**, sur 168 requêtes. Les pages consultées (GET) ne sont jamais
concernées, et la sonde `/health` reste publique.

### Les messages d'erreur ne racontent plus l'intérieur du serveur

**AVANT** — 27 endroits des API du Viewer, du Calendrier, des Statistiques et de
l'Éditeur renvoyaient au navigateur le texte brut de l'exception. Concrètement,
l'écran affichait des choses comme :

```
integer division or modulo by zero
invalid literal for int() with base 10: 'abc'
(sqlite3.OperationalError) no such table: profiles
[SQL: SELECT media_items.id AS media_items_id, media_items.platform ...]
[Errno 28] No space left on device: '/data/downloads/secret.jpg'
```

soit la requête SQL complète, la structure de la base, et le chemin absolu du
volume de stockage.

**APRÈS** — les 27 endroits renvoient « Erreur serveur ». L'exception complète
continue d'être écrite dans les journaux du serveur : le propriétaire ne perd
aucune information de diagnostic, il cesse seulement de la publier.

### Le champ « Port du serveur » ne ment plus

**AVANT** — le formulaire des Réglages proposait de modifier le port. Le champ
était en réalité ignoré depuis le lot précédent (l'enregistrer aurait fait
écouter le conteneur sur le mauvais port et tué le déploiement) : le propriétaire
pouvait le changer, cliquer « Sauvegarder », lire « Sauvegarde OK » — et rien ne
se passait.
**APRÈS** — le port reste **affiché**, en lecture seule, avec la mention « Défini
par la plateforme d'hébergement, non modifiable ici ». Il n'est plus envoyé.

### La médiathèque ne tombe plus sur une adresse mal formée

**AVANT** — `?per_page=0` provoquait une division par zéro, `?page=abc` une
erreur de conversion : dans les deux cas un écran en erreur 500, doublé de la
fuite de message décrite plus haut. `?per_page=999999` chargeait toute la
médiathèque en mémoire d'un coup.
**APRÈS** — la pagination est bornée entre 1 et 200 éléments ; une valeur absurde
retombe silencieusement sur la valeur par défaut et la page s'affiche
normalement. Aucune requête n'est refusée à l'utilisateur.

---

## 3. Le lissage : cohérence entre les deux chantiers

Les deux lots ayant travaillé en aveugle, la passe de lissage a corrigé leurs
contradictions.

### Les messages de l'interface suivaient une palette parallèle

**AVANT** — les fragments renvoyés par l'API et les messages du formulaire des
Réglages posaient leur couleur en dur : `style="color:red;"`, `color:green`,
`color:#888`. 21 occurrences, qui contournaient entièrement le système de jetons
que l'autre chantier venait d'établir : en thème sombre, un rouge pur sur fond
noir, hors palette et agressif.
**APRÈS** — ces 21 fragments utilisent `.text-error`, `.text-success` et
`.text-muted`, donc la palette sémantique et les deux thèmes.

### Le cache des navigateurs ne se purgeait qu'à moitié

**AVANT** — l'empreinte qui force les navigateurs à recharger les fichiers de
style était une constante écrite à la main : livrer un correctif sans penser à
la modifier laissait les visiteurs avec l'ancienne version. Et les quatre écrans
(Viewer, Éditeur, Calendrier, Statistiques) n'avaient **aucune** empreinte : leurs
fichiers restaient en cache indéfiniment.
**APRÈS** — l'empreinte est calculée sur la date de modification du fichier, et
les **10 fichiers** de l'application en portent une. Un correctif de style ou de
comportement arrive chez le propriétaire sans qu'il ait à vider son cache.

### Commentaires devenus faux

Trois commentaires du code annonçaient encore un travail « hors périmètre, à
faire ailleurs » alors qu'il vient d'être fait. Ils décrivent désormais l'état
réel, et le point le plus important — le branchement de la protection inter-site —
est gardé par un test qui échoue si quelqu'un le débranche.

---

## 4. Ce qui reste ouvert

Par ordre de gravité décroissante.

### Sécurité — deux angles morts subsistent sur le téléchargement rapide

1. **Aucun nom de domaine n'est résolu.** Le contrôle juge l'adresse telle
   qu'elle est écrite. Un domaine public dont l'enregistrement DNS pointe vers
   l'intérieur du serveur (par exemple `localtest.me`, qui résout publiquement
   vers `127.0.0.1`) traverse le contrôle intact. Le corriger proprement suppose
   de résoudre le nom *et* de répondre à la fenêtre entre le contrôle et la
   requête réelle — le navigateur re-résout ensuite, et un DNS malveillant peut
   répondre différemment la seconde fois. La bonne réponse est d'épingler
   l'adresse résolue côté navigateur : un lot dédié.
2. **Les redirections ne sont pas surveillées.** Seule l'adresse *initiale* est
   jugée. Une page hostile qui répond « va plutôt là » vers une adresse interne
   est suivie. Intercepter la chaîne demande de brancher un intercepteur dans les
   quatre chemins de téléchargement — un changement de structure, pas un
   correctif minimal.

*Portée réelle* : ces deux angles morts ne s'ouvrent que si le propriétaire colle
lui-même une adresse fabriquée par un tiers dans le champ de téléchargement
rapide.

### Fiabilité

3. **Le compteur de téléchargements simultanés ne se libère jamais tout seul.**
   Trois places sont disponibles ; une place n'est rendue qu'à la fin du
   téléchargement qui la détient. Si un téléchargement se fige, la place est
   perdue jusqu'au redémarrage, et trois blocages désactivent définitivement la
   fonction. Deux des quatre chemins de téléchargement n'ont d'ailleurs aucun
   délai d'expiration explicite. À traiter ensemble : délai sur les quatre
   chemins, puis libération garantie.
4. **Filtres du Viewer non bornés.** La pagination est désormais protégée, mais
   `?profile_id=abc` et `?min_rating=abc` produisent toujours une erreur 500
   (désormais sans fuite de message). Même famille de défaut, même correctif
   possible que la pagination.
5. **Les sauvegardes de session sont horodatées à la seconde.** Deux envois dans
   la même seconde écrasent la même copie de secours ; la garantie « on conserve
   les cinq dernières » n'est donc pas stricte. Sans effet en usage réel, l'envoi
   étant manuel.

### Interface

6. **La palette de commandes (⌘K) n'est pas fonctionnelle.** Seul l'emplacement
   est posé ; le déclencheur reste masqué tant qu'aucun écran n'enregistre de
   commandes. C'était le mandat du socle — « prévoir l'emplacement » — et afficher
   un bouton mort aurait été pire. À remplir lors de la refonte des écrans.
7. **Deux paires de couleurs restent sous le seuil WCAG, à dessein.** Le texte
   désactivé (2,64:1) en est explicitement exempté par la norme, et les
   placeholders sont partout doublés d'une étiquette permanente. Le filet des
   champs au repos (1,46:1) demanderait, pour atteindre 3:1, de durcir tout le
   trait du produit — ce qui contredirait la direction esthétique « hiérarchie par
   filets discrets ». **C'est un arbitrage de design à trancher par le
   propriétaire**, pas un oubli.
8. **Une douzaine de jetons sont déclarés sans être encore consommés** (largeur
   d'inspecteur, tailles de vignettes, gouttières de grille), et une police
   secondaire est chargée sur les huit pages alors que seuls quatre écrans
   l'utilisent encore. Ce n'est pas du code mort : c'est le vocabulaire des
   écrans à venir. À réévaluer à la fin de la refonte, quand le dernier écran
   historique sera repris.

### Dette de fond, inchangée

9. **Les 30 marqueurs `xfail`** décrivent toujours 30 bugs connus et non
   corrigés, tous rattachés aux lots 3.x — principalement le ménage du stockage
   (vignettes orphelines, répertoires de l'éditeur et du calendrier qui
   grossissent sans limite) et les reprises après échec du transcodage vidéo.
   Aucun n'a été touché par cette vague.
