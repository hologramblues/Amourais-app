"""Viewer API endpoints for media browsing, comments, ratings, and saved memes."""

from __future__ import annotations

import base64
import os
import struct
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from loguru import logger
from sqlalchemy import case, func, or_

from app.config import DOWNLOAD_DIR, EDITOR_OUTPUT_DIR
from app.db import (
    Collection,
    CollectionItem,
    MediaComment,
    MediaItem,
    MediaRating,
    Profile,
    SavedMeme,
    ScheduledPost,
    SessionLocal,
)
from app.scraper.pipeline import PHASH_DEGENERE, empreintes

viewer_api_bp = Blueprint("viewer_api", __name__)

# Bornes de pagination (risque #63, AUDIT.md §9/2.4).
# `int(request.args.get(...))` sur une entrée libre a trois défauts :
#   - `?page=abc` lève ValueError et part en 500 ;
#   - `?per_page=0` provoque une division par zéro au calcul de total_pages ;
#   - `?per_page=999999` charge toute la médiathèque en mémoire d'un coup.
# On borne donc, sans jamais refuser la requête : une pagination absurde
# retombe silencieusement sur la valeur par défaut.
_PER_PAGE_DEFAUT = 60
_PER_PAGE_MAX = 200


def _pagination_args():
    """(page, per_page) lus dans la query string, toujours dans les bornes."""
    def _entier(nom: str, defaut: int) -> int:
        try:
            return int(request.args.get(nom, defaut))
        except (TypeError, ValueError):
            return defaut

    page = max(1, _entier("page", 1))
    per_page = min(_PER_PAGE_MAX, max(1, _entier("per_page", _PER_PAGE_DEFAUT)))
    return page, per_page


# ---------------------------------------------------------------------------
# Dimensions : sans elles, aucune grille à ratios préservés
# ---------------------------------------------------------------------------
# Critères V3 et V5 (REFERENCES.md §4.1) : la vignette doit garder le ratio
# d'origine ET son emplacement doit être dimensionné AVANT que l'image
# n'arrive. Les deux exigent width/height CÔTÉ SERVEUR, or la colonne est
# nulle pour tout média téléchargé sans métadonnées (12 des 16 items de la
# base réelle). On lit donc l'en-tête du fichier — quelques octets, aucune
# décompression, aucune dépendance : Pillow n'est pas installé ici.
_BACKFILL_MAX = 400  # bornage : jamais plus de N fichiers sondés par requête
# `width IS NULL` n'est porté par aucun index : une fois la colonne remplie,
# relancer la requête à CHAQUE page coûterait un balayage complet de la table
# pour zéro ligne. On ne resonde donc qu'au plus toutes les 60 secondes.
_BACKFILL_REPOS_S = 60
_backfill_a_jour_jusqua = 0.0


def _dimensions_jpeg(f) -> tuple[int, int] | None:
    """Parcourt les segments JPEG jusqu'au SOFn, qui porte hauteur et largeur."""
    if f.read(2) != b"\xff\xd8":
        return None
    while True:
        octet = f.read(1)
        while octet == b"\xff":  # les octets de bourrage se répètent
            octet = f.read(1)
        if not octet:
            return None
        marqueur = octet[0]
        entete = f.read(2)
        if len(entete) < 2:
            return None
        taille = struct.unpack(">H", entete)[0]
        # SOF0..SOF15, hors marqueurs de table (C4, C8, CC)
        if 0xC0 <= marqueur <= 0xCF and marqueur not in (0xC4, 0xC8, 0xCC):
            corps = f.read(5)
            if len(corps) < 5:
                return None
            hauteur, largeur = struct.unpack(">HH", corps[1:5])
            return largeur, hauteur
        f.seek(taille - 2, 1)
        if f.read(1) != b"\xff":
            return None
        f.seek(-1, 1)


def _dimensions_fichier(chemin: Path) -> tuple[int, int] | None:
    """(largeur, hauteur) lues dans l'en-tête, ou None si le format est inconnu."""
    try:
        with open(chemin, "rb") as f:
            tete = f.read(32)
            if len(tete) < 16:
                return None
            if tete[:8] == b"\x89PNG\r\n\x1a\n":
                largeur, hauteur = struct.unpack(">II", tete[16:24])
                return largeur, hauteur
            if tete[:6] in (b"GIF87a", b"GIF89a"):
                largeur, hauteur = struct.unpack("<HH", tete[6:10])
                return largeur, hauteur
            if tete[:4] == b"RIFF" and tete[8:12] == b"WEBP":
                if tete[12:16] == b"VP8X":
                    f.seek(24)
                    bloc = f.read(6)
                    largeur = int.from_bytes(bloc[0:3], "little") + 1
                    hauteur = int.from_bytes(bloc[3:6], "little") + 1
                    return largeur, hauteur
                if tete[12:16] == b"VP8 ":
                    f.seek(26)
                    bloc = f.read(4)
                    return (
                        struct.unpack("<H", bloc[0:2])[0] & 0x3FFF,
                        struct.unpack("<H", bloc[2:4])[0] & 0x3FFF,
                    )
                return None
            if tete[:2] == b"\xff\xd8":
                f.seek(0)
                return _dimensions_jpeg(f)
    except (OSError, struct.error, IndexError):
        return None
    return None


def _completer_les_dimensions(db) -> int:
    """Renseigne width/height des médias locaux qui n'en ont pas encore.

    Travail borné (`_BACKFILL_MAX`), idempotent, et convergent : une fois la
    colonne remplie la requête ne ramène plus rien. Pour une vidéo, l'en-tête
    du conteneur n'est pas lisible sans décodeur — on retombe alors sur la
    vignette JPEG déjà générée sur disque, qui porte le bon ratio.
    """
    global _backfill_a_jour_jusqua
    if time.monotonic() < _backfill_a_jour_jusqua:
        return 0

    manquants = (
        db.query(MediaItem)
        .filter(MediaItem.width.is_(None), MediaItem.local_path.isnot(None))
        .limit(_BACKFILL_MAX)
        .all()
    )
    if not manquants:
        _backfill_a_jour_jusqua = time.monotonic() + _BACKFILL_REPOS_S
        return 0

    dossier_vignettes = DOWNLOAD_DIR / ".thumbs"
    complet = 0
    for item in manquants:
        nom = Path(item.local_path).name
        taille = _dimensions_fichier(DOWNLOAD_DIR / nom)
        if taille is None:
            taille = _dimensions_fichier(dossier_vignettes / f"{Path(nom).stem}.jpg")
        if taille is None:
            continue
        item.width, item.height = taille
        complet += 1

    if complet:
        db.commit()
        logger.info("Dimensions complétées pour {} médias", complet)
    else:
        # Aucun de ces fichiers n'est lisible (format exotique, fichier
        # disparu) : inutile de les resonder à chaque page.
        _backfill_a_jour_jusqua = time.monotonic() + _BACKFILL_REPOS_S
    return complet


# ---------------------------------------------------------------------------
# Filtres — TOUS appliqués en SQL, jamais après chargement
# ---------------------------------------------------------------------------

_TRIS = {
    "date_desc", "date_asc", "rating_desc", "rating_asc", "size_desc", "size_asc",
}

# Les dimensions de filtrage, dans l'ordre où elles apparaissent en jetons.
_DIMENSIONS = (
    "q", "platform", "profile_id", "type", "orientation",
    "rating", "source", "used", "caption", "collection", "from", "to",
)


def _ids_quicklink(db):
    """Sous-requête : les profils synthétiques du Quick Download."""
    return db.query(Profile.id).filter(Profile.username.like("__quick_download_%"))


def _entier_ou_none(valeur: str):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def _horodatage(valeur: str):
    """`YYYY-MM-DD` -> timestamp unix, ou None si la date est illisible."""
    try:
        return int(datetime.strptime(valeur.strip(), "%Y-%m-%d").timestamp())
    except (TypeError, ValueError, AttributeError):
        return None


def _lire_filtres() -> dict:
    """Les filtres de la query string, normalisés. Aucun accès base ici."""
    args = request.args
    return {
        "q": args.get("q", args.get("search", "")).strip(),
        "platform": args.get("platform", "").strip(),
        "profile_id": _entier_ou_none(args.get("profile_id", "")),
        "type": args.get("type", "").strip(),
        "orientation": args.get("orientation", "").strip(),
        "rating": _entier_ou_none(args.get("rating", args.get("min_rating", ""))),
        "source": args.get("source", "").strip(),
        "used": args.get("used", "").strip(),
        "caption": args.get("caption", "").strip(),
        "collection": _entier_ou_none(args.get("collection", "")),
        "from": _horodatage(args.get("from", "")),
        "to": _horodatage(args.get("to", "")),
    }


# `posted_at` est nul pour les médias sans date de publication connue ; la
# date de découverte est alors la seule vérité disponible. On trie et on
# filtre sur cette date effective, pas sur une colonne à moitié vide.
_DATE_EFFECTIVE = func.coalesce(MediaItem.posted_at, MediaItem.discovered_at)


