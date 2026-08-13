"""
Analytics API blueprint — Instagram account stats for @samourais_.

Endpoints:
    GET /api/analytics/account-overview      — profile info, followers, engagement rate
    GET /api/analytics/follower-growth       — historical followers/following per day
    GET /api/analytics/engagement            — engagement rate per post over period
    GET /api/analytics/content-breakdown     — images vs videos distribution
    GET /api/analytics/best-posting-times    — hour distribution of original post times
    GET /api/analytics/top-posts             — top 10 posts by engagement (likes + comments)
    GET /api/analytics/posting-frequency     — posts per week
    GET /api/analytics/media-performance     — classement des médias scrapés par
                                               performance RÉELLE du post d'origine

All endpoints accept ?days=7|30|90 for period filtering.
"""

from __future__ import annotations

import re
import time
from bisect import bisect_right
from datetime import datetime, timedelta
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request
from loguru import logger
from sqlalchemy import func, case, desc, or_, Text

from app.db import (
    IgInsightSnapshot, MediaItem, Profile, ProfileSnapshot, SessionLocal,
)

analytics_api_bp = Blueprint("analytics_api", __name__)


def _days_param() -> int:
    """Read the ?days query param, default 30."""
    try:
        d = int(request.args.get("days", 30))
        return max(1, min(d, 365))
    except (ValueError, TypeError):
        return 30


def _cutoff_ts(days: int) -> int:
    """Return a unix timestamp for `days` ago."""
    return int((datetime.now() - timedelta(days=days)).timestamp())


_SAMOURAIS_USERNAME = "samourais_"


def analysable_profiles(db):
    """Profiles the Analytics screen can actually chart.

    Instagram first (that's where the engagement columns are fed), then
    the rest, so the default pick below lands on a useful account.
    """
    rows = db.query(Profile).order_by(Profile.platform != "instagram", Profile.id).all()
    return rows


def _get_main_profile(db):
    """Resolve which profile the Analytics screen is looking at.

    L'écran était câblé en dur sur `@samourais_`, un compte qui
    N'EXISTE PAS dans la base de production : au premier chargement sur
    les vraies données, les 9 endpoints répondaient tous 404 et l'écran
    n'affichait qu'un bandeau rouge et cinq tirets. Aucun chiffre réel
    n'était atteignable, quel que soit le compte réellement suivi.

    Ordre de résolution, du plus explicite au plus tolérant :
      1. `?profile_id=` — le sélecteur de compte de la barre du haut ;
      2. `@samourais_` s'il existe vraiment (compatibilité) ;
      3. le premier profil Instagram actif ;
      4. à défaut, le premier profil actif, quelle que soit la
         plateforme — mieux vaut un écran partiellement rempli qu'un
         écran vide.
    """
    raw = request.args.get("profile_id")
    if raw:
        try:
            wanted = db.query(Profile).filter(Profile.id == int(raw)).first()
            if wanted:
                return wanted
        except (ValueError, TypeError):
            pass

    legacy = (
        db.query(Profile)
        .filter(
            Profile.platform == "instagram",
            Profile.username == _SAMOURAIS_USERNAME,
        )
        .first()
    )
    if legacy:
        return legacy

    candidates = analysable_profiles(db)
    for p in candidates:
        if p.platform == "instagram" and p.is_active:
            return p
    for p in candidates:
        if p.is_active:
            return p
    return candidates[0] if candidates else None


def _no_profile_error(db):
    """Compose the 404 body: cause + corrective gesture (critère G27)."""
    known = analysable_profiles(db)
    if known:
        names = ", ".join(f"@{p.username}" for p in known[:5])
        msg = (
            "Aucun profil sélectionné pour les analytics. "
            f"Profils disponibles : {names}. "
            "Choisis-en un dans le sélecteur de compte, en haut de l'écran."
        )
    else:
        msg = (
            "Aucun profil suivi pour l'instant. "
            "Ajoute un compte dans Profils pour alimenter les analytics."
        )
    return jsonify({"error": msg}), 404


