"""
LOT A — DOUBLONS (V27/V28/V29) et COLLECTIONS MANUELLES (V20).

Ce module couvre ce qu'une vérification au navigateur ne peut PAS prouver :

  * l'ordre « base d'abord, disque ensuite » des deux chemins de suppression.
    Un commit refusé ne doit laisser AUCUN fichier détruit. On ne peut pas
    provoquer un commit raté à la souris ;
  * la convergence de la boucle d'empreintes sur PLUS d'un lot (la
    bibliothèque réelle en tient un seul) ;
  * la garantie du bucketing 16 bits contre une comparaison exhaustive, sur
    un volume qu'aucun écran ne montrera jamais en entier ;
  * la multi-appartenance d'un média, et le fait que supprimer une
    collection ne supprime aucun média.

Rien ici ne touche `data/` : le socle de `conftest.py` isole DATA_DIR.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from app.db import (
    Collection,
    CollectionItem,
    MediaComment,
    MediaItem,
    MediaRating,
)
from app.scraper import pipeline
from app.web import viewer_api


# ===========================================================================
# 1. Empreintes — md5 et dhash
# ===========================================================================

def _fabriquer_image(chemin, motif: str, taille: str = "64x64"):
    """Une petite image réelle produite par ffmpeg — aucune dépendance ajoutée.

    `motif` est une source lavfi complète, p. ex. `testsrc` ou `color=c=black`.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"{motif}:size={taille}:duration=1:rate=1"
         if "=" in motif else f"{motif}=size={taille}:duration=1:rate=1",
         "-frames:v", "1", str(chemin)],
        check=True,
    )
    return chemin


@pytest.fixture
def image_png(tmp_path):
    def _fabriquer(nom: str, motif: str, taille: str = "64x64"):
        return _fabriquer_image(tmp_path / nom, motif, taille)
    return _fabriquer


def test_md5_distingue_deux_fichiers_et_reconnait_une_copie(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    copie = tmp_path / "copie.bin"
    a.write_bytes(b"x" * 5000)
    b.write_bytes(b"x" * 4999 + b"y")  # UN octet d'écart
    copie.write_bytes(a.read_bytes())

    assert pipeline.md5_fichier(a) == pipeline.md5_fichier(copie)
    assert pipeline.md5_fichier(a) != pipeline.md5_fichier(b)


def test_md5_dun_fichier_absent_ne_leve_pas(tmp_path):
    assert pipeline.md5_fichier(tmp_path / "jamais_ecrit.bin") is None


def test_le_dhash_fait_64_bits_et_est_stable(image_png):
    chemin = image_png("motif.png", "testsrc")
    premier = pipeline.phash_fichier(chemin)
    assert premier is not None
    assert len(premier) == 16          # 64 bits en hexadécimal
    int(premier, 16)                   # hexadécimal valide
    assert pipeline.phash_fichier(chemin) == premier  # déterministe


def test_une_image_unie_donne_une_empreinte_degeneree(image_png):
    """Une image sans contraste ne « ressemble » à rien : l'écran l'écarte.

    C'est la raison d'être de `PHASH_DEGENERE` — sans ce filtre, toutes les
    images unies formeraient un seul groupe géant et faux.
    """
    uni = image_png("uni.png", "color=c=black")
    assert pipeline.phash_fichier(uni) in pipeline.PHASH_DEGENERE


def test_un_reencodage_reste_proche_alors_que_le_md5_change(image_png, tmp_path):
    """Le cas d'usage entier : même image, autre fichier."""
    source = image_png("src.png", "testsrc", taille="320x240")
    variante = tmp_path / "variante.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-q:v", "12", str(variante)],
        check=True,
    )

    assert pipeline.md5_fichier(source) != pipeline.md5_fichier(variante)

    a = int(pipeline.phash_fichier(source), 16)
    b = int(pipeline.phash_fichier(variante), 16)
    distance = bin(a ^ b).count("1")
    assert distance <= 6, f"un simple ré-encodage a déplacé {distance} bits sur 64"


def test_empreintes_dun_chemin_inexistant_rend_deux_none(tmp_path):
    assert pipeline.empreintes(tmp_path / "nulle_part.jpg") == (None, None)
    assert pipeline.empreintes(None) == (None, None)


# ===========================================================================
# 2. Recherche de paires — exhaustif vs bucketing
# ===========================================================================