def _appliquer_filtres(db, query, filtres: dict, sauf: str = ""):
    """Applique les filtres actifs à la requête, en SQL.

    `sauf` retire UNE dimension : c'est ce qui permet de compter les facettes
    d'un axe sous les autres filtres actifs (critère V13).
    """
    def actif(nom):
        return nom != sauf and filtres.get(nom) not in (None, "")

    if actif("platform"):
        query = query.filter(MediaItem.platform == filtres["platform"])
    if actif("profile_id"):
        query = query.filter(MediaItem.profile_id == filtres["profile_id"])
    if actif("type"):
        query = query.filter(MediaItem.media_type == filtres["type"])

    if actif("orientation"):
        # Ratio calculé en SQL : aucun média n'est chargé pour être trié après.
        sens = filtres["orientation"]
        query = query.filter(MediaItem.width.isnot(None), MediaItem.height.isnot(None))
        if sens == "portrait":
            query = query.filter(MediaItem.height > MediaItem.width)
        elif sens == "paysage":
            query = query.filter(MediaItem.width > MediaItem.height)
        elif sens == "carre":
            query = query.filter(MediaItem.width == MediaItem.height)

    if actif("source"):
        quicklink = _ids_quicklink(db).subquery()
        cible = db.query(quicklink.c.id)
        if filtres["source"] == "quicklink":
            query = query.filter(MediaItem.profile_id.in_(cible))
        elif filtres["source"] == "profiles":
            query = query.filter(~MediaItem.profile_id.in_(cible))

    if actif("q"):
        motif = f"%{filtres['q']}%"
        query = query.filter(
            or_(MediaItem.caption.ilike(motif), MediaItem.post_id.ilike(motif))
        )

    if actif("caption"):
        présent = MediaItem.caption.isnot(None) & (MediaItem.caption != "")
        query = query.filter(présent if filtres["caption"] == "oui" else ~présent)

    if actif("used"):
        utilisés = db.query(ScheduledPost.source_media_id).filter(
            ScheduledPost.source_media_id.isnot(None)
        )
        if filtres["used"] == "oui":
            query = query.filter(MediaItem.id.in_(utilisés))
        else:
            query = query.filter(~MediaItem.id.in_(utilisés))

    if actif("collection"):
        # V20 : le filtre porte sur la table d'association, jamais sur une
        # colonne du média — un média appartient à plusieurs collections.
        dedans = db.query(CollectionItem.media_item_id).filter(
            CollectionItem.collection_id == filtres["collection"]
        )
        query = query.filter(MediaItem.id.in_(dedans))

    if actif("from"):
        query = query.filter(_DATE_EFFECTIVE >= filtres["from"])
    if actif("to"):
        # Borne haute inclusive : la journée entière, pas son premier instant.
        query = query.filter(_DATE_EFFECTIVE < filtres["to"] + 86400)

    if actif("rating"):
        notés = (
            db.query(MediaRating.media_item_id)
            .group_by(MediaRating.media_item_id)
            .having(func.avg(MediaRating.rating) >= filtres["rating"])
            .subquery()
        )
        query = query.filter(MediaItem.id.in_(db.query(notés.c.media_item_id)))

    return query


def _base_query(db):
    return db.query(MediaItem).filter(MediaItem.status.in_(("uploaded", "downloaded")))


