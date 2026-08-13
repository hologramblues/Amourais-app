# CHANGELOG — Vague 1 « Arrêter l'hémorragie »

Branche : `chantier/vague-1-robustesse`
Date : 13 août 2026
Référence : `AUDIT.md` §9 (plan d'exécution), `tests/README.md` §5 (table des xfail)

Ce document est écrit **pour le propriétaire de l'application**, pas pour un développeur.
Chaque entrée dit ce qui se passait AVANT et ce qui se passe APRÈS, du point de vue de
quelqu'un qui utilise le produit.

---

## Résumé en une page

| | Avant la vague 1 | Après la vague 1 |
|---|---|---|
| Suite de tests | 350 verts, 85 bugs connus non corrigés | **416 verts, 31 bugs connus restants** |
| Bugs réellement corrigés | — | **54 tests basculés au vert** (14 lots) |
| Base de production | intacte | **intacte** (voir « Preuve d'innocuité ») |
| L'application démarre | oui | **oui** — 57 routes, les 9 pages répondent 200 |

Le trou le plus grave était une **exécution de code à distance sans mot de passe** :
n'importe qui sur Internet pouvait ouvrir la console de débogage Werkzeug et exécuter du
Python sur le serveur. Il est fermé, et fermé **trois fois** (les trois déclencheurs
connus), y compris si `FLASK_DEBUG` reste à `1`.

---

## Lot A — Sécurité web (lots 1.0, 1.0a, 1.0b, 1.2, 1.3b)

Fichiers : `app/web/app.py`, `app/web/api.py`, `app/config.py`, `run.py`

### 1.0 — La console de débogage n'est plus atteignable (risque #8, AUDIT §6.3)

`app/web/api.py` (validation du corps de `POST /api/quick-download`) et
`app/web/app.py:103-146` (trois `errorhandler` globaux).

- **AVANT** — Un inconnu envoyait `{"url": 42}` à l'API de téléchargement rapide. Le code
  appelait `.strip()` sur un nombre, ce qui plantait, et Flask répondait avec la **console
  interactive Werkzeug** : une invite Python exécutable dans le navigateur, sur votre
  serveur, sans mot de passe. C'est la prise de contrôle complète de la machine et de vos
  données.
- **APRÈS** — La même requête reçoit `400 {"error": "URL invalide : chaine attendue"}`.
  Plus largement, **aucune** erreur imprévue ne peut plus faire remonter de trace technique :
  une page `/api/...` répond en JSON sobre, une page normale affiche une page d'erreur
  sobre. Vérifié en forçant `FLASK_DEBUG=1`, c'est-à-dire dans le pire cas.
- **Effet de bord bénéfique** — Les erreurs 404 / 405 / 413 restent ce qu'elles sont ; elles
  changent seulement d'habillage. Le formulaire d'upload de l'éditeur, quand le fichier est
  trop gros, affiche désormais « Fichier trop volumineux (max 100 Mo) » au lieu d'injecter
  une page d'erreur brute dans l'interface.

### 1.0a — Un mot de passe accentué ne fait plus tomber le site (risque #59)

`app/web/app.py:74-86`.

- **AVANT** — Si votre mot de passe contenait un accent (`pässwörd`), **toute** requête
  portant un en-tête d'authentification faisait planter le serveur — ce qui ouvrait à
  nouveau la console de débogage. Un inconnu pouvait déclencher ça à volonté en envoyant
  un en-tête bidon. C'était le déclencheur le plus fiable du trou ci-dessus.
- **APRÈS** — La comparaison se fait sur des octets. Un en-tête bidon reçoit `401`, et vous,
  avec vos vrais identifiants accentués, entrez normalement.

### 1.0b — L'interface Réglages ne peut plus écraser vos identifiants (risque #52)

`app/web/api.py` — `APP_USERNAME`, `APP_PASSWORD`, `FLASK_SECRET_KEY` retirés de la liste
blanche.

- **AVANT** — Le formulaire Réglages acceptait d'écrire vos identifiants d'application dans
  le fichier de configuration du volume. Une requête forgée pouvait donc **changer votre mot
  de passe** et vous verrouiller hors de votre propre outil.
- **APRÈS** — Ces trois clés sont refusées. Vérifié : la requête ne les écrit pas dans le
  fichier. Aucun champ du formulaire Réglages ne les proposait, donc rien ne casse.

### 1.2 — Un seul planificateur, plus deux (risque #8, second volet)

`app/config.py:46` (`FLASK_DEBUG` par défaut à `"0"`) et `run.py:95` (`use_reloader=False`).

- **AVANT** — En mode debug, Flask relançait le programme dans un second processus. Vous
  aviez donc **deux planificateurs** qui s'ignoraient : deux navigateurs lancés sur le même
  profil, des doublons en base, et le second processus marquait « échoué » les jobs bien
  vivants du premier.
- **APRÈS** — Un seul planificateur. Un seul « Scheduler started » dans les logs.

### 1.3b — Une faute de frappe dans Réglages ne brique plus l'application (risque #41)

`app/web/api.py` (refus en 400) et `app/config.py:24-42` (fonction `_env_int`).

- **AVANT** — Vous tapiez une lettre dans un champ numérique des Réglages (par ex. `PORT`).
  La valeur était enregistrée sur le volume persistant. Au redémarrage suivant,
  l'application refusait de démarrer — **y compris la page Réglages**, seul endroit d'où
  vous auriez pu réparer. Panne totale et définitive, non réparable depuis l'interface.
- **APRÈS** — Double protection. (1) La sauvegarde refuse la valeur et répond 400 : rien
  n'est écrit. (2) Même si une valeur illisible se trouvait déjà sur le volume, le
  démarrage la journalise en avertissement et retombe sur la valeur par défaut. L'application
  démarre toujours.

---

## Lot B — Migrations de schéma (lot 1.1)

Fichier : `app/db.py:261-322`

### 1.1 — Les bases anciennes redeviennent utilisables (risque #1)

- **AVANT** — Deux colonnes (`backfill_from`, `backfill_to`) existaient dans le modèle depuis
  des mois mais n'étaient **jamais ajoutées** aux bases créées avant. Sur une telle base,
  la moindre consultation d'un profil échouait : `no such column: profiles.backfill_from`.
  La page d'accueil renvoyait une erreur. (C'est exactement ce qu'on observe encore sur la
  copie locale de la base de production, dont le schéma est ancien.)
- **APRÈS** — La migration ajoute les deux colonnes au démarrage. Elle reste **non
  destructive par construction** : elle ne fait qu'ajouter des colonnes, jamais supprimer ni
  modifier une donnée.
- **Durcissement supplémentaire** — Avant, la migration avalait **toutes** les erreurs en
  silence : base verrouillée, disque plein, table absente… l'application démarrait sur un
  schéma à moitié migré et se cassait plus tard, ailleurs, de façon incompréhensible.
  Désormais seul le cas « la colonne existe déjà » est ignoré ; toute autre erreur est
  journalisée bruyamment et arrête le démarrage. Vous voyez le vrai problème tout de suite.

---

## Lot C — Cycle de vie des jobs (lot 1.3)

Fichier : `app/scheduler.py`

### 1.3 — Plus de job figé sur « en attente » à vie (risque #54)

- **AVANT** — Trois chemins menaient au même symptôme : un job restait éternellement au
  statut « en attente » dans la page Jobs, sans jamais démarrer ni échouer. Le profil
  concerné était gelé jusqu'au prochain redémarrage, **sans le moindre signal** dans
  l'interface. Pire, sur le troisième chemin, une seule erreur sur un profil faisait sauter
  **tous les profils restants** du cycle : vos autres comptes n'étaient pas scrapés du tout.
- **APRÈS** — (a) Profil déjà verrouillé → le job est marqué « échoué », visible. (b) Erreur
  avant le démarrage → « échoué ». (c) La gestion d'erreur est descendue **dans** la boucle :
  un profil en échec n'annule plus les autres, et le job n'est validé qu'une fois son
  thread réellement démarré. La même protection a été posée sur le **scrape manuel**, celui
  que vous déclenchez au bouton — c'est le cas le plus souvent oublié.

---

## Lot D — Extraction et persistance (lots 1.4, 1.4b, 1.5, 1.5b, 1.6, 1.7)

Fichiers : `app/scraper/pipeline.py`, `instagram.py`, `tiktok.py`, `twitter.py`, `reddit.py`

### 1.4 — Un profil cassé cesse d'être réessayé en boucle (risque #10)

`pipeline.py` — horodatage du profil même en cas d'exception.

- **AVANT** — Si l'extraction plantait, la date de dernier scrape n'était pas posée. Le
  profil restait donc « dû » à **chaque** cycle du planificateur : relance permanente,
  navigateur lancé en boucle, sur un profil qui ne peut de toute façon pas fonctionner.
- **APRÈS** — La date est posée même en cas d'échec. Le profil repasse dans la file normale.

### 1.4b — Un blocage plateforme ne se déguise plus en « rien de neuf » (risque #53)

`pipeline.py:62-89` + les 4 extracteurs.

- **AVANT** — Votre proxy meurt, votre session Instagram est expirée, ou la plateforme vous
  bloque : le job s'affichait avec le **même badge orange « vide »** qu'un scrape parfaitement
  sain qui n'avait simplement rien trouvé de nouveau. Impossible de distinguer « tout va
  bien, rien de neuf » de « je ne collecte plus rien depuis trois semaines ». C'est le
  défaut le plus coûteux du produit : une panne silencieuse.
- **APRÈS** — Le job passe en « **échoué** » avec un message d'erreur explicite. Les deux
  situations sont maintenant discernables d'un coup d'œil. Couvert sur les **4** plateformes.

### 1.5 — Le backfill peut de nouveau se terminer (risque #5)

`tiktok.py`, `twitter.py`, `reddit.py` — compteur `total_seen`.

- **AVANT** — Seul Instagram comptait les posts vus. Sur les trois autres plateformes le
  compteur restait à zéro, donc la condition d'arrêt du rattrapage historique n'était jamais
  atteinte : un backfill ne se terminait pas.
- **APRÈS** — Les 4 plateformes comptent, y compris sur leur chemin de secours.

### 1.5b — Instagram : plus de doublons ni de basse résolution (risque #45)

`instagram.py:136`.

- **AVANT** — Instagram était lu par deux chemins qui identifiaient les posts **différemment**
  (un identifiant numérique interne d'un côté, le code court de l'autre). La déduplication
  était donc inopérante entre les deux : le même post pouvait être stocké deux fois, dont
  une en version basse résolution.
- **APRÈS** — Les deux chemins utilisent le code court. **Vérifié sur une copie en lecture
  seule de votre base réelle** : 100 % de vos identifiants Instagram sont déjà des codes
  courts. Aucun re-téléchargement massif, aucun doublon de migration.

### 1.6 — Un doublon n'annule plus tout le lot (risque #9)

`pipeline.py` — `db.begin_nested()`.

- **AVANT** — Sur un lot de 40 nouveaux médias, si **un seul** était déjà en base, la
  transaction entière était annulée : les 39 autres étaient perdus. Et le compteur
  « nouveaux médias » du job affichait 40 alors que 0 avait été enregistré — un chiffre
  qui mentait.
- **APRÈS** — Seule la ligne fautive est annulée ; les médias sains sont conservés. Le
  compteur n'est incrémenté qu'après enregistrement réel.

### 1.7 — Les légendes cessent de disparaître (risque #30)

`instagram.py:105-118`.

- **AVANT** — Une erreur de parenthésage faisait que les légendes reçues au format GraphQL —
  **le format nominal** — étaient lues comme vides et enregistrées à `NULL`. Vos posts
  arrivaient dans la bibliothèque sans texte.
- **APRÈS** — La légende est extraite correctement sur les trois formats rencontrés.

### 1.7 (second volet) — Le titre d'un post du calendrier s'affiche (voir « Réconciliation »)

---

## Lot E — Conteneur (lot 1.8)

Fichiers : `Dockerfile`, `.dockerignore` (nouveau), `.gitignore`

### 1.8a — Le contrôle de santé Railway cesse de mentir (risque #37)

`Dockerfile:31-34`.

- **AVANT** — Railway sondait la page d'accueil `/` toutes les 30 secondes. Deux
  conséquences : (1) dès que vous posiez un mot de passe, la sonde recevait un `401` et
  Railway déclarait votre application **en panne en permanence** — redémarrages en boucle ;
  (2) quand le mot de passe était vide, chaque sonde **rendait le tableau de bord complet**,
  soit 8 requêtes SQL toutes les 30 secondes, pour rien.
- **APRÈS** — La sonde vise `/health`, une route publique par conception qui répond « ok »
  sans toucher la base. Vérifié : avec mot de passe posé, `/health` répond 200 et `/` répond
  401, comme attendu.

### 1.8b — Vos secrets et vos médias ne partent plus dans l'image (risque #36)

`.dockerignore` (nouveau fichier).

- **AVANT** — Le Dockerfile copiait **tout** le répertoire du projet dans l'image :
  votre fichier `.env` (donc vos secrets Google, vos mots de passe) s'y retrouvait figé, ainsi
  que `data/` (votre base et vos médias), `venv/`, `.git/` et `node_modules/`.
- **APRÈS** — Seul ce qui est nécessaire à l'exécution entre dans l'image. Image plus petite,
  et vos secrets ne sont plus gravés dans une couche d'image.

### 1.8c — La base ne peut plus être versionnée par accident

`.gitignore` — `data/samourais.db*`.

- **AVANT** — Trois motifs listés à la main ; le fichier `-shm` (créé par SQLite) n'était
  pas couvert et pouvait être committé.
- **APRÈS** — Un seul motif couvre la base et tous ses fichiers annexes.

---

## Réconciliation — corrections ajoutées par la passe de lissage

Trois trous laissés entre les périmètres des cinq lots. Aucun n'était couvert par un test,
donc aucun n'aurait été vu par la suite.

### R1 — Lot 1.7 était livré à moitié : le titre des posts du calendrier

`app/calendar/api.py:87` — `AUDIT.md` §5 ligne 199.

Le lot 1.7 comporte **deux** volets dans le plan : la légende Instagram *et* le titre du
calendrier. Le fichier `app/calendar/api.py` n'appartenait au périmètre d'aucun des cinq
lots — personne ne l'a corrigé.

- **AVANT** — Un post de calendrier ayant un **titre** mais **pas de légende** s'affichait
  « Sans titre » dans le calendrier. Le titre que vous aviez saisi était ignoré.
- **APRÈS** — Le titre s'affiche. Vérifié sur les 4 combinaisons possibles : seul ce cas
  change, les trois autres sont identiques au comportement actuel.

### R2 — `fetch_error` devient un champ officiel

`app/scraper/base.py:48-51`.

Le lot 1.4b devait poser un champ `fetch_error` sur `ExtractorResult`, mais `base.py`
était explicitement **exclu** du périmètre du lot D. Le champ était donc posé « à la
volée » sur l'objet, et relu défensivement par le pipeline.

- **AVANT** — Le signal « plateforme injoignable » reposait sur un attribut non déclaré.
  Aucun effet visible aujourd'hui, mais une modification anodine de la classe
  (`slots=True`) aurait pu le faire disparaître silencieusement — et le risque #53 serait
  revenu sans que personne ne le voie.
- **APRÈS** — Le champ est déclaré avec sa valeur par défaut. Aucun changement de
  comportement : les 447 tests donnent le même résultat.

### R3 — Commentaire du Dockerfile devenu faux

`Dockerfile:31`. Le commentaire citait des numéros de ligne (`app/web/app.py:68-70, 84-85`)
qui ne désignaient déjà plus la bonne chose après les modifications du lot A. Remplacé par
une référence stable. Purement documentaire, aucun effet sur l'image.

---

## Preuve d'innocuité — votre base de production

**Aucune donnée réelle n'a été perdue.** Vérifié directement.

- Votre base `data/samourais.db` est **bit à bit identique** à sa version d'avant la vague
  (empreinte `b43a7ee2…`), date de modification inchangée (10 mars).
- Contenu vérifié sur une **copie** en lecture seule : contrôle d'intégrité SQLite `ok`,
  2 profils (`instagram/yugnat999`, `twitter/FAFO_TV`), 16 médias, 42 jobs, 1 commentaire,
  1 note, 1 session — et exactement 16 fichiers médias correspondants sur le disque.
- Vos 16 fichiers de `data/downloads/`, `data/sessions/instagram.json` et `data/editor/*`
  ont des empreintes inchangées.

**Une nuance à connaître sur le protocole de contrôle.** L'empreinte de référence
`2ab4596d…` portait sur **28** fichiers ; il y en a désormais **26**. Les deux fichiers
disparus sont `samourais.db-wal` et `samourais.db-shm` : ce sont les **journaux temporaires**
de SQLite, que le moteur supprime de lui-même à chaque fermeture propre de la base. Ils ont
été consommés par un test de démarrage exécuté pendant la vague. Ce ne sont pas des données —
la preuve étant que la base elle-même n'a pas changé d'un octet, ce qui n'aurait pas été le
cas si ces journaux avaient contenu des écritures en attente.

> **Recommandation de protocole** : l'empreinte de contrôle devrait exclure
> `samourais.db-wal` et `samourais.db-shm`. En l'état, **tout démarrage normal de
> l'application** la fait changer, ce qui déclenche une fausse alerte de destruction de
> données. Empreinte stable proposée :
> `find data -type f ! -name '*.db-wal' ! -name '*.db-shm' | sort | xargs shasum | shasum`
> → vaut aujourd'hui, et de façon reproductible, `1a0d4337…` sur 26 fichiers.

---

## Ce qui reste ouvert

### A. Signalé par un relecteur, non repris par personne — à arbitrer

| # | Sujet | Fichier | Pourquoi ce n'est pas fait |
|---|---|---|---|
| O1 | **Réglages : message d'erreur invisible.** Quand une valeur numérique est refusée, la bannière verte « Sauvegarde effectuée » s'affiche **quand même** et le message rouge n'apparaît jamais. Vous croyez avoir enregistré alors que rien ne l'est. | `app/web/templates/settings.html:425-431` | Hors périmètre de tous les lots. **C'est le défaut le plus gênant au quotidien qui reste** : il rend le correctif 1.3b invisible pour vous. Correctif : conditionner la bannière au succès réel de la requête. |
| O2 | **`PORT` réglable depuis l'interface.** Enregistrer le formulaire « Avancé » écrit `PORT=3000` sur le volume, valeur qui écrase ensuite le port imposé par Railway → contrôle de santé en échec permanent. | `app/web/api.py`, `settings.html:381-408` | Défaut **préexistant**. Le lot 2.1 le prévoit déjà (retrait de `PORT` de la liste blanche **et** du formulaire). Un test épingle nommément les 27 clés actuelles : le retrait doit se faire avec la mise à jour du test. |
| O3 | **Journalisation incohérente.** `app/db.py` est le seul module du projet à utiliser la journalisation standard de Python ; les 19 autres utilisent loguru. Le message de migration sort donc **sans horodatage ni niveau** dans les logs Railway. | `app/db.py:1` | Purement cosmétique, et le message **sort bien** (vérifié sur un vrai démarrage). Le corriger casserait un bon test de régression. À traiter dans un lot « cohérence des logs » dédié (voisin du lot 3.1). |
| O4 | **Image Docker jamais construite.** Le démon Docker était indisponible sur la machine (colima arrêté). Le `Dockerfile` modifié n'a été validé que statiquement. | `Dockerfile` | **À faire avant le prochain déploiement** : démarrer colima puis `docker build -t samourais:test .`. Le diff se limite à un commentaire et au chemin sondé, le risque est faible mais non nul. |

### B. Arbitrages assumés — comportements volontairement laissés en l'état

| # | Sujet | Décision |
|---|---|---|
| O5 | **Le pipeline saute le téléchargement quand la plateforme est injoignable.** Tant qu'Instagram est bloqué, les médias déjà repérés mais non encore téléchargés ne le sont plus. | Assumé : laisser tourner le téléchargement sur un réseau mort brûlerait le budget de réessai de ces médias pour une cause qui ne les concerne pas. À trancher au lot 3.4 (découpler téléchargement et scrape). |
| O6 | **Un compte réel à 0 post pourrait être classé « échoué ».** Le détecteur de panne considère un résultat totalement vide comme un échec. | Assumé : c'est ce que les tests exigent, et ça capture la « session morte ». Recommandation pour le lot 3.3 : introduire un statut `bloqué` distinct de `échoué`. |
| O7 | **Détection de migration par le texte du message SQLite.** Si une version future de SQLite reformule « duplicate column name », le démarrage échouerait. | Risque prospectif, pas actuel (vérifié sur SQLite 3.45.3, message stable depuis des années). Durcissement possible : lire `PRAGMA table_info` plutôt que de se fier au message. |
| O8 | **Instagram : repli résiduel vers l'identifiant numérique.** Pour un post sans code court, l'ancien espace d'identifiants subsiste. | Non corrigé : le supprimer **perdrait** le post au lieu de le stocker sous un identifiant imparfait. Cas rare, sans effet sur vos données réelles. Ajouter un avertissement de log pour rendre ce chemin visible. |

### C. Lots reportés aux vagues suivantes — les 31 bugs connus restants

Chacun est gardé par un test qui échouera volontairement tant que le bug est là.

| Lot | Sujet | Tests en attente |
|---|---|---|
| **2.5** | Un fichier téléchargé n'est jamais vérifié : une page d'erreur HTML peut être stockée comme s'il s'agissait d'une photo, et l'extension du fichier vient du serveur distant (faille de script inter-site) | 2 |
| **3.2b** | Un média appartenant à un profil désactivé sort **définitivement** du réessai automatique | 1 |
| **3.3** | Un scrape sain sans nouveauté est toujours affiché « vide » au lieu de « terminé » ; un job « vide » n'a pas de date de fin | 2 |
| **3.4** | **Le volume n'est jamais purgé** : miniatures orphelines, répertoires de l'éditeur, médias du calendrier. Plus : fichiers partiels abandonnés après un téléchargement raté, aucun contrôle d'espace disque, et un refus définitif (403) réessayé 3 fois pour rien | 8 |
| **3.4b** | Les vidéos en flux (HLS) n'ont aucun réessai, contrairement aux téléchargements directs | 2 |
| **3.7** | Les suppressions en cascade sont **décoratives** : supprimer un profil laisse ses médias, ses statistiques Instagram et des références mortes dans l'éditeur | 3 |
| **3.8** | Les index déclarés ne sont jamais créés sur une base préexistante → lenteurs | 1 |
| **4.2** | La reprise des jobs au démarrage n'a aucune notion de propriété de processus | 1 (⚠️ **à réécrire** si la correction passe par un PID ou un battement de cœur) |
| **5.4** | En mode Google Drive, la bibliothèque reste **grise** : l'adresse Drive n'est jamais transmise à l'interface | 1 |
| **≈1.5** | Le rattrapage historique (`backfill_from` / `backfill_to`) n'est honoré par aucun extracteur ; le mode quotidien s'arrête sur un post épinglé | 8 |
| **≈1.5b** | Les statistiques de compte (abonnés, biographie) ne sont pas recopiées → vos historiques de profil sont vides | 1 |
| **#6** | `/api/debug/volume` est **public** et liste le contenu de votre volume, sans mot de passe | 1 |
| **#18** | Un jeton Google est renouvelé **une fois par fichier** au lieu d'une fois par job (200 médias = 200 renouvellements) | 1 |

**Priorité recommandée pour la vague 2** : le risque **#6** (page de diagnostic publique) est
le seul trou de sécurité restant qui soit exploitable par un inconnu sans mot de passe, et il
n'a **aucun lot attribué**. Il devrait en recevoir un.

---

## Vérifications finales

```
$ venv/bin/python -m pytest tests/ -q          (3 exécutions, dont une à ordre fixe)
416 passed, 31 xfailed in 2.60s
416 passed, 31 xfailed in 2.23s
416 passed, 31 xfailed in 2.27s
0 failed, 0 XPASS — résultat déterministe.

$ find data -type f | sort | xargs shasum | shasum
1a0d433713b94b04a3a22aa42acdfb6395b5c2b2   (26 fichiers)
$ shasum data/samourais.db
b43a7ee25f9bbd28fd180f3857c7f65473555c56   — identique à l'avant-vague

$ venv/bin/python -c "import app.config"            -> OK
$ venv/bin/python -c "import run"                   -> OK
$ venv/bin/python -c "... create_app()"             -> OK, 57 routes
Pages /health / /profiles /viewer /editor /calendar /analytics /settings /jobs -> 200
```

**Comptabilité des corrections.** 85 bugs connus au départ, 31 restants : **54 corrigés**.
Ce chiffre se décompose exactement selon le contrat de `tests/README.md` §5 —
19 (sécurité web) + 2 (schéma) + 15 (cycle de vie des jobs) + 6 (extraction/persistance)
+ 12 (contrats vérifiés sur les 4 plateformes) = 54. **Aucun marqueur n'a été retiré à tort,
et aucun bug n'a été corrigé par accident sans être compté.**