def _distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def test_sous_le_seuil_la_comparaison_est_exhaustive():
    empreintes = [(1, 0b1010), (2, 0b1011), (3, 0xFFFF_FFFF_FFFF_FFFF)]
    paires, exhaustif = viewer_api._paires_similaires(empreintes, 4)

    assert exhaustif is True
    trouvees = {(a, b): d for a, b, d in paires}
    assert trouvees[(1, 2)] == 1
    assert (1, 3) not in trouvees  # 62 bits d'écart, hors seuil


def test_chaque_paire_porte_sa_distance_ce_qui_rend_le_seuil_reglable():
    """V28 : le curseur filtre des distances DÉJÀ calculées, il ne rescanne pas."""
    empreintes = [(1, 0), (2, 0b1), (3, 0b111), (4, 0b1111_1111)]
    paires, _ = viewer_api._paires_similaires(empreintes, 16)

    par_couple = {(a, b): d for a, b, d in paires}
    assert par_couple[(1, 2)] == 1
    assert par_couple[(1, 3)] == 3
    assert par_couple[(1, 4)] == 8
    # Resserrer à 2 bits ne demande que de filtrer cette liste, pas de
    # relancer quoi que ce soit.
    assert {c for c, d in par_couple.items() if d <= 2} == {(1, 2), (2, 3)}
    assert {c for c, d in par_couple.items() if d <= 0} == set()


def test_le_bucketing_ne_rate_aucune_paire_a_trois_bits_ou_moins(monkeypatch):
    """Principe des tiroirs : 4 tranches, au plus 3 bits déplacés → une tranche
    reste intacte, donc la paire est forcément candidate.

    On force le mode bucketing en abaissant le seuil d'exhaustivité, puis on
    compare son résultat à la vérité obtenue par force brute.
    """
    monkeypatch.setattr(viewer_api, "_SEUIL_EXHAUSTIF", 5)

    import random
    alea = random.Random(20260813)
    base = [alea.getrandbits(64) for _ in range(40)]
    # Des voisins à 1, 2 et 3 bits, qui DOIVENT être retrouvés.
    voisins = []
    for valeur in base[:10]:
        for nb_bits in (1, 2, 3):
            v = valeur
            for bit in alea.sample(range(64), nb_bits):
                v ^= 1 << bit
            voisins.append(v)
    empreintes = [(i, v) for i, v in enumerate(base + voisins)]

    paires, exhaustif = viewer_api._paires_similaires(empreintes, 3)
    assert exhaustif is False, "le test doit s'exécuter en mode bucketing"

    attendues = set()
    for i in range(len(empreintes)):
        for j in range(i + 1, len(empreintes)):
            if _distance(empreintes[i][1], empreintes[j][1]) <= 3:
                attendues.add((empreintes[i][0], empreintes[j][0]))

    obtenues = {(a, b) for a, b, _ in paires}
    assert attendues <= obtenues, "des paires à ≤ 3 bits ont été manquées"


# ===========================================================================
# 3. Les colonnes sont NULLABLES — l'app doit vivre sans empreintes
# ===========================================================================

def test_la_liste_des_medias_repond_sans_aucune_empreinte(client, make_media_item):
    """L'état de la base réelle du propriétaire : md5 et phash vides partout."""
    make_media_item(status="uploaded", local_path="/tmp/x.jpg")

    reponse = client.get("/api/viewer/media")
    assert reponse.status_code == 200
    assert reponse.get_json()["total"] == 1


def test_sans_empreinte_aucun_groupe_mais_un_compte_honnete(client, make_media_item):
    for i in range(3):
        make_media_item(post_id=f"p{i}", status="uploaded", local_path=f"/tmp/{i}.jpg")

    data = client.get("/api/viewer/duplicates/exact").get_json()
    assert data["groupes"] == []
    # Le nombre de médias non comparés est dit, pas caché.
    assert data["sans_empreinte"] == 3

    data = client.get("/api/viewer/duplicates/similar").get_json()
    assert data["paires"] == []
    assert data["sans_empreinte"] == 3


def test_les_doublons_exacts_regroupent_par_md5(client, make_media_item):
    make_media_item(post_id="a", status="uploaded", local_path="/tmp/a.jpg",
                    md5="aa" * 16, file_size=1000)
    make_media_item(post_id="b", status="uploaded", local_path="/tmp/b.jpg",
                    md5="aa" * 16, file_size=1000)
    make_media_item(post_id="c", status="uploaded", local_path="/tmp/c.jpg",
                    md5="bb" * 16, file_size=1000)

    data = client.get("/api/viewer/duplicates/exact").get_json()
    assert data["total_groupes"] == 1
    groupe = data["groupes"][0]
    assert len(groupe["items"]) == 2
    assert groupe["octets_recuperables"] == 1000
    # V27 : définition, poids et format présents pour CHAQUE candidat.
    for item in groupe["items"]:
        assert {"width", "height", "file_size", "format"} <= set(item)
        assert item["format"] == "JPG"