# ---------------------------------------------------------------------------
# Media listing
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media")
def list_media():
    """List media items with pagination, filters, and average ratings.

    Tous les filtres sont appliqués en SQL : la pagination porte donc sur
    l'ensemble filtré, jamais sur une page déjà chargée puis élaguée en
    Python — la seule forme qui reste correcte ET rapide sur une grosse
    bibliothèque.
    """
    db = SessionLocal()
    try:
        page, per_page = _pagination_args()
        sort = request.args.get("sort", "date_desc")
        if sort not in _TRIS:
            sort = "date_desc"

        # Ratios d'origine (V3) et emplacements pré-dimensionnés (V5) :
        # ils supposent width/height connus. On les complète ici, une fois.
        _completer_les_dimensions(db)

        filtres = _lire_filtres()
        query = _appliquer_filtres(db, _base_query(db), filtres)

        # Sorting
        if sort == "date_asc":
            query = query.order_by(_DATE_EFFECTIVE.asc(), MediaItem.id.asc())
        elif sort == "size_desc":
            query = query.order_by(MediaItem.file_size.desc().nullslast(), MediaItem.id.desc())
        elif sort == "size_asc":
            query = query.order_by(MediaItem.file_size.asc().nullslast(), MediaItem.id.asc())
        elif sort in ("rating_desc", "rating_asc"):
            # Sort by average rating — join with subquery
            avg_sub = (
                db.query(
                    MediaRating.media_item_id,
                    func.avg(MediaRating.rating).label("avg_rating"),
                )
                .group_by(MediaRating.media_item_id)
                .subquery()
            )
            colonne = (
                avg_sub.c.avg_rating.desc().nullslast()
                if sort == "rating_desc"
                else avg_sub.c.avg_rating.asc().nullsfirst()
            )
            query = query.outerjoin(
                avg_sub, MediaItem.id == avg_sub.c.media_item_id
            ).order_by(colonne, _DATE_EFFECTIVE.desc())
        else:  # date_desc (default)
            query = query.order_by(_DATE_EFFECTIVE.desc(), MediaItem.id.desc())

        total = query.count()
        items = query.offset((page - 1) * per_page).limit(per_page).all()

        # Batch-load average ratings for returned items
        item_ids = [i.id for i in items]
        avg_ratings = {}
        if item_ids:
            rows = (
                db.query(
                    MediaRating.media_item_id,
                    func.avg(MediaRating.rating).label("avg"),
                    func.count(MediaRating.id).label("cnt"),
                )
                .filter(MediaRating.media_item_id.in_(item_ids))
                .group_by(MediaRating.media_item_id)
                .all()
            )
            for row in rows:
                avg_ratings[row.media_item_id] = {
                    "avg": round(float(row.avg), 1),
                    "count": row.cnt,
                }

        # Batch-load comment counts
        comment_counts = {}
        if item_ids:
            rows = (
                db.query(
                    MediaComment.media_item_id,
                    func.count(MediaComment.id).label("cnt"),
                )
                .filter(MediaComment.media_item_id.in_(item_ids))
                .group_by(MediaComment.media_item_id)
                .all()
            )
            for row in rows:
                comment_counts[row.media_item_id] = row.cnt

        # Usage : déjà programmé / publié, ou jamais utilisé (V19).
        utilisés = set()
        if item_ids:
            utilisés = {
                row[0]
                for row in db.query(ScheduledPost.source_media_id)
                .filter(ScheduledPost.source_media_id.in_(item_ids))
                .distinct()
                .all()
            }

        # Profils : un seul aller-retour, pour nommer la source sur la fiche.
        noms_profils = {
            p.id: p.username
            for p in db.query(Profile.id, Profile.username).all()
        }

        result = []
        for item in items:
            # Build media file URL
            file_url = None
            if item.local_path:
                # Extract just the filename from the local path
                filename = item.local_path.split("/")[-1] if "/" in item.local_path else item.local_path
                file_url = f"/media/file/{filename}"

            # Build thumbnail URL for grid cards (server-side ffmpeg, all media types)
            thumb_url = None
            if file_url:
                thumb_url = file_url.replace("/media/file/", "/media/thumb/")

            rating_data = avg_ratings.get(item.id, {"avg": 0, "count": 0})
            result.append({
                "id": item.id,
                "post_id": item.post_id,
                "post_url": item.post_url,
                "media_type": item.media_type,
                "media_url": item.media_url,
                "file_url": file_url,
                "thumb_url": thumb_url,
                "caption": item.caption,
                # Phrase du futur meme (Tri rapide). Exposee DANS LA LISTE et
                # pas seulement dans la fiche : le Tri rapide empile des
                # dizaines de cartes d'un coup, il ne peut pas aller chercher
                # chaque phrase par une requete de detail.
                "phrase": item.phrase,
                "platform": item.platform,
                "profile_id": item.profile_id,
                "posted_at": item.posted_at,
                "discovered_at": item.discovered_at,
                "file_size": item.file_size,
                "profile_username": noms_profils.get(item.profile_id),
                "used": item.id in utilisés,
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
                "avg_rating": rating_data["avg"],
                "rating_count": rating_data["count"],
                "comment_count": comment_counts.get(item.id, 0),
            })

        return jsonify({
            "items": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })
    except Exception as exc:
        logger.error("Error listing media: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Facettes — critère V13
# ---------------------------------------------------------------------------
# « Le panneau de filtres n'affiche que les valeurs ramenant >= 1 résultat,
# chacune avec son compte, et ces comptes se recalculent après application
# d'un premier filtre. » Chaque axe est donc compté sous TOUS les autres
# filtres actifs, mais pas sous le sien : c'est ce qui permet de changer de
# plateforme sans que la liste des plateformes ne se réduise à une seule.

def _compter(db, filtres, axe, colonne):
    """[(valeur, compte)] pour un axe, trié par compte décroissant."""
    query = _appliquer_filtres(db, _base_query(db), filtres, sauf=axe)
    rows = (
        query.with_entities(colonne.label("v"), func.count(MediaItem.id).label("n"))
        .group_by(colonne)
        .order_by(func.count(MediaItem.id).desc())
        .all()
    )
    return [{"valeur": r.v, "compte": r.n} for r in rows if r.v is not None]


@viewer_api_bp.route("/viewer/facets")
def list_facets():
    """Comptes par axe de filtrage, recalculés sous les filtres actifs."""
    db = SessionLocal()
    try:
        filtres = _lire_filtres()

        # Orientation : trois seaux calculés en SQL par un CASE.
        orientation = case(
            (MediaItem.height > MediaItem.width, "portrait"),
            (MediaItem.width > MediaItem.height, "paysage"),
            (MediaItem.width == MediaItem.height, "carre"),
            else_=None,
        )

        facettes = {
            "platform": _compter(db, filtres, "platform", MediaItem.platform),
            "type": _compter(db, filtres, "type", MediaItem.media_type),
            "profile_id": _compter(db, filtres, "profile_id", MediaItem.profile_id),
            "orientation": _compter(db, filtres, "orientation", orientation),
        }

        # Usage et présence de légende : deux valeurs chacun, comptées à part
        # parce que ce sont des prédicats, pas des colonnes.
        utilisés = db.query(ScheduledPost.source_media_id).filter(
            ScheduledPost.source_media_id.isnot(None)
        )
        base_usage = _appliquer_filtres(db, _base_query(db), filtres, sauf="used")
        facettes["used"] = [
            {"valeur": "oui", "compte": base_usage.filter(MediaItem.id.in_(utilisés)).count()},
            {"valeur": "non", "compte": base_usage.filter(~MediaItem.id.in_(utilisés)).count()},
        ]

        présent = MediaItem.caption.isnot(None) & (MediaItem.caption != "")
        base_légende = _appliquer_filtres(db, _base_query(db), filtres, sauf="caption")
        facettes["caption"] = [
            {"valeur": "oui", "compte": base_légende.filter(présent).count()},
            {"valeur": "non", "compte": base_légende.filter(~présent).count()},
        ]

        quicklink = db.query(_ids_quicklink(db).subquery().c.id)
        base_source = _appliquer_filtres(db, _base_query(db), filtres, sauf="source")
        facettes["source"] = [
            {"valeur": "profiles",
             "compte": base_source.filter(~MediaItem.profile_id.in_(quicklink)).count()},
            {"valeur": "quicklink",
             "compte": base_source.filter(MediaItem.profile_id.in_(quicklink)).count()},
        ]

        # Note : seuils cumulatifs 1+ .. 5, comptés sous les autres filtres.
        base_note = _appliquer_filtres(db, _base_query(db), filtres, sauf="rating")
        moyennes = (
            db.query(MediaRating.media_item_id, func.avg(MediaRating.rating).label("m"))
            .group_by(MediaRating.media_item_id)
            .subquery()
        )
        notes = []
        for seuil in range(1, 6):
            n = base_note.join(moyennes, MediaItem.id == moyennes.c.media_item_id).filter(
                moyennes.c.m >= seuil
            ).count()
            if n:
                notes.append({"valeur": str(seuil), "compte": n})
        facettes["rating"] = notes

        # Collections : comptées sous les autres filtres, comme tout le reste.
        base_collection = _appliquer_filtres(db, _base_query(db), filtres, sauf="collection")
        ids_visibles = base_collection.with_entities(MediaItem.id).subquery()
        comptes_collection = dict(
            db.query(CollectionItem.collection_id, func.count(CollectionItem.id))
            .filter(CollectionItem.media_item_id.in_(db.query(ids_visibles.c.id)))
            .group_by(CollectionItem.collection_id)
            .all()
        )
        facettes["collection"] = [
            {"valeur": c.id, "libelle": c.name, "compte": comptes_collection.get(c.id, 0)}
            for c in db.query(Collection).order_by(func.lower(Collection.name)).all()
            if comptes_collection.get(c.id, 0) > 0
        ]

        # Profils : le compte seul ne suffit pas à nommer l'entrée du menu.
        noms = {
            p.id: {"username": p.username, "platform": p.platform}
            for p in db.query(Profile.id, Profile.username, Profile.platform).all()
        }
        for entrée in facettes["profile_id"]:
            détail = noms.get(entrée["valeur"], {})
            entrée["libelle"] = "@{} · {}".format(
                détail.get("username", entrée["valeur"]), détail.get("platform", "")
            )

        total = _appliquer_filtres(db, _base_query(db), filtres).count()
        return jsonify({"facettes": facettes, "total": total})
    except Exception as exc:
        logger.error("Error listing facets: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Single media detail
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>")
def get_media(media_id: int):
    """Get a single media item with comments and ratings."""
    db = SessionLocal()
    try:
        # `Query.get()` est un legacy SQLAlchemy 1.x : il émet une
        # DeprecationWarning, que la suite de tests traite en erreur
        # (pytest.ini, filterwarnings = error). `Session.get()` est
        # l'orthographe 2.0, strictement équivalente.
        item = db.get(MediaItem, media_id)
        if not item:
            return jsonify({"error": "Not found"}), 404

        # Get profile info
        profile = db.get(Profile, item.profile_id)

        # Comments
        comments = (
            db.query(MediaComment)
            .filter(MediaComment.media_item_id == media_id)
            .order_by(MediaComment.created_at.desc())
            .all()
        )

        # Ratings
        ratings = (
            db.query(MediaRating)
            .filter(MediaRating.media_item_id == media_id)
            .all()
        )

        avg_rating = 0
        if ratings:
            avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1)

        file_url = None
        if item.local_path:
            filename = item.local_path.split("/")[-1] if "/" in item.local_path else item.local_path
            file_url = f"/media/file/{filename}"

        # V20 : la fiche liste TOUTES les collections d'appartenance, pas une.
        collections = (
            db.query(Collection.id, Collection.name)
            .join(CollectionItem, CollectionItem.collection_id == Collection.id)
            .filter(CollectionItem.media_item_id == media_id)
            .order_by(func.lower(Collection.name))
            .all()
        )

        return jsonify({
            "id": item.id,
            "collections": [{"id": c.id, "name": c.name} for c in collections],
            "md5": item.md5,
            "phash": item.phash,
            "post_id": item.post_id,
            "post_url": item.post_url,
            "media_type": item.media_type,
            "media_url": item.media_url,
            "file_url": file_url,
            "caption": item.caption,
            "phrase": item.phrase,
            "platform": item.platform,
            "profile_id": item.profile_id,
            "profile_username": profile.username if profile else None,
            "posted_at": item.posted_at,
            "discovered_at": item.discovered_at,
            "file_size": item.file_size,
            "file_name": Path(item.local_path).name if item.local_path else None,
            "used": db.query(ScheduledPost.id)
            .filter(ScheduledPost.source_media_id == media_id)
            .first()
            is not None,
            "width": item.width,
            "height": item.height,
            "duration": item.duration,
            "avg_rating": avg_rating,
            "ratings": [
                {"user_name": r.user_name, "rating": r.rating, "created_at": r.created_at}
                for r in ratings
            ],
            "comments": [
                {
                    "id": c.id,
                    "user_name": c.user_name,
                    "text": c.comment_text,
                    "created_at": c.created_at,
                }
                for c in comments
            ],
        })
    except Exception as exc:
        logger.error("Error getting media {}: {}", media_id, exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>/comment", methods=["POST"])
def add_comment(media_id: int):
    """Add a comment to a media item."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        user_name = (data.get("user_name") or "").strip()
        text = (data.get("text") or "").strip()

        if not user_name or not text:
            return jsonify({"error": "user_name and text required"}), 400

        # `Query.get()` est un legacy SQLAlchemy 1.x : il emet une
        # DeprecationWarning, que la suite traite en erreur (pytest.ini,
        # filterwarnings = error) — aucun test ne pouvait donc exercer
        # cette route. `Session.get()` est l'orthographe 2.0, strictement
        # equivalente ; c'est celle qu'utilise deja `get_media`.
        item = db.get(MediaItem, media_id)
        if not item:
            return jsonify({"error": "Media not found"}), 404

        comment = MediaComment(
            media_item_id=media_id,
            user_name=user_name,
            comment_text=text,
        )
        db.add(comment)
        db.commit()

        return jsonify({
            "id": comment.id,
            "user_name": comment.user_name,
            "text": comment.comment_text,
            "created_at": comment.created_at,
        }), 201
    except Exception as exc:
        db.rollback()
        logger.error("Error adding comment: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/media/<int:media_id>/comment/<int:comment_id>", methods=["DELETE"])
def delete_comment(media_id: int, comment_id: int):
    """Delete a comment (only the author can delete)."""
    db = SessionLocal()
    try:
        user_name = request.args.get("user_name", "").strip()
        # `Query.get()` est un legacy SQLAlchemy 1.x : il emet une
        # DeprecationWarning, que la suite traite en erreur (pytest.ini,
        # filterwarnings = error) — aucun test ne pouvait donc exercer
        # cette route. `Session.get()` est l'orthographe 2.0, strictement
        # equivalente ; c'est celle qu'utilise deja `get_media`.
        comment = db.get(MediaComment, comment_id)
        if not comment or comment.media_item_id != media_id:
            return jsonify({"error": "Comment not found"}), 404
        if comment.user_name != user_name:
            return jsonify({"error": "Not authorized"}), 403
        db.delete(comment)
        db.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        db.rollback()
        logger.error("Error deleting comment: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/media/<int:media_id>/rate", methods=["POST"])
def rate_media(media_id: int):
    """Add or update a rating for a media item (one per user)."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        user_name = (data.get("user_name") or "").strip()
        rating = data.get("rating")

        if not user_name or rating is None:
            return jsonify({"error": "user_name and rating required"}), 400

        rating = int(rating)
        if rating < 1 or rating > 5:
            return jsonify({"error": "Rating must be 1-5"}), 400

        # `Query.get()` est un legacy SQLAlchemy 1.x : il emet une
        # DeprecationWarning, que la suite traite en erreur (pytest.ini,
        # filterwarnings = error) — aucun test ne pouvait donc exercer
        # cette route. `Session.get()` est l'orthographe 2.0, strictement
        # equivalente ; c'est celle qu'utilise deja `get_media`.
        item = db.get(MediaItem, media_id)
        if not item:
            return jsonify({"error": "Media not found"}), 404

        # Upsert: update existing or create new
        existing = (
            db.query(MediaRating)
            .filter(
                MediaRating.media_item_id == media_id,
                MediaRating.user_name == user_name,
            )
            .first()
        )

        if existing:
            existing.rating = rating
            existing.created_at = int(datetime.now().timestamp())
        else:
            existing = MediaRating(
                media_item_id=media_id,
                user_name=user_name,
                rating=rating,
            )
            db.add(existing)

        db.commit()

        # Return updated average
        all_ratings = (
            db.query(MediaRating)
            .filter(MediaRating.media_item_id == media_id)
            .all()
        )
        avg = round(sum(r.rating for r in all_ratings) / len(all_ratings), 1) if all_ratings else 0

        return jsonify({
            "user_name": user_name,
            "rating": rating,
            "avg_rating": avg,
            "rating_count": len(all_ratings),
        })
    except Exception as exc:
        db.rollback()
        logger.error("Error rating media: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Phrase du futur meme — refonte UI « PAS-A-PAS »
# ---------------------------------------------------------------------------
# Le Tri rapide de la galerie ecrit une phrase par media ; l'etape « Texte »
# de l'editeur la relit pour pre-remplir le bandeau. C'est le SEUL ajout de
# donnees du lot.
#
# POURQUOI UN ENDPOINT DEDIE, ET PAS UN CHAMP DE PLUS SUR /rate : la note vit
# dans `media_ratings`, une table PAR UTILISATEUR (contrainte unique
# media + user_name) ; la phrase est une colonne du media, une seule pour
# tout le monde. Deux durees de vie, deux tables, deux routes. Le contrat est
# en revanche calque sur `rate_media` a la ligne pres — meme verbe, meme
# forme d'URL, meme SessionLocal/try/rollback/close, meme 404 sur media
# inconnu, meme renvoi de la valeur telle qu'elle vient d'etre ecrite.

#: Plafond de longueur. Un bandeau de meme tient en une ou deux lignes ; au-dela
#: ce n'est plus une phrase, c'est un collage qui ne s'affichera jamais en
#: entier sur le canvas. La borne evite aussi qu'un POST malformant fasse
#: gonfler la base sans limite.
PHRASE_MAX = 500


@viewer_api_bp.route("/viewer/media/<int:media_id>/phrase", methods=["POST"])
def set_media_phrase(media_id: int):
    """Ecrit (ou efface) la phrase du futur meme d'un media."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        if "phrase" not in data:
            return jsonify({"error": "phrase required"}), 400

        phrase = data.get("phrase")
        if phrase is None:
            phrase = ""
        if not isinstance(phrase, str):
            return jsonify({"error": "phrase must be a string"}), 400

        phrase = phrase.strip()
        if len(phrase) > PHRASE_MAX:
            return jsonify({"error": f"phrase must be <= {PHRASE_MAX} chars"}), 400

        item = db.get(MediaItem, media_id)
        if not item:
            return jsonify({"error": "Media not found"}), 404

        # Chaine vide -> NULL : un seul etat « pas de phrase » en base, donc un
        # seul test cote client (`if (media.phrase)`), et une colonne qui reste
        # vide tant que personne n'a trie.
        item.phrase = phrase or None
        db.commit()

        return jsonify({"id": media_id, "phrase": item.phrase})
    except Exception as exc:
        db.rollback()
        logger.error("Error setting media phrase: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Profiles list (for filter dropdown)
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/profiles")
def list_profiles():
    """List profiles for the viewer filter dropdown."""
    db = SessionLocal()
    try:
        profiles = db.query(Profile).filter(Profile.is_active == True).order_by(Profile.platform, Profile.username).all()
        return jsonify([
            {
                "id": p.id,
                "platform": p.platform,
                "username": p.username,
                "display_name": p.display_name,
            }
            for p in profiles
        ])
    except Exception as exc:
        logger.error("Error listing profiles: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Saved Memes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Batch delete media
# ---------------------------------------------------------------------------

def _effacer_les_fichiers(noms: list[str]) -> int:
    """Efface les fichiers nommés (et leur vignette). Renvoie le nb d'échecs.

    N'est appelée QU'APRÈS un commit réussi : un fichier effacé ne revient
    pas, une ligne effacée en base non plus, mais l'inverse — une ligne
    survivante pointant vers un fichier détruit — est le seul des deux qui
    perde le média du propriétaire sans trace.
    """
    vignettes = DOWNLOAD_DIR / ".thumbs"
    echecs = 0
    for nom in noms:
        for chemin in (DOWNLOAD_DIR / nom, vignettes / f"{Path(nom).stem}.jpg"):
            try:
                if chemin.is_file():
                    chemin.unlink()
            except OSError as exc:
                logger.error("Fichier non effacé {} : {}", chemin, exc)
                echecs += 1
    return echecs


@viewer_api_bp.route("/viewer/media/batch", methods=["DELETE"])
def batch_delete_media():
    """Delete multiple media items at once (files + DB records)."""
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        ids = data.get("ids", [])

        if not ids or not isinstance(ids, list):
            return jsonify({"error": "ids array required"}), 400

        if len(ids) > 500:
            return jsonify({"error": "Maximum 500 items per batch"}), 400

        items = db.query(MediaItem).filter(MediaItem.id.in_(ids)).all()

        # ORDRE CRITIQUE : la base D'ABORD, le disque ENSUITE.
        # L'ordre inverse — celui d'avant — effaçait les fichiers puis
        # tentait le commit : un commit refusé (base verrouillée, contrainte,
        # disque plein) laissait des lignes pointant vers des fichiers
        # détruits, c'est-à-dire une perte de données irréversible pour un
        # échec récupérable. Ici, si le commit échoue, RIEN n'a été touché.
        chemins = [Path(item.local_path).name for item in items if item.local_path]
        for item in items:
            db.delete(item)  # cascade : commentaires, notes, appartenances
        deleted = len(items)
        db.commit()

        errors = _effacer_les_fichiers(chemins)
        logger.info("Batch deleted {} media items ({} file errors)", deleted, errors)

        return jsonify({"deleted": deleted, "errors": errors})
    except Exception as exc:
        db.rollback()
        logger.error("Error in batch delete: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Saved Memes
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/memes")
def list_memes():
    """List saved memes for the viewer memes tab."""
    db = SessionLocal()
    try:
        page, per_page = _pagination_args()

        query = db.query(SavedMeme).order_by(SavedMeme.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "items": [
                {
                    "id": m.id,
                    "title": m.title or "",
                    "caption": m.caption or "",
                    "media_type": m.media_type,
                    "template_format": m.template_format or "",
                    "file_url": f"/api/viewer/memes/{m.id}/file",
                    "thumbnail_url": f"/api/viewer/memes/{m.id}/file",
                    "file_size": m.file_size,
                    "created_at": m.created_at,
                }
                for m in items
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        })
    except Exception as exc:
        logger.error("Error listing memes: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/memes/<int:meme_id>/file")
def serve_meme_file(meme_id: int):
    """Serve a saved meme file."""
    db = SessionLocal()
    try:
        meme = db.query(SavedMeme).filter_by(id=meme_id).first()
        if not meme:
            return jsonify({"error": "Meme not found"}), 404

        if not meme.file_path or not os.path.exists(meme.file_path):
            return jsonify({"error": "Meme file not found on disk"}), 404

        mime = "video/mp4" if meme.media_type == "video" else "image/png"
        return send_file(meme.file_path, mimetype=mime)
    except Exception as exc:
        logger.error("Error serving meme file: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/memes", methods=["POST"])
def save_meme():
    """Save a meme from the editor to the viewer gallery.

    Accepts JSON with:
        - image_data: base64 encoded image data (for images)
        - title: optional title
        - caption: optional caption
        - template_format: square | portrait | story
        - media_type: image | video (default: image)
        - source_media_id: optional source media ID
    """
    from nanoid import generate as nanoid

    db = SessionLocal()
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        media_type = data.get("media_type", "image")
        image_data = data.get("image_data", "")

        if not image_data and media_type == "image":
            return jsonify({"error": "No image data provided"}), 400

        # Ensure output dir
        EDITOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        meme_dir = EDITOR_OUTPUT_DIR / "memes"
        meme_dir.mkdir(parents=True, exist_ok=True)

        # Save file
        file_id = nanoid()
        if media_type == "image":
            # Decode base64
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            file_bytes = base64.b64decode(image_data)
            file_path = str(meme_dir / f"{file_id}.png")
            with open(file_path, "wb") as f:
                f.write(file_bytes)
            file_size = len(file_bytes)
        else:
            return jsonify({"error": "Video meme saving not yet supported"}), 400

        # Save to DB
        meme = SavedMeme(
            title=data.get("title", ""),
            caption=data.get("caption", ""),
            media_type=media_type,
            template_format=data.get("template_format", ""),
            file_path=file_path,
            file_size=file_size,
            source_media_id=data.get("source_media_id"),
        )
        db.add(meme)
        db.commit()
        db.refresh(meme)

        logger.info("Saved meme #{} to {}", meme.id, file_path)

        return jsonify({
            "id": meme.id,
            "file_url": f"/api/viewer/memes/{meme.id}/file",
            "message": "Meme sauvegarde avec succes!",
        }), 201

    except Exception as exc:
        logger.exception("Error saving meme: {}", exc)
        db.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/memes/<int:meme_id>", methods=["DELETE"])
def delete_meme(meme_id: int):
    """Delete a saved meme."""
    db = SessionLocal()
    try:
        meme = db.query(SavedMeme).filter_by(id=meme_id).first()
        if not meme:
            return jsonify({"error": "Meme not found"}), 404

        # Delete file
        if meme.file_path and os.path.exists(meme.file_path):
            os.remove(meme.file_path)

        db.delete(meme)
        db.commit()

        return jsonify({"message": "Meme supprime"})
    except Exception as exc:
        logger.error("Error deleting meme: {}", exc)
        db.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ===========================================================================
# DOUBLONS — empreintes, recherche, déduplication (V27, V28, V29)
# ===========================================================================
# Deux questions distinctes, deux endpoints distincts, jamais mélangés :
#   /viewer/duplicates/exact   — fichiers IDENTIQUES au bit près (md5).
#   /viewer/duplicates/similar — images VISUELLEMENT proches (phash + Hamming).
# Le second stocke la distance de CHAQUE paire dans sa réponse : c'est ce qui
# permet au curseur de similarité de resserrer ou d'élargir le résultat sans
# relancer le moindre calcul (V28).

#: Un lot de calcul d'empreintes. Assez petit pour qu'une requête réponde en
#: quelques secondes (ffmpeg : ~20 ms par image, ~90 ms par vidéo), assez
#: grand pour que la barre de progression avance visiblement.
_LOT_EMPREINTES = 25

#: En dessous de ce nombre d'empreintes, la comparaison est EXHAUSTIVE :
#: n(n-1)/2 distances, soit ~1,1 million d'opérations entières au plafond —
#: moins d'une seconde, et surtout AUCUNE paire manquée.
_SEUIL_EXHAUSTIF = 1500

#: Au-delà, on passe au bucketing en 4 tranches de 16 bits. Un seau qui
#: dépasse cette taille est ignoré : il ne désigne plus des candidats mais
#: une collision massive (des milliers de vignettes unies, par exemple), et
#: son produit croisé ferait exploser le temps de réponse.
_SEAU_MAX = 400

#: Plafond de distance exploré par le scan. 64 bits au total : au-delà de 16
#: bits d'écart, deux images n'ont plus rien de commun. Le curseur client
#: joue à l'intérieur de cette plage déjà calculée.
_DISTANCE_SCAN_MAX = 16


def _nom_fichier(item: MediaItem) -> str | None:
    return Path(item.local_path).name if item.local_path else None


def _carte(item: MediaItem, profils: dict, comptes: dict) -> dict:
    """La fiche d'un candidat au doublon.

    V27 exige résolution, poids et format visibles pour CHAQUE candidat :
    ce sont les trois seuls critères qui permettent de choisir lequel garder.
    On y ajoute ce que la déduplication risque de détruire — commentaires,
    notes, collections — parce que V29 demande de le montrer AVANT.
    """
    nom = _nom_fichier(item)
    file_url = f"/media/file/{nom}" if nom else None
    detail = comptes.get(item.id, {})
    return {
        "id": item.id,
        "file_url": file_url,
        "thumb_url": f"/media/thumb/{nom}" if nom else None,
        "file_name": nom,
        "format": (Path(nom).suffix.lstrip(".").upper() if nom else None),
        "media_type": item.media_type,
        "width": item.width,
        "height": item.height,
        "file_size": item.file_size,
        "duration": item.duration,
        "posted_at": item.posted_at,
        "discovered_at": item.discovered_at,
        "platform": item.platform,
        "profile_username": profils.get(item.profile_id),
        "post_url": item.post_url,
        "caption": item.caption,
        "md5": item.md5,
        "phash": item.phash,
        "comment_count": detail.get("commentaires", 0),
        "rating_count": detail.get("notes", 0),
        "collections": detail.get("collections", []),
    }


def _comptes_metadonnees(db, ids: list[int]) -> dict:
    """{media_id: {commentaires, notes, collections[]}} en 3 requêtes."""
    detail: dict[int, dict] = {i: {"commentaires": 0, "notes": 0, "collections": []} for i in ids}
    if not ids:
        return detail

    for mid, n in (
        db.query(MediaComment.media_item_id, func.count(MediaComment.id))
        .filter(MediaComment.media_item_id.in_(ids))
        .group_by(MediaComment.media_item_id)
        .all()
    ):
        detail[mid]["commentaires"] = n

    for mid, n in (
        db.query(MediaRating.media_item_id, func.count(MediaRating.id))
        .filter(MediaRating.media_item_id.in_(ids))
        .group_by(MediaRating.media_item_id)
        .all()
    ):
        detail[mid]["notes"] = n

    for mid, nom in (
        db.query(CollectionItem.media_item_id, Collection.name)
        .join(Collection, Collection.id == CollectionItem.collection_id)
        .filter(CollectionItem.media_item_id.in_(ids))
        .all()
    ):
        detail[mid]["collections"].append(nom)

    return detail


def _profils(db) -> dict:
    return {p.id: p.username for p in db.query(Profile.id, Profile.username).all()}


def _stock_local(db):
    """Les médias dont le fichier est censé être sur ce disque."""
    return db.query(MediaItem).filter(
        MediaItem.local_path.isnot(None),
        MediaItem.status.in_(("uploaded", "downloaded")),
    )


# ---------------------------------------------------------------------------
# Empreintes : calcul différé sur la bibliothèque DÉJÀ stockée
# ---------------------------------------------------------------------------
# Sans ça la fonctionnalité serait vide : les 16 médias du propriétaire ont été
# téléchargés avant l'existence des colonnes. Le calcul se fait par lots, à la
# demande du client, qui affiche la progression — pas de thread de fond, pas
# de tâche fantôme, et une requête qui échoue ne perd que son lot.

def _restants(db) -> int:
    """Médias locaux à qui il manque au moins une des deux empreintes."""
    return _stock_local(db).filter(
        or_(MediaItem.md5.is_(None), MediaItem.phash.is_(None))
    ).count()


@viewer_api_bp.route("/viewer/fingerprints/status")
def fingerprints_status():
    """Combien de médias locaux ont déjà leurs deux empreintes."""
    db = SessionLocal()
    try:
        return jsonify({
            "total": _stock_local(db).count(),
            "md5": _stock_local(db).filter(MediaItem.md5.isnot(None)).count(),
            "phash": _stock_local(db).filter(MediaItem.phash.isnot(None)).count(),
            "restants": _restants(db),
            "lot": _LOT_EMPREINTES,
        })
    except Exception as exc:
        logger.error("Etat des empreintes : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/fingerprints/compute", methods=["POST"])
def fingerprints_compute():
    """Calcule les empreintes d'UN lot de médias, puis rend la main.

    Le client rappelle tant que `restants` n'est pas nul : la progression est
    donc réelle et visible, et une bibliothèque de plusieurs milliers de
    fichiers ne bloque jamais une requête plus de quelques secondes.
    """
    db = SessionLocal()
    try:
        restants_avant = _restants(db)

        manquants = (
            _stock_local(db)
            .filter(or_(MediaItem.md5.is_(None), MediaItem.phash.is_(None)))
            .order_by(MediaItem.id)
            .limit(_LOT_EMPREINTES)
            .all()
        )

        traites = 0
        illisibles = 0
        for item in manquants:
            nom = _nom_fichier(item)
            chemin = DOWNLOAD_DIR / nom if nom else None
            md5 = phash = None
            if chemin is not None:
                md5, phash = empreintes(chemin, item.media_type)
            if md5 is None and phash is None:
                # Fichier absent ou illisible. On ne réécrit rien : le média
                # restera « sans empreinte » et sera compté comme tel, ce qui
                # est la vérité — plutôt qu'une empreinte inventée.
                illisibles += 1
                continue
            if md5 is not None:
                item.md5 = md5
            if phash is not None:
                item.phash = phash
            traites += 1

        if traites:
            db.commit()

        restants = _restants(db)

        # `termine` mesure le PROGRÈS RÉEL, pas le nombre de lignes touchées.
        # Un fichier lisible mais dont l'image n'est pas exploitable (archive,
        # vidéo corrompue, PDF renommé) reçoit son md5 à chaque passage sans
        # jamais recevoir de phash : `traites` resterait donc éternellement
        # positif et le client boucherait à l'infini sur le même lot. Compter
        # les restants avant et après est le seul critère qui ne peut pas
        # tourner en rond.
        return jsonify({
            "traites": traites,
            "illisibles": illisibles,
            "restants": restants,
            "termine": restants >= restants_avant,
        })
    except Exception as exc:
        db.rollback()
        logger.error("Calcul des empreintes : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode 1 — FICHIERS IDENTIQUES (md5)
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/duplicates/exact")
def duplicates_exact():
    """Groupes de médias partageant le même md5 : identiques au bit près."""
    db = SessionLocal()
    try:
        cles = [
            row[0]
            for row in db.query(MediaItem.md5)
            .filter(
                MediaItem.md5.isnot(None),
                MediaItem.local_path.isnot(None),
                MediaItem.status.in_(("uploaded", "downloaded")),
            )
            .group_by(MediaItem.md5)
            .having(func.count(MediaItem.id) > 1)
            .all()
        ]

        groupes = []
        if cles:
            items = _stock_local(db).filter(MediaItem.md5.in_(cles)).all()
            profils = _profils(db)
            comptes = _comptes_metadonnees(db, [i.id for i in items])
            par_cle: dict[str, list] = {}
            for item in items:
                par_cle.setdefault(item.md5, []).append(item)
            for cle, membres in par_cle.items():
                if len(membres) < 2:
                    continue
                membres.sort(key=lambda i: (i.discovered_at or 0, i.id))
                groupes.append({
                    "cle": cle,
                    "items": [_carte(i, profils, comptes) for i in membres],
                    # Ce qui serait récupéré en ne gardant qu'un exemplaire.
                    "octets_recuperables": sum(i.file_size or 0 for i in membres[1:]),
                })
            groupes.sort(key=lambda g: -g["octets_recuperables"])

        sans_empreinte = _stock_local(db).filter(MediaItem.md5.is_(None)).count()
        return jsonify({
            "groupes": groupes,
            "total_groupes": len(groupes),
            "total_items": sum(len(g["items"]) for g in groupes),
            "octets_recuperables": sum(g["octets_recuperables"] for g in groupes),
            "sans_empreinte": sans_empreinte,
        })
    except Exception as exc:
        logger.error("Doublons exacts : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode 2 — VISUELLEMENT SIMILAIRES (phash + distance de Hamming)
# ---------------------------------------------------------------------------

def _paires_similaires(empreintes_64: list[tuple[int, int]], distance_max: int):
    """[(id_a, id_b, distance)] et un drapeau « comparaison exhaustive ».

    Sous `_SEUIL_EXHAUSTIF` empreintes on compare TOUT : c'est exact, et
    c'est le cas de la bibliothèque réelle. Au-delà, on passe par des seaux
    de 16 bits — quatre tranches, quatre index. Deux empreintes distantes de
    3 bits ou moins partagent forcément une tranche intacte (principe des
    tiroirs, 4 tranches pour au plus 3 bits déplacés) : à ce seuil le
    bucketing ne rate RIEN. Entre 4 et 16 bits il devient un pré-filtre —
    rapide et très majoritairement complet, mais plus une garantie. La
    réponse porte le drapeau pour que l'écran puisse le dire.
    """
    n = len(empreintes_64)
    paires: list[tuple[int, int, int]] = []

    if n <= _SEUIL_EXHAUSTIF:
        for i in range(n):
            id_a, val_a = empreintes_64[i]
            for j in range(i + 1, n):
                id_b, val_b = empreintes_64[j]
                d = bin(val_a ^ val_b).count("1")
                if d <= distance_max:
                    paires.append((id_a, id_b, d))
        return paires, True

    seaux: dict[tuple[int, int], list[int]] = {}
    for position, (_, valeur) in enumerate(empreintes_64):
        for tranche in range(4):
            seaux.setdefault((tranche, (valeur >> (16 * tranche)) & 0xFFFF), []).append(position)

    vues: set[tuple[int, int]] = set()
    for membres in seaux.values():
        if len(membres) < 2 or len(membres) > _SEAU_MAX:
            continue
        for a in range(len(membres)):
            for b in range(a + 1, len(membres)):
                couple = (membres[a], membres[b])
                if couple in vues:
                    continue
                vues.add(couple)
                id_a, val_a = empreintes_64[membres[a]]
                id_b, val_b = empreintes_64[membres[b]]
                d = bin(val_a ^ val_b).count("1")
                if d <= distance_max:
                    paires.append((id_a, id_b, d))
    return paires, False


@viewer_api_bp.route("/viewer/duplicates/similar")
def duplicates_similar():
    """Paires visuellement proches, CHACUNE avec sa distance de Hamming.

    Le scan explore jusqu'à `_DISTANCE_SCAN_MAX` bits une bonne fois. Le
    curseur de l'écran ne fait ensuite que filtrer sur la distance déjà
    renvoyée : resserrer de 10 à 2 bits ne relance aucun calcul (V28).
    """
    db = SessionLocal()
    try:
        items = (
            _stock_local(db)
            .filter(MediaItem.phash.isnot(None))
            .order_by(MediaItem.id)
            .all()
        )

        utilisables: list[tuple[int, int]] = []
        uniformes = 0
        illisibles = 0
        for item in items:
            if item.phash in PHASH_DEGENERE:
                # Image unie : son empreinte « ressemble » à toutes les autres
                # images unies. La compter produirait un groupe géant et faux.
                uniformes += 1
                continue
            try:
                utilisables.append((item.id, int(item.phash, 16)))
            except (TypeError, ValueError):
                illisibles += 1

        paires, exhaustif = _paires_similaires(utilisables, _DISTANCE_SCAN_MAX)

        ids_concernes = sorted({i for p in paires for i in p[:2]})
        cartes = []
        if ids_concernes:
            profils = _profils(db)
            comptes = _comptes_metadonnees(db, ids_concernes)
            membres = _stock_local(db).filter(MediaItem.id.in_(ids_concernes)).all()
            cartes = [_carte(i, profils, comptes) for i in membres]

        sans_empreinte = _stock_local(db).filter(MediaItem.phash.is_(None)).count()
        return jsonify({
            "items": cartes,
            "paires": [{"a": a, "b": b, "distance": d} for a, b, d in paires],
            "distance_scan": _DISTANCE_SCAN_MAX,
            "compares": len(utilisables),
            "uniformes": uniformes,
            "illisibles": illisibles,
            "sans_empreinte": sans_empreinte,
            "exhaustif": exhaustif,
        })
    except Exception as exc:
        logger.error("Doublons visuels : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Déduplication — V29 : rien ne part sans dire quoi garder
# ---------------------------------------------------------------------------

@viewer_api_bp.route("/viewer/duplicates/resolve", methods=["POST"])
def duplicates_resolve():
    """Garde UN exemplaire, supprime les autres, transfère ce qui est demandé.

    Corps attendu ::

        {"keep_id": 12,
         "remove_ids": [34, 56],
         "conserver": {"notes": true, "commentaires": true, "collections": true}}

    `keep_id` et `conserver` sont OBLIGATOIRES et sans valeur par défaut
    silencieuse : l'écran doit avoir posé la question. Une suppression sans
    ces deux réponses est refusée (400), pas devinée.
    """
    db = SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        keep_id = data.get("keep_id")
        remove_ids = data.get("remove_ids")
        conserver = data.get("conserver")

        if not isinstance(keep_id, int):
            return jsonify({"error": "keep_id manquant : indiquez l'exemplaire à garder"}), 400
        if not isinstance(remove_ids, list) or not remove_ids:
            return jsonify({"error": "remove_ids doit lister au moins un média"}), 400
        if not isinstance(conserver, dict):
            return jsonify({"error": "conserver manquant : indiquez les métadonnées à reprendre"}), 400
        if len(remove_ids) > 500:
            return jsonify({"error": "Maximum 500 médias par opération"}), 400

        remove_ids = [i for i in remove_ids if isinstance(i, int) and i != keep_id]
        if not remove_ids:
            return jsonify({"error": "Aucun média à supprimer une fois l'exemplaire gardé exclu"}), 400

        garde = db.query(MediaItem).filter(MediaItem.id == keep_id).first()
        if garde is None:
            return jsonify({"error": "L'exemplaire à garder est introuvable"}), 404

        a_supprimer = db.query(MediaItem).filter(MediaItem.id.in_(remove_ids)).all()
        if not a_supprimer:
            return jsonify({"error": "Aucun des médias à supprimer n'existe"}), 404
        ids_reels = [i.id for i in a_supprimer]

        transferts = {"commentaires": 0, "notes": 0, "collections": 0}

        if conserver.get("commentaires"):
            # Un commentaire n'a pas de contrainte d'unicité : tout suit.
            for c in db.query(MediaComment).filter(MediaComment.media_item_id.in_(ids_reels)).all():
                c.media_item_id = keep_id
                transferts["commentaires"] += 1

        if conserver.get("notes"):
            # UNE note par (média, pseudo). Si l'exemplaire gardé a déjà la
            # note de ce pseudo, c'est ELLE qui fait foi : on ne l'écrase pas
            # avec celle d'un doublon, et on ne viole pas la contrainte.
            deja = {
                r.user_name
                for r in db.query(MediaRating).filter(MediaRating.media_item_id == keep_id).all()
            }
            for r in db.query(MediaRating).filter(MediaRating.media_item_id.in_(ids_reels)).all():
                if r.user_name in deja:
                    continue
                r.media_item_id = keep_id
                deja.add(r.user_name)
                transferts["notes"] += 1

        if conserver.get("collections"):
            deja_col = {
                l.collection_id
                for l in db.query(CollectionItem).filter(CollectionItem.media_item_id == keep_id).all()
            }
            for l in db.query(CollectionItem).filter(CollectionItem.media_item_id.in_(ids_reels)).all():
                if l.collection_id in deja_col:
                    continue
                l.media_item_id = keep_id
                deja_col.add(l.collection_id)
                transferts["collections"] += 1

        # Le fichier gardé ne doit JAMAIS partir : deux lignes peuvent pointer
        # vers le même fichier (même md5 réimporté au même emplacement).
        nom_garde = _nom_fichier(garde)
        chemins = [
            n for n in (_nom_fichier(i) for i in a_supprimer)
            if n and n != nom_garde
        ]

        for item in a_supprimer:
            db.delete(item)

        # COMMIT AVANT LE DISQUE. Si la base refuse, aucun fichier n'a bougé.
        db.commit()

        echecs = _effacer_les_fichiers(chemins)
        logger.info(
            "Déduplication : {} supprimés, gardé #{}, transferts {}",
            len(ids_reels), keep_id, transferts,
        )
        return jsonify({
            "supprimes": len(ids_reels),
            "garde": keep_id,
            "transferts": transferts,
            "fichiers_en_echec": echecs,
        })
    except Exception as exc:
        db.rollback()
        logger.error("Déduplication : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ===========================================================================
# COLLECTIONS MANUELLES (V20)
# ===========================================================================
# Un média appartient à AUTANT de collections qu'on veut, en travers des
# profils. Supprimer une collection ne supprime AUCUN média : seule
# l'appartenance disparaît.

_NOM_COLLECTION_MAX = 60


def _nom_valide(brut) -> tuple[str | None, str | None]:
    """(nom nettoyé, message d'erreur). L'un des deux est None."""
    nom = (brut or "").strip() if isinstance(brut, str) else ""
    if not nom:
        return None, "Le nom de la collection ne peut pas être vide"
    if len(nom) > _NOM_COLLECTION_MAX:
        return None, f"Le nom ne peut pas dépasser {_NOM_COLLECTION_MAX} caractères"
    return nom, None


def _collision_de_nom(db, nom: str, sauf_id: int | None = None) -> bool:
    """Une collection porte-t-elle déjà ce nom, à la casse près ?"""
    q = db.query(Collection.id).filter(func.lower(Collection.name) == nom.lower())
    if sauf_id is not None:
        q = q.filter(Collection.id != sauf_id)
    return q.first() is not None


def _collection_json(c: Collection, compte: int) -> dict:
    return {"id": c.id, "name": c.name, "count": compte,
            "created_at": c.created_at, "updated_at": c.updated_at}


def _comptes_par_collection(db) -> dict:
    return dict(
        db.query(CollectionItem.collection_id, func.count(CollectionItem.id))
        .group_by(CollectionItem.collection_id)
        .all()
    )


@viewer_api_bp.route("/viewer/collections")
def list_collections():
    """Toutes les collections, avec leur compteur — la navigation latérale."""
    db = SessionLocal()
    try:
        comptes = _comptes_par_collection(db)
        collections = db.query(Collection).order_by(func.lower(Collection.name)).all()
        return jsonify({
            "collections": [_collection_json(c, comptes.get(c.id, 0)) for c in collections],
        })
    except Exception as exc:
        logger.error("Liste des collections : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/collections", methods=["POST"])
def create_collection():
    """Crée une collection. Le nom est unique, à la casse près."""
    db = SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        nom, erreur = _nom_valide(data.get("name"))
        if erreur:
            return jsonify({"error": erreur}), 400
        if _collision_de_nom(db, nom):
            return jsonify({"error": f"Une collection s'appelle déjà « {nom} »"}), 409

        collection = Collection(name=nom)
        db.add(collection)
        db.commit()
        db.refresh(collection)

        # Ajout immédiat d'une sélection : « créer et y verser » en un geste.
        ids = data.get("ids")
        ajoutes = 0
        if isinstance(ids, list) and ids:
            ajoutes = _ajouter_a_la_collection(db, collection.id, ids)

        return jsonify({**_collection_json(collection, ajoutes), "ajoutes": ajoutes}), 201
    except Exception as exc:
        db.rollback()
        logger.error("Création de collection : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/collections/<int:collection_id>", methods=["PATCH"])
def rename_collection(collection_id: int):
    """Renomme une collection. Aucune appartenance n'est touchée."""
    db = SessionLocal()
    try:
        collection = db.query(Collection).filter(Collection.id == collection_id).first()
        if collection is None:
            return jsonify({"error": "Collection introuvable"}), 404

        data = request.get_json(silent=True) or {}
        nom, erreur = _nom_valide(data.get("name"))
        if erreur:
            return jsonify({"error": erreur}), 400
        if _collision_de_nom(db, nom, sauf_id=collection_id):
            return jsonify({"error": f"Une collection s'appelle déjà « {nom} »"}), 409

        collection.name = nom
        collection.updated_at = int(datetime.now().timestamp())
        db.commit()
        comptes = _comptes_par_collection(db)
        return jsonify(_collection_json(collection, comptes.get(collection.id, 0)))
    except Exception as exc:
        db.rollback()
        logger.error("Renommage de collection : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/collections/<int:collection_id>", methods=["DELETE"])
def delete_collection(collection_id: int):
    """Supprime la collection. AUCUN média n'est supprimé, aucun fichier.

    Seules les lignes d'appartenance disparaissent — c'est exactement ce que
    le dialogue de confirmation annonce à l'écran.
    """
    db = SessionLocal()
    try:
        collection = db.query(Collection).filter(Collection.id == collection_id).first()
        if collection is None:
            return jsonify({"error": "Collection introuvable"}), 404

        nom = collection.name
        liberes = (
            db.query(func.count(CollectionItem.id))
            .filter(CollectionItem.collection_id == collection_id)
            .scalar()
        ) or 0
        db.delete(collection)  # cascade ORM : seulement collection_items
        db.commit()
        logger.info("Collection « {} » supprimée ({} appartenances, 0 média)", nom, liberes)
        return jsonify({"deleted": collection_id, "name": nom, "appartenances_retirees": liberes})
    except Exception as exc:
        db.rollback()
        logger.error("Suppression de collection : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


def _ajouter_a_la_collection(db, collection_id: int, ids) -> int:
    """Ajoute les médias à la collection, sans doublon. Renvoie le nb ajoutés."""
    voulus = {i for i in ids if isinstance(i, int)}
    if not voulus:
        return 0
    existants = {
        row[0]
        for row in db.query(MediaItem.id).filter(MediaItem.id.in_(voulus)).all()
    }
    deja = {
        row[0]
        for row in db.query(CollectionItem.media_item_id)
        .filter(
            CollectionItem.collection_id == collection_id,
            CollectionItem.media_item_id.in_(existants),
        )
        .all()
    }
    nouveaux = existants - deja
    for media_id in sorted(nouveaux):
        db.add(CollectionItem(collection_id=collection_id, media_item_id=media_id))
    if nouveaux:
        db.commit()
    return len(nouveaux)


@viewer_api_bp.route("/viewer/collections/<int:collection_id>/items", methods=["POST"])
def add_to_collection(collection_id: int):
    """Ajoute un LOT de médias (la sélection multiple du viewer)."""
    db = SessionLocal()
    try:
        collection = db.query(Collection).filter(Collection.id == collection_id).first()
        if collection is None:
            return jsonify({"error": "Collection introuvable"}), 404

        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids doit lister au moins un média"}), 400
        if len(ids) > 500:
            return jsonify({"error": "Maximum 500 médias par lot"}), 400

        demandes = len({i for i in ids if isinstance(i, int)})
        ajoutes = _ajouter_a_la_collection(db, collection_id, ids)
        comptes = _comptes_par_collection(db)
        return jsonify({
            "ajoutes": ajoutes,
            "deja_presents": max(0, demandes - ajoutes),
            "collection": _collection_json(collection, comptes.get(collection_id, 0)),
        })
    except Exception as exc:
        db.rollback()
        logger.error("Ajout à une collection : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/collections/<int:collection_id>/items", methods=["DELETE"])
def remove_from_collection(collection_id: int):
    """Retire des médias de la collection. Les médias eux-mêmes restent."""
    db = SessionLocal()
    try:
        collection = db.query(Collection).filter(Collection.id == collection_id).first()
        if collection is None:
            return jsonify({"error": "Collection introuvable"}), 404

        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids doit lister au moins un média"}), 400
        # Même plafond que l'ajout (voir add_to_collection) : les deux
        # moitiés du même endpoint doivent valider pareil, sinon la
        # requête IN(...) peut être arbitrairement longue.
        if len(ids) > 500:
            return jsonify({"error": "Maximum 500 médias par lot"}), 400

        voulus = [i for i in ids if isinstance(i, int)]
        retires = 0
        if voulus:
            liens = (
                db.query(CollectionItem)
                .filter(
                    CollectionItem.collection_id == collection_id,
                    CollectionItem.media_item_id.in_(voulus),
                )
                .all()
            )
            for lien in liens:
                db.delete(lien)
            retires = len(liens)
            db.commit()

        comptes = _comptes_par_collection(db)
        return jsonify({
            "retires": retires,
            "collection": _collection_json(collection, comptes.get(collection_id, 0)),
        })
    except Exception as exc:
        db.rollback()
        logger.error("Retrait d'une collection : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Corbeille — la sortie « Passer » du tri rapide
# ---------------------------------------------------------------------------
#
# La corbeille est une COLLECTION ORDINAIRE nommée « Corbeille », pas une
# colonne sur les médias ni une table à part. Trois conséquences voulues :
#   - elle apparaît dans la barre latérale comme les autres, on peut donc
#     l'ouvrir et regarder ce qu'on y a jeté avant de vider ;
#   - « Passer » n'efface RIEN : il pose une appartenance, réversible par le
#     bouton Annuler du tri comme par un retrait manuel ;
#   - la seule opération destructrice est « Vider », explicite, confirmée,
#     et déclenchée par le propriétaire — jamais par un glissé du doigt.
CORBEILLE_NOM = "Corbeille"


def _corbeille(db, creer: bool = False):
    """La collection corbeille. `creer=True` la crée si elle manque.

    Recherche insensible à la casse, comme l'unicité des noms de collection :
    sans ça, une corbeille renommée « corbeille » à la main donnerait DEUX
    corbeilles, dont une invisible du tri.
    """
    collection = (
        db.query(Collection)
        .filter(func.lower(Collection.name) == CORBEILLE_NOM.lower())
        .first()
    )
    if collection is None and creer:
        collection = Collection(name=CORBEILLE_NOM)
        db.add(collection)
        db.commit()
        db.refresh(collection)
    return collection


def _etat_corbeille(db) -> dict:
    """Ce que l'écran doit savoir AVANT de proposer de vider.

    `aussi_ailleurs` compte les médias jetés qui appartiennent en plus à une
    autre collection. Vider les supprime quand même — mais le dialogue le
    dit, au lieu de faire disparaître sans prévenir un média rangé ailleurs.
    """
    collection = _corbeille(db)
    if collection is None:
        return {"id": None, "count": 0, "aussi_ailleurs": 0}

    dedans = (
        db.query(CollectionItem.media_item_id)
        .filter(CollectionItem.collection_id == collection.id)
        .subquery()
    )
    count = db.query(func.count()).select_from(dedans).scalar() or 0
    aussi_ailleurs = (
        db.query(func.count(func.distinct(CollectionItem.media_item_id)))
        .filter(
            CollectionItem.collection_id != collection.id,
            CollectionItem.media_item_id.in_(db.query(dedans.c.media_item_id)),
        )
        .scalar()
    ) or 0
    return {"id": collection.id, "count": count, "aussi_ailleurs": aussi_ailleurs}


@viewer_api_bp.route("/viewer/corbeille")
def get_corbeille():
    """Compteur de la corbeille — lu à l'ouverture du tri et après chaque geste."""
    db = SessionLocal()
    try:
        return jsonify(_etat_corbeille(db))
    except Exception as exc:
        logger.error("Lecture de la corbeille : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/corbeille/items", methods=["POST"])
def corbeille_ajouter():
    """Jette des médias à la corbeille. Crée la collection au premier jet."""
    db = SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids doit lister au moins un média"}), 400
        if len(ids) > 500:
            return jsonify({"error": "Maximum 500 médias par lot"}), 400

        collection = _corbeille(db, creer=True)
        ajoutes = _ajouter_a_la_collection(db, collection.id, ids)
        return jsonify({"ajoutes": ajoutes, "corbeille": _etat_corbeille(db)})
    except Exception as exc:
        db.rollback()
        logger.error("Jet à la corbeille : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/corbeille/items", methods=["DELETE"])
def corbeille_retirer():
    """Ressort des médias de la corbeille — c'est « Annuler » du tri.

    Corbeille absente = rien à ressortir : on répond 200 avec 0 retiré, et
    surtout PAS 404. Annuler un « Passer » ne doit jamais afficher d'erreur
    au propriétaire pour une collection que le serveur n'a pas encore créée.
    """
    db = SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids doit lister au moins un média"}), 400
        if len(ids) > 500:
            return jsonify({"error": "Maximum 500 médias par lot"}), 400

        collection = _corbeille(db)
        retires = 0
        if collection is not None:
            voulus = [i for i in ids if isinstance(i, int)]
            if voulus:
                liens = (
                    db.query(CollectionItem)
                    .filter(
                        CollectionItem.collection_id == collection.id,
                        CollectionItem.media_item_id.in_(voulus),
                    )
                    .all()
                )
                for lien in liens:
                    db.delete(lien)
                retires = len(liens)
                db.commit()
        return jsonify({"retires": retires, "corbeille": _etat_corbeille(db)})
    except Exception as exc:
        db.rollback()
        logger.error("Retrait de la corbeille : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


@viewer_api_bp.route("/viewer/corbeille/vider", methods=["POST"])
def corbeille_vider():
    """Supprime DÉFINITIVEMENT les médias de la corbeille (base + fichiers).

    Le client n'envoie AUCUNE liste d'identifiants : le serveur supprime ce
    qui est dans la corbeille, rien d'autre. Un lot d'ids fourni par la page
    serait la seule façon qu'un bug d'affichage — ou une requête forgée —
    fasse détruire des médias que le propriétaire n'a jamais jetés.

    Ordre repris de `batch_delete_media` : la base d'abord, le disque
    ensuite. Un commit refusé laisse alors TOUT en place ; l'ordre inverse
    laisserait des lignes pointant vers des fichiers déjà détruits.
    """
    db = SessionLocal()
    try:
        collection = _corbeille(db)
        if collection is None:
            return jsonify({"supprimes": 0, "errors": 0, "corbeille": _etat_corbeille(db)})

        ids = [
            row[0]
            for row in db.query(CollectionItem.media_item_id)
            .filter(CollectionItem.collection_id == collection.id)
            .all()
        ]
        if not ids:
            return jsonify({"supprimes": 0, "errors": 0, "corbeille": _etat_corbeille(db)})

        items = db.query(MediaItem).filter(MediaItem.id.in_(ids)).all()
        chemins = [Path(item.local_path).name for item in items if item.local_path]
        # Identifiants relevés AVANT le delete : après le commit, lire .id
        # sur une instance supprimée peut repartir en base pour rien.
        supprimes_ids = [item.id for item in items]
        for item in items:
            db.delete(item)  # cascade : commentaires, notes, appartenances
        supprimes = len(items)
        db.commit()

        errors = _effacer_les_fichiers(chemins)
        logger.info("Corbeille vidée : {} médias supprimés ({} fichiers en échec)",
                    supprimes, errors)
        return jsonify({
            "supprimes": supprimes,
            # Les identifiants détruits : la page les retire de sa grille et
            # de la pile de tri sans recharger, et sans garder de vignettes
            # mortes pointant vers des fichiers qui n'existent plus.
            "supprimes_ids": supprimes_ids,
            "errors": errors,
            "corbeille": _etat_corbeille(db),
        })
    except Exception as exc:
        db.rollback()
        logger.error("Vidage de la corbeille : {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()
