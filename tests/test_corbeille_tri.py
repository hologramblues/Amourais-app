"""
La corbeille du tri rapide — « Passer » range, « Vider » détruit.

CE QUE CE MODULE PROTÈGE
------------------------
Deux gestes du même écran ont des conséquences opposées, et c'est
exactement ce qui rend l'endroit dangereux :

  - « Passer » se fait AU DOIGT, des dizaines de fois d'affilée, sans
    confirmation. Il ne doit RIEN détruire — ni la ligne, ni le fichier.
    Un jour où « Passer » supprimerait, le propriétaire perdrait une
    médiathèque en trente secondes de glissés, sans un seul dialogue.
  - « Vider » détruit pour de bon, fichiers compris. Il doit détruire
    EXACTEMENT le contenu de la corbeille : ni les médias rangés
    ailleurs, ni ceux qu'une requête forgée nommerait dans le corps.

§1 fige la non-destruction du geste courant. §2 fige le périmètre du
geste destructeur — dont le cas qui compte : le serveur ignore toute
liste d'identifiants envoyée par la page. §3 relit le code de l'écran :
le glissé « passer » doit appeler la corbeille, et « Annuler » doit l'en
ressortir, sinon un média repris puis gardé resterait condamné.

CE QU'IL NE PROTÈGE PAS
-----------------------
Aucun JavaScript n'est exécuté ici. Le glissé réel, le dialogue de
confirmation et le retrait des vignettes après vidage ont été vérifiés au
navigateur. §3 ne fige que les appels qui rendent ces comportements
possibles.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.db import Collection, CollectionItem, MediaItem
from app.web import viewer_api

RACINE = Path(__file__).resolve().parent.parent
VIEWER_JS = RACINE / "app" / "web" / "static" / "viewer.js"
VIEWER_HTML = RACINE / "app" / "web" / "templates" / "viewer.html"

API = "/api/viewer"


def _poser(tmp_path, nom: str) -> Path:
    chemin = tmp_path / nom
    chemin.write_bytes(b"contenu du media")
    return chemin


def _jeter(client, *ids):
    return client.post(
        f"{API}/corbeille/items",
        data=json.dumps({"ids": list(ids)}),
        content_type="application/json",
    )


# ===========================================================================
# §1. « Passer » range — il ne détruit rien
# ===========================================================================


def test_jeter_a_la_corbeille_ne_supprime_ni_la_ligne_ni_le_fichier(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    """Le geste le plus répété de l'écran est le seul à n'avoir aucun garde-fou."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    fichier = _poser(tmp_path, "passe.jpg")
    media = make_media_item(status="uploaded", local_path=str(fichier))

    reponse = _jeter(client, media.id)

    assert reponse.status_code == 200
    assert reponse.get_json()["ajoutes"] == 1
    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=media.id).first() is not None
    assert fichier.exists(), "« Passer » a effacé un fichier"


def test_la_corbeille_est_une_collection_ordinaire_visible_dans_la_liste(
    client, make_media_item
):
    """Elle doit s'ouvrir comme les autres : on regarde AVANT de vider."""
    media = make_media_item()
    _jeter(client, media.id)

    noms = [c["name"] for c in client.get(f"{API}/collections").get_json()["collections"]]
    assert "Corbeille" in noms


def test_deux_jets_du_meme_media_ne_font_pas_deux_lignes(client, db_session, make_media_item):
    media = make_media_item()
    _jeter(client, media.id)
    second = _jeter(client, media.id)

    assert second.get_json()["ajoutes"] == 0
    assert second.get_json()["corbeille"]["count"] == 1


def test_annuler_ressort_le_media_de_la_corbeille(client, make_media_item):
    """Sans ça, un média repris puis gardé resterait dans les condamnés."""
    media = make_media_item()
    _jeter(client, media.id)

    reponse = client.delete(
        f"{API}/corbeille/items",
        data=json.dumps({"ids": [media.id]}),
        content_type="application/json",
    )

    assert reponse.status_code == 200
    assert reponse.get_json()["retires"] == 1
    assert reponse.get_json()["corbeille"]["count"] == 0