# ===========================================================================
# 4. Calcul différé — la boucle converge sur plusieurs lots
# ===========================================================================

def test_la_boucle_dempreintes_converge_sur_plusieurs_lots(
    client, make_media_item, monkeypatch, tmp_path
):
    """Plus de médias que la taille d'un lot : le client doit pouvoir boucler.

    C'est le cas qu'une bibliothèque de 18 fichiers ne produit jamais.
    """
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(viewer_api, "_LOT_EMPREINTES", 4)

    total = 10
    for i in range(total):
        _fabriquer_image(tmp_path / f"f{i}.png", "testsrc", taille=f"{32 + i}x32")
        make_media_item(post_id=f"p{i}", status="uploaded",
                        local_path=str(tmp_path / f"f{i}.png"))

    assert client.get("/api/viewer/fingerprints/status").get_json()["restants"] == total

    tours = 0
    while True:
        tours += 1
        assert tours < 20, "la boucle ne converge pas"
        data = client.post("/api/viewer/fingerprints/compute").get_json()
        if data["termine"] or data["restants"] == 0:
            break

    assert tours >= 3, "un seul lot aurait suffi : le test ne prouve rien"
    etat = client.get("/api/viewer/fingerprints/status").get_json()
    assert etat["md5"] == total
    assert etat["phash"] == total
    assert etat["restants"] == 0


def test_un_fichier_absent_ne_fait_pas_boucler_indefiniment(
    client, make_media_item, monkeypatch, tmp_path
):
    """Sans le drapeau `termine`, un fichier disparu relancerait le même lot."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    make_media_item(status="uploaded", local_path=str(tmp_path / "disparu.jpg"))

    data = client.post("/api/viewer/fingerprints/compute").get_json()
    assert data["traites"] == 0
    assert data["illisibles"] == 1
    assert data["termine"] is True
    assert data["restants"] == 1  # dit sans mentir : il reste non comparable


def test_un_fichier_lisible_mais_sans_image_arrete_aussi_la_boucle(
    client, make_media_item, monkeypatch, tmp_path
):
    """LE piège : md5 réussit, phash échoue, à CHAQUE passage.

    Compter les lignes touchées ferait boucler le client indéfiniment sur le
    même lot — la condition d'arrêt porte donc sur le progrès réel.
    """
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(viewer_api, "_LOT_EMPREINTES", 2)

    # Deux vraies images (qui, elles, aboutissent) et un fichier lisible dont
    # aucune image ne peut sortir : c'est lui qui piégeait la boucle.
    for i in range(2):
        _fabriquer_image(tmp_path / f"ok{i}.png", "testsrc", taille=f"{40 + i}x40")
        make_media_item(post_id=f"ok{i}", status="uploaded",
                        local_path=str(tmp_path / f"ok{i}.png"))
    (tmp_path / "pas_une_image.bin").write_bytes(b"ceci n'est pas une image" * 50)
    make_media_item(post_id="bin", status="uploaded",
                    local_path=str(tmp_path / "pas_une_image.bin"))

    tours = 0
    while True:
        tours += 1
        assert tours < 10, "la boucle ne converge pas sur le fichier inexploitable"
        data = client.post("/api/viewer/fingerprints/compute").get_json()
        if data["termine"] or data["restants"] == 0:
            break

    # La boucle s'arrête, et elle dit la vérité : un média reste hors
    # comparaison, avec son md5 mais sans empreinte visuelle.
    assert data["restants"] == 1
    etat = client.get("/api/viewer/fingerprints/status").get_json()
    assert etat["md5"] == 3
    assert etat["phash"] == 2


# ===========================================================================
# 5. LE POINT CRITIQUE — la base est committée AVANT le disque
# ===========================================================================

def _fichier_pose(tmp_path, nom: str):
    chemin = tmp_path / nom
    chemin.write_bytes(b"contenu du media")
    return chemin


def test_un_commit_rate_ne_detruit_aucun_fichier_en_suppression_par_lot(
    client, make_media_item, monkeypatch, tmp_path
):
    """Risque de PERTE DE DONNÉES : l'ordre inverse effaçait le fichier puis
    échouait au commit, laissant une ligne qui pointe vers du néant."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    fichier = _fichier_pose(tmp_path, "precieux.jpg")
    media = make_media_item(status="uploaded", local_path=str(fichier))

    vrai_commit = viewer_api.SessionLocal

    class SessionQuiRefuseDeCommiter:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, nom):
            return getattr(self._inner, nom)

        def commit(self):
            raise RuntimeError("base verrouillée")

    monkeypatch.setattr(
        viewer_api, "SessionLocal",
        lambda: SessionQuiRefuseDeCommiter(vrai_commit()),
    )

    reponse = client.delete(
        "/api/viewer/media/batch",
        data=json.dumps({"ids": [media.id]}),
        content_type="application/json",
    )

    assert reponse.status_code == 500
    assert fichier.exists(), "le fichier a été effacé alors que le commit a échoué"


