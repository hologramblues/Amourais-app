"""
PIPELINE DE SCRAPE — `app/scraper/pipeline.py` (vague 0).

Le cœur du produit : extract -> insert -> download -> statut.

────────────────────────────────────────────────────────────────────────────
CE QUI EST SIMULÉ, ET POURQUOI C'EST SUFFISANT
────────────────────────────────────────────────────────────────────────────
Le pipeline n'a que **trois** points de contact avec le monde extérieur :

  * l'extracteur, résolu par `_get_extractor` via le dict de module
    `pipeline._EXTRACTORS` (`pipeline.py:31, 36-46`). Ce dict est peuplé
    PARESSEUSEMENT (`if not _EXTRACTORS:`) : le pré-remplir avec un stub
    court-circuite entièrement scrapling/patchright — aucun navigateur n'est
    jamais démarré (§7.2 AUDIT.md) ;
  * `download_media`, importé PAR VALEUR (`pipeline.py:26`) : on patche donc
    `pipeline.download_media`, pas `downloaders.download_media` (§7.1) ;
  * `get_proxy_for_platform`, que l'audit interdit de déclencher dans les
    tests (il fait `load_dotenv(..., override=True)`, `config.py:84-85`).

`STORAGE_MODE` et `_now_ts` sont neutralisés pour la même raison : constante
importée par valeur pour l'un, horloge murale pour l'autre (LOI n°5).

────────────────────────────────────────────────────────────────────────────
LE TEST CENTRAL DE TOUT LE CHANTIER
────────────────────────────────────────────────────────────────────────────
`test_scrape_sain_et_echec_total_de_fetch_doivent_etre_discernables` :
aujourd'hui un scrape parfaitement sain sans nouveauté et un échec TOTAL de
fetch (navigateur mort, proxy mort, timeout) produisent le **même** badge
orange `empty`, sans `error_message` et sans bouton Retry. C'est le mode
d'échec silencieux le plus large du produit (§4.1, §4.20, risque #53).
Le test est `xfail(strict=True)` : le jour où le lot 1.4b atterrit, il passe
XPASS et fait échouer la suite en réclamant le retrait du marqueur.

────────────────────────────────────────────────────────────────────────────
NUMÉROTATION DES RISQUES
────────────────────────────────────────────────────────────────────────────
Les numéros cités sont ceux du tableau §5 d'AUDIT.md dans sa version
courante. Un renvoi utile : la commande de lot désigne le rollback global sur
`IntegrityError` comme « risque #5 » ; dans le tableau §5 actuel ce mécanisme
porte le **#9** (« `IntegrityError` → rollback global », `pipeline.py:255-266`,
§4.4), le #5 y désignant l'absence de `total_seen` dans 3 extracteurs. Les
deux numéros sont rappelés dans les `reason` concernés pour qu'aucune
recherche ne tombe à côté.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.scraper.pipeline as pipeline
from app.db import MediaItem, ProfileSnapshot, ScrapeJob
from app.scraper.base import (
    ExtractorResult,
    MediaItemData,
    PlatformExtractor,
    ProfileInfo,
)
from app.scraper.downloaders import DownloadResult
from conftest import FIXED_NOW

pytestmark = pytest.mark.pipeline


# ===========================================================================
# Outillage
# ===========================================================================


def media_data(post_id: str, *, media_url: str | None = None, **overrides) -> MediaItemData:
    """`MediaItemData` minimal, tel qu'en produisent les 4 extracteurs."""
    defaults = {
        "post_id": post_id,
        "post_url": f"https://instagram.invalid/p/{post_id}/",
        "media_type": "image",
        "media_url": media_url or f"https://cdn.instagram.invalid/{post_id}.jpg",
        "caption": f"caption {post_id}",
        "like_count": 12,
        "comment_count": 3,
        "view_count": 400,
    }
    defaults.update(overrides)
    return MediaItemData(**defaults)


def resultat_sain(media: list[MediaItemData] | None = None, *, total_seen: int = 0) -> ExtractorResult:
    """Ce que renvoie un extracteur qui a RÉELLEMENT atteint la plateforme.

    `profile_info` est renseigné : c'est le cas nominal des 4 plateformes, et
    c'est ce qui provoque les `db.commit()` de `pipeline.py:195` et `:222` —
    les points de reprise que le `rollback()` de `:260` ne peut pas défaire
    (cf. §4.4).
    """
    return ExtractorResult(
        profile_info=ProfileInfo(
            display_name="Compte de test",
            avatar_url="https://cdn.instagram.invalid/avatar.jpg",
            biography="bio",
            is_verified=True,
            followers_count=4321,
            following_count=210,
            media_count=57,
        ),
        media=list(media or []),
        total_seen=total_seen,
    )


