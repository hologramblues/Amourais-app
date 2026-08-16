"""
MOTEUR DE SANTÉ DE SESSION — un état honnête par plateforme.

────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE
────────────────────────────────────────────────────────────────────────────
L'écran Réglages déduisait jusqu'ici l'état d'une session de DEUX faits qui ne
disent rien de la session : le fichier de cookies existe, et il a telle date de
modification. Un fichier de cookies parfaitement lisible dont Instagram a
invalidé le `sessionid` côté serveur affichait donc « vert ».

Mesuré en conditions réelles le 15/08/2026 sur `data/sessions/instagram.json` :
le fichier date du 10 mars, `ds_user_id` a expiré 68 jours plus tôt, et le
scrape rapporte 0 média sur un mur de connexion. L'ancien voyant restait vert.

────────────────────────────────────────────────────────────────────────────
DEUX SIGNAUX, JAMAIS UN SEUL
────────────────────────────────────────────────────────────────────────────
a) SIGNAL PASSIF — `etat_passif()`. Gratuit, permanent, sans réseau. Il lit le
   fichier de cookies (présence, cookies critiques, dates d'expiration) et
   l'historique récent des jobs de la plateforme. Il se met à jour tout seul à
   chaque scrape. C'est LUI qui alimente l'affichage par défaut.

b) SONDE ACTIVE — `sonder()`. Coûteuse : elle lance un vrai navigateur furtif,
   charge une page de la plateforme avec les cookies stockés, et classe la
   réponse. Elle tranche quand le passif est ambigu et sert de bouton
   « Vérifier maintenant ».

────────────────────────────────────────────────────────────────────────────
QUATRE ÉTATS, JAMAIS UN BOOLÉEN
────────────────────────────────────────────────────────────────────────────
    « connecté »   la page répond avec du contenu authentifié
    « déconnecté » mur de connexion / redirection vers la page de login
    « bloqué »     défi, captcha, limitation de débit, page d'erreur
    « inconnu »    pas de cookies, ou la sonde n'a pas pu conclure

RÈGLE CARDINALE : « inconnu » n'est JAMAIS présenté comme « connecté ». C'est
exactement la confusion que ce chantier combat. Une sonde qui échoue (navigateur
absent, réseau mort, délai dépassé) rend « inconnu », jamais autre chose.

────────────────────────────────────────────────────────────────────────────
LES INDICES DE DÉCONNEXION, ET POURQUOI CEUX-LÀ
────────────────────────────────────────────────────────────────────────────
La vraie page renvoyée par Instagram affichait « Use another profile » et
« Create new account ». Ces chaînes sont des libellés d'INTERFACE EN ANGLAIS :
elles changent à la prochaine refonte, et disparaissent dès que la locale du
navigateur change. Elles ne sont donc PAS le critère principal.

Les indices retenus, du plus robuste au moins robuste :

  1. URL FINALE — après redirections, l'URL contient `/accounts/login`
     (Instagram), `/login`, `/i/flow/login` (X). C'est un fait de protocole :
     la plateforme elle-même déclare que la requête n'est pas authentifiée.
     La sonde vise volontairement une page RÉSERVÉE AUX CONNECTÉS pour
     provoquer cette redirection.
  2. DRAPEAU D'ÉTAT DANS LE JSON EMBARQUÉ — `"is_logged_in": false`. C'est une
     clé d'API, pas un libellé traduit.
  3. FORMULAIRE DE CONNEXION — un `<input name="password">` dans le document.
     Structurel, indépendant de la langue.
  4. COOKIE D'IDENTITÉ ABSENT DE LA RÉPONSE — `ds_user_id` (Instagram) n'est
     plus posé par le serveur. Indice FAIBLE : jamais suffisant seul, il ne
     sert qu'à corroborer.

Les libellés anglais ne sont utilisés nulle part comme critère de décision.

────────────────────────────────────────────────────────────────────────────
CE QUI EST VÉRIFIÉ ET CE QUI NE L'EST PAS
────────────────────────────────────────────────────────────────────────────
Seul Instagram a été éprouvé sur une session RÉELLEMENT MORTE. Les réglages des
trois autres plateformes suivent la même mécanique mais n'ont PAS été confrontés
à leurs vraies pages : ils sont marqués `verifie=False` dans `_SONDES`, et
l'état renvoyé le dit.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from loguru import logger

# ---------------------------------------------------------------------------
# Les quatre états
# ---------------------------------------------------------------------------
ETAT_CONNECTE = "connecté"
ETAT_DECONNECTE = "déconnecté"
ETAT_BLOQUE = "bloqué"
ETAT_INCONNU = "inconnu"

#: Ordre de gravité — sert à trier l'affichage, du plus urgent au plus calme.
GRAVITE = {ETAT_DECONNECTE: 0, ETAT_BLOQUE: 1, ETAT_INCONNU: 2, ETAT_CONNECTE: 3}

PLATEFORMES: tuple[str, ...] = ("instagram", "reddit", "tiktok", "twitter")

#: Sources d'un verdict — d'où vient la preuve.
SOURCE_COOKIES = "cookies"
SOURCE_JOBS = "jobs"
SOURCE_SONDE = "sonde"
SOURCE_AUCUNE = "aucune"

# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------
#: Nombre de jobs récents examinés par le signal passif.
FENETRE_JOBS = 3

#: Marqueur laissé par `pipeline._plateforme_non_atteinte` sur un job dont la
#: plateforme n'a pas répondu. C'est le lien exact entre le pipeline et ce
#: module : si ce message change dans pipeline.py, le signal passif se tait
#: (il rend « inconnu »), il ne ment pas.
MARQUEUR_NON_ATTEINT = "n'a pas été atteint"

#: Au-delà, un verdict de sonde n'est plus considéré comme frais.
FRAICHEUR_SONDE_S = 6 * 3600 + 1800  # 6 h 30 — un cran au-dessus du job planifié

#: Un job plus vieux que ça ne prouve plus rien sur l'état actuel.
FRAICHEUR_JOBS_S = 3 * 24 * 3600  # 3 jours

#: Seuil d'alerte avant expiration d'un cookie critique.
ALERTE_EXPIRATION_S = 7 * 24 * 3600  # 7 jours

#: Délai franc de la sonde, mur d'horloge. Au-delà : « inconnu ».
DELAI_SONDE_S = 90

# ---------------------------------------------------------------------------
# Gestes correctifs
# ---------------------------------------------------------------------------
GESTE_REIMPORTER = (
    "Reconnectez-vous à la plateforme dans votre navigateur, réexportez les "
    "cookies, puis importez-les dans Réglages → Sessions."
)
GESTE_ATTENDRE = (
    "Attendez quelques heures, vérifiez le proxy de la plateforme, puis "
    "relancez la sonde."
)
GESTE_IMPORTER = "Importez un export de cookies dans Réglages → Sessions."
GESTE_RESONDER = "Relancez la sonde pour trancher."


# ---------------------------------------------------------------------------
# Description d'une sonde par plateforme
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Sonde:
    """Tout ce qu'il faut savoir pour interroger et classer une plateforme."""

    plateforme: str
    #: Page VISÉE par la sonde. On choisit délibérément une page réservée aux
    #: comptes connectés : déconnecté, la plateforme redirige, et c'est cette
    #: redirection — un fait de protocole — qui devient l'indice principal.
    url_sonde: str
    #: Cookies sans lesquels l'export est une session anonyme.
    cookies_critiques: tuple[str, ...]
    #: Cookie qui porte l'identité de l'utilisateur, s'il existe.
    cookie_identite: str | None
    #: Fragments d'URL qui signent une page de connexion.
    motifs_url_login: tuple[str, ...]
    #: Fragments d'URL qui signent un défi / une suspension / un captcha.
    motifs_url_blocage: tuple[str, ...]
    #: `(libellé lisible, expression régulière)` prouvant une page AUTHENTIFIÉE.
    #: Le libellé — et non le motif — est ce qui remonte dans l'UI.
    motifs_connecte: tuple[tuple[str, str], ...] = ()
    #: `(libellé lisible, expression régulière)` prouvant une page DÉCONNECTÉE.
    #: Ce sont des drapeaux d'état ou des identifiants techniques, JAMAIS des
    #: libellés d'interface traduisibles.
    motifs_deconnecte: tuple[tuple[str, str], ...] = ()
    #: Chemin de la page réservée aux comptes connectés. Quand la réponse
    #: FINALE n'est plus sur ce chemin, la plateforme a refusé de servir la
    #: page de compte : c'est un fait de protocole, indépendant de la langue,
    #: du thème et de toute refonte d'interface.
    chemin_reserve: str = ""
    #: Cette configuration a-t-elle été confrontée à une vraie page morte ?
    verifie: bool = False