def test_un_commit_rate_ne_detruit_aucun_fichier_en_deduplication(
    client, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde = make_media_item(post_id="garde", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "garde.jpg")),
                            md5="cc" * 16)
    doublon_fichier = _fichier_pose(tmp_path, "doublon.jpg")
    doublon = make_media_item(post_id="doublon", status="uploaded",
                              local_path=str(doublon_fichier), md5="cc" * 16)

    vrai = viewer_api.SessionLocal

    class SessionQuiRefuseDeCommiter:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, nom):
            return getattr(self._inner, nom)

        def commit(self):
            raise RuntimeError("disque plein")

    monkeypatch.setattr(
        viewer_api, "SessionLocal", lambda: SessionQuiRefuseDeCommiter(vrai())
    )

    reponse = client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps({
            "keep_id": garde.id,
            "remove_ids": [doublon.id],
            "conserver": {"notes": True, "commentaires": True, "collections": True},
        }),
        content_type="application/json",
    )

    assert reponse.status_code == 500
    assert doublon_fichier.exists(), "fichier détruit malgré un commit refusé"


def test_la_deduplication_efface_bien_le_fichier_quand_tout_va_bien(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde_fichier = _fichier_pose(tmp_path, "garde.jpg")
    perdu_fichier = _fichier_pose(tmp_path, "perdu.jpg")
    garde_id = make_media_item(post_id="g", status="uploaded",
                               local_path=str(garde_fichier)).id
    # L'identifiant est lu AVANT la suppression : après, l'instance ORM est
    # détachée et le moindre accès à un attribut relancerait un SELECT.
    perdu_id = make_media_item(post_id="p", status="uploaded",
                               local_path=str(perdu_fichier)).id

    reponse = client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps({
            "keep_id": garde_id,
            "remove_ids": [perdu_id],
            "conserver": {"notes": False, "commentaires": False, "collections": False},
        }),
        content_type="application/json",
    )

    assert reponse.status_code == 200
    assert reponse.get_json()["supprimes"] == 1
    assert garde_fichier.exists()
    assert not perdu_fichier.exists()
    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=perdu_id).first() is None


# ===========================================================================
# 6. V29 — rien ne part sans dire QUOI garder et QUOI reprendre
# ===========================================================================

@pytest.mark.parametrize("corps", [
    {"remove_ids": [1], "conserver": {}},                 # pas d'exemplaire gardé
    {"keep_id": 1, "conserver": {}},                      # rien à supprimer
    {"keep_id": 1, "remove_ids": [2]},                    # métadonnées non tranchées
])
def test_la_deduplication_refuse_une_demande_incomplete(client, corps):
    reponse = client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps(corps), content_type="application/json",
    )
    assert reponse.status_code == 400


def test_les_metadonnees_demandees_rejoignent_lexemplaire_garde(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde = make_media_item(post_id="g", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "g.jpg")))
    perdu = make_media_item(post_id="p", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "p.jpg")))

    db_session.add(MediaComment(media_item_id=perdu.id, user_name="Jérémie",
                                comment_text="à ne pas perdre"))
    db_session.add(MediaRating(media_item_id=perdu.id, user_name="Jérémie", rating=5))
    collection = Collection(name="Références")
    db_session.add(collection)
    db_session.commit()
    db_session.add(CollectionItem(collection_id=collection.id, media_item_id=perdu.id))
    db_session.commit()

    reponse = client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps({
            "keep_id": garde.id,
            "remove_ids": [perdu.id],
            "conserver": {"notes": True, "commentaires": True, "collections": True},
        }),
        content_type="application/json",
    )

    assert reponse.get_json()["transferts"] == {
        "commentaires": 1, "notes": 1, "collections": 1,
    }
    db_session.expire_all()
    assert db_session.query(MediaComment).filter_by(media_item_id=garde.id).count() == 1
    assert db_session.query(MediaRating).filter_by(media_item_id=garde.id).count() == 1
    assert db_session.query(CollectionItem).filter_by(media_item_id=garde.id).count() == 1