def test_annuler_avant_toute_corbeille_repond_200_et_non_404(client, make_media_item):
    """Annuler ne doit jamais afficher d'erreur pour une collection absente."""
    media = make_media_item()

    reponse = client.delete(
        f"{API}/corbeille/items",
        data=json.dumps({"ids": [media.id]}),
        content_type="application/json",
    )

    assert reponse.status_code == 200
    assert reponse.get_json()["retires"] == 0


def test_le_compteur_signale_les_medias_ranges_aussi_ailleurs(client, make_media_item):
    """Le dialogue doit pouvoir prévenir AVANT de faire disparaître un média
    d'une collection que le propriétaire croyait sûre."""
    range_ailleurs = make_media_item(post_id="range")
    simple = make_media_item(post_id="simple")
    client.post(
        f"{API}/collections",
        data=json.dumps({"name": "Meilleures vannes", "ids": [range_ailleurs.id]}),
        content_type="application/json",
    )
    _jeter(client, range_ailleurs.id, simple.id)

    etat = client.get(f"{API}/corbeille").get_json()
    assert etat["count"] == 2
    assert etat["aussi_ailleurs"] == 1


# ===========================================================================
# §2. « Vider » détruit — mais rien d'autre que la corbeille
# ===========================================================================


def test_vider_supprime_la_ligne_et_le_fichier_des_medias_jetes(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    fichier = _poser(tmp_path, "jete.jpg")
    media = make_media_item(status="uploaded", local_path=str(fichier))
    # L'identifiant est relevé MAINTENANT : après le vidage, relire
    # `media.id` ferait repartir SQLAlchemy chercher une ligne détruite.
    media_id = media.id
    _jeter(client, media_id)

    reponse = client.post(f"{API}/corbeille/vider")

    assert reponse.status_code == 200
    corps = reponse.get_json()
    assert corps["supprimes"] == 1
    assert corps["supprimes_ids"] == [media_id]
    assert corps["corbeille"]["count"] == 0
    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=media_id).first() is None
    assert not fichier.exists()