def resultat_echec_total_de_fetch() -> ExtractorResult:
    """Le résultat d'un extracteur dont le fetch a totalement échoué.

    Avant le lot 1.4b, c'était la réplique EXACTE de ce que renvoyaient les 4
    extracteurs (`instagram.py:436-439`, `tiktok.py:331-334`, `twitter.py:330-333`,
    `reddit.py:391-394`) : un `ExtractorResult()` vide, sans la moindre trace de
    l'échec — indiscernable d'un profil sans nouveauté (§4.20, risque #53).

    Depuis le lot 1.4b, les 4 extracteurs posent en plus un `fetch_error` que le
    pipeline consomme. Ce résultat-ci reste volontairement NU : il garde le cas
    résiduel — un extracteur (présent ou futur) qui ne rapporte RIEN du tout,
    ni média, ni post vu, ni bribe de profil. Le pipeline doit le traiter comme
    une plateforme non atteinte, pas comme un scrape sain.
    """
    return ExtractorResult()


@pytest.fixture(autouse=True)
def _pipeline_sandbox(monkeypatch):
    """Neutralise les dépendances non déterministes du pipeline.

    * `_now_ts` : LOI n°5 — aucune dépendance à l'horloge murale.
    * `get_proxy_for_platform` : AUDIT.md §7.4 interdit de le déclencher
      (`load_dotenv(override=True)` mute `os.environ` globalement).
    * `STORAGE_MODE` : constante importée par valeur (`pipeline.py:21`) ;
      on fige le mode local pour ne jamais approcher Google Drive.
    """
    monkeypatch.setattr(pipeline, "_now_ts", lambda: FIXED_NOW)
    monkeypatch.setattr(pipeline, "get_proxy_for_platform", lambda platform: "")
    # Même règle pour l'aiguillage Apify (lot A) : `get_apify_settings` fait
    # aussi `load_dotenv(override=True)`. Sans jeton, le backend reste
    # « navigateur » et `_choisir_extracteur` retombe sur `_EXTRACTORS`.
    monkeypatch.setattr(
        pipeline, "get_apify_settings", lambda: ("", "apify~instagram-post-scraper")
    )
    monkeypatch.setattr(pipeline, "STORAGE_MODE", "local")


@pytest.fixture
def install_extractor(monkeypatch):
    """Enregistre un extracteur factice dans `pipeline._EXTRACTORS`.

    Pré-remplir le dict désactive le `if not _EXTRACTORS:` de
    `pipeline.py:36` : les vrais extracteurs — et donc scrapling — ne sont
    jamais importés. `conftest._reset_module_singletons` vide le dict après
    chaque test, `monkeypatch.setitem` le fait aussi : ceinture et bretelles.
    """
    appels: list[SimpleNamespace] = []

    def _install(result=None, *, platform="instagram", raises=None):
        class _StubExtractor(PlatformExtractor):
            def extract(self, profile_url, known_post_ids, options=None):
                appels.append(
                    SimpleNamespace(
                        profile_url=profile_url,
                        known_post_ids=set(known_post_ids),
                        options=options,
                    )
                )
                if raises is not None:
                    raise raises
                return result if result is not None else ExtractorResult()

        monkeypatch.setitem(pipeline._EXTRACTORS, platform, _StubExtractor)
        return appels

    _install.appels = appels
    return _install


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    """Remplace `pipeline.download_media` — aucun octet ne transite par le réseau.

    Le faux téléchargement écrit un VRAI fichier sous `tmp_path` : l'étape 9
    du pipeline (`pipeline.py:373-378`) teste `os.path.isfile(mi.local_path)`,
    un stub qui ne crée rien fausserait donc `media_uploaded`.
    """
    repertoire = tmp_path / "downloads"
    repertoire.mkdir()
    etat = SimpleNamespace(
        urls=[],
        echouent=set(),
        repertoire=repertoire,
        # `laisse_un_residu` reproduit le risque #14 côté downloaders : l'échec
        # survient APRÈS qu'un fichier partiel a été écrit. Il permet de tester
        # que le pipeline n'adopte pas ce résidu (il ne le supprime pas non
        # plus — c'est le xfail #14 de tests/test_stockage.py).
        laisse_un_residu=False,
        residus=[],
    )

    def _faux_download_media(url: str) -> DownloadResult:
        etat.urls.append(url)
        if url in etat.echouent:
            if etat.laisse_un_residu:
                partiel = repertoire / f"partiel{len(etat.urls)}.bin"
                partiel.write_bytes(b"octets-partiels")
                etat.residus.append(partiel)
            raise RuntimeError(f"HTTP 403 Forbidden sur {url}")
        destination = repertoire / f"media{len(etat.urls)}.bin"
        destination.write_bytes(b"octets-de-test")
        return DownloadResult(
            local_path=str(destination),
            file_size=destination.stat().st_size,
            mime_type="application/octet-stream",
            content_hash=f"hash-{len(etat.urls)}",
        )

    monkeypatch.setattr(pipeline, "download_media", _faux_download_media)
    return etat