def test_une_note_deja_posee_sur_lexemplaire_garde_nest_pas_ecrasee(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    """La contrainte d'unicité (média, pseudo) interdit deux notes : c'est
    celle de l'exemplaire GARDÉ qui fait foi."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde = make_media_item(post_id="g", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "g.jpg")))
    perdu = make_media_item(post_id="p", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "p.jpg")))
    db_session.add(MediaRating(media_item_id=garde.id, user_name="Jérémie", rating=4))
    db_session.add(MediaRating(media_item_id=perdu.id, user_name="Jérémie", rating=1))
    db_session.commit()

    reponse = client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps({
            "keep_id": garde.id, "remove_ids": [perdu.id],
            "conserver": {"notes": True, "commentaires": False, "collections": False},
        }),
        content_type="application/json",
    )

    assert reponse.get_json()["transferts"]["notes"] == 0
    db_session.expire_all()
    notes = db_session.query(MediaRating).filter_by(media_item_id=garde.id).all()
    assert [n.rating for n in notes] == [4]


def test_les_metadonnees_non_demandees_partent_avec_le_doublon(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde = make_media_item(post_id="g", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "g.jpg")))
    perdu = make_media_item(post_id="p", status="uploaded",
                            local_path=str(_fichier_pose(tmp_path, "p.jpg")))
    db_session.add(MediaComment(media_item_id=perdu.id, user_name="X", comment_text="jetable"))
    db_session.commit()

    client.post(
        "/api/viewer/duplicates/resolve",
        data=json.dumps({
            "keep_id": garde.id, "remove_ids": [perdu.id],
            "conserver": {"notes": False, "commentaires": False, "collections": False},
        }),
        content_type="application/json",
    )

    db_session.expire_all()
    assert db_session.query(MediaComment).count() == 0


# ===========================================================================
# 7. Collections (V20)
# ===========================================================================

def test_un_media_appartient_a_plusieurs_collections_simultanement(
    client, make_media_item
):
    media = make_media_item(status="uploaded", local_path="/tmp/x.jpg")
    ids = []
    for nom in ("Références", "À publier"):
        creation = client.post("/api/viewer/collections",
                               data=json.dumps({"name": nom}),
                               content_type="application/json")
        assert creation.status_code == 201
        ids.append(creation.get_json()["id"])

    for collection_id in ids:
        ajout = client.post(f"/api/viewer/collections/{collection_id}/items",
                            data=json.dumps({"ids": [media.id]}),
                            content_type="application/json")
        assert ajout.get_json()["ajoutes"] == 1

    fiche = client.get(f"/api/viewer/media/{media.id}").get_json()
    assert sorted(c["name"] for c in fiche["collections"]) == ["Références", "À publier"]


def test_ajouter_deux_fois_ne_cree_pas_de_doublon(client, make_media_item):
    media = make_media_item(status="uploaded", local_path="/tmp/x.jpg")
    cid = client.post("/api/viewer/collections", data=json.dumps({"name": "C"}),
                      content_type="application/json").get_json()["id"]

    premier = client.post(f"/api/viewer/collections/{cid}/items",
                          data=json.dumps({"ids": [media.id]}),
                          content_type="application/json").get_json()
    second = client.post(f"/api/viewer/collections/{cid}/items",
                         data=json.dumps({"ids": [media.id]}),
                         content_type="application/json").get_json()

    assert premier["ajoutes"] == 1
    assert second["ajoutes"] == 0
    assert second["deja_presents"] == 1
    assert second["collection"]["count"] == 1


def test_une_collection_regroupe_des_medias_de_profils_differents(
    client, make_profile, make_media_item
):
    """La raison d'être de V20 : les dossiers par profil ne peuvent pas le faire."""
    a = make_profile(platform="instagram", username="un")
    b = make_profile(platform="tiktok", username="deux")
    m1 = make_media_item(a, post_id="m1", status="uploaded", local_path="/tmp/1.jpg")
    m2 = make_media_item(b, post_id="m2", status="uploaded", local_path="/tmp/2.jpg")

    cid = client.post("/api/viewer/collections",
                      data=json.dumps({"name": "Transverse", "ids": [m1.id, m2.id]}),
                      content_type="application/json").get_json()["id"]

    liste = client.get(f"/api/viewer/media?collection={cid}").get_json()
    assert liste["total"] == 2
    assert {i["profile_id"] for i in liste["items"]} == {a.id, b.id}


def test_supprimer_une_collection_ne_supprime_aucun_media(
    client, db_session, make_media_item
):
    media = make_media_item(status="uploaded", local_path="/tmp/x.jpg")
    cid = client.post("/api/viewer/collections",
                      data=json.dumps({"name": "Jetable", "ids": [media.id]}),
                      content_type="application/json").get_json()["id"]

    reponse = client.delete(f"/api/viewer/collections/{cid}")
    assert reponse.status_code == 200
    assert reponse.get_json()["appartenances_retirees"] == 1

    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=media.id).first() is not None
    assert db_session.query(CollectionItem).count() == 0
    assert db_session.query(Collection).count() == 0