@analytics_api_bp.route("/analytics/profiles", methods=["GET"])
def analytics_profiles():
    """List the accounts the screen's selector can switch between."""
    db = SessionLocal()
    try:
        current = _get_main_profile(db)
        return jsonify({
            "profiles": [
                {
                    "id": p.id,
                    "username": p.username,
                    "platform": p.platform,
                    "isActive": bool(p.is_active),
                }
                for p in analysable_profiles(db)
            ],
            "currentId": current.id if current else None,
        })
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Account Overview
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/account-overview", methods=["GET"])
def account_overview():
    """Profile info: followers, following, bio, avatar, verified, posts, engagement rate."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        # Average engagement rate from posts in the period
        avg_likes = (
            db.query(func.avg(MediaItem.ig_like_count))
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_like_count.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .scalar() or 0
        )
        avg_comments = (
            db.query(func.avg(MediaItem.ig_comment_count))
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_comment_count.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .scalar() or 0
        )
        followers = profile.followers_count or 0
        engagement_rate = (
            round((avg_likes + avg_comments) / followers * 100, 2)
            if followers > 0 else 0
        )

        total_posts = (
            db.query(func.count(func.distinct(MediaItem.post_url)))
            .filter(MediaItem.profile_id == profile.id)
            .scalar() or 0
        )

        # Follower delta over the period
        oldest_snapshot = (
            db.query(ProfileSnapshot)
            .filter(
                ProfileSnapshot.profile_id == profile.id,
                ProfileSnapshot.snapshot_at >= cutoff,
            )
            .order_by(ProfileSnapshot.snapshot_at.asc())
            .first()
        )
        follower_delta = 0
        if oldest_snapshot and oldest_snapshot.followers_count and profile.followers_count:
            follower_delta = profile.followers_count - oldest_snapshot.followers_count

        return jsonify({
            "username": profile.username,
            "display_name": profile.display_name,
            "avatar_url": profile.avatar_url,
            "biography": profile.biography,
            "is_verified": profile.is_verified or False,
            "followers_count": profile.followers_count or 0,
            "following_count": profile.following_count or 0,
            "media_count": profile.media_count or 0,
            "total_posts_scraped": total_posts,
            "engagement_rate": engagement_rate,
            "avg_likes": round(avg_likes, 1),
            "avg_comments": round(avg_comments, 1),
            "follower_delta": follower_delta,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in account overview: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Follower Growth
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/follower-growth", methods=["GET"])
def follower_growth():
    """Historical followers/following per day from ProfileSnapshot."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        snapshots = (
            db.query(ProfileSnapshot)
            .filter(
                ProfileSnapshot.profile_id == profile.id,
                ProfileSnapshot.snapshot_at >= cutoff,
            )
            .order_by(ProfileSnapshot.snapshot_at.asc())
            .all()
        )

        labels = []
        followers = []
        following = []
        for s in snapshots:
            day = datetime.fromtimestamp(s.snapshot_at).strftime("%Y-%m-%d")
            labels.append(day)
            followers.append(s.followers_count or 0)
            following.append(s.following_count or 0)

        return jsonify({
            "labels": labels,
            "followers": followers,
            "following": following,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in follower growth: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Engagement per post
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/engagement", methods=["GET"])
def engagement():
    """Engagement (likes + comments) per post over the period."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        rows = (
            db.query(
                MediaItem.posted_at,
                MediaItem.ig_like_count,
                MediaItem.ig_comment_count,
                MediaItem.post_url,
            )
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
                MediaItem.posted_at >= cutoff,
                MediaItem.ig_like_count.isnot(None),
            )
            .order_by(MediaItem.posted_at.asc())
            .all()
        )

        # Deduplicate by post_url (carousel items share the same post)
        seen_urls = set()
        labels = []
        likes = []
        comments = []
        for posted_at, lc, cc, url in rows:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            day = datetime.fromtimestamp(posted_at).strftime("%d/%m")
            labels.append(day)
            likes.append(lc or 0)
            comments.append(cc or 0)

        return jsonify({
            "labels": labels,
            "likes": likes,
            "comments": comments,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in engagement: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Content Breakdown
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/content-breakdown", methods=["GET"])
def content_breakdown():
    """Distribution of images vs videos."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        rows = (
            db.query(MediaItem.media_type, func.count(MediaItem.id))
            .filter(MediaItem.profile_id == profile.id)
            .group_by(MediaItem.media_type)
            .all()
        )
        data = {media_type: count for media_type, count in rows}
        return jsonify(data)
    except Exception as exc:
        logger.exception("Error in content breakdown: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Best Posting Times
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/best-posting-times", methods=["GET"])
def best_posting_times():
    """Hour distribution of original post times."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        rows = (
            db.query(MediaItem.posted_at)
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
            )
            .all()
        )

        hours = [0] * 24
        for (ts,) in rows:
            try:
                h = datetime.fromtimestamp(ts).hour
                hours[h] += 1
            except (OSError, ValueError):
                pass

        labels = [f"{h:02d}h" for h in range(24)]
        return jsonify({
            "labels": labels,
            "data": hours,
        })
    except Exception as exc:
        logger.exception("Error in best posting times: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Top Posts
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/top-posts", methods=["GET"])
def top_posts():
    """Top 10 posts by engagement (likes + comments)."""
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        rows = (
            db.query(
                MediaItem.id,
                MediaItem.post_url,
                MediaItem.media_type,
                MediaItem.caption,
                MediaItem.ig_like_count,
                MediaItem.ig_comment_count,
                MediaItem.ig_view_count,
                MediaItem.posted_at,
                MediaItem.local_path,
            )
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.ig_like_count.isnot(None),
            )
            .order_by(desc(MediaItem.ig_like_count + func.coalesce(MediaItem.ig_comment_count, 0)))
            .limit(10)
            .all()
        )

        results = []
        seen_urls = set()
        for r in rows:
            if r.post_url in seen_urls:
                continue
            seen_urls.add(r.post_url)
            results.append({
                "id": r.id,
                "post_url": r.post_url,
                "media_type": r.media_type,
                "caption": (r.caption or "")[:100],
                "likes": r.ig_like_count or 0,
                "comments": r.ig_comment_count or 0,
                "views": r.ig_view_count,
                "posted_at": r.posted_at,
            })
        return jsonify(results)
    except Exception as exc:
        logger.exception("Error in top posts: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Posting Frequency
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/posting-frequency", methods=["GET"])
def posting_frequency():
    """Number of posts per week."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        rows = (
            db.query(MediaItem.posted_at, MediaItem.post_url)
            .filter(
                MediaItem.profile_id == profile.id,
                MediaItem.posted_at.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .order_by(MediaItem.posted_at.asc())
            .all()
        )

        # Deduplicate by post_url (carousel items)
        seen_urls = set()
        post_dates = []
        for ts, url in rows:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            post_dates.append(ts)

        # Bucket by week
        buckets: dict[str, int] = {}
        for ts in post_dates:
            try:
                dt = datetime.fromtimestamp(ts)
                # ISO week start (Monday)
                week_start = dt - timedelta(days=dt.weekday())
                week_label = week_start.strftime("%d/%m")
                buckets[week_label] = buckets.get(week_label, 0) + 1
            except (OSError, ValueError):
                pass

        labels = list(buckets.keys())
        data = list(buckets.values())

        return jsonify({
            "labels": labels,
            "data": data,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in posting frequency: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Reach & Impressions (from IG Graph API insights)
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/reach-impressions", methods=["GET"])
def reach_impressions():
    """Daily reach and impressions from IgInsightSnapshot."""
    days = _days_param()
    cutoff = _cutoff_ts(days)
    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        if not profile:
            return _no_profile_error(db)

        snapshots = (
            db.query(IgInsightSnapshot)
            .filter(
                IgInsightSnapshot.profile_id == profile.id,
                IgInsightSnapshot.snapshot_at >= cutoff,
            )
            .order_by(IgInsightSnapshot.snapshot_at.asc())
            .all()
        )

        labels = []
        reach = []
        impressions = []
        profile_views = []
        engaged = []

        for s in snapshots:
            day = datetime.fromtimestamp(s.snapshot_at).strftime("%Y-%m-%d")
            labels.append(day)
            reach.append(s.reach or 0)
            impressions.append(s.impressions or 0)
            profile_views.append(s.profile_views or 0)
            engaged.append(s.accounts_engaged or 0)

        return jsonify({
            "labels": labels,
            "reach": reach,
            "impressions": impressions,
            "profile_views": profile_views,
            "accounts_engaged": engaged,
            "days": days,
        })
    except Exception as exc:
        logger.exception("Error in reach-impressions: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# IG API Status
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/ig-api-status", methods=["GET"])
def ig_api_status():
    """Check if IG Graph API is configured and working."""
    from app.instagram_api import is_configured

    db = SessionLocal()
    try:
        profile = _get_main_profile(db)
        has_profile = profile is not None
        configured = is_configured()

        # Count insight snapshots
        snapshot_count = 0
        last_snapshot = None
        if profile:
            snapshot_count = (
                db.query(func.count(IgInsightSnapshot.id))
                .filter(IgInsightSnapshot.profile_id == profile.id)
                .scalar() or 0
            )
            latest = (
                db.query(IgInsightSnapshot)
                .filter(IgInsightSnapshot.profile_id == profile.id)
                .order_by(IgInsightSnapshot.snapshot_at.desc())
                .first()
            )
            if latest:
                last_snapshot = latest.snapshot_at

        return jsonify({
            "configured": configured,
            "has_profile": has_profile,
            "snapshot_count": snapshot_count,
            "last_snapshot": last_snapshot,
        })
    except Exception as exc:
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# Manual trigger for IG stats collection
# ──────────────────────────────────────────────────────────
@analytics_api_bp.route("/analytics/collect-now", methods=["POST"])
def collect_now():
    """Manually trigger an IG stats collection."""
    import threading
    from app.analytics.ig_collector import collect_ig_stats

    def _run():
        try:
            collect_ig_stats()
        except Exception as e:
            logger.error("Manual IG collection failed: {}", e)

    threading.Thread(target=_run, daemon=True, name="ig-manual").start()
    return jsonify({"ok": True, "message": "Collection lancee en arriere-plan"})


# ══════════════════════════════════════════════════════════════════════
# LOT B — Performance réelle des médias scrapés
# ----------------------------------------------------------------------
# Le principe qui gouverne tout cet écran vaut ici aussi, et c'est le
# point le plus délicat du lot : UN ZÉRO MESURÉ N'EST PAS UNE ABSENCE
# DE MESURE.  Les trois colonnes `ig_*` de `media_items` sont nullables
# et, sur la base de production d'aujourd'hui, elles sont TOUTES nulles :
# les médias présents ont été collectés avant que le pipeline ne
# persiste les compteurs (`pipeline.py:292-294`).  Un `COALESCE(x, 0)`
# rangerait donc les 16 médias au fond du classement comme s'ils
# avaient fait zéro like — un mensonge.
#
# Conséquence de conception, tenue de bout en bout :
#   * `likes`, `comments`, `views` valent `null` quand la mesure
#     n'existe pas, JAMAIS 0 ;
#   * `measured` sépare les deux populations, et le classement ne
#     numérote que la première ;
#   * `views_state` distingue en plus « non applicable » (une photo n'a
#     pas de vues) de « non mesuré » (une vidéo dont on n'a pas lu le
#     compteur).
#
# Déduplication : un carrousel produit N lignes `media_items` qui
# partagent le MÊME post d'origine, donc les mêmes compteurs.  Sans
# regroupement, un carrousel de dix images occuperait dix lignes du
# classement avec le score du post.  On regroupe par `post_url` (à
# défaut `post_id`) et on ne garde qu'un représentant.
# ══════════════════════════════════════════════════════════════════════

#: Plafond de lignes `media_items` lues pour bâtir un classement.  Les
#: carrousels multiplient les lignes par post : on lit large, puis on
#: regroupe, puis on tronque à `_PERF_POSTS_MAX` posts.
_PERF_ROWS_MAX = 4000

#: Nombre de posts renvoyés au plus.
_PERF_POSTS_DEFAUT = 500
_PERF_POSTS_MAX = 1000

#: Suffixe `_0`, `_1`… ajouté par l'extracteur Instagram aux enfants d'un
#: carrousel (`instagram.py` : `child_id = f"{post_id}_{idx}"`).
_SUFFIXE_ENFANT = re.compile(r"_\d+$")


def _thumb_url(local_path: str | None) -> str | None:
    """Vignette servie par `/media/thumb/<fichier>` (routes.py:272).

    Même convention que le viewer (`viewer_api.py:433-435`) : la
    miniature est dérivée du nom de fichier local, jamais de l'URL
    distante — une URL Instagram expire, un fichier local non.
    """
    if not local_path:
        return None
    nom = local_path.rsplit("/", 1)[-1]
    return f"/media/thumb/{nom}" if nom else None


def _historique_abonnes(db, profile_ids):
    """{profile_id: ([horodatages triés], [abonnés])} depuis ProfileSnapshot.

    Sert à rapporter l'engagement d'un post au nombre d'abonnés AU
    MOMENT où il a été publié, et non à celui d'aujourd'hui.  Sans
    instantané antérieur au post, la valeur du jour est utilisée mais
    l'item le déclare (`followers_basis: "current"`) : l'écran peut
    alors dire que le taux est approché, au lieu de le présenter comme
    exact.
    """
    index: dict[int, tuple[list[int], list[int]]] = {}
    if not profile_ids:
        return index
    rows = (
        db.query(
            ProfileSnapshot.profile_id,
            ProfileSnapshot.snapshot_at,
            ProfileSnapshot.followers_count,
        )
        .filter(
            ProfileSnapshot.profile_id.in_(profile_ids),
            ProfileSnapshot.followers_count.isnot(None),
        )
        .order_by(ProfileSnapshot.profile_id, ProfileSnapshot.snapshot_at.asc())
        .all()
    )
    for pid, ts, abo in rows:
        ts_liste, abo_liste = index.setdefault(pid, ([], []))
        ts_liste.append(ts)
        abo_liste.append(abo)
    return index


def _abonnes_au_post(index, profil, posted_at):
    """(abonnés, base) au moment du post — (None, None) si inconnu."""
    if posted_at is not None and profil is not None:
        paire = index.get(profil.id)
        if paire and paire[0]:
            i = bisect_right(paire[0], posted_at)
            if i > 0:
                return paire[1][i - 1], "snapshot"
    actuel = getattr(profil, "followers_count", None) if profil else None
    if actuel:
        return actuel, "current"
    return None, None


def _viewer_url(profil_id, post_id, enfants):
    """Lien vers le viewer, dont l'état de vue tient déjà dans l'URL.

    `?q=` cherche dans `caption` ET `post_id` (viewer_api.py:271-275) :
    filtrer sur l'identifiant du post ouvre le viewer exactement sur ce
    post — et, pour un carrousel, sur ses enfants, puisqu'on retire le
    suffixe `_N` avant de chercher.
    """
    if post_id is None:
        return None
    racine = _SUFFIXE_ENFANT.sub("", post_id) if enfants > 1 else post_id
    params = {"q": racine}
    if profil_id is not None:
        params["profile_id"] = profil_id
    return "/viewer?" + urlencode(params)


def _cle_post(post_url, post_id, profile_id):
    """Clé de regroupement d'un post : ses enfants la partagent."""
    if post_url:
        return f"u:{profile_id}:{post_url}"
    return f"i:{profile_id}:{_SUFFIXE_ENFANT.sub('', post_id or '')}"


def _max_mesure(a, b):
    """Maximum en ignorant les absences — `None` n'est pas zéro."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


@analytics_api_bp.route("/analytics/media-performance", methods=["GET"])
def media_performance():
    """Classement des médias scrapés par performance réelle du post d'origine.

    Paramètres :
        days       — période (partagé avec tout l'écran)
        profile_id — compte analysé
        scope      — `profile` (défaut) ou `all` (tous les comptes suivis)
        limit      — nombre de posts renvoyés au plus
    """
    days = _days_param()
    cutoff = _cutoff_ts(days)
    scope = "all" if request.args.get("scope") == "all" else "profile"
    try:
        limite = int(request.args.get("limit", _PERF_POSTS_DEFAUT))
    except (TypeError, ValueError):
        limite = _PERF_POSTS_DEFAUT
    limite = max(1, min(limite, _PERF_POSTS_MAX))

    db = SessionLocal()
    try:
        tous = analysable_profiles(db)
        if not tous:
            return _no_profile_error(db)

        if scope == "all":
            profils = tous
        else:
            courant = _get_main_profile(db)
            if not courant:
                return _no_profile_error(db)
            profils = [courant]

        par_id = {p.id: p for p in profils}
        ids = list(par_id.keys())

        base = db.query(MediaItem).filter(MediaItem.profile_id.in_(ids))

        lignes = (
            base.filter(
                MediaItem.posted_at.isnot(None),
                MediaItem.posted_at >= cutoff,
            )
            .order_by(MediaItem.posted_at.desc(), MediaItem.id.desc())
            .limit(_PERF_ROWS_MAX)
            .all()
        )
        lignes_tronquees = len(lignes) >= _PERF_ROWS_MAX

        # ── Regroupement par post d'origine ───────────────────────────
        groupes: dict[str, dict] = {}
        for m in lignes:
            cle = _cle_post(m.post_url, m.post_id, m.profile_id)
            g = groupes.get(cle)
            if g is None:
                g = groupes[cle] = {
                    "repr": m,
                    "enfants": 0,
                    "video": False,
                    "likes": None,
                    "comments": None,
                    "views": None,
                }
            g["enfants"] += 1
            if m.media_type == "video":
                g["video"] = True
            g["likes"] = _max_mesure(g["likes"], m.ig_like_count)
            g["comments"] = _max_mesure(g["comments"], m.ig_comment_count)
            g["views"] = _max_mesure(g["views"], m.ig_view_count)
            # Représentant : celui qui porte un fichier local (donc une
            # vignette), à défaut le plus ancien identifiant.
            actuel = g["repr"]
            if (m.local_path and not actuel.local_path) or (
                bool(m.local_path) == bool(actuel.local_path) and m.id < actuel.id
            ):
                g["repr"] = m

        index_abo = _historique_abonnes(db, ids)

        items = []
        for g in groupes.values():
            m = g["repr"]
            profil = par_id.get(m.profile_id)
            likes, comments, views = g["likes"], g["comments"], g["views"]

            mesure = likes is not None or comments is not None
            engagement = None
            if mesure:
                engagement = (likes or 0) + (comments or 0)

            if views is not None:
                etat_vues = "value"
            elif g["video"]:
                etat_vues = "unmeasured"
            else:
                etat_vues = "na"

            manquantes = []
            if likes is None:
                manquantes.append("likes")
            if comments is None:
                manquantes.append("comments")
            if etat_vues == "unmeasured":
                manquantes.append("views")

            abo, base_abo = _abonnes_au_post(index_abo, profil, m.posted_at)
            taux = None
            if engagement is not None and abo:
                taux = round(engagement / abo * 100, 3)

            items.append({
                "id": m.id,
                "post_id": m.post_id,
                "post_url": m.post_url,
                "platform": m.platform,
                "profile_id": m.profile_id,
                "profile_username": profil.username if profil else None,
                "media_type": "carousel" if g["enfants"] > 1 else m.media_type,
                "children": g["enfants"],
                "caption": (m.caption or "")[:160],
                "posted_at": m.posted_at,
                "thumb_url": _thumb_url(m.local_path),
                "viewer_url": _viewer_url(m.profile_id, m.post_id, g["enfants"]),
                "likes": likes,
                "comments": comments,
                "views": views,
                "views_state": etat_vues,
                "engagement": engagement,
                # Un seul des deux compteurs relevé : la somme existe,
                # mais elle est incomplète et doit se dire telle.
                "engagement_partial": bool(
                    mesure and (likes is None or comments is None)
                ),
                "measured": mesure,
                "missing": manquantes,
                # Deux chemins seulement alimentent `ig_*`, tous deux
                # Instagram : l'extracteur public (`instagram.py` →
                # `pipeline.py:292-294`) et la Graph API
                # (`ig_collector.collect_media_insights`). Les
                # extracteurs Twitter, TikTok et Reddit ne lisent AUCUN
                # compteur. Sur ces plateformes, l'absence n'est pas un
                # retard de collecte : elle est définitive en l'état, et
                # l'écran doit le dire au lieu de proposer un geste qui
                # ne changerait rien.
                "metrics_supported": m.platform == "instagram",
                "followers": abo,
                "followers_basis": base_abo,
                "rate": taux,
            })

        # Les mesurés d'abord, du plus engageant au moins engageant ; les
        # non mesurés ensuite, par date — ils ne sont PAS classés.
        items.sort(
            key=lambda it: (
                0 if it["measured"] else 1,
                -(it["engagement"] or 0) if it["measured"] else 0,
                -(it["posted_at"] or 0),
            )
        )
        posts_tronques = len(items) > limite
        items = items[:limite]

        # ── Compteurs de cadrage, hors période affichée ───────────────
        # La clé SQL reprend le regroupement Python : un post, pas une
        # ligne. Le profil en fait partie, sinon deux comptes ayant
        # aspiré le même post d'origine n'en compteraient qu'un.
        cle_post_sql = func.coalesce(MediaItem.post_url, MediaItem.post_id)
        mesure_sql = or_(
            MediaItem.ig_like_count.isnot(None),
            MediaItem.ig_comment_count.isnot(None),
        )
        mesures_hors_periode = (
            db.query(func.count(func.distinct(
                MediaItem.profile_id.cast(Text) + "|" + cle_post_sql
            )))
            .filter(MediaItem.profile_id.in_(ids), mesure_sql)
            .scalar() or 0
        )

        # « Sans date » se compte par POST, pas par ligne : un post dont
        # UNE ligne porte une date est daté. Compter les lignes nulles
        # ferait passer pour non datable un post parfaitement daté qui
        # traîne un doublon sans date — c'est exactement ce qu'on a
        # observé sur la copie de travail.
        sous_dates = (
            db.query(func.max(MediaItem.posted_at).label("d"))
            .filter(MediaItem.profile_id.in_(ids))
            .group_by(MediaItem.profile_id, cle_post_sql)
            .having(func.max(MediaItem.posted_at).is_(None))
            .subquery()
        )
        sans_date = db.query(func.count()).select_from(sous_dates).scalar() or 0
        medias_total = (
            db.query(func.count(MediaItem.id))
            .filter(MediaItem.profile_id.in_(ids))
            .scalar() or 0
        )

        mesures = sum(1 for it in items if it["measured"])
        mesurables = sum(1 for it in items if it["metrics_supported"])
        avec_taux = sum(1 for it in items if it["rate"] is not None)
        # Cette ventilation décrit les posts RÉELLEMENT dotés d'un taux :
        # elle doit donc se compter sur la même population qu'`avec_taux`.
        # Un post non mesuré peut porter une base d'abonnés (elle est
        # connue) sans porter de taux (l'engagement, lui, manque) : le
        # compter ici gonflait `snapshot_backed` au-delà de `rated` et
        # rendait `current_fallback` NÉGATIF — l'écran affichait alors
        # « 7 sur un instantané et -1 sur les abonnés d'aujourd'hui »
        # pour 6 posts. Un compte de posts ne peut pas être négatif.
        sur_instantane = sum(
            1 for it in items
            if it["rate"] is not None and it["followers_basis"] == "snapshot"
        )

        return jsonify({
            "scope": scope,
            "days": days,
            "cutoff": cutoff,
            "profiles": [
                {
                    "id": p.id,
                    "username": p.username,
                    "platform": p.platform,
                    "followers_count": p.followers_count,
                }
                for p in profils
            ],
            "items": items,
            "counts": {
                "posts": len(items),
                "measured": mesures,
                "unmeasured": len(items) - mesures,
                "measurable": mesurables,
                "rated": avec_taux,
                "measured_all_time": mesures_hors_periode,
                "undated": sans_date,
                "media_rows": medias_total,
            },
            "normalization": {
                # Sur quoi le taux est normalisé, dit explicitement :
                # sans cette phrase, comparer deux comptes d'audiences
                # différentes n'aurait aucun sens.
                "basis": "followers_at_post",
                "snapshot_backed": sur_instantane,
                "current_fallback": avec_taux - sur_instantane,
            },
            "truncated": bool(lignes_tronquees or posts_tronques),
        })
    except Exception as exc:
        logger.exception("Error in media performance: {}", exc)
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        db.close()