@pytest.fixture
def run_job(db_session):
    """Exécute `run_scrape_job` et resynchronise la session du test.

    Le pipeline ouvre sa PROPRE session (`pipeline.py:90`) sur le même fichier
    SQLite : sans `commit()` avant et `expire_all()` après, le test lirait des
    objets périmés de son identity map.
    """

    def _run(job_id: int) -> None:
        db_session.commit()
        pipeline.run_scrape_job(job_id)
        db_session.expire_all()

    return _run


def post_ids_en_base(db_session, profile) -> set[str]:
    return {
        row[0]
        for row in db_session.query(MediaItem.post_id)
        .filter(MediaItem.profile_id == profile.id)
        .all()
    }


# ===========================================================================
# 1. LE TEST CENTRAL — `empty` confond succès et session morte
#    §4.1, §4.20, risques #3 et #53, tests T4 et T21, lots 1.4b et 3.3
# ===========================================================================


def _scenario_scrape_sain_sans_nouveaute(factories, install_extractor, run_job):
    """Profil sain, 25 posts vus sur la page, aucun nouveau. Rien d'anormal."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[], total_seen=25))
    run_job(job.id)
    return profil, job


def _scenario_echec_total_de_fetch(factories, install_extractor, run_job):
    """Navigateur/proxy morts : l'extracteur avale l'exception et rend du vide."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    install_extractor(resultat_echec_total_de_fetch())
    run_job(job.id)
    return profil, job


def test_scrape_sain_sans_nouveaute_nest_pas_signale_comme_une_erreur(
    factories, install_extractor, run_job, downloads
):
    """RÉGRESSION (lot 3.3) : un scrape sain ne produit ni erreur ni ambiguïté.

    Ce test portait avant le lot 3.3 une assertion tolérante
    (`status in {"empty", "completed"}`) parce que la valeur correcte n'était
    pas encore atteignable. Le statut étant désormais décidé sur `total_seen`,
    la tolérance n'a plus lieu d'être : elle rendrait le test incapable de
    rougir sur un retour en arrière.
    """
    profil, job = _scenario_scrape_sain_sans_nouveaute(factories, install_extractor, run_job)

    assert job.status == "completed"
    assert job.error_message is None
    assert job.media_found == 0
    assert job.media_new == 0
    # Le profil a bien été atteint : il est marqué comme scrapé.
    assert profil.last_scraped_at == FIXED_NOW


def test_scrape_sain_sans_nouveaute_devrait_etre_completed(
    factories, install_extractor, run_job, downloads
):
    """25 posts vus, tous déjà connus : c'est un SUCCÈS, pas une anomalie.

    Corrigé par le lot 3.3 : `pipeline.py` décide `empty` sur `total_seen`
    (posts VUS sur la page) et non plus sur `media_found` (posts NOUVEAUX).
    """
    _profil, job = _scenario_scrape_sain_sans_nouveaute(factories, install_extractor, run_job)

    assert job.status == "completed"


def test_echec_total_de_fetch_doit_produire_un_job_failed(
    factories, install_extractor, run_job, downloads
):
    """Un échec total de fetch doit être un échec visible, avec sa cause."""
    _profil, job = _scenario_echec_total_de_fetch(factories, install_extractor, run_job)

    assert job.status == "failed"
    assert job.error_message  # non vide : l'utilisateur doit savoir POURQUOI