_SONDES: dict[str, Sonde] = {
    "instagram": Sonde(
        plateforme="instagram",
        # `/accounts/edit/` est une page de compte : déconnecté, Instagram
        # redirige vers `/accounts/login/?next=/accounts/edit/`. La sonde ne
        # fait qu'un GET, elle ne soumet jamais de formulaire.
        url_sonde="https://www.instagram.com/accounts/edit/",
        cookies_critiques=("sessionid",),
        cookie_identite="ds_user_id",
        motifs_url_login=("/accounts/login", "/accounts/signup"),
        motifs_url_blocage=(
            "/challenge",
            "/accounts/suspended",
            "/accounts/disabled",
            "captcha",
        ),
        motifs_connecte=(
            ('drapeau "is_logged_in": true', r'"is_logged_in"\s*:\s*true'),
            ("bloc \"logged_in_user\"", r'"logged_in_user"'),
            ('identifiant de visiteur "viewerId"', r'"viewerId"\s*:\s*"\d+"'),
        ),
        # Relevés sur la VRAIE page servie aux cookies morts le 15/08/2026 :
        #   "is_logged_out_user":true   → drapeau d'état de PolarisExperimentUtils
        #   "is_logged_in":false        → drapeau d'état des autres surfaces IG
        #   caa_feta_logged_out_homepage_root → racine GraphQL de l'accueil
        #                                       déconnecté
        # Aucun libellé d'interface (« Use another profile », « Create new
        # account ») n'est utilisé : ces chaînes changent de langue et de
        # formulation, ces trois-là sont des identifiants d'API.
        motifs_deconnecte=(
            ('drapeau "is_logged_out_user": true', r'"is_logged_out_user"\s*:\s*true'),
            ('drapeau "is_logged_in": false', r'"is_logged_in"\s*:\s*false'),
            (
                "racine GraphQL de l'accueil déconnecté",
                r"caa_feta_logged_out_homepage_root",
            ),
        ),
        chemin_reserve="/accounts/edit",
        verifie=True,  # éprouvé le 15/08/2026 sur une session réellement morte
    ),
    "tiktok": Sonde(
        plateforme="tiktok",
        url_sonde="https://www.tiktok.com/setting",
        cookies_critiques=("sessionid",),
        cookie_identite="sessionid",
        motifs_url_login=("/login",),
        motifs_url_blocage=("/captcha", "/verify", "/notfound"),
        motifs_connecte=(('drapeau "isLogin": true', r'"isLogin"\s*:\s*true'),),
        motifs_deconnecte=(('drapeau "isLogin": false', r'"isLogin"\s*:\s*false'),),
        chemin_reserve="/setting",
    ),
    "twitter": Sonde(
        plateforme="twitter",
        url_sonde="https://x.com/settings/account",
        cookies_critiques=("auth_token",),
        cookie_identite="twid",
        motifs_url_login=("/login", "/i/flow/login"),
        motifs_url_blocage=("/account/access", "/i/flow/consent", "captcha"),
        motifs_connecte=(('drapeau "isLoggedIn": true', r'"isLoggedIn"\s*:\s*true'),),
        motifs_deconnecte=(('drapeau "isLoggedIn": false', r'"isLoggedIn"\s*:\s*false'),),
        chemin_reserve="/settings",
    ),
    "reddit": Sonde(
        plateforme="reddit",
        url_sonde="https://www.reddit.com/settings/",
        cookies_critiques=("reddit_session",),
        cookie_identite="reddit_session",
        motifs_url_login=("/login",),
        motifs_url_blocage=("/over18", "blocked", "captcha"),
        motifs_connecte=(
            ('drapeau "isLoggedIn": true', r'"isLoggedIn"\s*:\s*true'),
            ('drapeau "loggedIn": true', r'"loggedIn"\s*:\s*true'),
        ),
        motifs_deconnecte=(('drapeau "isLoggedIn": false', r'"isLoggedIn"\s*:\s*false'),),
        chemin_reserve="/settings",
    ),
}

