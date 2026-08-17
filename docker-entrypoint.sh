#!/bin/sh
# ---------------------------------------------------------------------------
# Point d'entrée du conteneur.
#
# POURQUOI IL EXISTE
# Un volume monté sur /data RECOUVRE le répertoire de l'image, propriétaire
# compris. Le `chown appuser /data` fait au build ne vaut donc que pour un
# volume vierge : Railway (comme Docker en général) monte un volume neuf en
# root:root. `ensure_data_dirs()` lèverait alors une PermissionError au
# démarrage et le conteneur redémarrerait en boucle.
#
# Ce script tourne en root, ajuste la propriété du volume, PUIS abandonne ses
# privilèges pour exécuter la commande en tant qu'appuser. Le processus final
# n'est donc jamais root, ce qui était tout l'intérêt du lot 4.3.
#
# TOLÉRANT PAR CONSTRUCTION : si le chown échoue (volume en lecture seule,
# système de fichiers exotique, conteneur déjà non-root), on le DIT et on
# continue. L'application a son propre diagnostic de volume au démarrage et
# échouera bruyamment si elle ne peut réellement pas écrire — mieux vaut ça
# qu'un point d'entrée qui refuse de démarrer pour une précaution.
# ---------------------------------------------------------------------------
set -e

DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    if ! chown -R appuser:appuser "$DATA_DIR" 2>/dev/null; then
        echo "entrypoint: chown de $DATA_DIR impossible — on continue," >&2
        echo "entrypoint: l'application dira au démarrage si elle peut écrire." >&2
    fi

    # setpriv est fourni par util-linux (présent dans python:3.12-slim) et
    # remplace le processus sans laisser de père : gunicorn devient PID 1 et
    # reçoit donc bien le SIGTERM de Railway au redéploiement, ce dont dépend
    # l'arrêt gracieux du scheduler (lot 4.4).
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid=appuser --regid=appuser --init-groups -- "$@"
    fi
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec appuser "$@"
    fi
    echo "entrypoint: ni setpriv ni su-exec — exécution en root (dégradé)." >&2
fi

exec "$@"
