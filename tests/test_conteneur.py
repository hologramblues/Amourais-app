"""
Assertions statiques sur les fichiers de conteneurisation (lot 1.8).

Volet « conteneur » du test T15 d'AUDIT.md §8 — risques #36 (pas de
`.dockerignore` : `.env` et `data/` figés dans une couche d'image) et #37
(HEALTHCHECK sur `/` au lieu de `/health`).

Ces tests ne lancent ni Docker ni l'application : ils lisent deux fichiers de
configuration. Ils sont là pour qu'une régression sur le `Dockerfile` ou le
`.dockerignore` rougisse au lieu de partir en production.

Le troisième volet de T15 — « `requirements.txt` contient un serveur WSGI » —
n'est PAS testé ici : il appartient au lot 4.2 (gunicorn), qui n'est pas livré.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.static_check

RACINE = Path(__file__).resolve().parents[1]
DOCKERFILE = RACINE / "Dockerfile"
DOCKERIGNORE = RACINE / ".dockerignore"
GITIGNORE = RACINE / ".gitignore"


def _motifs(fichier: Path) -> list[str]:
    """Motifs utiles d'un fichier d'exclusion : ni commentaires ni lignes vides."""
    lignes = fichier.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lignes if l.strip() and not l.strip().startswith("#")]


# ===========================================================================
# Risque #37 — le healthcheck doit sonder l'endpoint public
# ===========================================================================


def test_le_healthcheck_docker_doit_sonder_health_et_pas_la_racine():
    """`/` repasse par l'auth Basic (app/web/app.py:79-92) : sondé, il rend le
    healthcheck unhealthy en permanence dès qu'`APP_PASSWORD` est posé.
    `/health` est public par conception (app/web/app.py:68-70, 84-85)."""
    contenu = DOCKERFILE.read_text(encoding="utf-8")

    # La directive peut être écrite sur plusieurs lignes (continuations « \ »).
    aplati = contenu.replace("\\\n", " ")
    lignes_hc = [l for l in aplati.splitlines() if l.strip().startswith("HEALTHCHECK")]
    assert len(lignes_hc) == 1, "un et un seul HEALTHCHECK attendu dans le Dockerfile"

    directive = lignes_hc[0]
    assert "/health" in directive, (
        "le HEALTHCHECK doit sonder /health (endpoint public), pas une route "
        f"protégée par l'authentification Basic. Directive lue : {directive!r}"
    )
    # Et surtout : plus aucune sonde de la racine.
    assert "8080}/ " not in directive and not directive.rstrip().endswith("8080}/"), (
        "le HEALTHCHECK sonde encore « / » : cf. risque #37 d'AUDIT.md"
    )


# ===========================================================================
# Risque #36 — le contexte de build ne doit embarquer ni secrets ni données
# ===========================================================================


def test_un_dockerignore_doit_exister():
    assert DOCKERIGNORE.is_file(), (
        "`.dockerignore` absent : « COPY . . » embarque .env, data/, venv/ et "
        ".git/ dans les couches de l'image (risque #36)"
    )


@pytest.mark.parametrize(
    "motif",
    [".env", "data/", "venv/", "node_modules/", ".git/"],
)
def test_le_dockerignore_doit_exclure_les_secrets_et_les_donnees(motif: str):
    """Les cinq exclusions non négociables : secrets, base de PRODUCTION,
    environnement virtuel, dépendances Node héritées, historique Git."""
    assert motif in _motifs(DOCKERIGNORE), (
        f"`{motif}` n'est pas exclu du contexte de build (risque #36)"
    )


@pytest.mark.parametrize("indispensable", ["app", "run.py", "requirements.txt"])
def test_le_dockerignore_ne_doit_rien_exclure_dont_lexecution_a_besoin(
    indispensable: str,
):
    """Garde-fou inverse : une exclusion trop large casse le déploiement.
    Le Dockerfile fait `COPY requirements.txt .` puis `COPY . .` puis lance
    `python run.py`, qui importe `app/`."""
    for motif in _motifs(DOCKERIGNORE):
        if motif.startswith("!"):
            continue  # une exception réintègre, elle n'exclut pas
        nu = motif.rstrip("/")
        assert nu != indispensable, (
            f"`{motif}` exclut `{indispensable}`, dont le conteneur a besoin "
            "à l'exécution"
        )


def test_le_gitignore_doit_couvrir_la_base_de_production():
    """Troisième volet de T15 encore rattaché au lot 1.8 : la base SQLite et ses
    fichiers -wal/-shm ne doivent jamais être versionnés."""
    assert any(m.startswith("data/samourais.db") for m in _motifs(GITIGNORE)), (
        "aucun motif ne couvre `data/samourais.db*` (base + fichiers -wal/-shm)"
    )