#: Un `<input name="password">` quelque part dans le document : structurel,
#: indépendant de la langue de l'interface.
_MOTIF_FORMULAIRE_LOGIN = re.compile(
    r"<input[^>]*\bname\s*=\s*[\"']?password[\"']?", re.IGNORECASE
)
#: Drapeau d'API, pas un libellé traduit.
_MOTIF_DECONNECTE_JSON = re.compile(r'"is_logged_in"\s*:\s*false', re.IGNORECASE)
#: Statuts HTTP qui signent un blocage plutôt qu'une déconnexion.
_STATUTS_BLOCAGE = (403, 429, 503)


# ---------------------------------------------------------------------------
# L'état renvoyé
# ---------------------------------------------------------------------------
@dataclass
class EtatSession:
    """Verdict complet pour une plateforme, prêt pour l'UI et pour l'API."""

    plateforme: str
    etat: str
    message: str
    geste: str | None = None
    source: str = SOURCE_AUCUNE
    #: Horodatage de l'ÉVALUATION (unix).
    verifie_le: int | None = None
    #: Dernière preuve positive connue (unix), tous signaux confondus.
    dernier_succes: int | None = None
    #: Dernière exécution de la sonde active (unix).
    derniere_sonde: int | None = None
    #: Date de modification du fichier de cookies (unix).
    cookies_le: int | None = None
    #: Expiration la plus proche parmi les cookies critiques (unix).
    expire_le: int | None = None
    #: Message d'alerte AVANT expiration (ou après). `None` quand rien à dire.
    alerte: str | None = None
    #: Les faits qui ont produit le verdict, en clair.
    indices: list[str] = field(default_factory=list)
    #: Cette plateforme a-t-elle une sonde éprouvée sur une vraie page morte ?
    sonde_verifiee: bool = False

    @property
    def urgent(self) -> bool:
        """Vrai quand l'état demande une action du propriétaire."""
        return self.etat in (ETAT_DECONNECTE, ETAT_BLOQUE)

    def en_dict(self) -> dict[str, Any]:
        return {
            "plateforme": self.plateforme,
            "etat": self.etat,
            "message": self.message,
            "geste": self.geste,
            "source": self.source,
            "verifie_le": self.verifie_le,
            "dernier_succes": self.dernier_succes,
            "derniere_sonde": self.derniere_sonde,
            "cookies_le": self.cookies_le,
            "expire_le": self.expire_le,
            "alerte": self.alerte,
            "indices": list(self.indices),
            "urgent": self.urgent,
            "sonde_verifiee": self.sonde_verifiee,
        }


# ---------------------------------------------------------------------------
# Petits utilitaires
# ---------------------------------------------------------------------------
def _maintenant() -> int:
    return int(time.time())


def _jours(secondes: float) -> int:
    """Nombre de jours, ARRONDI (et non tronqué) : « expiré depuis 67,9 j » se
    dit « 68 j », pas « 67 j »."""
    return int(round(abs(secondes) / 86400))


def _dossier_sessions() -> Path:
    """Répertoire des cookies, lu À CHAQUE APPEL.

    Import tardif volontaire : `app.config` fige `SESSIONS_DIR` à l'import, et
    un import par valeur ici rendrait le module intestable et sourd à tout
    changement de DATA_DIR.
    """
    from app import config

    return Path(config.SESSIONS_DIR)


def chemin_cookies(plateforme: str) -> Path:
    return _dossier_sessions() / f"{plateforme}.json"


def _charger_cookies(plateforme: str) -> list[dict]:
    """Cookies stockés, ou `[]` si le fichier manque / est illisible."""
    chemin = chemin_cookies(plateforme)
    if not chemin.exists():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Cookies illisibles pour {} : {}", plateforme, exc)
        return []
    if not isinstance(donnees, list):
        return []
    return [c for c in donnees if isinstance(c, dict)]


def _expiration(cookie: dict) -> float | None:
    brut = cookie.get("expirationDate", cookie.get("expires"))
    if brut in (None, "", -1, 0):
        return None
    try:
        valeur = float(brut)
    except (TypeError, ValueError):
        return None
    return valeur if valeur > 0 else None


def _cookies_playwright(bruts: Iterable[dict]) -> list[dict]:
    """Convertit le format Cookie-Editor vers celui de Playwright.

    Les cookies EXPIRÉS sont transmis tels quels, sans filtrage : la sonde doit
    voir exactement ce que voit le scraper. Playwright les acceptera puis ne
    les enverra pas — et c'est précisément la réalité qu'on veut mesurer.
    """
    convertis: list[dict] = []
    for c in bruts:
        nom = str(c.get("name", "") or "")
        if not nom:
            continue
        cookie: dict[str, Any] = {
            "name": nom,
            "value": str(c.get("value", "") or ""),
            "domain": str(c.get("domain", "") or ""),
            "path": str(c.get("path", "/") or "/"),
        }
        if not cookie["domain"]:
            continue
        if "secure" in c:
            cookie["secure"] = bool(c["secure"])
        if "httpOnly" in c:
            cookie["httpOnly"] = bool(c["httpOnly"])
        expire = _expiration(c)
        if expire is not None:
            cookie["expires"] = expire
        same_site = str(c.get("sameSite", "") or "").capitalize()
        if same_site in ("Strict", "Lax", "None"):
            cookie["sameSite"] = same_site
        convertis.append(cookie)
    return convertis


