# SAMOURAIS SCRAPPER — Documentation

Plateforme unifiée de **scraping, édition et publication de médias sociaux**, construite en Python/Flask. Elle scrape automatiquement des profils Instagram, TikTok, Twitter/X et Reddit, stocke les médias localement ou sur Google Drive, et fournit une interface web complète : galerie, éditeur de memes, calendrier de publication et dashboard analytics.

---

## Sommaire

1. [Vue d'ensemble](#vue-densemble)
2. [Stack technique](#stack-technique)
3. [Architecture](#architecture)
4. [Modules fonctionnels](#modules-fonctionnels)
5. [Modèle de données](#modèle-de-données)
6. [Jobs planifiés (scheduler)](#jobs-planifiés-scheduler)
7. [API HTTP](#api-http)
8. [Configuration](#configuration)
9. [Installation locale](#installation-locale)
10. [Déploiement (Railway)](#déploiement-railway)
11. [Arborescence du projet](#arborescence-du-projet)

---

## Vue d'ensemble

L'application tourne comme un **serveur Flask unique** (`run.py`) qui, au démarrage :

1. Effectue un diagnostic du volume de données (persistance, espace disque, marqueur de boot) ;
2. Crée les répertoires de données nécessaires (`data/downloads`, `data/sessions`, etc.) ;
3. Initialise la base SQLite via SQLAlchemy ;
4. Démarre le scheduler APScheduler (6 jobs récurrents + reprise des jobs orphelins) ;
5. Lance le serveur web sur le port configuré (8080 par défaut).

Interface accessible sur `http://localhost:8080`.

## Stack technique

| Couche | Technologies |
|---|---|
| Backend | Python 3, Flask 3.1, SQLAlchemy 2.0, APScheduler 3.10 |
| Scraping | Scrapling (StealthyFetcher / patchright — Chromium furtif), httpx |
| Traitement vidéo | FFmpeg (via `ffmpeg-python`) |
| Analytics | Instagram Graph API (Meta), Chart.js |
| Frontend | Jinja2, PicoCSS, HTMX, Fabric.js (éditeur), FullCalendar.js (calendrier), Chart.js |
| Stockage | Disque local ou Google Drive (OAuth2) |
| Base de données | SQLite (`data/samourais.db`) |
| Logs | Loguru |

## Architecture

```
                    ┌─────────────────────────────┐
                    │         run.py              │
                    │  diagnostics + init + boot  │
                    └──────┬───────────────┬──────┘
                           │               │
              ┌────────────▼───┐   ┌───────▼────────────┐
              │  Flask (web)   │   │  APScheduler       │
              │  pages + APIs  │   │  6 jobs récurrents │
              └──────┬─────────┘   └───────┬────────────┘
                     │                     │
        ┌────────────┼─────────────────────┼──────────────┐
        │            │                     │              │
  ┌─────▼─────┐ ┌────▼──────┐    ┌─────────▼───┐  ┌───────▼──────┐
  │  Scraper  │ │  Editor   │    │  Calendar   │  │  Analytics   │
  │ pipeline  │ │  FFmpeg   │    │ publication │  │ IG Graph API │
  └─────┬─────┘ └───────────┘    └─────────────┘  └──────────────┘
        │
  ┌─────▼──────────────────────┐
  │  Storage : local / GDrive  │
  └────────────────────────────┘
```

**Pipeline de scraping** (`app/scraper/pipeline.py`) — cycle de vie complet d'un job :
`extract → insert (DB) → download → upload (storage) → cleanup → update stats`

Chaque plateforme a son extracteur dédié (`instagram.py`, `tiktok.py`, `twitter.py`, `reddit.py`) basé sur Scrapling : chargement des cookies de session, interception des réponses GraphQL/API pour récupérer du JSON structuré, et parsing DOM en secours.

## Modules fonctionnels

| Module | Rôle | Fichiers clés |
|---|---|---|
| **Scrapper** | Scraping automatique récurrent de profils (IG, TikTok, X, Reddit) | `app/scraper/` |
| **Quick Download** | Coller un lien de post unique → détection de la plateforme → téléchargement du média | `app/scraper/quick_download.py` |
| **Viewer** | Galerie des médias scrapés : lightbox, notes (ratings), commentaires, suppression par lot, téléchargement | `app/web/viewer_api.py`, `viewer.html` |
| **Meme Editor** | Éditeur Fabric.js côté client + export vidéo FFmpeg côté serveur ; memes sauvegardés en DB | `app/editor/` |
| **Calendar** | Planification de posts multiplateforme (FullCalendar.js), publication automatique à l'échéance | `app/calendar/api.py` |
| **Analytics** | Dashboard Chart.js alimenté par l'Instagram Graph API : followers, engagement, reach, meilleurs horaires… | `app/analytics/` |
| **Storage** | Upload vers Google Drive avec hiérarchie `Racine / {Plateforme} / @{username}`, ou stockage disque | `app/storage.py` |

## Modèle de données

Tables SQLAlchemy définies dans `app/db.py` :

| Table | Contenu |
|---|---|
| `profiles` | Profils suivis (plateforme, username, intervalle de scrape, stats courantes) |
| `media_items` | Médias scrapés (type, URL source, chemin fichier, métadonnées du post) |
| `media_comments` | Commentaires utilisateur sur un média (module Viewer) |
| `media_ratings` | Notes attribuées aux médias |
| `profile_snapshots` | Historique des stats de profil (croissance des followers) |
| `ig_insight_snapshots` | Snapshots des insights Instagram Graph API |
| `scrape_jobs` | Jobs de scraping (statut queued / running / done / failed) |
| `scheduled_posts` | Posts planifiés dans le calendrier |
| `saved_memes` | Memes créés dans l'éditeur |

## Jobs planifiés (scheduler)

`app/scheduler.py` — APScheduler en arrière-plan. Au boot, les jobs `queued`/`running` orphelins d'un déploiement précédent sont récupérés (`_recover_stale_jobs`).

| # | Job | Fréquence | Rôle |
|---|---|---|---|
| 1 | `check_due_profiles` | Toutes les 30 min (+ 1 exécution au boot) | Lance le scraping des profils dont l'intervalle est écoulé |
| 2 | `retry_failed_media` | Toutes les 2 h | Retente le téléchargement des médias en échec |
| 3 | `cleanup_temp_files` | Tous les jours à 03:00 UTC | Nettoyage des fichiers temporaires |
| 4 | `check_due_posts` | Toutes les 5 min | Publie les posts planifiés arrivés à échéance |
| 5 | `collect_ig_stats` | Toutes les 6 h (+ au boot, différé 15 s) | Stats de compte via Instagram Graph API |
| 6 | `collect_media_insights` | Tous les jours à 06:30 UTC | Insights par média via Graph API |

La concurrence des scrapes est bornée (`MAX_CONCURRENT_SCRAPES`, verrou par profil) et un délai est appliqué entre profils (`DELAY_BETWEEN_PROFILES_MS`).

## API HTTP

### Pages (`app/web/routes.py`)

| Route | Page |
|---|---|
| `/` | Dashboard |
| `/profiles` | Gestion des profils suivis |
| `/jobs` | Historique des jobs de scraping |
| `/viewer` | Galerie des médias |
| `/editor` | Éditeur de memes |
| `/calendar` | Calendrier de publication |
| `/analytics` | Dashboard analytics |
| `/settings` | Réglages (env, session IG, Graph API) |
| `/auth/google` + `/auth/google/callback` | OAuth2 Google Drive |
| `/media/file/<path>` / `/media/thumb/<path>` | Fichiers médias et vignettes |
| `/health` | Healthcheck (Railway) |

### API principale (`app/web/api.py`)

| Méthode + Route | Rôle |
|---|---|
| `POST /profiles` | Ajouter un profil à suivre |
| `PATCH /profiles/<id>` | Modifier un profil (intervalle, actif…) |
| `DELETE /profiles/<id>` | Supprimer un profil |
| `POST /profiles/<id>/scrape` | Lancer un scrape manuel |
| `GET /jobs/recent`, `GET /jobs/list` | Liste des jobs |
| `POST /jobs/<id>/retry` | Relancer un job échoué |
| `GET /status` | Statut global |
| `GET /debug/volume` | Diagnostics du volume de données |
| `POST /settings/env` | Écrire les variables d'environnement persistées |
| `POST /settings/ig-api` | Configurer l'Instagram Graph API |
| `POST /settings/session` | Importer une session/cookies de plateforme |
| `POST /quick-download` | Télécharger un média depuis une URL unique |

### Viewer (`app/web/viewer_api.py`)

`GET /viewer/media` (liste filtrable), `GET /viewer/media/<id>`, `POST .../comment`, `DELETE .../comment/<id>`, `POST .../rate`, `GET /viewer/profiles`, `DELETE /viewer/media/batch`, `GET|POST /viewer/memes`, `GET /viewer/memes/<id>/file`, `DELETE /viewer/memes/<id>`.

### Calendrier (`app/calendar/api.py`)

`GET|POST /calendar/posts`, `PATCH|DELETE /calendar/posts/<id>`, `POST /calendar/posts/<id>/publish` (publication immédiate), `GET /calendar/posts/<id>/media`.

### Analytics (`app/analytics/api.py`)

`/analytics/account-overview`, `/follower-growth`, `/engagement`, `/content-breakdown`, `/best-posting-times`, `/top-posts`, `/posting-frequency`, `/reach-impressions`, `/ig-api-status`, `POST /analytics/collect-now`.

### Éditeur (`app/editor/api.py`)

`GET /editor/health`, `POST /editor/process-video` (traitement FFmpeg : crop, format, hauteur de frame…), `GET /editor/media/<id>`.

## Configuration

Configuration par variables d'environnement, chargées depuis **`data/.env`** (persisté sur le volume, éditable depuis la page Réglages) avec `.env` du projet en secours. Voir `.env.example`.

| Variable | Défaut | Rôle |
|---|---|---|
| `PORT` | `8080` | Port du serveur |
| `FLASK_DEBUG` | `1` | Mode debug |
| `APP_USERNAME` / `APP_PASSWORD` | `admin` / *(vide)* | Authentification de l'interface (désactivée si mot de passe vide) |
| `DATA_DIR` | `<projet>/data` | Racine des données (`/data` sur Railway) |
| `STORAGE_MODE` | `local` | `local` (disque) ou `gdrive` (Google Drive) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` / `GOOGLE_REFRESH_TOKEN` | — | OAuth2 Google Drive |
| `GDRIVE_ROOT_FOLDER_NAME` | `SAMOURAIS SCRAPPER` | Dossier racine sur Drive |
| `IG_ACCESS_TOKEN` / `IG_USER_ID` / `FB_APP_ID` / `FB_APP_SECRET` | — | Instagram Graph API (analytics) |
| `DEFAULT_SCRAPE_INTERVAL_MINUTES` | `360` | Intervalle de scrape par défaut |
| `MAX_SCROLLS` / `BACKFILL_MAX_SCROLLS` / `DAILY_MAX_SCROLLS` | `30` / `200` / `40` | Profondeur de scroll par type de scrape |
| `SCROLL_PAUSE_MS` | `3000` | Pause entre scrolls |
| `DELAY_BETWEEN_PROFILES_MS` | `10000` | Délai entre deux profils |
| `MAX_CONCURRENT_SCRAPES` | `2` | Scrapes simultanés max |
| `BROWSER_POOL_SIZE` | `2` | Taille du pool de navigateurs |
| `PROXY_URL` (+ `PROXY_INSTAGRAM`, `PROXY_TIKTOK`, `PROXY_TWITTER`, `PROXY_REDDIT`) | — | Proxy global ou par plateforme |
| `EDITOR_MAX_FILE_SIZE_MB` | `100` | Taille max d'upload dans l'éditeur |
| `LOG_LEVEL` | `INFO` | Niveau de log |

## Installation locale

```bash
git clone https://github.com/hologramblues/Amourais-app.git
cd Amourais-app

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m patchright install chromium   # navigateur Chromium furtif

cp .env.example .env                    # éditer si besoin
python run.py                           # -> http://localhost:8080
```

**Prérequis** : Python 3.10+, FFmpeg installé sur le système (traitement vidéo de l'éditeur).

Pour scraper Instagram/TikTok/X, importer une session (cookies) via la page **Réglages** → elle est stockée dans `data/sessions/<plateforme>.json`.

## Déploiement (Railway)

Le projet inclut un `Dockerfile` et un `railway.toml` :

- **Build** : Dockerfile (installe Chromium/patchright et FFmpeg) ;
- **Healthcheck** : `GET /health` (timeout 300 s), redémarrage `on_failure` (3 tentatives max) ;
- **Volume persistant** : monter le volume `samourais_data` sur `/data` (DB, médias, sessions, réglages survivent aux redéploiements) ;
- Railway injecte automatiquement `PORT`.

Au boot, `run.py` logge un diagnostic complet du volume (persistance détectée via un fichier marqueur, espace disque, contenu de `/data`).

## Arborescence du projet

```
SAMOURAIS SCRAPPER/
├── run.py                  # Point d'entrée : diagnostics, init DB, scheduler, Flask
├── requirements.txt
├── Dockerfile
├── railway.toml            # Config déploiement Railway
├── .env.example
├── app/
│   ├── config.py           # Configuration (.env, chemins, constantes)
│   ├── db.py               # Modèles SQLAlchemy + init_db
│   ├── scheduler.py        # APScheduler : 6 jobs récurrents, verrous, reprise
│   ├── storage.py          # Upload Google Drive (OAuth2) / stockage local
│   ├── instagram_api.py    # Client Instagram Graph API (Meta)
│   ├── scraper/
│   │   ├── base.py         # Classe de base des extracteurs
│   │   ├── pipeline.py     # Orchestration : extract → download → upload → stats
│   │   ├── instagram.py    # Extracteur Instagram (interception GraphQL)
│   │   ├── tiktok.py       # Extracteur TikTok
│   │   ├── twitter.py      # Extracteur Twitter/X
│   │   ├── reddit.py       # Extracteur Reddit
│   │   ├── downloaders.py  # Téléchargement des fichiers médias
│   │   └── quick_download.py  # Téléchargement depuis une URL unique
│   ├── editor/
│   │   ├── api.py          # Endpoints de l'éditeur
│   │   └── processing.py   # Traitement vidéo FFmpeg
│   ├── calendar/
│   │   └── api.py          # CRUD posts planifiés + publication
│   ├── analytics/
│   │   ├── api.py          # Endpoints analytics (Chart.js)
│   │   └── ig_collector.py # Collecte périodique via IG Graph API
│   └── web/
│       ├── app.py          # create_app, /health, enregistrement des blueprints
│       ├── routes.py       # Pages HTML + fichiers médias + OAuth Google
│       ├── api.py          # API principale (profils, jobs, réglages, quick-dl)
│       ├── viewer_api.py   # API galerie (médias, notes, commentaires, memes)
│       ├── templates/      # Jinja2 : dashboard, viewer, editor, calendar…
│       └── static/         # CSS/JS par module + logo
└── data/                   # (gitignoré, volume persistant en production)
    ├── samourais.db        # Base SQLite
    ├── .env                # Réglages persistés (éditables via l'UI)
    ├── downloads/          # Médias scrapés
    ├── sessions/           # Sessions/cookies par plateforme
    ├── cookies/
    ├── calendar/           # Médias des posts planifiés
    └── editor/             # uploads/ et outputs/ de l'éditeur
```