def test_echec_total_de_fetch_ne_doit_pas_marquer_le_profil_comme_scrape(
    factories, install_extractor, run_job, downloads
):
    profil, _job = _scenario_echec_total_de_fetch(factories, install_extractor, run_job)

    assert profil.last_scraped_at is None


def test_scrape_sain_et_echec_total_de_fetch_doivent_etre_discernables(
    factories, install_extractor, run_job, downloads
):
    """Le jour où les cookies expirent, l'écran DOIT changer.

    Deux profils, deux jobs, deux situations diamétralement opposées :
    l'une est le fonctionnement nominal, l'autre est la panne totale.
    Elles ne peuvent pas porter le même statut.
    """
    _p1, job_sain = _scenario_scrape_sain_sans_nouveaute(factories, install_extractor, run_job)
    statut_sain = job_sain.status

    _p2, job_echec = _scenario_echec_total_de_fetch(factories, install_extractor, run_job)
    statut_echec = job_echec.status

    assert statut_sain != statut_echec, (
        f"scrape sain et échec total de fetch portent tous deux le statut "
        f"{statut_sain!r} : aucun opérateur ne peut les distinguer"
    )


# ===========================================================================
# 2. ÉCHEC D'EXTRACTION — martèlement du profil
#    §4.5, risque #10, lot 1.4
# ===========================================================================


def test_exception_dextraction_marque_le_job_failed(
    factories, install_extractor, run_job, downloads
):
    """Caractérisation : une exception levée HORS du bloc de fetch remonte bien.

    C'est le seul chemin d'échec d'extraction qui atteint `failed`
    (pipeline.py:164-167) — à distinguer soigneusement du §4.20, où
    l'exception est avalée par l'extracteur lui-même.
    """
    profil = factories.profile(platform="instagram")
    job = factories.scrape_job(profil)
    install_extractor(raises=RuntimeError("le navigateur n'a pas démarré"))

    run_job(job.id)

    assert job.status == "failed"
    assert job.error_message == "Extraction error: le navigateur n'a pas démarré"
    assert job.completed_at == FIXED_NOW
    assert job.media_found == 0


def test_exception_dextraction_doit_quand_meme_horodater_le_profil(
    factories, install_extractor, run_job, downloads
):
    """Un profil cassé ne doit pas être redû à chaque cycle."""
    profil = factories.profile(platform="instagram", last_scraped_at=None)
    job = factories.scrape_job(profil)
    install_extractor(raises=RuntimeError("le navigateur n'a pas démarré"))

    run_job(job.id)

    assert profil.last_scraped_at == FIXED_NOW