def _noms_de_cookies(cookies: Any) -> set[str]:
    """Noms non vides d'un jeu de cookies, quelle qu'en soit la forme.

    Scrapling rend tantôt un `dict {nom: valeur}`, tantôt une suite de dicts
    `{"name": ..., "value": ...}` selon le moteur : les deux sont acceptés.
    """
    noms: set[str] = set()
    if not cookies:
        return noms
    if isinstance(cookies, dict):
        return {str(k).lower() for k, v in cookies.items() if str(v or "").strip()}
    if isinstance(cookies, (list, tuple)):
        for c in cookies:
            if isinstance(c, dict):
                nom = str(c.get("name", "") or "")
                if nom and str(c.get("value", "") or "").strip():
                    noms.add(nom.lower())
    return noms


def _chemin(url: str) -> str:
    """Chemin d'une URL, sans schéma, hôte, query ni fragment.

    Écrit à la main plutôt qu'avec `urlsplit` pour rester indulgent avec ce que
    rendent les moteurs (URL relative, URL vide, URL déjà réduite au chemin).
    """
    url = (url or "").strip()
    if not url:
        return ""
    sans_schema = url.split("://", 1)[-1] if "://" in url else url
    debut = sans_schema.find("/")
    chemin = sans_schema[debut:] if debut >= 0 else "/"
    for coupure in ("?", "#"):
        chemin = chemin.split(coupure, 1)[0]
    return chemin or "/"


def _horodate(ts: int | float | None) -> str:
    if not ts:
        return "date inconnue"
    return datetime.fromtimestamp(float(ts)).strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# SIGNAL PASSIF — 1re moitié : le fichier de cookies
# ---------------------------------------------------------------------------
def etat_des_cookies(plateforme: str, maintenant: int | None = None) -> EtatSession:
    """Ce que le seul fichier de cookies permet d'affirmer, sans réseau.

    Trois verdicts possibles, et un seul est optimiste :

      * pas de fichier / fichier vide     → « inconnu »   (JAMAIS « connecté »)
      * cookie critique absent            → « déconnecté »
      * cookie critique expiré            → « déconnecté » (le navigateur ne
        l'enverra tout simplement pas : la session est morte, c'est un fait,
        pas une estimation)
      * sinon                             → aucun verdict, `None` en `etat`
        (voir `etat_passif`, qui enchaîne sur l'historique des jobs)
    """
    maintenant = maintenant or _maintenant()
    sonde = _SONDES.get(plateforme)
    critiques = sonde.cookies_critiques if sonde else ()
    chemin = chemin_cookies(plateforme)
    cookies = _charger_cookies(plateforme)

    cookies_le: int | None = None
    try:
        if chemin.exists():
            cookies_le = int(chemin.stat().st_mtime)
    except OSError:
        cookies_le = None

    base = EtatSession(
        plateforme=plateforme,
        etat=ETAT_INCONNU,
        message="",
        source=SOURCE_COOKIES,
        verifie_le=maintenant,
        cookies_le=cookies_le,
        sonde_verifiee=bool(sonde and sonde.verifie),
    )

    if not cookies:
        base.etat = ETAT_INCONNU
        base.message = (
            "Aucun cookie enregistré pour cette plateforme."
            if not chemin.exists()
            else "Le fichier de cookies est vide ou illisible."
        )
        base.geste = GESTE_IMPORTER
        base.indices = ["aucun cookie exploitable"]
        return base

    presents = {
        str(c.get("name", "")).strip().lower(): c
        for c in cookies
        if str(c.get("value", "") or "").strip()
    }

    manquants = [n for n in critiques if n.lower() not in presents]
    if manquants:
        base.etat = ETAT_DECONNECTE
        base.message = (
            f"Cookie d'authentification absent : {', '.join(manquants)}. "
            "L'export est une session anonyme."
        )
        base.geste = GESTE_REIMPORTER
        base.indices = [f"cookie critique absent : {n}" for n in manquants]
        return base

    # Expirations des cookies critiques + du cookie d'identité.
    surveilles = list(critiques)
    if sonde and sonde.cookie_identite:
        surveilles.append(sonde.cookie_identite)

    expirations: dict[str, float] = {}
    for nom in surveilles:
        cookie = presents.get(nom.lower())
        if cookie is None:
            continue
        expire = _expiration(cookie)
        if expire is not None:
            expirations[nom] = expire

    if expirations:
        base.expire_le = int(min(expirations.values()))

    expires = {n: e for n, e in expirations.items() if e <= maintenant}
    if expires:
        detail = ", ".join(
            f"{n} (expiré depuis {_jours(maintenant - e)} j)"
            for n, e in sorted(expires.items(), key=lambda kv: kv[1])
        )
        base.etat = ETAT_DECONNECTE
        base.message = f"Cookie expiré : {detail}. Le navigateur ne l'envoie plus."
        base.geste = GESTE_REIMPORTER
        base.indices = [f"cookie expiré : {n}" for n in expires]
        return base

    # Rien de rédhibitoire : on ne conclut PAS « connecté » ici — un fichier
    # bien formé ne prouve rien sur ce qu'en pense le serveur.
    proche = min(expirations.items(), key=lambda kv: kv[1]) if expirations else None
    if proche and proche[1] - maintenant <= ALERTE_EXPIRATION_S:
        base.alerte = (
            f"Le cookie {proche[0]} expire dans {_jours(proche[1] - maintenant)} j "
            f"({_horodate(proche[1])}) — réexportez la session avant."
        )
    base.etat = ETAT_INCONNU
    base.message = "Cookies présents et non expirés."
    base.indices = ["cookies critiques présents et valides sur leurs dates"]
    return base


# ---------------------------------------------------------------------------
# SIGNAL PASSIF — 2e moitié : l'historique des jobs
# ---------------------------------------------------------------------------
#: Statuts prouvant que la plateforme a RÉPONDU (même sans nouveauté).
_STATUTS_ATTEINTS = ("completed", "partial", "empty")


