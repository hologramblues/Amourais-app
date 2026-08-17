# Suite de tests — SAMOURAIS SCRAPPER (vague 0)

**347 tests verts, 85 xfail, ~2,6 s.** Aucun réseau, aucun navigateur, aucun ffmpeg,
aucun Google Drive, aucun accès à `data/`.

Cette suite est un **filet de sécurité**, pas une spécification. Elle n'introduit aucun
changement de comportement : elle décrit le code tel qu'il est aujourd'hui — **bugs
compris** — pour que la vague 1 puisse le corriger sans rien casser à l'aveugle.

---

## 1. Lancer la suite

```bash
cd "/Users/jeremiegalan/SAMOURAIS SCRAPPER"
./venv/bin/python -m pytest tests/
```

Utilisez **l'interpréteur du venv**, jamais le python système : la suite dépend de
SQLAlchemy 2.0.47 et de `pytest-timeout`.

```bash
# un seul module
./venv/bin/python -m pytest tests/test_pipeline.py

# par domaine (marqueurs déclarés dans pytest.ini)
./venv/bin/python -m pytest tests/ -m scheduler
./venv/bin/python -m pytest tests/ -m "storage or downloader"
./venv/bin/python -m pytest tests/ -m security

# voir la liste des xfail et leur motif
./venv/bin/python -m pytest tests/ -q          # `-ra` est déjà dans addopts

# les 10 tests les plus lents
./venv/bin/python -m pytest tests/ --durations=10
```

`pytest.ini` (à la racine) impose déjà `--strict-markers`, `--strict-config`,
`--timeout=30`, `xfail_strict = true` et `filterwarnings = error`. Aucune option
supplémentaire n'est nécessaire.

### Installation

```bash
./venv/bin/pip install -r requirements-dev.txt
```

---

## 2. Les trois garde-fous du socle

`tests/conftest.py` installe trois protections **avant le premier import** de
`app.config` :

1. **Isolation de la base.** `app/config.py` fige `DATA_DIR` et `DB_PATH` à l'import
   (lignes 16 et 60) et `app/db.py` crée son engine au niveau module (ligne 237).
   `DATA_DIR` est donc positionné sur un répertoire éphémère **avant** tout import.
   `tests/test_socle.py` échoue si cette isolation est rompue.
2. **Interdiction du réseau.** `socket.connect`, `create_connection` et `getaddrinfo`
   lèvent `NetworkAccessAttempted` (une `BaseException`, donc non avalée par un
   `except Exception` applicatif). Les tests HTTP passent par `httpx.MockTransport`,
   qui ne traverse jamais cette barrière.
3. **Interdiction d'écrire dans `data/`.** `open`, `os.open`, `sqlite3.connect`,
   `shutil.*`, `os.remove`… lèvent `ProductionDataWriteAttempted` si le chemin visé
   est sous le vrai `data/`. La base de production du propriétaire est intouchable,
   même par accident.

**Empreinte d'intégrité de `data/`** (28 fichiers) — inchangée par la suite :

```bash
find data -type f | sort | xargs shasum | shasum
# 2ab4596d9151b42acedab8ec4e91494af7d03020
```

---

## 3. Ce que couvre chaque module

| Module | Tests | xfail | Périmètre applicatif |
|---|---:|---:|---|
| `test_socle.py` | 47 | 0 | Le socle lui-même : isolation `DATA_DIR`, purge entre tests, garde-fous réseau et `data/`, fabriques |
| `test_db_schema.py` | 41 | 6 | `app/db.py` — colonnes, index, migrations, cascades, intégrité référentielle (T1, T9) |
| `test_extracteurs_contrat.py` | 34 | 23 | `app/scraper/{instagram,tiktok,twitter,reddit}.py` — contrat commun des 4 extracteurs + parseurs purs |
| `test_pipeline.py` | 25 | 8 | `app/scraper/pipeline.py` — extract → insert → download → décision de statut (T4, T21) |
| `test_scheduler.py` | 60 | 8 | `app/scheduler.py` — cycle de vie des jobs, verrous, sémaphore, récupération au boot, retry (T3) |
| `test_stockage.py` | 39 | 13 | `app/storage.py`, `app/scraper/downloaders.py`, `cleanup_temp_files` (T10, T11) |
| `test_web.py` | 101 | 27 | `app/web/` — routes, API, authentification, traversée de chemin, `/api/settings/env` (T6, T8, T20, T22, T24) |

