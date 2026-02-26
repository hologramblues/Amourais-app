# SAMOURAIS SCRAPPER

Plateforme unifiee de scraping, edition et publication de medias sociaux.

## Modules

| Module | Description |
|--------|-------------|
| **Scrapper** | Scraping automatique Instagram, TikTok, Twitter/X, Reddit |
| **Quick Download** | Coller un lien unique pour telecharger un media |
| **Viewer** | Galerie avec lightbox, ratings, commentaires |
| **Meme Editor** | Editeur Fabric.js + export video FFmpeg |
| **Calendar** | Planification multiplateforme avec FullCalendar.js |
| **Analytics** | Dashboard Chart.js avec metriques reelles |

## Stack

- **Backend** : Python / Flask / SQLAlchemy / APScheduler
- **Scraping** : Scrapling (patchright headless browser)
- **Frontend** : Jinja2 + PicoCSS + HTMX + Fabric.js + FullCalendar.js + Chart.js
- **Storage** : Local (disk) ou Google Drive

## Installation locale

```bash
# Cloner
git clone https://github.com/hologramblues/Amourais-app.git
cd Amourais-app

# Environnement Python
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m patchright install chromium

# Configuration
cp .env.example .env
# Editer .env si besoin

# Lancer
python run.py
# -> http://localhost:8080
```

## Deploiement Railway

Le projet inclut un `Dockerfile` et un `railway.toml` prets pour Railway.
Railway injecte automatiquement la variable `PORT`.

## Structure

```
app/
  analytics/     # API analytics (Chart.js)
  calendar/      # API calendrier (FullCalendar.js)
  editor/        # API editeur (FFmpeg video processing)
  scraper/       # Extracteurs par plateforme + quick download
  web/           # Routes Flask, templates, static assets
  config.py      # Configuration (.env)
  db.py          # Modeles SQLAlchemy
  scheduler.py   # APScheduler (4 jobs recurrents)
  storage.py     # Local / Google Drive upload
data/            # DB, downloads, cookies (gitignored)
run.py           # Point d'entree
```