def _lire_jobs_recents(plateforme: str, limite: int) -> list[dict]:
    """Derniers jobs de la plateforme, du plus récent au plus ancien.

    Renvoie des dicts simples : ce module ne doit rien rendre d'attaché à une
    session SQLAlchemy refermée.
    """
    from app.db import Profile, ScrapeJob, SessionLocal

    db = SessionLocal()
    try:
        lignes = (
            db.query(ScrapeJob)
            .join(Profile, Profile.id == ScrapeJob.profile_id)
            .filter(Profile.platform == plateforme)
            .filter(ScrapeJob.status.notin_(["queued", "running"]))
            .order_by(ScrapeJob.created_at.desc(), ScrapeJob.id.desc())
            .limit(limite)
            .all()
        )
        return [
            {
                "id": j.id,
                "status": j.status or "",
                "error_message": j.error_message or "",
                "created_at": j.created_at,
                "completed_at": j.completed_at,
                "media_found": j.media_found or 0,
            }
            for j in lignes
        ]
    except Exception as exc:  # base absente, schéma incomplet…
        logger.warning("Historique des jobs illisible pour {} : {}", plateforme, exc)
        return []
    finally:
        db.close()


def etat_passif(plateforme: str, maintenant: int | None = None) -> EtatSession:
    """Verdict GRATUIT : fichier de cookies + historique des jobs. Aucun réseau.

    C'est le signal qui alimente l'affichage par défaut. Il se met à jour tout
    seul à chaque scrape, sans jamais lancer de navigateur.
    """
    maintenant = maintenant or _maintenant()
    verdict = etat_des_cookies(plateforme, maintenant)

    # Un verdict de cookies négatif est DÉTERMINISTE (cookie absent ou expiré) :
    # aucun historique ne peut le contredire, on s'arrête là.
    if verdict.etat == ETAT_DECONNECTE:
        return verdict

    jobs = _lire_jobs_recents(plateforme, FENETRE_JOBS)

    # Dernière preuve positive : un job qui a bel et bien atteint la plateforme.
    for job in jobs:
        if job["status"] in _STATUTS_ATTEINTS:
            verdict.dernier_succes = job["completed_at"] or job["created_at"]
            break

    if not jobs:
        verdict.indices.append("aucun job récent pour cette plateforme")
        verdict.geste = verdict.geste or GESTE_RESONDER
        verdict.message = (verdict.message + " Aucun scrape récent pour trancher.").strip()
        return verdict

    frais = [
        j
        for j in jobs
        if j["created_at"] and maintenant - j["created_at"] <= FRAICHEUR_JOBS_S
    ]
    if not frais:
        verdict.indices.append(
            f"dernier job il y a {_jours(maintenant - (jobs[0]['created_at'] or maintenant))} j "
            "— trop ancien pour conclure"
        )
        verdict.geste = verdict.geste or GESTE_RESONDER
        return verdict

    non_atteints = [
        j
        for j in frais
        if j["status"] == "failed" and MARQUEUR_NON_ATTEINT in (j["error_message"] or "")
    ]

    # TOUS les jobs récents ont échoué faute d'avoir atteint la plateforme :
    # c'est la signature d'une session morte (ou d'un blocage durable).
    if non_atteints and len(non_atteints) == len(frais):
        verdict.etat = ETAT_DECONNECTE
        verdict.source = SOURCE_JOBS
        verdict.message = (
            f"Les {len(frais)} derniers scrapes n'ont pas atteint la plateforme "
            f"(dernier : {_horodate(frais[0]['created_at'])})."
        )
        verdict.geste = GESTE_REIMPORTER
        verdict.indices.append(
            f"{len(non_atteints)}/{len(frais)} job(s) récent(s) « profil non atteint »"
        )
        return verdict

    atteints = [j for j in frais if j["status"] in _STATUTS_ATTEINTS]
    if atteints:
        verdict.etat = ETAT_CONNECTE
        verdict.source = SOURCE_JOBS
        verdict.message = (
            f"Dernier scrape réussi le {_horodate(atteints[0]['completed_at'] or atteints[0]['created_at'])} "
            "— la plateforme a répondu."
        )
        verdict.geste = None
        verdict.indices.append(f"{len(atteints)}/{len(frais)} job(s) récent(s) ont abouti")
        verdict.dernier_succes = (
            atteints[0]["completed_at"] or atteints[0]["created_at"]
        )
        return verdict

    # Des échecs, mais pas la signature « non atteint » : on ne tranche pas.
    verdict.etat = ETAT_INCONNU
    verdict.source = SOURCE_JOBS
    verdict.message = (
        f"{len(frais)} scrape(s) récent(s) en échec, sans signature de déconnexion."
    )
    verdict.geste = GESTE_RESONDER
    verdict.indices.append("échecs récents de cause indéterminée")
    return verdict