def test_profil_introuvable_marque_le_job_failed(factories, run_job, db_session):
    """Caractérisation : `pipeline.py:112` — l'autre écrivain de `failed`.

    Depuis le lot 3.7, `PRAGMA foreign_keys=ON` est posé sur chaque connexion :
    supprimer un profil emporte ses jobs par `ON DELETE CASCADE`, et un job
    orphelin ne peut plus être FABRIQUÉ. Reste l'état que cette branche doit
    savoir traiter : un job orphelin DÉJÀ EN BASE. SQLite ne revalide jamais
    les lignes existantes quand on active le PRAGMA — un tel job survit donc
    à la migration, et `pipeline.py:109-113` reste son seul filet.

    On le reproduit fidèlement par une connexion sqlite3 nue (le PRAGMA y est
    OFF par défaut, comme il l'était pour toute l'application avant le lot).
    """
    import sqlite3

    import app.db as app_db

    profil = factories.profile(platform="instagram")
    job = factories.scrape_job(profil)
    job_id, profil_id = job.id, profil.id
    db_session.commit()  # libère le verrou d'écriture avant la connexion nue

    conn = sqlite3.connect(str(app_db.DB_PATH))
    try:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profil_id,))
        conn.commit()
        restants = conn.execute(
            "SELECT COUNT(*) FROM scrape_jobs WHERE id = ?", (job_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert restants == 1, "le job orphelin doit survivre à son profil"
    db_session.expire_all()

    run_job(job_id)

    assert job.status == "failed"
    assert job.error_message == "Profile not found"


# ===========================================================================
# 3. DOUBLON EN COURS DE LOT — le rollback global
#    §4.4, risque #9 (désigné « risque #5 » dans la commande du lot 1.6)
# ===========================================================================


def _scenario_doublon_au_milieu_du_lot(factories, install_extractor, run_job):
    """Un lot de 4 médias dont le 3ᵉ existe déjà en base.

    L'ordre est essentiel : A et B sont `flush`és AVANT la collision, C après.
    Le `db.rollback()` de `pipeline.py:260` annule la transaction entière
    depuis le dernier `commit()` — donc A et B, jamais C.
    """
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    deja_present = factories.media_item(
        profil, post_id="POST_CONNU", media_type="image", status="uploaded"
    )
    job = factories.scrape_job(profil)

    install_extractor(
        resultat_sain(
            media=[
                media_data("POST_A"),
                media_data("POST_B"),
                media_data("POST_CONNU", media_url=deja_present.media_url),
                media_data("POST_C"),
            ],
            total_seen=4,
        )
    )
    run_job(job.id)
    return profil, job


def test_un_doublon_nest_pas_reinsere(
    factories, install_extractor, run_job, downloads, db_session
):
    """Caractérisation : la contrainte `idx_media_dedup` joue bien son rôle."""
    profil, _job = _scenario_doublon_au_milieu_du_lot(factories, install_extractor, run_job)

    lignes_connues = (
        db_session.query(MediaItem)
        .filter(MediaItem.profile_id == profil.id, MediaItem.post_id == "POST_CONNU")
        .count()
    )
    assert lignes_connues == 1


def test_un_doublon_ne_doit_pas_annuler_les_items_sains_du_meme_lot(
    factories, install_extractor, run_job, downloads, db_session
):
    """Les médias sains insérés avant la collision doivent survivre."""
    profil, _job = _scenario_doublon_au_milieu_du_lot(factories, install_extractor, run_job)

    assert post_ids_en_base(db_session, profil) == {
        "POST_CONNU",
        "POST_A",
        "POST_B",
        "POST_C",
    }


def test_media_new_doit_egaler_le_nombre_de_lignes_reellement_persistees(
    factories, install_extractor, run_job, downloads, db_session
):
    """Le compteur affiché doit être vérifiable en base."""
    profil, job = _scenario_doublon_au_milieu_du_lot(factories, install_extractor, run_job)

    nouvelles_lignes = (
        db_session.query(MediaItem)
        .filter(
            MediaItem.profile_id == profil.id,
            MediaItem.post_id != "POST_CONNU",
        )
        .count()
    )
    assert job.media_new == nouvelles_lignes


# ===========================================================================
# 4. IDEMPOTENCE
# ===========================================================================


def test_relancer_le_meme_job_ne_cree_aucun_doublon(
    factories, install_extractor, run_job, downloads, db_session
):
    """Rejouer un job à l'identique doit être sans effet sur la base."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    media = [media_data("POST_1"), media_data("POST_2"), media_data("POST_3")]
    install_extractor(resultat_sain(media=media, total_seen=3))

    run_job(job.id)
    assert job.media_new == 3
    lignes_apres_run_1 = db_session.query(MediaItem).count()

    run_job(job.id)  # exactement la même sortie d'extracteur

    assert db_session.query(MediaItem).count() == lignes_apres_run_1 == 3
    assert post_ids_en_base(db_session, profil) == {"POST_1", "POST_2", "POST_3"}


def test_second_passage_ne_compte_aucun_media_nouveau(
    factories, install_extractor, run_job, downloads
):
    """`media_found` compte ce qu'a rendu l'extracteur, `media_new` ce qui est entré."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job1 = factories.scrape_job(profil)
    media = [media_data("POST_1"), media_data("POST_2")]
    install_extractor(resultat_sain(media=media, total_seen=2))

    run_job(job1.id)
    job2 = factories.scrape_job(profil)
    run_job(job2.id)

    assert job2.media_found == 2
    assert job2.media_new == 0
    # Les médias du premier passage sont déjà `uploaded` : rien à retélécharger.
    assert job2.media_downloaded == 0


def test_les_post_ids_connus_sont_transmis_a_lextracteur(
    factories, install_extractor, run_job, downloads
):
    """La déduplication amont repose sur `known_post_ids` (pipeline.py:150-155)."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    factories.media_item(profil, post_id="DEJA_VU_1")
    factories.media_item(profil, post_id="DEJA_VU_2")
    job = factories.scrape_job(profil)
    appels = install_extractor(resultat_sain(media=[], total_seen=2))

    run_job(job.id)

    assert len(appels) == 1
    assert appels[0].known_post_ids == {"DEJA_VU_1", "DEJA_VU_2"}
    assert appels[0].profile_url == profil.profile_url


# ===========================================================================
# 5. COMPTAGE ET DÉCISION DU STATUT FINAL
#    §4.1 / test T4 — `pipeline.py:412-424`
# ===========================================================================


@pytest.mark.parametrize(
    "nb_medias, nb_echecs_de_telechargement, statut_attendu",
    [
        (0, 0, "empty"),
        (3, 0, "completed"),
        (3, 1, "partial"),
        (2, 2, "partial"),
    ],
    ids=["aucun-media", "tout-reussi", "un-echec", "tout-echoue"],
)
def test_matrice_du_statut_final(
    factories,
    install_extractor,
    run_job,
    downloads,
    nb_medias,
    nb_echecs_de_telechargement,
    statut_attendu,
):
    """Caractérisation de la décision de `pipeline.py:417-424`.

    NB : le cas `media_found == 0` est ici volontairement accompagné de
    `total_seen == 0` (page réellement vide). Le cas discriminant
    `media_found == 0` **avec** `total_seen > 0` est traité en section 1,
    parce que lui seul est fautif.
    """
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    media = [media_data(f"POST_{i}") for i in range(nb_medias)]
    for item in media[:nb_echecs_de_telechargement]:
        downloads.echouent.add(item.media_url)
    install_extractor(resultat_sain(media=media, total_seen=nb_medias))

    run_job(job.id)

    assert job.status == statut_attendu
    assert job.media_found == nb_medias
    assert job.media_new == nb_medias
    assert job.media_downloaded == nb_medias - nb_echecs_de_telechargement
    assert job.media_uploaded == nb_medias - nb_echecs_de_telechargement
    # Lot 3.3 : `empty` a rejoint les statuts TERMINAUX de `_mark_job`, les
    # quatre cas s'horodatent donc de la même façon.
    assert job.completed_at == FIXED_NOW


def test_un_job_empty_devrait_avoir_une_date_de_fin(
    factories, install_extractor, run_job
):
    """RÉGRESSION (lot 3.3) : `empty` est un état TERMINAL, il s'horodate.

    Avant le lot 3.3, `_mark_job` ne posait `completed_at` que pour
    `completed`/`failed`/`partial` : un job `empty` restait sans date de fin et
    l'interface n'avait rien à afficher.
    """
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[], total_seen=0))

    run_job(job.id)

    assert job.status == "empty"
    assert job.started_at == FIXED_NOW
    assert job.completed_at == FIXED_NOW


def test_letape_de_telechargement_ramasse_les_pending_des_jobs_precedents(
    factories, install_extractor, run_job, downloads
):
    """Caractérisation : l'étape 8 traite TOUS les `pending` du profil.

    `pipeline.py:274-281` ne filtre pas sur le job courant : `media_downloaded`
    peut donc dépasser `media_found`. C'est aussi le mécanisme du risque #60
    (le sémaphore de scrape reste tenu pendant toute cette phase non bornée).
    """
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    factories.media_item(profil, post_id="RESTE_D_UN_JOB_PRECEDENT", status="pending")
    job = factories.scrape_job(profil)
    install_extractor(
        resultat_sain(media=[media_data("POST_1"), media_data("POST_2")], total_seen=2)
    )

    run_job(job.id)

    assert job.media_found == 2
    assert job.media_new == 2
    assert job.media_downloaded == 3
    assert job.status == "completed"


def test_profil_et_snapshot_mis_a_jour_depuis_profile_info(
    factories, install_extractor, run_job, downloads, db_session
):
    """Étape 6 : `profile_info` alimente le profil et un snapshot quotidien."""
    profil = factories.profile(
        platform="instagram", scrape_mode="daily", display_name="ancien", followers_count=1
    )
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[], total_seen=5))

    run_job(job.id)

    assert profil.display_name == "Compte de test"
    assert profil.followers_count == 4321
    assert profil.following_count == 210
    assert profil.media_count == 57
    assert profil.is_verified is True

    snapshots = (
        db_session.query(ProfileSnapshot)
        .filter(ProfileSnapshot.profile_id == profil.id)
        .all()
    )
    assert len(snapshots) == 1
    assert snapshots[0].followers_count == 4321


def test_les_compteurs_dengagement_sont_persistes(
    factories, install_extractor, run_job, downloads, db_session
):
    """`like_count`/`comment_count`/`view_count` -> colonnes `ig_*` (pipeline.py:247-249)."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[media_data("POST_1")], total_seen=1))

    run_job(job.id)

    item = (
        db_session.query(MediaItem)
        .filter(MediaItem.profile_id == profil.id, MediaItem.post_id == "POST_1")
        .one()
    )
    assert (item.ig_like_count, item.ig_comment_count, item.ig_view_count) == (12, 3, 400)
    assert item.platform == "instagram"
    assert item.status == "uploaded"
    assert item.downloaded_at == FIXED_NOW


@pytest.mark.parametrize(
    "mode, max_scrolls_attendu",
    [("backfill", pipeline.BACKFILL_MAX_SCROLLS), ("daily", pipeline.DAILY_MAX_SCROLLS)],
)
def test_les_options_dextraction_refletent_le_mode_du_profil(
    factories, install_extractor, run_job, downloads, mode, max_scrolls_attendu
):
    profil = factories.profile(platform="instagram", scrape_mode=mode)
    job = factories.scrape_job(profil)
    appels = install_extractor(resultat_sain(media=[], total_seen=3))

    run_job(job.id)

    options = appels[0].options
    assert options.scrape_mode == mode
    assert options.max_scrolls == max_scrolls_attendu
    assert options.proxy is None


# ===========================================================================
# 6. TRANSITION AUTOMATIQUE backfill -> daily (`pipeline.py:441-461`)
#    Le seul consommateur de `total_seen` — d'où le risque #5 (§4.3).
# ===========================================================================


def test_backfill_reste_actif_quand_la_page_ne_renvoie_rien(
    factories, install_extractor, run_job, downloads
):
    """`media_new == 0` ET `total_seen == 0` : blocage possible, on ne bascule pas."""
    profil = factories.profile(platform="instagram", scrape_mode="backfill")
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[], total_seen=0))

    run_job(job.id)

    assert profil.scrape_mode == "backfill"


