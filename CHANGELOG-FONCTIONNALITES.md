# Ce que ta version apporte

Écrit pour toi, pas pour un développeur. Chaque section dit **ce que ça fait**,
**comment t'en servir**, **ce que ça ne fait pas** (honnêtement), et **ce qui
manque encore**.

Rien n'a été supprimé de ce que tu avais. Tes 2 comptes suivis, tes 16 médias,
tes 42 traitements, ton commentaire et ta note sont intacts — vérifié sur une
copie de ta vraie base, avant et après la mise à jour.

---

## 1. Retrouver les doublons

### Ce que ça fait

Ta bibliothèque grossit à chaque collecte, et le même visuel finit par revenir
plusieurs fois : réenregistré, recompressé par la plateforme, ou simplement
repris par un autre compte. L'application sait maintenant les retrouver, de
deux façons **volontairement séparées** :

- **Fichiers identiques** — deux fichiers rigoureusement identiques, au bit
  près. Aucune ambiguïté possible : c'est la même chose deux fois.
- **Visuellement similaires** — la même image recompressée, redimensionnée ou
  légèrement recadrée. Ce sont deux fichiers *différents* qui montrent la même
  chose.

La distinction compte : le premier cas se supprime les yeux fermés, le second
demande de regarder.

### Comment t'en servir

1. Écran **Médias** → bouton **Doublons** en haut à droite.
2. Au premier usage, un message t'annonce que tes médias n'ont pas encore
   d'empreinte et **ne peuvent donc pas être comparés**. Clique sur
   **Calculer les empreintes (16)**. C'est à faire une seule fois ; les médias
   collectés ensuite sont traités automatiquement.
3. Deux onglets : **Fichiers identiques** et **Visuellement similaires**.
4. Sur l'onglet « similaires », le curseur **Tolérance** resserre ou élargit la
   recherche. Il ne relance aucun calcul : tout est déjà mesuré, le curseur ne
   fait que filtrer. C'est instantané.
5. Chaque candidat affiche sa **définition**, son **poids** et son **format** —
   les trois seuls critères qui permettent de choisir lequel garder.
6. Le bouton de déduplication ouvre une fenêtre qui te dit, **avant** que quoi
   que ce soit ne soit touché : quel exemplaire est gardé, lesquels seront
   **supprimés définitivement**, et combien de commentaires, de notes et de
   collections sont en jeu. Tu peux changer l'exemplaire gardé.
7. Les commentaires, les notes et les appartenances aux collections des
   exemplaires supprimés sont **transférés** sur celui que tu gardes, si tu
   coches l'option. Sinon, ils partent avec eux.

### Ses limites, honnêtement

- **La suppression est définitive.** Le fichier quitte le disque, il n'y a pas
  de corbeille. C'est pourquoi la fenêtre de confirmation est aussi bavarde.
- **Le calcul d'empreintes ne se fait pas tout seul la première fois.** Sur des
  milliers de médias, prévois plusieurs minutes de calcul par lots, avec la
  barre de progression. Tu peux quitter l'écran et revenir.
- **Les vidéos sont comparées sur leur image de couverture**, pas sur leur
  contenu animé. Deux vidéos différentes qui commencent par le même plan
  peuvent donc être proposées comme « similaires ». Regarde avant de valider.
- **À tolérance large, les groupes s'élargissent par proche en proche** : si A
  ressemble à B et B à C, les trois sont réunis, même si A et C ne se
  ressemblent pas tant que ça. L'en-tête du groupe affiche pour cette raison
  l'écart **maximum** du groupe. C'est aussi ce que font les outils du marché.

### Ce qui reste à faire

**Le traitement en lot.** Aujourd'hui, chaque groupe se règle avec sa propre
fenêtre de confirmation. Sur 16 médias c'est confortable ; sur les milliers que
tu vises, un scan qui remonte 200 groupes, c'est 200 fenêtres — la
fonctionnalité cesserait d'être utilisable au moment précis où elle devient
nécessaire. Il manque une règle nommée du type « garde la plus haute définition
dans les 240 groupes », avec un récapitulatif chiffré (N fichiers, M Go) et
**une seule** confirmation portant sur la règle. C'est le manque le plus
important de cette livraison.

---

## 2. Les collections

### Ce que ça fait

Un média appartient à un compte : c'est la collecte qui en décide, pas toi. Une
**collection** est un regroupement que **tu** crées, qui traverse les comptes et
les plateformes : « Inspiration Q4 », « À publier », « Références typo ». Un
même média peut appartenir à plusieurs collections en même temps, sans être
dupliqué.

### Comment t'en servir

1. Écran **Médias** → colonne de gauche **Collections** (raccourci `C`) →
   **+ Nouvelle**.
2. Pour remplir : sélectionne plusieurs médias dans la grille, puis
   **Collections** → la collection voulue. Tu peux en ajouter des dizaines d'un
   coup.
3. Cliquer une collection **filtre** la grille. L'adresse de la page change :
   tu peux mettre en favori une vue précise (une collection + une plateforme +
   un tri) et la retrouver identique plus tard.