# ---------------------------------------------------------------------------
# SONDE ACTIVE — classement d'une réponse (fonction PURE, testable sans réseau)
# ---------------------------------------------------------------------------
def classer_reponse(
    plateforme: str,
    *,
    url_finale: str,
    statut_http: int | None,
    html: str,
    cookies: Any = None,
) -> tuple[str, str, list[str]]:
    """Classe une page déjà chargée. Aucun réseau, aucun effet de bord.

    Renvoie `(état, message, indices)`. Les indices sont les FAITS retenus, en
    clair : c'est ce qui rend le verdict discutable plutôt qu'oraculaire.

    L'ordre d'examen est un ordre de FIABILITÉ décroissante (cf. l'en-tête du
    module) : URL finale, puis drapeau JSON, puis structure du document, et le
    cookie d'identité seulement en corroboration.
    """
    sonde = _SONDES.get(plateforme)
    if sonde is None:
        return ETAT_INCONNU, f"Plateforme inconnue : {plateforme}", []

    url = (url_finale or "").lower()
    html = html or ""
    indices: list[str] = []

    if not url and not html:
        return (
            ETAT_INCONNU,
            "La sonde n'a rien reçu (page vide).",
            ["réponse vide"],
        )

    # 1. Blocage déclaré par l'URL ou par le statut HTTP.
    for motif in sonde.motifs_url_blocage:
        if motif in url:
            indices.append(f"URL finale contient « {motif} »")
            return (
                ETAT_BLOQUE,
                f"La plateforme a renvoyé une page de défi ou de restriction ({motif}).",
                indices,
            )
    if statut_http in _STATUTS_BLOCAGE:
        indices.append(f"statut HTTP {statut_http}")
        return (
            ETAT_BLOQUE,
            f"La plateforme répond {statut_http} : accès restreint ou débit limité.",
            indices,
        )

    # 2. Déconnexion — indice n°1 : l'URL finale.
    for motif in sonde.motifs_url_login:
        if motif in url:
            indices.append(f"URL finale contient « {motif} »")
            return (
                ETAT_DECONNECTE,
                "La plateforme redirige vers sa page de connexion : la session "
                "n'est plus valide côté serveur.",
                indices,
            )

    # 3. Déconnexion — indice n°2 : un drapeau d'état dans le JSON embarqué.
    drapeaux = [
        libelle
        for libelle, motif in sonde.motifs_deconnecte
        if re.search(motif, html, re.IGNORECASE)
    ]
    # Filet générique, toutes plateformes : le drapeau `is_logged_in: false`
    # d'Instagram est aussi celui de plusieurs surfaces Meta.
    generique = 'drapeau "is_logged_in": false'
    if _MOTIF_DECONNECTE_JSON.search(html) and generique not in drapeaux:
        drapeaux.append(generique)
    indices.extend(f"marqueur de déconnexion : {libelle}" for libelle in drapeaux)

    # 4. Déconnexion — indice n°3 : un formulaire de connexion dans le document.
    #    (Sur Instagram la page est rendue par React : ce formulaire n'existe
    #     souvent PAS dans le HTML initial. L'indice est donc utile mais jamais
    #     nécessaire — d'où les trois autres.)
    formulaire = bool(_MOTIF_FORMULAIRE_LOGIN.search(html))
    if formulaire:
        indices.append("formulaire de connexion présent (<input name=password>)")

    # 5. Corroboration FAIBLE : le cookie d'identité n'est plus posé.
    noms = _noms_de_cookies(cookies)
    identite_absente = bool(
        sonde.cookie_identite and noms and sonde.cookie_identite.lower() not in noms
    )
    if identite_absente:
        indices.append(
            f"cookie d'identité {sonde.cookie_identite} absent de la réponse (indice faible)"
        )

    if drapeaux or formulaire:
        return (
            ETAT_DECONNECTE,
            "La page servie est un mur de connexion : la session stockée n'est "
            "plus authentifiée.",
            indices,
        )

    # 6. Preuve positive d'authentification.
    for libelle, motif in sonde.motifs_connecte:
        if re.search(motif, html, re.IGNORECASE):
            indices.append(f"marqueur d'authentification : {libelle}")
            return (
                ETAT_CONNECTE,
                "La plateforme répond avec du contenu authentifié.",
                indices,
            )

    # 7. Déconnexion — indice de PROTOCOLE : la sonde visait une page réservée
    #    aux comptes connectés, et la réponse finale n'est plus sur ce chemin.
    #    Mesuré le 15/08/2026 : GET /accounts/edit/ → 302 → https://www.instagram.com/
    #    C'est la plateforme elle-même qui refuse de servir la page de compte.
    #    Placé APRÈS la preuve positive : une page authentifiée qui redirigerait
    #    ailleurs (locale, onglet par défaut) reste classée « connectée ».
    if sonde.chemin_reserve and not _chemin(url).startswith(sonde.chemin_reserve):
        indices.append(
            f"page réservée quittée : {sonde.chemin_reserve} → {_chemin(url) or '/'}"
        )
        return (
            ETAT_DECONNECTE,
            "La plateforme a refusé de servir une page de compte et a redirigé "
            "ailleurs : la session stockée n'est plus authentifiée.",
            indices,
        )

    if sonde.cookie_identite and sonde.cookie_identite.lower() in noms:
        indices.append(f"cookie d'identité {sonde.cookie_identite} reposé par le serveur")
        return (
            ETAT_CONNECTE,
            "La plateforme a reposé le cookie d'identité : la session est active.",
            indices,
        )

    # 8. Rien de concluant. On le DIT — on ne suppose pas « connecté ».
    indices.append("aucun marqueur d'authentification ni de déconnexion")
    return (
        ETAT_INCONNU,
        "La page a répondu mais ne permet pas de conclure sur la session.",
        indices,
    )


# ---------------------------------------------------------------------------
# SONDE ACTIVE — exécution
# ---------------------------------------------------------------------------
#: Une seule sonde à la fois, et un verrou DÉDIÉ : la sonde ne partage NI le
#: sémaphore de scrape (`scheduler._scrape_semaphore`) NI aucun slot de job.
#: Un contrôle de session ne doit jamais retarder un scrape.
_VERROU_SONDE = threading.BoundedSemaphore(1)


class SondeOccupee(RuntimeError):
    """Levée quand une sonde tourne déjà."""


def _executer_avec_delai(fonction: Callable[[], Any], delai_s: float):
    """Exécute `fonction` dans un thread et abandonne au bout de `delai_s`.

    Le délai est un MUR D'HORLOGE : `StealthyFetcher` a ses propres délais
    internes, mais un navigateur bloqué avant eux (téléchargement de Camoufox,
    proxy qui pend) ferait attendre l'appelant indéfiniment. Le thread est
    `daemon` : il ne retient jamais l'arrêt du process.

    Renvoie `(resultat, exception, expire)`.
    """
    boite: dict[str, Any] = {}

    def _cible():
        try:
            boite["resultat"] = fonction()
        except BaseException as exc:  # noqa: BLE001 — on rapporte, on ne masque pas
            boite["exception"] = exc

    fil = threading.Thread(target=_cible, daemon=True, name="sonde-session")
    fil.start()
    fil.join(delai_s)
    if fil.is_alive():
        return None, None, True
    return boite.get("resultat"), boite.get("exception"), False


def _fetch_reel(url: str, cookies: Sequence[dict], delai_s: float, proxy: str | None):
    """Charge `url` dans le navigateur furtif. Import tardif de scrapling."""
    from scrapling.fetchers import StealthyFetcher

    kwargs: dict[str, Any] = {
        "headless": True,
        "network_idle": False,
        # `timeout` de scrapling est en millisecondes.
        "timeout": int(delai_s * 1000),
    }
    if cookies:
        kwargs["cookies"] = list(cookies)
    if proxy:
        kwargs["proxy"] = proxy
        kwargs["geoip"] = True
    return StealthyFetcher.fetch(url, **kwargs)