def test_supprimer_un_media_retire_ses_appartenances(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    media = make_media_item(status="uploaded", local_path=str(_fichier_pose(tmp_path, "m.jpg")))
    client.post("/api/viewer/collections",
                data=json.dumps({"name": "C", "ids": [media.id]}),
                content_type="application/json")

    client.delete("/api/viewer/media/batch", data=json.dumps({"ids": [media.id]}),
                  content_type="application/json")

    db_session.expire_all()
    assert db_session.query(CollectionItem).count() == 0
    assert db_session.query(Collection).count() == 1  # la collection, elle, reste


def test_un_nom_de_collection_est_unique_a_la_casse_pres(client):
    assert client.post("/api/viewer/collections", data=json.dumps({"name": "Refs"}),
                       content_type="application/json").status_code == 201
    conflit = client.post("/api/viewer/collections", data=json.dumps({"name": "refs"}),
                          content_type="application/json")
    assert conflit.status_code == 409


def test_un_nom_vide_est_refuse(client):
    reponse = client.post("/api/viewer/collections", data=json.dumps({"name": "   "}),
                          content_type="application/json")
    assert reponse.status_code == 400


def test_renommer_ne_touche_pas_les_appartenances(client, make_media_item):
    media = make_media_item(status="uploaded", local_path="/tmp/x.jpg")
    cid = client.post("/api/viewer/collections",
                      data=json.dumps({"name": "Avant", "ids": [media.id]}),
                      content_type="application/json").get_json()["id"]

    reponse = client.patch(f"/api/viewer/collections/{cid}",
                           data=json.dumps({"name": "Après"}),
                           content_type="application/json")
    assert reponse.status_code == 200
    assert reponse.get_json() == {**reponse.get_json(), "name": "Après", "count": 1}


def test_retirer_un_media_de_la_collection_le_laisse_en_bibliotheque(
    client, db_session, make_media_item
):
    media = make_media_item(status="uploaded", local_path="/tmp/x.jpg")
    cid = client.post("/api/viewer/collections",
                      data=json.dumps({"name": "C", "ids": [media.id]}),
                      content_type="application/json").get_json()["id"]

    reponse = client.delete(f"/api/viewer/collections/{cid}/items",
                            data=json.dumps({"ids": [media.id]}),
                            content_type="application/json")

    assert reponse.get_json()["retires"] == 1
    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=media.id).first() is not None


def test_le_filtre_collection_se_compte_dans_les_facettes(client, make_media_item):
    """V13 + V14 : la collection est un filtre comme un autre."""
    m1 = make_media_item(post_id="a", status="uploaded", local_path="/tmp/a.jpg")
    make_media_item(post_id="b", status="uploaded", local_path="/tmp/b.jpg")
    cid = client.post("/api/viewer/collections",
                      data=json.dumps({"name": "Une", "ids": [m1.id]}),
                      content_type="application/json").get_json()["id"]

    facettes = client.get("/api/viewer/facets").get_json()["facettes"]
    assert facettes["collection"] == [{"valeur": cid, "libelle": "Une", "compte": 1}]

    assert client.get(f"/api/viewer/media?collection={cid}").get_json()["total"] == 1
    assert client.get("/api/viewer/media").get_json()["total"] == 2