### Ce qui n'est **jamais** déclenché

`start_scheduler()` / APScheduler, un vrai `threading.Thread` de scrape,
scrapling / patchright, `get_proxy_for_platform` (il fait `load_dotenv(override=True)`
et muterait `os.environ` globalement), ffmpeg, l'API Google Drive, et tout `time.sleep`
réel — le backoff est neutralisé **et** son montant est vérifié.

Le temps est toujours **injecté** (`conftest.FIXED_NOW = 1_700_000_000`) : aucune
dépendance à l'horloge murale, donc aucun test instable à minuit ou au changement
d'heure.

---

## 4. Le mécanisme `xfail(strict=True)` — à lire avant de corriger quoi que ce soit

### La règle

Pour chaque bug connu et documenté dans `AUDIT.md`, la suite contient **deux** tests :

* un test **vert** de *caractérisation*, qui fige le comportement fautif d'aujourd'hui.
  Il rougit si le comportement change — y compris s'il change en mieux, ce qui est
  exactement le signal recherché ;
* un test **`xfail(strict=True)`** qui exprime le comportement **correct attendu**,
  avec le numéro de risque et le lot de correction prévu :

```python
@pytest.mark.xfail(
    strict=True,
    reason="risque #3 / §4.1 AUDIT.md — `_mark_job` (pipeline.py:66) n'horodate "
           "`completed_at` que pour completed/failed/partial ; lot 3.3",
)
def test_un_job_empty_devrait_avoir_une_date_de_fin(...):
    ...
    assert job.completed_at == FIXED_NOW
```

Aucun test rouge n'est jamais écrit pour un bug connu. **La suite est verte
aujourd'hui.**

### Pourquoi un XPASS doit être fêté, pas réparé

`strict=True` signifie : *si ce test réussit, la suite échoue.*

Le jour où quelqu'un corrige le bug, pytest affiche :

```
FAILED tests/test_pipeline.py::test_un_job_empty_devrait_avoir_une_date_de_fin
  [XPASS(strict)] risque #3 / §4.1 AUDIT.md — ... ; lot 3.3
```

**Ce rouge-là est une bonne nouvelle.** Il ne dit pas « tu as cassé quelque chose », il
dit : *« le bug #3 est mort — retire ce marqueur `xfail` et le test devient un test de
non-régression permanent »*. C'est le mécanisme central de tout le chantier : chaque bug
de l'audit **garde sa propre correction**.

La bonne réaction à un XPASS :

1. vérifier que le correctif est bien celui du lot annoncé dans le `reason` ;
2. **supprimer le décorateur `@pytest.mark.xfail(...)`** ;
3. supprimer ou réécrire son jumeau vert de caractérisation, devenu faux ;
4. commiter les deux ensemble.

La mauvaise réaction : mettre `strict=False`, retirer le test, ou l'ignorer. Cela
transforme le garde-fou en décoration.

`pytest.ini` pose en outre `xfail_strict = true` globalement : même un `strict=True`
oublié reste strict. Ceinture et bretelles.

---

## 5. LA TABLE DES XFAIL — contrat entre la suite et le plan d'exécution

**49 marqueurs, 85 tests xfail** (certains sont paramétrés). Tous sont `strict=True`.
Chaque ligne dit à celui qui corrigera le bug **quel marqueur retirer**.

Lisez ce tableau dans le sens du lot : *« je livre le lot 1.3 → je dois faire passer en
vert et déxfailer ces 5 tests-là »*.