def sonder(
    plateforme: str,
    *,
    delai_s: float = DELAI_SONDE_S,
    marge_s: float = 15.0,
    proxy: str | None = None,
    fetcher: Callable[..., Any] | None = None,
    enregistrer: bool = True,
) -> EtatSession:
    """SONDE ACTIVE : lance un navigateur, charge une page, classe la réponse.

    Coûteuse (dizaines de secondes) — à n'appeler que depuis le job planifié ou
    le bouton « Vérifier maintenant ». Ne lève JAMAIS : tout échec devient
    « inconnu », jamais « connecté ».

    `delai_s` est le délai passé au navigateur ; `marge_s` est le sursis
    accordé au-dessus avant que le mur d'horloge ne coupe — il laisse au
    navigateur la chance d'abandonner proprement de lui-même.

    `fetcher` permet d'injecter un faux navigateur dans les tests ; laissé à
    `None`, c'est `StealthyFetcher.fetch` qui est utilisé.
    """
    maintenant = _maintenant()
    sonde = _SONDES.get(plateforme)
    if sonde is None:
        return EtatSession(
            plateforme=plateforme,
            etat=ETAT_INCONNU,
            message=f"Plateforme inconnue : {plateforme}",
            source=SOURCE_SONDE,
            verifie_le=maintenant,
        )

    depart = etat_des_cookies(plateforme, maintenant)
    depart.source = SOURCE_SONDE
    depart.derniere_sonde = maintenant

    cookies_bruts = _charger_cookies(plateforme)
    if not cookies_bruts:
        # Rien à tester : inutile de payer un navigateur pour le découvrir.
        depart.etat = ETAT_INCONNU
        depart.geste = GESTE_IMPORTER
        if enregistrer:
            enregistrer_etat(depart)
        return depart

    if proxy is None:
        try:
            from app.config import get_proxy_for_platform

            proxy = get_proxy_for_platform(plateforme) or None
        except Exception:  # pragma: no cover - configuration exotique
            proxy = None

    cookies_pw = _cookies_playwright(cookies_bruts)
    appel = fetcher or _fetch_reel

    logger.info("Sonde de session {} → {}", plateforme, sonde.url_sonde)
    debut = time.monotonic()
    reponse, erreur, expire = _executer_avec_delai(
        lambda: appel(sonde.url_sonde, cookies_pw, delai_s, proxy), delai_s + marge_s
    )
    duree = time.monotonic() - debut

    resultat = EtatSession(
        plateforme=plateforme,
        etat=ETAT_INCONNU,
        message="",
        source=SOURCE_SONDE,
        verifie_le=maintenant,
        derniere_sonde=maintenant,
        cookies_le=depart.cookies_le,
        expire_le=depart.expire_le,
        alerte=depart.alerte,
        sonde_verifiee=sonde.verifie,
    )

    if expire:
        resultat.message = (
            f"La sonde a dépassé son délai de {int(delai_s)} s sans conclure."
        )
        resultat.geste = GESTE_RESONDER
        resultat.indices = ["délai de sonde dépassé"]
        logger.warning("Sonde {} : délai dépassé ({:.0f}s)", plateforme, duree)
        if enregistrer:
            enregistrer_etat(resultat)
        return resultat

    if erreur is not None or reponse is None:
        resultat.message = (
            "La sonde n'a pas pu s'exécuter (navigateur indisponible, réseau ou "
            "proxy en échec)."
        )
        resultat.geste = GESTE_RESONDER
        resultat.indices = [f"échec de la sonde : {type(erreur).__name__ if erreur else 'aucune réponse'}"]
        logger.warning("Sonde {} en échec : {}", plateforme, erreur)
        if enregistrer:
            enregistrer_etat(resultat)
        return resultat

    url_finale = str(getattr(reponse, "url", "") or "")
    statut = getattr(reponse, "status", None)
    html = getattr(reponse, "html_content", None) or getattr(reponse, "body", "") or ""
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    cookies_reponse = getattr(reponse, "cookies", None)

    etat, message, indices = classer_reponse(
        plateforme,
        url_finale=url_finale,
        statut_http=statut if isinstance(statut, int) else None,
        html=html,
        cookies=cookies_reponse,
    )

    resultat.etat = etat
    resultat.message = message
    resultat.indices = indices + [
        f"URL finale : {url_finale[:200]}" if url_finale else "URL finale inconnue",
        f"statut HTTP : {statut}" if statut is not None else "statut HTTP inconnu",
        f"sonde exécutée en {duree:.0f} s",
    ]
    resultat.geste = {
        ETAT_DECONNECTE: GESTE_REIMPORTER,
        ETAT_BLOQUE: GESTE_ATTENDRE,
        ETAT_INCONNU: GESTE_RESONDER,
        ETAT_CONNECTE: None,
    }[etat]
    if etat == ETAT_CONNECTE:
        resultat.dernier_succes = maintenant

    logger.info(
        "Sonde {} → {} ({:.0f}s) — {}", plateforme, etat, duree, "; ".join(indices)
    )
    if enregistrer:
        enregistrer_etat(resultat)
    return resultat


def sonder_si_libre(plateforme: str, **kwargs) -> EtatSession:
    """`sonder()` protégée par un verrou dédié : une sonde à la fois.

    Lève `SondeOccupee` sans attendre quand une sonde tourne déjà — un appel
    HTTP ne doit jamais pendre derrière un navigateur.
    """
    if not _VERROU_SONDE.acquire(blocking=False):
        raise SondeOccupee("Une sonde est déjà en cours")
    try:
        return sonder(plateforme, **kwargs)
    finally:
        _VERROU_SONDE.release()


def sonde_en_cours() -> bool:
    """Vrai quand une sonde occupe le verrou (lecture non bloquante)."""
    if _VERROU_SONDE.acquire(blocking=False):
        _VERROU_SONDE.release()
        return False
    return True