4. La fiche d'un média (raccourci `I`) liste ses collections, avec une croix
   pour l'en retirer.
5. Renommer, supprimer : les deux petites icônes à droite du nom.

### Ses limites, honnêtement

- **Supprimer une collection ne supprime aucun média** — l'application te le
  dit explicitement avant de confirmer. Seul le regroupement disparaît.
- **Les collections ne servent que dans l'écran Médias.** Le calendrier et
  l'éditeur ne savent pas encore filtrer par collection : ils voient toute la
  bibliothèque.
- Pas d'ordre manuel à l'intérieur d'une collection, pas de sous-collections,
  pas de couleur ni d'icône.

### Ce qui reste à faire

Pouvoir choisir une collection comme source dans le **calendrier** et dans le
sélecteur **Bibliothèque** de l'éditeur — c'est là que le regroupement
deviendrait vraiment un outil de travail et pas seulement de rangement.

---

## 3. Analytics : le classement des médias, et le refus de mentir

### Ce que ça fait

L'écran **Analytics** classe tes posts par performance réelle : likes,
commentaires, vues, engagement total et **taux d'engagement**.

Le point important n'est pas le classement — c'est ce que l'écran fait quand il
**ne sait pas**. Un post dont les compteurs n'ont jamais été relevés n'est pas
un post à zéro. Le premier est une absence de mesure ; le second est un échec
mesuré. Les confondre reviendrait à te faire juger ton propre contenu sur un
défaut de collecte.

Concrètement :

- une case **vide** signifie « pas mesuré », jamais « zéro » ;
- un post non mesuré porte le rang **—**, jamais un numéro, et il n'est jamais
  classé en dessous d'un post mesuré à zéro comme s'il avait échoué ;
- les moyennes ne se calculent que sur les posts réellement mesurés, et l'écran
  dit sur combien ;
- l'export CSV rappelle la convention en toutes lettres : « une cellule vide =
  mesure ABSENTE, elle ne vaut pas zéro ».

Le **taux d'engagement** est normalisé sur le nombre d'abonnés **au moment du
post** quand un relevé antérieur existe (exact), sinon sur les abonnés
d'aujourd'hui — et dans ce cas l'écran écrit « abonnés du jour (approché) ».

### Comment t'en servir

1. Écran **Analytics** → section **Classement des médias scrapés**.
2. **Portée** : un seul compte, ou tous. En mode « tous les comptes », l'écran
   rappelle que seuls les **taux** sont comparables entre comptes d'audiences
   différentes — pas les likes bruts.
3. **Colonnes** : masque celles qui ne te servent pas ; ton choix est mémorisé.
4. Un clic sur une ligne ouvre le post correspondant dans l'écran Médias.
5. **Exporter CSV** pour un tableur.

### Ses limites, honnêtement

- **Les compteurs ne sont collectés que sur Instagram.** Sur Twitter/X, TikTok
  et Reddit, aucun chiffre n'est relevé : l'absence y est définitive en l'état,
  et l'écran le dit plutôt que de te proposer une nouvelle collecte inutile.
- **Aujourd'hui, chez toi, ce classement est vide.** Sur tes 16 médias, aucun
  ne porte de compteur et 12 n'ont même pas de date de publication en base.
  L'écran l'annonce et te propose d'élargir la période ou de relancer une
  collecte. Ce n'est pas une panne : c'est l'état réel de tes données.
- **L'historique d'abonnés est vide** tant que l'API Instagram Graph n'est pas
  connectée (Réglages). Le scraping calcule les statistiques du profil mais ne
  les enregistrait pas.
- Les carrousels sont fusionnés en **un seul post**, comme sur Instagram.

### Ce qui reste à faire

Connecter l'API Instagram Graph pour que la couverture, les impressions, les
visites de profil et l'historique d'abonnés cessent d'être vides — c'est le seul
geste qui débloque la moitié de l'écran.

---

## 4. L'éditeur : retoucher avant de publier

### Ce que ça fait

L'éditeur ne se contente plus de poser un bandeau de texte. Tu peux maintenant
travailler l'image elle-même :

- **recadrage** aux formats 1:1, 4:5, 9:16, 16:9 ou libre ;
- **rotation** par quarts de tour et **miroir** horizontal / vertical ;
- **luminosité**, **contraste**, **saturation** ;
- **export PNG ou JPEG**, avec une **taille cible** (×0,5, ×1, ×1,5, ×2) et,
  en JPEG, un réglage de qualité.

Le libellé affiché avant l'export (« 1620×1620 px, JPEG 60 % ») correspond
exactement au fichier produit — vérifié en ouvrant les fichiers, pas en se fiant
à leur nom.

Tout se passe **dans ton navigateur** : aucun fichier temporaire sur le serveur,
aucune attente de traitement.

### Comment t'en servir

1. Écran **Éditeur** → charge une image (ton ordinateur, Google Drive, ou
   **Bibliothèque** pour reprendre un média déjà collecté).
