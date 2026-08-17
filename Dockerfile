# SAMOURAIS SCRAPPER — Python/Flask
FROM python:3.12-slim

# FFmpeg + Chromium deps for scrapling/patchright headless browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core fonts-liberation curl util-linux \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps — encore en root : site-packages appartient a root.
# `patchright install` est DELIBEREMENT separe et repousse plus bas, APRES le
# passage en utilisateur non privilegie (voir le bloc suivant).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Utilisateur non privilegie (lot 4.3 d'AUDIT.md §9)
# ---------------------------------------------------------------------------
# ORDRE CRITIQUE : l'utilisateur est cree, et on bascule dessus, AVANT
# `patchright install`. Patchright (fork de Playwright) telecharge ses
# navigateurs dans le CACHE UTILISATEUR — verifie sur cette base de code :
# `_transport.py` ne force `PLAYWRIGHT_BROWSERS_PATH=0` que pour les binaires
# geles (`sys.frozen` / `__compiled__`), ce qui n'est pas notre cas. Installer
# en root deposerait donc les navigateurs dans /root/.cache/ms-playwright,
# illisible pour l'utilisateur final : le scraping casserait SILENCIEUSEMENT
# (les extracteurs avalent l'exception de fetch et rendent un job `empty`,
# indiscernable d'un scrape sain — AUDIT.md §4.20).
#
# Ceinture ET bretelles : le chemin des navigateurs est en plus rendu EXPLICITE
# par PLAYWRIGHT_BROWSERS_PATH, pose avant l'installation et toujours present a
# l'execution. Plus rien ne depend alors de la resolution de $HOME.
ENV HOME=/home/appuser \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN useradd --create-home --home-dir /home/appuser --shell /usr/sbin/nologin appuser \
    && mkdir -p /ms-playwright /data \
    && chown -R appuser:appuser /ms-playwright /data /app /home/appuser

USER appuser

# Navigateurs installes EN TANT QU'APPUSER, dans PLAYWRIGHT_BROWSERS_PATH.
RUN python -m patchright install chromium

# Copy app source — possede par appuser des la copie (pas de chown -R d'une
# couche entiere ensuite, qui doublerait la taille de l'image).
COPY --chown=appuser:appuser . .

# Default data directory — overridden by DATA_DIR env var when a Railway
# volume is mounted (e.g. DATA_DIR=/data with volume at /data).
# The app creates subdirs at startup via ensure_data_dirs().
#
# Un volume monte sur /data RECOUVRE le repertoire de l'image, proprietaire
# compris : le `chown` ci-dessus ne vaut donc que pour un volume vierge, et
# Railway monte un volume neuf en root:root. Sans traitement, appuser ne
# pourrait pas ecrire et `ensure_data_dirs()` ferait redemarrer le conteneur
# en boucle.
# TRAITE par docker-entrypoint.sh : il demarre en root, ajuste la propriete du
# volume, puis abandonne ses privileges (setpriv) avant d'exec-er gunicorn.
# Le processus final tourne donc en appuser. Si le chown est impossible, le
# point d'entree le DIT et laisse demarrer : c'est alors le diagnostic de
# volume de run.py qui tranchera, bruyamment (« CANNOT WRITE to DATA_DIR »).
ENV DATA_DIR=/data

# Railway injects PORT env var; fallback 8080
EXPOSE 8080

# Health check — /health is public by design (route `_health` + son exemption
# dans `_require_auth`, app/web/app.py),
# alors que "/" repasse par l'authentification Basic : le sonder rendait le
# healthcheck unhealthy en permanence dès qu'APP_PASSWORD est posé, et, quand
# il est vide, faisait rendre tout le dashboard (8 requêtes SQL) toutes les 30 s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# ---------------------------------------------------------------------------
# Serveur de production (lot 4.2)
# ---------------------------------------------------------------------------
# Forme SHELL avec `exec` — et non forme exec JSON — pour deux raisons qui
# doivent tenir ENSEMBLE :
#   * `${PORT}` doit etre developpe a l'execution (Railway l'injecte) ;
#   * `exec` remplace le shell, donc gunicorn devient PID 1 et recoit SIGTERM
#     DIRECTEMENT. Sans `exec`, /bin/sh serait PID 1 et ne relaierait pas le
#     signal : l'arret gracieux du lot 4.4 ne se declencherait jamais.
#
# REGLES NON NEGOCIABLES DE CETTE LIGNE :
#   --workers 1   L'ordonnanceur APScheduler et ses verrous anti-doublon sont
#                 EN MEMOIRE. Deux workers = deux ordonnanceurs qui ne se
#                 voient pas = deux navigateurs sur le meme profil (risque #8,
#                 AUDIT.md §2.1 / §4.21). La montee en charge passe par
#                 --threads, JAMAIS par --workers.
#   pas de --preload   Construirait l'application dans le maitre, donc
#                 demarrerait l'ordonnanceur AVANT le fork ; les threads ne
#                 survivent pas a fork() et le worker heriterait d'un
#                 ordonnanceur `running=True` sans aucun thread actif.
#   pas de --max-requests   Recycler le worker redemarrerait l'ordonnanceur et
#                 tuerait les scrapes en cours.
#   "run:create_wsgi_app()"   Fabrique, pas variable de module : importer
#                 run.py reste sans effet de bord (lot 4.1).
#   --timeout 120  Le defaut (30 s) tuerait le worker — donc l'ordonnanceur et
#                 tous les scrapes en cours — sur une route lente (ffmpeg,
#                 upload Drive).
#   --graceful-timeout 30  Doit rester SUPERIEUR a SHUTDOWN_WAIT_SECONDS
#                 (defaut 8 s dans run.py), sinon gunicorn SIGKILLe le worker
#                 avant la fin de l'arret gracieux.
# Le conteneur DEMARRE en root pour que le point d'entree puisse ajuster la
# propriete du volume monte sur /data — un volume neuf arrive en root:root et
# recouvre le `chown` fait au build. Le point d'entree abandonne ensuite ses
# privileges : le processus final tourne bien en appuser, jamais en root.
USER root
ENTRYPOINT ["/app/docker-entrypoint.sh"]

CMD exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 1 \
    --threads "${GUNICORN_THREADS:-8}" \
    --worker-class gthread \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    "run:create_wsgi_app()"