# ---------------------------------------------------------------------------
# PERSISTANCE
# ---------------------------------------------------------------------------
def enregistrer_etat(etat: EtatSession) -> None:
    """Écrit le dernier verdict connu dans `session_health`.

    Toujours « au mieux » : une base absente ou un schéma incomplet ne doit
    jamais faire échouer une sonde. La table est NULLABLE de bout en bout et
    l'application fonctionne quand elle est vide.
    """
    from app.db import SessionHealth, SessionLocal

    db = SessionLocal()
    try:
        ligne = (
            db.query(SessionHealth)
            .filter(SessionHealth.platform == etat.plateforme)
            .one_or_none()
        )
        if ligne is None:
            ligne = SessionHealth(platform=etat.plateforme)
            db.add(ligne)
        ligne.state = etat.etat
        ligne.message = etat.message
        ligne.remedy = etat.geste
        ligne.source = etat.source
        ligne.details = json.dumps(etat.indices, ensure_ascii=False)
        ligne.checked_at = etat.verifie_le
        ligne.cookies_mtime = etat.cookies_le
        ligne.expires_at = etat.expire_le
        if etat.source == SOURCE_SONDE:
            ligne.last_probe_at = etat.derniere_sonde or etat.verifie_le
        if etat.etat == ETAT_CONNECTE:
            ligne.last_ok_at = etat.dernier_succes or etat.verifie_le
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Etat de session non enregistré pour {} : {}", etat.plateforme, exc)
    finally:
        db.close()


def lire_etat_enregistre(plateforme: str) -> dict[str, Any] | None:
    """Dernier verdict stocké, ou `None` quand la table est vide."""
    from app.db import SessionHealth, SessionLocal

    db = SessionLocal()
    try:
        ligne = (
            db.query(SessionHealth)
            .filter(SessionHealth.platform == plateforme)
            .one_or_none()
        )
        if ligne is None:
            return None
        try:
            indices = json.loads(ligne.details) if ligne.details else []
        except (TypeError, ValueError):
            indices = []
        return {
            "etat": ligne.state,
            "message": ligne.message,
            "geste": ligne.remedy,
            "source": ligne.source,
            "indices": indices if isinstance(indices, list) else [],
            "verifie_le": ligne.checked_at,
            "derniere_sonde": ligne.last_probe_at,
            "dernier_succes": ligne.last_ok_at,
            "cookies_le": ligne.cookies_mtime,
            "expire_le": ligne.expires_at,
        }
    except Exception as exc:
        logger.warning("Etat de session illisible pour {} : {}", plateforme, exc)
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# LECTURE COMBINÉE — ce que l'UI affiche
# ---------------------------------------------------------------------------
def etat_courant(plateforme: str, maintenant: int | None = None) -> EtatSession:
    """Fusionne le signal passif et le dernier verdict de sonde.

    Arbitrage, dans cet ordre :

      1. Un verdict passif « déconnecté » issu du FICHIER de cookies (absent ou
         expiré) l'emporte toujours : c'est déterministe, aucune sonde même
         fraîche ne peut le contredire.
      2. Sinon, une sonde FRAÎCHE (< 6 h 30) et concluante l'emporte : elle a vu
         la vraie page.
      3. Sinon, le signal passif.

    Le verdict retenu porte toujours l'horodatage de la preuve utilisée, et les
    deux signaux restent lisibles via `etats_detailles`.
    """
    maintenant = maintenant or _maintenant()
    passif = etat_passif(plateforme, maintenant)
    stocke = lire_etat_enregistre(plateforme)

    if stocke:
        passif.derniere_sonde = stocke.get("derniere_sonde")
        if passif.dernier_succes is None:
            passif.dernier_succes = stocke.get("dernier_succes")

    if passif.etat == ETAT_DECONNECTE and passif.source == SOURCE_COOKIES:
        return passif

    if not stocke or not stocke.get("etat"):
        return passif

    sonde_le = stocke.get("derniere_sonde") or stocke.get("verifie_le")
    fraiche = bool(sonde_le and maintenant - sonde_le <= FRAICHEUR_SONDE_S)
    if not fraiche or stocke["etat"] == ETAT_INCONNU:
        return passif

    # À ce stade le passif ne peut plus être un « déconnecté » de FICHIER (cas 1
    # traité plus haut). S'il vient des JOBS, la sonde fraîche l'emporte : elle
    # a vu la vraie page, l'historique n'en est qu'une déduction.
    resultat = EtatSession(
        plateforme=plateforme,
        etat=stocke["etat"],
        message=stocke.get("message") or "",
        geste=stocke.get("geste"),
        source=SOURCE_SONDE,
        verifie_le=sonde_le,
        dernier_succes=stocke.get("dernier_succes") or passif.dernier_succes,
        derniere_sonde=sonde_le,
        cookies_le=passif.cookies_le,
        expire_le=passif.expire_le,
        alerte=passif.alerte,
        indices=list(stocke.get("indices") or []),
        sonde_verifiee=passif.sonde_verifiee,
    )
    return resultat


def etats_detailles(plateforme: str, maintenant: int | None = None) -> dict[str, Any]:
    """Le verdict retenu ET les deux signaux qui l'ont produit, séparément."""
    maintenant = maintenant or _maintenant()
    retenu = etat_courant(plateforme, maintenant)
    passif = etat_passif(plateforme, maintenant)
    charge = retenu.en_dict()
    charge["signaux"] = {
        "passif": passif.en_dict(),
        "sonde": lire_etat_enregistre(plateforme),
    }
    return charge


def etats_de_toutes_les_plateformes(
    plateformes: Sequence[str] | None = None,
    maintenant: int | None = None,
) -> list[dict[str, Any]]:
    """Verdicts de toutes les plateformes, les plus urgents d'abord."""
    maintenant = maintenant or _maintenant()
    noms = tuple(plateformes) if plateformes else PLATEFORMES
    etats = [etat_courant(p, maintenant) for p in noms]
    etats.sort(key=lambda e: (GRAVITE.get(e.etat, 9), e.plateforme))
    return [e.en_dict() for e in etats]


def plateformes_sondables() -> list[str]:
    """Plateformes qui ont un fichier de cookies : les seules à sonder."""
    return [p for p in PLATEFORMES if chemin_cookies(p).exists()]