2. Bloc **Retouche** : recadrage, rotation, miroir, réglages. Le rendu à
   l'écran est celui du fichier final.
3. Bloc **Fichier de sortie** : format, taille cible, qualité.
4. **Télécharger le meme**, **Sauvegarder dans Viewer** (le meme apparaît dans
   l'onglet Memes de l'écran Médias) ou **Planifier**.

### Ce qui a été corrigé et que tu ne verras donc plus

Deux pannes silencieuses ont été trouvées et refermées : un fichier illisible
(mauvaise extension, média tronqué) ou un média dont le fichier a disparu du
disque se chargeaient **sans rien dire**, plan de travail vide — et l'export
annonçait ensuite « réussi » sur un meme sans photo. Désormais l'échec
s'affiche, il est expliqué, et les trois boutons d'export restent **verrouillés**
tant qu'une image exploitable n'est pas chargée.

### Ses limites, honnêtement

- **Une vidéo ne se retouche pas.** En mode vidéo, seuls le découpage
  (trim) et le bandeau sont disponibles ; les blocs de retouche disparaissent.
  L'export vidéo passe par le serveur et prend quelques minutes : ne ferme pas
  l'onglet.
- **Rien de la retouche ne survit.** Recadrage, rotation, miroir et réglages ne
  vivent que le temps de la session : recharge la page, tout est perdu.
  Enregistré dans le Viewer, tu obtiens une image aplatie qu'on ne peut pas
  rouvrir pour corriger une saturation trop forte ou repasser du 1:1 au 9:16 —
  il faut tout refaire depuis la source.

### Ce qui reste à faire

**Pouvoir reprendre un visuel.** C'est le manque le plus sensible de l'éditeur,
et celui qui te sépare encore d'un outil payant du marché : chez eux, un visuel
se rouvre et se corrige. La mécanique est prête côté technique ; il manque de
mémoriser les réglages à côté de l'image enregistrée.

---

## 5. Finitions de cohérence entre écrans

Petites choses, mais ce sont elles qui font qu'un logiciel semble d'une seule
pièce :

- **Les onglets du navigateur ont enfin un nom.** Quatre écrans s'appelaient
  tous « SAMOURAIS » : impossible de retrouver le bon onglet. Ils s'appellent
  maintenant « SAMOURAIS — Profils », « — Jobs », « — Réglages »,
  « — Dashboard », et l'éditeur utilise le même tiret que les autres.
- **Le Dashboard parle enfin français.** Il affichait « Activite recente »,
  « Media total », « Telecharger », « Profiles suivis », « Status » — sans
  accents et à moitié en anglais, alors que les sept autres écrans étaient en
  français correct.
- **Les dates du viewer ne mentent plus.** Quand un média n'a pas de date de
  publication en base, la grille le classait quand même dans un mois, en se
  rabattant **en silence** sur la date à laquelle le scraping l'a découvert.
  L'écran Analytics, lui, refusait de dater ces mêmes médias. Le même fait se
  lisait donc de deux façons opposées selon l'écran. L'en-tête de groupe précise
  maintenant « 12 à la date de découverte », avec l'explication au survol.
- **La console du navigateur s'est tue.** Onze traces de mise au point
  déversaient les paramètres d'export vidéo à chaque utilisation.
- **Une validation manquante** a été rétablie côté serveur : le retrait de
  médias d'une collection accepte désormais le même plafond que l'ajout.
- **Un filet de sécurité sur la mesure.** Cinq tests automatiques verrouillent
  désormais l'invariant du chapitre 3 (« non mesuré n'est pas zéro »). Ils ont
  été éprouvés en réintroduisant volontairement les anciens défauts : ils les
  attrapent.

---

## Ce qui n'a pas été fait, et pourquoi

Par honnêteté, voilà ce qui a été vu et **délibérément laissé** :

- **Trois écrans (Éditeur, Calendrier, Analytics) n'utilisent pas la
  bibliothèque de composants commune.** C'est la cause mécanique des petites
  divergences d'apparence entre écrans. La correction a été *mesurée* avant
  d'être tentée : sur Analytics, elle déplace 604 éléments sur 650, jusqu'à
  533 pixels. Ce n'est pas un ajustement, c'est une refonte de trois écrans.
  Elle mérite son propre chantier, pas une fin de journée.
- **Les quatre écrans redéfinissent leur en-tête** au lieu de la partager. Même
  raison : trop risqué à ce stade.
- **Supprimer un média laisse une référence orpheline** dans les posts
  planifiés. Sans effet visible aujourd'hui (le calendrier n'utilise pas cette
  référence), mais à traiter dans un chantier d'intégrité.
- **L'enregistrement d'un meme ne vérifie pas** que les données reçues sont bien
  une image. Inatteignable depuis l'interface, à durcir néanmoins.
- **La fenêtre d'export vidéo** a ses couleurs écrites en dur dans le code ;
  elle s'affiche correctement dans les deux thèmes uniquement grâce à un
  rattrapage fragile. À reprendre proprement.
