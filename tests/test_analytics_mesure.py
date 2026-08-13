"""
ANALYTICS — L'INVARIANT DE MESURE (« non mesuré n'est pas zéro »).

POURQUOI CE FICHIER EXISTE
──────────────────────────
Le classement des médias (`GET /api/analytics/media-performance`) repose tout
entier sur une distinction que rien ne protégeait : un post dont les
compteurs n'ont jamais été relevés n'est PAS un post à zéro. Le premier est
une absence de mesure, le second un échec mesuré. Les confondre transforme un
défaut de collecte en jugement sur le contenu du propriétaire.

Cette distinction ne vivait dans aucun test. Le seul défaut majeur trouvé sur
ce lot — un nombre de posts NÉGATIF affiché en clair (« 7 sur un instantané
et -1 sur les abonnés d'aujourd'hui ») — se nichait précisément dans le code
qui rédige la phrase d'honnêteté de l'écran, et la suite restait verte.

Les trois invariants verrouillés ici :

  1. un compteur jamais relevé sort à `null`, jamais à `0` ;
  2. un post non mesuré n'est jamais classé AU-DESSUS d'un post mesuré,
     fût-il mesuré à zéro — et il n'emprunte pas le rang d'un autre ;
  3. `snapshot_backed + current_fallback == rated` : la ventilation de la
     normalisation se compte sur sa propre population. C'est l'égalité que
     le bug négatif violait.

Ils sont écrits contre l'API HTTP, pas contre les fonctions internes : c'est
ce que l'écran consomme réellement.
"""

from __future__ import annotations

import json

from datetime import datetime


ROUTE = "/api/analytics/media-performance"

#: `media_performance` borne la période avec `datetime.now()` et plafonne
#: `days` à 365 (app/analytics/api.py:37-48). Un horodatage figé comme
#: `FIXED_NOW` tomberait donc TOUJOURS hors période et le classement
#: reviendrait vide, quel que soit `days`. Les dates de ces tests sont
#: donc dérivées de la même horloge que l'endpoint — c'est sa conception,
#: pas une facilité de test.
def _il_y_a(heures: int) -> int:
    return int(datetime.now().timestamp()) - heures * 3600


def _appeler(client, **params):
    params.setdefault("days", 30)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    reponse = client.get(f"{ROUTE}?{query}")
    assert reponse.status_code == 200, reponse.data
    return json.loads(reponse.data)


def _par_post(charge, post_id):
    for item in charge["items"]:
        if item["post_id"] == post_id:
            return item
    raise AssertionError(
        f"post {post_id} absent du classement : {[i['post_id'] for i in charge['items']]}"
    )


def test_un_compteur_jamais_releve_sort_a_null_et_jamais_a_zero(
    client, make_profile, make_media_item
):
    """L'absence de mesure doit traverser l'API sans se faire arrondir à 0."""
    profil = make_profile(platform="instagram", username="mesure_absente")
    make_media_item(
        profil,
        post_id="JAMAIS_RELEVE",
        ig_like_count=None,
        ig_comment_count=None,
        ig_view_count=None,
        posted_at=_il_y_a(1),
    )

    item = _par_post(_appeler(client, profile_id=profil.id), "JAMAIS_RELEVE")

    assert item["likes"] is None
    assert item["comments"] is None
    assert item["views"] is None
    assert item["engagement"] is None
    assert item["measured"] is False
    # Et l'écran doit pouvoir DIRE ce qui manque, pas seulement l'ignorer.
    assert set(item["missing"]) >= {"likes", "comments"}


def test_un_zero_mesure_reste_un_zero_et_ne_se_confond_pas_avec_labsence(
    client, make_profile, make_media_item
):
    """Un post réellement relevé à 0 est mesuré : il porte 0, pas null."""
    profil = make_profile(platform="instagram", username="zero_mesure")
    make_media_item(
        profil,
        post_id="ZERO_MESURE",
        ig_like_count=0,
        ig_comment_count=0,
        ig_view_count=0,
        posted_at=_il_y_a(1),
    )

    item = _par_post(_appeler(client, profile_id=profil.id), "ZERO_MESURE")

    assert item["likes"] == 0
    assert item["comments"] == 0
    assert item["engagement"] == 0
    assert item["measured"] is True
    assert item["missing"] == []