def test_vider_ne_touche_pas_aux_medias_qui_ne_sont_pas_dans_la_corbeille(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    """LE test de périmètre : un média jamais passé doit survivre intact."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    garde_fichier = _poser(tmp_path, "garde.jpg")
    garde = make_media_item(post_id="garde", status="uploaded",
                            local_path=str(garde_fichier))
    jete = make_media_item(post_id="jete", status="uploaded",
                           local_path=str(_poser(tmp_path, "jete.jpg")))
    garde_id, jete_id = garde.id, jete.id
    _jeter(client, jete_id)

    client.post(f"{API}/corbeille/vider")

    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=garde_id).first() is not None
    assert garde_fichier.exists(), "un média jamais passé a été détruit"
    assert db_session.query(MediaItem).filter_by(id=jete_id).first() is None


def test_vider_ignore_les_identifiants_envoyes_par_le_client(
    client, db_session, make_media_item, monkeypatch, tmp_path
):
    """Le serveur supprime CE QU'IL A dans la corbeille, jamais ce qu'on lui
    nomme : sinon un bug d'affichage — ou une requête forgée — détruirait
    des médias que le propriétaire n'a jamais jetés."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    convoite_fichier = _poser(tmp_path, "convoite.jpg")
    convoite = make_media_item(post_id="convoite", status="uploaded",
                               local_path=str(convoite_fichier))
    jete = make_media_item(post_id="jete", status="uploaded",
                           local_path=str(_poser(tmp_path, "jete.jpg")))
    _jeter(client, jete.id)

    reponse = client.post(
        f"{API}/corbeille/vider",
        data=json.dumps({"ids": [convoite.id]}),
        content_type="application/json",
    )

    assert reponse.get_json()["supprimes_ids"] == [jete.id]
    db_session.expire_all()
    assert db_session.query(MediaItem).filter_by(id=convoite.id).first() is not None
    assert convoite_fichier.exists()


def test_vider_une_corbeille_absente_repond_zero_sans_erreur(client):
    reponse = client.post(f"{API}/corbeille/vider")

    assert reponse.status_code == 200
    assert reponse.get_json()["supprimes"] == 0


def test_un_commit_rate_pendant_le_vidage_ne_detruit_aucun_fichier(
    client, make_media_item, monkeypatch, tmp_path
):
    """Base d'abord, disque ensuite : un commit refusé doit tout laisser en
    place. L'ordre inverse laisserait des lignes pointant vers du néant."""
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    fichier = _poser(tmp_path, "precieux.jpg")
    media = make_media_item(status="uploaded", local_path=str(fichier))
    _jeter(client, media.id)

    vraie_fabrique = viewer_api.SessionLocal

    class SessionQuiRefuseDeCommiter:
        def __init__(self, inner):
            self._inner = inner
            self._commits = 0

        def __getattr__(self, nom):
            return getattr(self._inner, nom)

        def commit(self):
            raise RuntimeError("base verrouillée")

    monkeypatch.setattr(
        viewer_api, "SessionLocal",
        lambda: SessionQuiRefuseDeCommiter(vraie_fabrique()),
    )

    reponse = client.post(f"{API}/corbeille/vider")

    assert reponse.status_code == 500
    assert fichier.exists(), "fichier effacé alors que le commit a échoué"


def test_vider_efface_aussi_la_vignette(
    client, make_media_item, monkeypatch, tmp_path
):
    monkeypatch.setattr(viewer_api, "DOWNLOAD_DIR", tmp_path)
    fichier = _poser(tmp_path, "avec-vignette.jpg")
    vignettes = tmp_path / ".thumbs"
    vignettes.mkdir()
    vignette = vignettes / "avec-vignette.jpg"
    vignette.write_bytes(b"vignette")
    media = make_media_item(status="uploaded", local_path=str(fichier))
    _jeter(client, media.id)

    client.post(f"{API}/corbeille/vider")

    assert not fichier.exists()
    assert not vignette.exists()


def test_la_corbeille_survit_au_vidage_et_reste_reutilisable(
    client, db_session, make_media_item
):
    """Vider ne supprime pas la collection : le tri suivant y dépose encore."""
    premier = make_media_item(post_id="a")
    _jeter(client, premier.id)  # id lu AVANT le vidage : la ligne va disparaître
    client.post(f"{API}/corbeille/vider")

    # La session du test garde l'instance détruite en mémoire ; SQLite
    # réattribue son identifiant au média suivant, et les deux se
    # télescopent au flush. On la vide : artefact de banc d'essai, pas du
    # code de la corbeille.
    db_session.expunge_all()
    second = make_media_item(post_id="b")
    reponse = _jeter(client, second.id)

    assert reponse.get_json()["corbeille"]["count"] == 1
    db_session.expire_all()
    corbeilles = db_session.query(Collection).filter(Collection.name == "Corbeille").all()
    assert len(corbeilles) == 1, "une seconde corbeille a été créée"


# ===========================================================================
# §3. L'écran de tri appelle bien la corbeille
# ===========================================================================


def _bloc(source: str, entete: str) -> str:
    """Le corps d'une fonction, de sa signature à la fonction suivante."""
    debut = source.index(entete)
    suite = source.find("\n  function ", debut + len(entete))
    return source[debut: suite if suite > 0 else len(source)]


def test_passer_appelle_la_corbeille_et_garder_ne_l_appelle_pas():
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function deciderTri(")

    assert "jeterALaCorbeille" in bloc, "« Passer » ne range plus rien"
    # L'appel doit être CONDITIONNÉ : sans la garde, garder un média le
    # mettrait à la corbeille — c'est-à-dire le condamnerait au vidage.
    garde = re.search(r'if \(action !== "keep"\) jeterALaCorbeille', bloc)
    assert garde, "l'appel n'est pas réservé au geste « passer »"


def test_annuler_ressort_le_media_dans_le_code_de_l_ecran():
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function annulerTri(")

    assert "ressortirDeLaCorbeille" in bloc
    assert re.search(r'if \(derniere && derniere\.action !== "keep"\)', bloc), (
        "annuler un « Garder » ne doit rien retirer de la corbeille"
    )


def test_le_vidage_ne_transmet_aucune_liste_d_identifiants():
    """Le pendant côté page du test de périmètre : la requête est nue."""
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function viderCorbeille(")

    envoi = re.search(r'envoyer\(API \+ "/corbeille/vider",\s*\{([^}]*)\}', bloc)
    assert envoi, "l'appel de vidage a changé de forme"
    assert "ids" not in envoi.group(1), "la page envoie une liste d'identifiants"


def test_le_vidage_passe_par_une_confirmation():
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function viderCorbeille(")

    assert "confirmer(" in bloc, "le seul geste destructeur de l'écran n'est plus confirmé"
    # Un confirmer() dont on IGNORE la réponse serait un décor : le dialogue
    # s'afficherait, un « Annuler » ne changerait rien, et le vidage partirait
    # quand même. Le garde-fou réel est le retour anticipé sur `ok`, entre le
    # dialogue et la requête — c'est LUI qu'on fige, dans cet ordre.
    reponse_lue = re.search(
        r"confirmer\(.*?\.then\(function \(ok\) \{\s*if \(!ok\) return;",
        bloc, re.S,
    )
    assert reponse_lue, "la réponse du dialogue n'arrête plus le vidage"
    assert reponse_lue.end() < bloc.index("/corbeille/vider"), (
        "le vidage part avant que la réponse du dialogue soit lue"
    )


def test_le_bouton_vider_existe_dans_le_gabarit():
    html = VIEWER_HTML.read_text(encoding="utf-8")

    assert 'id="btn-tri-corbeille"' in html
    assert 'id="tri-corbeille-n"' in html
    # Masqué au départ : un bouton destructeur ne s'affiche pas quand il n'a
    # rien à détruire.
    bouton = html[html.index('id="btn-tri-corbeille"'):]
    bouton = bouton[:bouton.index(">")]
    assert "hidden" in bouton


def test_la_ligne_corbeille_n_offre_que_le_vidage():
    """Renommer la corbeille en ferait perdre la trace au tri ; la
    « supprimer » relâcherait son contenu sans rien effacer. Ces deux
    actions mentent : la ligne n'en propose qu'une, vider."""
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function rendreCollections(")

    cas = re.search(
        r'if \(c\.name\.toLowerCase\(\) === "corbeille"\) \{(.*?)\n      \}',
        bloc, re.S,
    )
    assert cas, "la Corbeille n'est plus traitée à part dans la barre latérale"
    corps = cas.group(1)
    assert "viderCorbeille" in corps
    assert "renommerCollection" not in corps
    assert "supprimerCollection" not in corps
    # Le cas doit SORTIR : sans le return, la ligne recevrait en plus les
    # deux actions ordinaires juste en dessous.
    assert "return;" in corps, "le cas Corbeille retombe sur les actions ordinaires"


def test_le_vidage_depuis_la_barre_laterale_relit_le_compteur_avant_le_dialogue():
    """Hors du tri, rien n'a chargé la corbeille : sans cette relecture, le
    dialogue annoncerait zéro média et le vidage ne partirait jamais."""
    js = VIEWER_JS.read_text(encoding="utf-8")
    bloc = _bloc(js, "function rendreCollections(")

    assert re.search(r"chargerCorbeille\(\)\.then\(viderCorbeille\)", bloc), (
        "le dialogue de la barre latérale part sur un compteur non relu"
    )