def test_backfill_bascule_en_daily_quand_tout_est_deja_connu(
    factories, install_extractor, run_job, downloads
):
    """`media_new == 0` mais `total_seen > 0` : le backfill est réellement terminé.

    C'est exactement la branche que les risques #5/#3 rendent inatteignable sur
    TikTok, Twitter et Reddit (leurs extracteurs n'incrémentent jamais
    `total_seen`, §4.3). Sur Instagram — la seule plateforme qui l'incrémente —
    elle fonctionne : ce test verrouille ce comportement correct.
    """
    profil = factories.profile(
        platform="instagram", scrape_mode="backfill", scrape_interval_minutes=30
    )
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[], total_seen=42))

    run_job(job.id)

    assert profil.scrape_mode == "daily"
    assert profil.scrape_interval_minutes == pipeline.DAILY_SCRAPE_INTERVAL_MINUTES


# ===========================================================================
# 7. ÉCHEC DE TÉLÉCHARGEMENT
#    Statut `download_failed` + risque #14 (fichiers partiels orphelins)
# ===========================================================================


def test_un_telechargement_en_echec_marque_litem_download_failed(
    factories, install_extractor, run_job, downloads, db_session
):
    """Caractérisation complète de `pipeline.py:295-305`."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    item_ko = media_data("POST_KO")
    downloads.echouent.add(item_ko.media_url)
    install_extractor(
        resultat_sain(media=[media_data("POST_OK"), item_ko], total_seen=2)
    )

    run_job(job.id)

    en_echec = (
        db_session.query(MediaItem)
        .filter(MediaItem.profile_id == profil.id, MediaItem.post_id == "POST_KO")
        .one()
    )
    assert en_echec.status == "download_failed"
    assert "403" in en_echec.error_message
    assert len(en_echec.error_message) <= 500
    assert en_echec.retry_count == 1
    assert en_echec.local_path is None
    assert en_echec.downloaded_at is None

    reussi = (
        db_session.query(MediaItem)
        .filter(MediaItem.profile_id == profil.id, MediaItem.post_id == "POST_OK")
        .one()
    )
    assert reussi.status == "uploaded"
    assert job.status == "partial"


def test_le_pipeline_ne_reference_aucun_residu_dun_telechargement_echoue(
    factories, install_extractor, run_job, downloads, db_session
):
    """Un `download_media` qui laisse un fichier partiel derrière lui ne doit
    pas contaminer la ligne en base.

    L'ancienne version de ce test asserait « le répertoire est vide » : le
    stub d'échec levait AVANT d'écrire, si bien que le vide était garanti par
    la fixture et non par le pipeline — aucune mutation de
    `app/scraper/pipeline.py` ne pouvait le faire rougir. On force donc ici le
    stub à écrire un résidu, et on vérifie ce que le pipeline contrôle
    réellement : `local_path` / `file_size` restent vierges sur l'item échoué.
    Le nettoyage du résidu sur disque, lui, relève de `downloaders.py` et est
    gardé par tests/test_stockage.py (risque #14, lot 3.4).
    """
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    item_ko = media_data("POST_KO")
    downloads.echouent.add(item_ko.media_url)
    downloads.laisse_un_residu = True
    install_extractor(resultat_sain(media=[item_ko], total_seen=1))

    run_job(job.id)

    assert downloads.residus, "le scénario doit bien produire un résidu à ignorer"
    en_echec = (
        db_session.query(MediaItem)
        .filter(MediaItem.profile_id == profil.id, MediaItem.post_id == "POST_KO")
        .one()
    )
    assert en_echec.status == "download_failed"
    assert en_echec.local_path is None
    assert en_echec.file_size is None
    assert job.media_downloaded == 0


# ---------------------------------------------------------------------------
# NOTE DE PÉRIMÈTRE (réconciliation inter-modules)
# ---------------------------------------------------------------------------
# Deux tests visant `app/scraper/downloaders.py` vivaient ici :
#   * `test_download_direct_ne_laisse_aucun_fichier_partiel` (xfail #14) ;
#   * `test_url_de_media_vide_est_rejetee_sans_toucher_au_disque`.
# Ils faisaient DOUBLON avec tests/test_stockage.py, domicile naturel de
# `downloaders.py` :
#   * test_stockage.py::test_aucun_fichier_partiel_ne_doit_survivre_a_trois_tentatives_ratees
#     (même risque #14, même code, même lot 3.4) ;
#   * test_stockage.py::test_download_media_refuse_une_url_vide.
# Deux marqueurs xfail strict pour UN seul correctif auraient obligé le lot
# 3.4 à chercher le second après avoir retiré le premier. Ce module s'en
# tient donc à `pipeline.py` : ce qu'il garde du téléchargement, c'est la
# façon dont le pipeline en CONSOMME le résultat (test ci-dessus).


# ===========================================================================
# 8. GARDE-FOUS DU MODULE LUI-MÊME
# ===========================================================================


def test_aucun_extracteur_reel_nest_charge_par_la_suite(install_extractor):
    """Vérifie que le stub court-circuite bien `_get_extractor`.

    Si ce test devient rouge, tous les autres deviennent suspects : cela
    signifierait que la suite instancie un vrai extracteur, donc scrapling et
    un navigateur (LOI n°3).
    """
    install_extractor(resultat_sain())

    extracteur = pipeline._get_extractor("instagram")

    assert type(extracteur).__name__ == "_StubExtractor"
    assert set(pipeline._EXTRACTORS) == {"instagram"}


def test_plateforme_inconnue_leve_value_error():
    with pytest.raises(ValueError, match="Unsupported platform"):
        pipeline._get_extractor("myspace")


def test_job_inexistant_est_ignore_sans_exception(run_job):
    """`pipeline.py:104-107` : un id inconnu ne doit pas faire tomber le thread."""
    run_job(999_999)


def test_le_pipeline_necrit_que_sous_le_data_dir_de_test(
    factories, install_extractor, run_job, downloads, test_data_dir
):
    """LOI n°1 : le pipeline complet ne doit approcher aucun chemin de production."""
    profil = factories.profile(platform="instagram", scrape_mode="daily")
    job = factories.scrape_job(profil)
    install_extractor(resultat_sain(media=[media_data("POST_1")], total_seen=1))

    run_job(job.id)

    assert str(pipeline.SessionLocal.kw["bind"].url).endswith(
        f"{test_data_dir}/samourais.db"
    )
    assert job.status == "completed"