def test_un_post_non_mesure_ne_se_classe_jamais_au_dessus_dun_post_mesure_a_zero(
    client, make_profile, make_media_item
):
    """
    Le piège exact que le tri doit éviter.

    Si quelqu'un « simplifie » le tri en `b.engagement - a.engagement`, les
    `null` redeviennent des zéros : le post non mesuré remonte au niveau du
    post mesuré à zéro, et l'absence de collecte se lit comme un échec de
    contenu. On vérifie donc l'ordre, pas seulement les valeurs.
    """
    profil = make_profile(platform="instagram", username="ordre")
    make_media_item(
        profil, post_id="FORT", ig_like_count=500, ig_comment_count=10,
        ig_view_count=9000, posted_at=_il_y_a(1),
    )
    make_media_item(
        profil, post_id="ZERO", ig_like_count=0, ig_comment_count=0,
        ig_view_count=0, posted_at=_il_y_a(2),
    )
    make_media_item(
        profil, post_id="INCONNU", ig_like_count=None, ig_comment_count=None,
        ig_view_count=None, posted_at=_il_y_a(0),
    )

    charge = _appeler(client, profile_id=profil.id)
    ordre = [it["post_id"] for it in charge["items"]]

    assert ordre.index("FORT") < ordre.index("ZERO"), (
        "le post le plus engageant doit rester en tête"
    )
    assert ordre.index("ZERO") < ordre.index("INCONNU"), (
        "un ZÉRO MESURÉ se classe AU-DESSUS d'un post non mesuré : le second "
        "n'est pas un mauvais post, c'est un post qu'on n'a pas mesuré. "
        f"Ordre obtenu : {ordre}"
    )
    # Et le non mesuré est bien compté comme tel, malgré sa date récente.
    assert charge["counts"]["measured"] == 2
    assert charge["counts"]["unmeasured"] == 1


def test_la_ventilation_de_la_normalisation_se_compte_sur_sa_propre_population(
    client, db_session, make_profile, make_media_item
):
    """
    `snapshot_backed + current_fallback == rated`, toujours.

    C'est l'égalité que le bug du compte NÉGATIF violait : un post non mesuré
    porte une base d'abonnés (elle est connue) sans porter de taux
    (l'engagement, lui, manque). Le compter dans `snapshot_backed` gonflait
    celui-ci au-delà de `rated` et rendait `current_fallback` négatif — un
    nombre de posts négatif, dans la phrase même qui explique la mesure.
    """
    from app.db import ProfileSnapshot

    profil = make_profile(platform="instagram", username="normalisation")
    # Un instantané d'abonnés ANTÉRIEUR aux posts : sans lui, tous les
    # posts retomberaient sur les abonnés du jour et la branche fautive
    # ne serait jamais exercée — le test n'aurait aucune prise.
    db_session.add(ProfileSnapshot(
        profile_id=profil.id, followers_count=20_000,
        following_count=100, media_count=10, snapshot_at=_il_y_a(48),
    ))
    db_session.commit()

    # Un post mesuré : il aura un taux, adossé à l'instantané.
    make_media_item(
        profil, post_id="AVEC_TAUX", ig_like_count=100, ig_comment_count=5,
        ig_view_count=1000, posted_at=_il_y_a(1),
    )
    # Deux posts NON mesurés : ils portent la MÊME base d'abonnés (elle est
    # connue) sans porter de taux (l'engagement, lui, manque). C'est la
    # configuration exacte qui rendait `current_fallback` négatif.
    for suffixe in ("SANS_1", "SANS_2"):
        make_media_item(
            profil, post_id=suffixe, ig_like_count=None,
            ig_comment_count=None, ig_view_count=None,
            posted_at=_il_y_a(1),
        )

    charge = _appeler(client, profile_id=profil.id)
    norm = charge["normalization"]
    notes = charge["counts"]["rated"]

    assert norm["snapshot_backed"] >= 0, "un nombre de posts ne peut être négatif"
    assert norm["current_fallback"] >= 0, "un nombre de posts ne peut être négatif"
    assert norm["snapshot_backed"] + norm["current_fallback"] == notes, (
        "la ventilation doit se compter sur la même population que son total : "
        f"{norm['snapshot_backed']} + {norm['current_fallback']} != {notes}"
    )
    # Le taux n'existe que là où l'engagement existe.
    taux_non_nuls = [it for it in charge["items"] if it["rate"] is not None]
    assert len(taux_non_nuls) == notes
    for item in charge["items"]:
        if not item["measured"]:
            assert item["rate"] is None, (
                "un post non mesuré ne peut pas porter un taux d'engagement"
            )


def test_une_plateforme_sans_compteurs_est_declaree_non_mesurable(
    client, make_profile, make_media_item
):
    """
    Absence DÉFINITIVE contre retard de collecte : ce n'est pas la même chose.

    Aucun extracteur Twitter/TikTok/Reddit ne lit de compteur. Sur ces
    plateformes, relancer un scraping ne changerait rien — l'écran doit donc
    pouvoir le dire au lieu de proposer un geste inutile.
    """
    ig = make_profile(platform="instagram", username="ig_mesurable")
    tw = make_profile(platform="twitter", username="tw_non_mesurable")
    make_media_item(
        ig, post_id="IG", ig_like_count=None, ig_comment_count=None,
        ig_view_count=None, posted_at=_il_y_a(1),
    )
    make_media_item(
        tw, post_id="TW", ig_like_count=None, ig_comment_count=None,
        ig_view_count=None, posted_at=_il_y_a(1),
    )

    charge_ig = _appeler(client, profile_id=ig.id)
    charge_tw = _appeler(client, profile_id=tw.id)

    assert _par_post(charge_ig, "IG")["metrics_supported"] is True
    assert _par_post(charge_tw, "TW")["metrics_supported"] is False
    # Les deux sont non mesurés, mais un seul est mesurable un jour.
    assert charge_ig["counts"]["measurable"] == 1
    assert charge_tw["counts"]["measurable"] == 0