### Lot 1.0 / 1.0a / 1.0b — sécurité web (vague 1, priorité maximale)

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_web.py::test_quick_download_corps_hostile_doit_donner_400_json` ×6 | #8 §6.3 (T20) | 1.0 | Un corps JSON hostile (`{"url": 42}`, tableau, chaîne…) renvoie **400 JSON**, jamais la console de debug Werkzeug |
| `test_web.py::test_une_exception_dans_une_vue_api_devrait_produire_du_json` | #8 §6.3 | 1.0 → 3.2 | Une exception de vue produit du JSON, pas du HTML Werkzeug |
| `test_web.py::test_le_code_applicatif_devrait_declarer_un_errorhandler` | #8 §6.3 | 1.0 / 3.2 | `app/` déclare au moins un `errorhandler` (assertion statique) |
| `test_web.py::test_mot_de_passe_accentue_en_tete_hostile_doit_donner_401` ×4 | #59 §6.3 (T24) | 1.0a | `hmac.compare_digest` compare des **bytes** : un en-tête non-ASCII donne 401, pas `TypeError` |
| `test_web.py::test_mot_de_passe_accentue_les_bons_identifiants_doivent_ouvrir_la_page` | #59 §6.3 | 1.0a | Un mot de passe accentué laisse entrer le propriétaire légitime |
| `test_web.py::test_les_identifiants_ne_devraient_pas_etre_whitelistes` ×3 | #52 §6.13 (T22) | 1.0b | `APP_USERNAME` / `APP_PASSWORD` / `FLASK_SECRET_KEY` **hors** de `ALLOWED_ENV_KEYS` |
| `test_web.py::test_settings_env_doit_rejeter_les_identifiants_de_lapplication` ×3 | #52 §6.13 (T22) | 1.0b | `POST /api/settings/env` refuse d'écrire les identifiants dans `DATA_DIR/.env` |
| `test_web.py::test_endpoint_de_diagnostic_ne_devrait_pas_etre_public` | #6 §6.1/§6.11 (T8) | *aucun lot* | `/api/debug/volume` n'expose pas le contenu de `DATA_DIR` sans authentification |

### Lot 1.1 — migrations de schéma

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_db_schema.py::test_migrate_add_columns_doit_ajouter_backfill_from_et_backfill_to` | #1 | 1.1 | `_migrate_add_columns` (db.py:271-282) ajoute les 2 colonnes manquantes |
| `test_db_schema.py::test_init_db_sur_base_legacy_doit_rendre_le_schema_conforme_aux_modeles` | #1 | 1.1 | `init_db()` rend une base préexistante conforme aux modèles |

### Lot 1.2 / 1.3 / 1.3b / 1.4 / 1.4b — cycle de vie des jobs

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_scheduler.py::test_run_job_safe_profil_verrouille_doit_marquer_le_job_failed` | #54 §4.2 chemin A | 1.3 | Verrou de profil déjà pris → job `failed`, jamais `queued` à vie |
| `test_scheduler.py::test_run_job_safe_exception_avant_running_doit_marquer_le_job_failed` | #54 §4.2 chemin B | 1.3 | Exception avant le passage `running` → job `failed` |
| `test_scheduler.py::test_check_due_profiles_une_erreur_de_thread_ne_doit_pas_sauter_les_autres` | #54 §4.2 chemin C | 1.3 | `try/except` **dans** la boucle : un profil en échec n'annule pas le cycle |
| `test_scheduler.py::test_check_due_profiles_le_job_dun_thread_non_demarre_ne_doit_pas_rester_queued` | #54 §4.2 chemin C | 1.3 | `t.start()` gardé : pas de job `queued` sans porteur |
| `test_scheduler.py::test_enqueue_manual_scrape_un_thread_qui_ne_demarre_pas_doit_marquer_le_job_failed` | #54 §4.2 chemin C | 1.3 | **Même garde sur le scrape MANUEL** (`scheduler.py:590-595`) — souvent oublié |
| `test_scheduler.py::test_recover_stale_jobs_doit_epargner_un_job_du_processus_courant` | #54 §4.21 | 1.2 + 4.2 | `_recover_stale_jobs` épargne les jobs du processus courant. ⚠️ Ce test ne bascule en XPASS que pour un correctif fondé sur **l'ancienneté** (`_PROCESS_START_TS`) ou `_running_profiles`. Un correctif par **PID ou heartbeat** exige de **réécrire ce test** — il resterait sinon silencieusement XFAIL |
| `test_pipeline.py::test_exception_dextraction_doit_quand_meme_horodater_le_profil` | #10 §4.5 | 1.4 | `last_scraped_at` posé même sur exception : le profil cesse d'être éternellement « dû » |
| `test_pipeline.py::test_echec_total_de_fetch_doit_produire_un_job_failed` | #53 §4.20 (T21) | 1.4b | Un fetch mort produit `failed` + `error_message`, pas `empty` |
| `test_pipeline.py::test_echec_total_de_fetch_ne_doit_pas_marquer_le_profil_comme_scrape` | #53 §4.20 (T21) | 1.4b | Plateforme non atteinte → `last_scraped_at` **non** mis à jour |
| **`test_pipeline.py::test_scrape_sain_et_echec_total_de_fetch_doivent_etre_discernables`** | #3 + #53 | 1.4b puis 3.3 | **LE TEST CENTRAL** : un cycle sain et un échec total cessent de produire le même badge orange `empty` |
| `test_web.py::test_settings_env_doit_rejeter_une_valeur_non_numerique` ×6 | #41 §4.14 (T6) | 1.3b | `save_env` refuse `PORT=abc` (400) au lieu de briquer le boot |

### Lot 1.5b / 1.6 / 1.7 — extraction et persistance

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_extracteurs_contrat.py::test_instagram_le_chemin_json_utilise_le_meme_espace_que_le_dom` | #45 §4.17 | 1.5b | Chemin JSON et `_dom_fallback` émettent le **même** espace d'identifiants |
| `test_extracteurs_contrat.py::test_instagram_dedup_entre_les_deux_chemins` | #45 §4.17 | 1.5b | La déduplication fonctionne entre les deux chemins d'extraction |
| `test_extracteurs_contrat.py::test_instagram_extract_caption_format_graphql` | #30 §4.9 | 1.7 | Parenthéser `(A or B) if cond else None` (instagram.py:109-113) |
| `test_extracteurs_contrat.py::test_instagram_la_caption_graphql_survit_a_extract` | #30 §4.9 | 1.7 | Les légendes cessent de partir en `NULL` en base |
| `test_extracteurs_contrat.py::test_instagram_extract_remonte_les_statistiques_de_compte` | *non numéroté* | à créer (≈1.5b) | `extract()` recopie followers/biography/media_count → les `ProfileSnapshot` cessent d'être vides |
| `test_pipeline.py::test_un_doublon_ne_doit_pas_annuler_les_items_sains_du_meme_lot` | #9 §4.4 | 1.6 | `db.begin_nested()` : une `IntegrityError` n'annule que la ligne fautive |
| `test_pipeline.py::test_media_new_doit_egaler_le_nombre_de_lignes_reellement_persistees` | #9 §4.4 | 1.6 | `media_new` n'est incrémenté qu'après succès |

> **Note de numérotation.** La commande du lot 1.6 désigne le rollback global comme
> « risque #5 » ; dans le tableau §5 d'`AUDIT.md` ce mécanisme porte le **#9**, le #5 y
> désignant l'absence de `total_seen` dans 3 extracteurs. Les `reason` concernés
> rappellent les deux numéros pour qu'aucune recherche ne tombe à côté.

### Lot 2.5 — intégrité du contenu téléchargé — ✅ LIVRÉ

Marqueurs retirés : les deux tests ci-dessous sont désormais des tests de
non-régression permanents.

| Test | Risque | Lot | Comportement obtenu |
|---|---|---|---|
| `test_stockage.py::test_une_page_derreur_html_doit_etre_rejetee` | §6.6 / T10 | 2.5 ✅ | Validation du contenu (content-type + magic bytes) : une page d'erreur HTML n'est pas stockée comme média |
| `test_stockage.py::test_guess_extension_devrait_appliquer_une_liste_blanche` | §6.6 / T10 | 2.5 ✅ | Liste blanche d'extensions (`downloaders.MEDIA_EXTENSIONS`) : plus de XSS stockée same-origin via le `Content-Type` distant |

Tests ajoutés par le lot (aucun n'est xfail) : `test_un_refus_json_est_rejete_sur_son_content_type`,
`test_une_page_html_deguisee_en_image_est_rejetee_sur_ses_magic_bytes`,
`test_une_reponse_tronquee_est_rejetee_et_retentee`,
`test_guess_extension_laisse_passer_les_vrais_medias`, et côté service des
fichiers (`routes.py`) `test_web.py::test_media_file_pose_nosniff`,
`test_media_file_ne_sert_jamais_inline_un_fichier_non_media`,
`test_media_file_sert_toujours_les_vrais_medias_inline`.

### Lot 3.3 / 3.4 / 3.4b — statuts, disque, nettoyage

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_pipeline.py::test_scrape_sain_sans_nouveaute_devrait_etre_completed` | #3 §4.1 | 3.3 (après 1.5) | Statut décidé sur `total_seen` : un cycle sain sans nouveauté est `completed` |
| `test_pipeline.py::test_un_job_empty_devrait_avoir_une_date_de_fin` | #3 §4.1 | 3.3 | `_mark_job` horodate aussi `empty` — **demi-correctif facile à oublier** |
| `test_stockage.py::test_aucun_fichier_partiel_ne_doit_survivre_a_trois_tentatives_ratees` | #14 | 3.4 ✅ | `try/finally` supprimant `dest` quand `iter_bytes` lève |
| `test_stockage.py::test_enospc_ne_doit_laisser_aucun_fichier_derriere_lui` | #14 §4.10 | 3.4 ✅ | `ENOSPC` nettoie le fichier (et reste NON retenté : `test_enospc_remonte_brut_sans_retry`) |
| `test_stockage.py::test_download_media_doit_verifier_lespace_disque_avant_decrire` | #14 §4.10 | 3.4 ✅ | Contrôle d'espace **avant** la requête réseau et **avant** toute écriture (`_assert_enough_free_space`, marge `_MIN_FREE_BYTES`) |
| `test_stockage.py::test_une_erreur_definitive_ne_devrait_pas_etre_retentee` | T11 §7.4 | 3.4 ✅ | Un 403 n'est pas retenté 3 fois — contrôle négatif : `test_une_panne_reellement_transitoire_reste_retentee` (429/5xx) |
| `test_stockage.py::test_le_menage_doit_purger_les_vignettes_orphelines` | #14 / #51 | 3.4 ✅ | `cleanup_temp_files` balaie `.thumbs` — mais épargne la vignette d'un média vivant (garde sur le radical) |
| `test_stockage.py::test_le_menage_doit_purger_les_repertoires_de_lediteur` | #14 | 3.4 ✅ | `EDITOR_UPLOAD_DIR` / `EDITOR_OUTPUT_DIR` balayés |
| `test_stockage.py::test_le_menage_doit_purger_les_medias_du_calendrier` | #56 | 3.4 ✅ | `CALENDAR_DIR` balayé — mais `ScheduledPost.media_path` et `SavedMeme.file_path` sont désormais lus comme références |
| `test_stockage.py::test_hls_doit_retenter_comme_le_telechargement_direct` | #64 | 3.4b ✅ | `_download_hls` a la même boucle `_MAX_RETRIES` + backoff que `_download_direct` (sauf timeout : définitif) |
| `test_stockage.py::test_hls_un_fichier_vide_doit_aussi_etre_retente` | #64 | 3.4b ✅ | Un ffmpeg produisant un fichier vide est retenté |

> **Lots 3.4 et 3.4b livrés** (les 9 marqueurs ci-dessus sont retirés ; les
> lignes 3.3 restent dues). Jumeaux verts de caractérisation réécrits en
> conséquence : `test_une_url_cdn_expiree_nest_plus_retentee`,
> `test_enospc_remonte_brut_sans_retry`, `test_hls_un_echec_ffmpeg_ne_laisse_aucun_residu`,
> `test_le_menage_ne_supprime_jamais_un_repertoire`. Contrôles négatifs ajoutés
> (sans eux, un correctif trop zélé resterait vert) :
> `test_le_menage_epargne_la_vignette_dun_media_vivant`,
> `test_le_menage_epargne_le_visuel_dun_post_programme`,
> `test_le_menage_epargne_les_fichiers_de_service`,
> `test_hls_un_timeout_ffmpeg_nest_jamais_retente`.

### Lot 3.2b / 3.7 / 3.8 / 3.9 / 5.4 — intégrité, retry, viewer

| Test | Risque | Lot | Comportement attendu une fois corrigé |
|---|---|---|---|
| `test_scheduler.py::test_retry_failed_media_un_item_de_profil_inactif_doit_rester_reessayable` | #44 | 3.2b | Un média de profil inactif ne quitte pas les statuts d'échec : il reste réessayable |
| `test_scheduler.py::test_retry_failed_media_doit_autoriser_cinq_tentatives_reelles` | #27 §4.12 | 3.9 | `retry_count` incrémenté **une** fois par cycle → 5 tentatives réelles, pas 3 |
| `test_db_schema.py::test_supprimer_un_profil_en_sql_doit_supprimer_ses_medias_en_cascade` | #11 | 3.7 | `PRAGMA foreign_keys=ON` : les `ON DELETE CASCADE` cessent d'être décoratifs |
| `test_db_schema.py::test_supprimer_un_profil_doit_supprimer_ses_snapshots_ig` | #12 | 3.7 | Cascade sur `IgInsightSnapshot` : un profil doté d'un snapshot IG redevient supprimable |
| `test_db_schema.py::test_supprimer_un_profil_doit_annuler_les_references_de_lediteur` | #11/#12 | 3.7 | `ondelete=SET NULL` sur les **2** `source_media_id` (`scheduled_posts`, `saved_memes`) — **troisième volet du lot, sans quoi il reste des références pendantes** |
| `test_db_schema.py::test_init_db_sur_base_legacy_doit_creer_les_index_declares` | #62 | 3.8 | `_migrate_add_indexes()` : `create_all()` n'indexe pas une table préexistante |
| `test_stockage.py::test_le_viewer_doit_exposer_gdrive_url_pour_les_medias_sur_drive` | §4.8 | 5.4 | `viewer_api.py` sérialise `gdrive_url` : la bibliothèque cesse d'être grise en mode Drive |

### Sans lot à ce jour — à ouvrir

| Test | Risque | Comportement attendu une fois corrigé |
|---|---|---|
| `test_stockage.py::test_un_job_ne_devrait_rafraichir_le_jeton_oauth_quune_fois` | #18 (§3 l.414, 🟠 Haut) | Un service Drive bâti **une fois par job** au lieu d'un refresh OAuth par fichier (200 médias = 200 refresh aujourd'hui) |
| `test_web.py::test_endpoint_de_diagnostic_ne_devrait_pas_etre_public` | #6 §6.1/§6.11 | Cf. tableau sécurité ci-dessus |
| `test_extracteurs_contrat.py::test_instagram_extract_remonte_les_statistiques_de_compte` | *non numéroté* | Cf. tableau extraction ci-dessus — **à numéroter dans `AUDIT.md` §4** |

---

## 6. Conventions à respecter en ajoutant un test

* **Ne jamais modifier `app/`.** Si un test ne peut pas passer sans, il devient un
  `xfail(strict=True)` documenté. C'est une information, pas un échec.
* **Toujours `strict=True`** sur un `xfail`, et un `reason` qui cite le **numéro de
  risque** (ou, à défaut, la section d'`AUDIT.md`) **et le lot de correction**. Un
  `reason` sans destinataire est un marqueur qui ne sera jamais retiré.
* **Un bug = deux tests** : la caractérisation verte et le xfail du comportement correct.
* **Jamais d'horloge murale** : `FIXED_NOW`, `frozen_now`, ou injection de `datetime`.
  Aucun `sleep` supérieur à 0,1 s.
* **Un test qui ne peut pas échouer est pire que pas de test.** Avant de commiter,
  cassez volontairement le code qu'il prétend couvrir et vérifiez qu'il rougit. Quatre
  tests de cette suite ont dû être réécrits pour cette raison (cf. §7).
* **Respecter le périmètre du module.** Un test de `downloaders.py` va dans
  `test_stockage.py`, pas dans `test_pipeline.py` : deux marqueurs xfail pour un seul
  correctif obligent le mainteneur à chercher le second après avoir retiré le premier.

### Fixtures partagées (`conftest.py`)

`db_session`, `factories` (+ raccourcis `make_profile`, `make_media_item`,
`make_scrape_job`, `make_scheduled_post`), `client`, `auth_client`, `flask_app`,
`make_flask_app`, `test_data_dir`, `settings_env_file`, `timezone`, `auth_header`.

Les fixtures propres à un domaine restent locales à leur module (`sched`,
`install_extractor`, `downloads`, `http_mock`, `ffmpeg_simule`, `menage`…) : les
remonter dans `conftest.py` les rendrait actives partout pour rien.

> Deux fixtures redirigent `DOWNLOAD_DIR`, dans **deux modules consommateurs
> différents** — `download_dir` (vers `downloaders.py`, dans `test_stockage.py`) et
> `download_dir_des_routes` (vers `web/routes.py`, dans `test_web.py`, avec un fichier
> piège hors du répertoire). Les noms sont distincts **à dessein** : ce ne sont pas des
> doublons.

---

## 7. Ce que la revue a corrigé (traçabilité)

Quatre tests **ne pouvaient structurellement pas échouer** et ont été réécrits ; leur
incapacité a été prouvée par mutation, et leur correction vérifiée de la même façon :

| Test | Défaut | Vérification |
|---|---|---|
| `test_stockage.py::test_le_menage_compare_les_chemins_en_absolu` | `Path(x).parent / "." / x.name` vaut `Path(x)` — pathlib normalise à la construction, il n'y avait aucun détour. Doublon exact de son voisin | Reconstruit avec `os.path.join` (qui ne normalise pas) + garde `assert chemin_avec_detour != str(reference)`. Mutation « `abspath` → identité » : **tue** le test |
| `test_scheduler.py::test_run_job_safe_execute_le_pipeline_et_relache_tout` | `acquire(blocking=False)` réussit encore après **une** fuite (`MAX_CONCURRENT_SCRAPES=2`). La fuite ne se voyait que 30 s plus tard, par timeout, sur un test innocent | Compte explicite des jetons + sémaphore neuf par test dans la fixture `sched`. Mutation « `release()` supprimé » : **tue** le bon test, en 0,49 s au lieu de 90 s |
| `test_pipeline.py::test_un_telechargement_en_echec_ne_produit_aucun_fichier` *(ancien nom — n'existe plus)* | Le stub levait avant d'écrire : le répertoire vide était garanti par la fixture, pas par le pipeline | Renommé `test_le_pipeline_ne_reference_aucun_residu_dun_telechargement_echoue`, le stub écrit désormais un résidu et l'assertion porte sur `local_path` / `file_size` / `media_downloaded` |
| `test_scheduler.py::test_check_due_profiles_cree_un_job_pour_un_profil_jamais_scrape` | `assert now == FIXED_NOW` était tautologique (`now` sortait de la fixture) | Assertion retirée ; la lecture de `_now_ts` reste gardée par `test_..._ignore_un_profil_pas_encore_du` |

Autres corrections : garde d'idempotence renforcée sur `_migrate_add_columns` (il
survivait à un no-op complet) ; garde d'existence de table sur la paramétrisation des
index ; `except: return` muet remplacé par une assertion sur la branche exception dans
le contrat des extracteurs ; assertion `statvfs` renforcée (volume plein simulé, aucune
requête ni écriture) au lieu d'un simple compteur d'appels ; citation `AUDIT.md`
corrigée sur `_RAISON_PROFIL` ; deux tests `downloaders.py` retirés de
`test_pipeline.py` (doublon inter-modules avec `test_stockage.py`).

Couverture ajoutée : références pendantes `scheduled_posts` / `saved_memes` (volet
manquant de T9) ; `enqueue_manual_scrape` (le scrape **manuel**, jusqu'ici non testé) ;
câblage de boot de `start_scheduler` par assertion statique ; `completed_at` sur
`empty` ; risque #18 (refresh OAuth par fichier) ; timeout ffmpeg HLS ;
`_pick_best_video_variant` et `_extract_media_from_tweet` (zéro couverture auparavant).

---

## 8. Limites connues

* **APScheduler n'est jamais démarré** (interdit par §7.4). Le *câblage* de
  `start_scheduler` est vérifié par lecture de source (`inspect.getsource`), pas par
  exécution.
* **Carrousels non couverts** côté pipeline et extracteurs : `_media_items_from_node`
  gère `edge_sidecar_to_children`, mais aucun test ne fait passer un post multi-médias.
  C'est la cause racine des KPI faux décrits en §8 d'`AUDIT.md` et le socle du futur
  test T18 (analytics).
* **Seuil `DAILY_KNOWN_THRESHOLD`** : seul le sens « trop bas » est verrouillé. Porter
  le seuil à `10**6` ou supprimer `stop_early` laisserait la suite verte.
* **`content_hash`** est calculé et stocké mais jamais relu (classé 💀 dans `AUDIT.md`) :
  aucun test ne l'asserte.
* **Mode local sans fichier** (`pipeline.py:373-378`) : la branche Drive symétrique gère
  le cas, la branche locale n'a aucun `else`. Non couvert.
* **`_folder_cache` global sans verrou** (risque #57, `storage.py:30`) : difficilement
  déterministe, non couvert.
