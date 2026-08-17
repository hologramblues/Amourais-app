"""
MODULE SCHEDULER — cycle de vie des jobs, verrous, récupération.
Cible : `app/scheduler.py` (vague 0, T3 du §7.4 d'AUDIT.md).

────────────────────────────────────────────────────────────────────────────
CE QUI N'EST JAMAIS DÉCLENCHÉ ICI
────────────────────────────────────────────────────────────────────────────
* `start_scheduler()` / APScheduler : interdits par §7.4 (« Ne jamais
  déclencher dans les tests ») — ils écrivent en base, démarrent un scheduler
  de fond et un thread `ig-initial`.
* Aucun vrai thread de scrape : `threading` est remplacé, DANS le module
  `app.scheduler` uniquement, par un faux `Thread` qui enregistre l'appel.
  Les fonctions sous test sont appelées directement, en synchrone.
* Aucun accès réseau, aucun navigateur : `run_scrape_job` est remplacé par un
  stub (il est importé PARESSEUSEMENT en `scheduler.py:97`, donc patcher
  `app.scraper.pipeline.run_scrape_job` suffit — §7.2).
* Aucun `time.sleep` réel : `DELAY_BETWEEN_PROFILES_MS` (10 000 ms en
  configuration de test !) est ramené à 0 pour que la branche
  `if delay_seconds > 0` (`scheduler.py:209`) ne soit jamais prise.
* Aucune dépendance à l'horloge murale : `_now_ts` est injecté.

────────────────────────────────────────────────────────────────────────────
CONVENTION DES xfail (LOI ABSOLUE n°4)
────────────────────────────────────────────────────────────────────────────
Chaque bug connu d'AUDIT.md est exprimé par un test du comportement CORRECT,
marqué `xfail(strict=True)` avec le numéro de risque et le lot de correction.
Le jour où le lot est livré, le test passe XPASS et fait échouer la suite :
c'est le signal « retire ce xfail, le bug est mort ».
"""

from __future__ import annotations

import inspect
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.scheduler as sched_mod
import app.scraper.pipeline as pipeline_mod
from app.config import MAX_CONCURRENT_SCRAPES
from app.db import MediaItem, ScheduledPost, ScrapeJob
from conftest import FIXED_NOW

pytestmark = pytest.mark.scheduler


# ===========================================================================
# Fixtures locales (le socle ne doit PAS être modifié — cf. consigne)
# ===========================================================================


_JETONS_ATTENDUS = max(1, MAX_CONCURRENT_SCRAPES)


def jetons_libres(sched) -> int:
    """Jetons encore disponibles dans le sémaphore global (`scheduler.py:52`).

    On lit l'attribut privé `_value` À DESSEIN : c'est exactement l'invariant
    visé par T3(c) (« le sémaphore est rendu »), et c'est la SEULE lecture qui
    détecte UNE fuite unitaire. `acquire(blocking=False)` ne la détecte pas :
    avec MAX_CONCURRENT_SCRAPES=2, après une fuite il reste un jeton et
    l'acquisition réussit encore — la fuite ne se manifestait alors que 30 s
    plus tard, par TIMEOUT, sur un test innocent.
    """
    return sched._scrape_semaphore._value


@pytest.fixture
def sched(monkeypatch):
    """Module `app.scheduler`, singletons remis à zéro et délais neutralisés.

    `DELAY_BETWEEN_PROFILES_MS` est importé PAR VALEUR par `scheduler.py:23`,
    mais relu à chaque tour de boucle (`:208`) : patcher le nom dans le module
    consommateur suffit (§7.1 AUDIT.md).

    `_scrape_semaphore` est un singleton de MODULE, donc partagé par les 63
    tests et par les autres modules de la suite. On le remplace par un
    exemplaire NEUF pour la durée de chaque test : le module le lit par nom
    global à chaque appel (`:93`, `:117`), donc le patch est vu. Sans cela,
    une fuite de jeton ne fait pas échouer le test fautif mais PEND un test
    ultérieur jusqu'au `--timeout=30` — le rouge serait attribué au mauvais
    test et le budget de 60 s de la LOI n°5 sauterait.
    """
    with sched_mod._running_lock:
        sched_mod._running_profiles.clear()
    monkeypatch.setattr(sched_mod, "DELAY_BETWEEN_PROFILES_MS", 0)
    monkeypatch.setattr(
        sched_mod, "_scrape_semaphore", threading.Semaphore(_JETONS_ATTENDUS)
    )
    yield sched_mod
    with sched_mod._running_lock:
        sched_mod._running_profiles.clear()


class _ThreadRecorder:
    """Faux `threading.Thread` : enregistre, ne démarre rien.

    `fail_on_start_number` reproduit le déclencheur concret du chemin C du
    §4.2 : `RuntimeError: can't start new thread` levée par `t.start()`
    (`scheduler.py:205`).
    """

    def __init__(self) -> None:
        self.created: list[SimpleNamespace] = []
        self.started: list[SimpleNamespace] = []
        self.fail_on_start_number: int | None = None
        self._start_calls = 0

    def install(self, monkeypatch, module) -> None:
        recorder = self

        class _FakeThread:
            def __init__(self, target=None, args=(), name=None, daemon=None, **kw):
                self.target = target
                self.args = tuple(args)
                self.name = name
                self.daemon = daemon
                self.record = SimpleNamespace(
                    target=target, args=tuple(args), name=name
                )
                recorder.created.append(self.record)

            def start(self):
                recorder._start_calls += 1
                if recorder.fail_on_start_number == recorder._start_calls:
                    raise RuntimeError("can't start new thread")
                recorder.started.append(self.record)

            def join(self, timeout=None):  # pragma: no cover - jamais utilisé
                return None

        # Remplacement CIBLÉ : seul `app.scheduler` voit ce faux module.
        # `_running_lock` est déjà construit (import), donc `Lock` n'est
        # pas nécessaire ici, mais on l'expose par sécurité.
        monkeypatch.setattr(
            module,
            "threading",
            SimpleNamespace(Thread=_FakeThread, Lock=threading.Lock),
        )

    @property
    def started_job_ids(self) -> list[int]:
        return [rec.args[0] for rec in self.started]

    @property
    def started_profile_ids(self) -> list[int]:
        return [rec.args[1] for rec in self.started]


@pytest.fixture
def fake_threads(sched, monkeypatch) -> _ThreadRecorder:
    recorder = _ThreadRecorder()
    recorder.install(monkeypatch, sched)
    return recorder


@pytest.fixture
def frozen_now(sched, monkeypatch):
    """Injecte l'horodatage vu par le scheduler (LOI ABSOLUE n°5)."""

    def _set(timestamp: int) -> int:
        value = int(timestamp)
        monkeypatch.setattr(sched, "_now_ts", lambda: value)
        return value

    return _set


class _PipelineStub:
    def __init__(self) -> None:
        self.calls: list[int] = []


@pytest.fixture
def stub_pipeline(monkeypatch) -> _PipelineStub:
    """Remplace `run_scrape_job` — aucun navigateur, aucun réseau."""
    stub = _PipelineStub()

    def _install(behaviour=None):
        def _run_scrape_job(job_id: int):
            stub.calls.append(job_id)
            if behaviour is not None:
                return behaviour(job_id)
            return None

        monkeypatch.setattr(pipeline_mod, "run_scrape_job", _run_scrape_job)

    stub.install = _install  # type: ignore[attr-defined]
    _install()
    return stub


@pytest.fixture
def reload_db(db_session):
    """Recharge un objet écrit par une AUTRE session (celle du scheduler)."""

    def _reload(obj):
        db_session.expire_all()
        return db_session.get(type(obj), obj.id)

    return _reload


def _jobs_of(db_session, profile_id) -> list[ScrapeJob]:
    db_session.expire_all()
    return (
        db_session.query(ScrapeJob)
        .filter(ScrapeJob.profile_id == profile_id)
        .order_by(ScrapeJob.id)
        .all()
    )


# ===========================================================================
# 1. _acquire_profile / _release_profile — le verrou de concurrence
# ===========================================================================


def test_acquire_profile_reussit_sur_un_profil_libre(sched):
    assert sched._acquire_profile(1) is True
    assert 1 in sched._running_profiles


def test_acquire_profile_refuse_la_double_acquisition(sched):
    assert sched._acquire_profile(1) is True
    assert sched._acquire_profile(1) is False
    assert sched._running_profiles == {1}


def test_acquire_profile_est_par_profil_et_non_global(sched):
    assert sched._acquire_profile(1) is True
    assert sched._acquire_profile(2) is True
    assert sched._running_profiles == {1, 2}


def test_release_profile_rend_le_profil_reacquerable(sched):
    sched._acquire_profile(1)
    sched._release_profile(1)

    assert 1 not in sched._running_profiles
    assert sched._acquire_profile(1) is True


def test_release_profile_dun_profil_non_acquis_est_inoffensif(sched):
    """`discard` et non `remove` (`scheduler.py:67`) : aucune KeyError."""
    sched._release_profile(999)  # ne doit rien lever

    assert sched._running_profiles == set()


def test_release_profile_est_idempotent(sched):
    sched._acquire_profile(1)
    sched._release_profile(1)
    sched._release_profile(1)

    assert sched._running_profiles == set()


# ===========================================================================
# 2. _run_job_safe — chemin nominal, verrou profil, sémaphore
# ===========================================================================


def test_run_job_safe_execute_le_pipeline_et_relache_tout(
    sched, factories, stub_pipeline, make_profile
):
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    sched._run_job_safe(job.id, profile.id)

    assert stub_pipeline.calls == [job.id]
    assert profile.id not in sched._running_profiles
    # Le sémaphore global (`scheduler.py:52`) doit être rendu INTÉGRALEMENT.
    # On compare le NOMBRE de jetons, pas la simple possibilité d'acquérir :
    # cf. la docstring de `jetons_libres` — une fuite unitaire passe sinon
    # inaperçue ici et fait pendre un test ultérieur.
    assert jetons_libres(sched) == _JETONS_ATTENDUS


def test_run_job_safe_profil_deja_verrouille_nappelle_pas_le_pipeline(
    sched, factories, stub_pipeline, make_profile
):
    """Chemin A du §4.2 : le second job n'exécute rien.

    Cette assertion-là reste vraie APRÈS le correctif du lot 1.3 (le job sera
    marqué `failed`, mais toujours sans exécuter le pipeline) : elle ne
    contredit donc pas le xfail ci-dessous.
    """
    profile = make_profile()
    job_b = factories.scrape_job(profile, status="queued")
    assert sched._acquire_profile(profile.id) is True  # job A « en cours »

    sched._run_job_safe(job_b.id, profile.id)

    assert stub_pipeline.calls == []


def test_run_job_safe_ne_relache_pas_le_verrou_du_job_en_cours(
    sched, factories, stub_pipeline, make_profile
):
    """Le `return` de `:88` précède le `try` : le `finally` de `:115-118`
    n'est pas exécuté, donc le verrou du job A survit. C'est correct."""
    profile = make_profile()
    job_b = factories.scrape_job(profile, status="queued")
    sched._acquire_profile(profile.id)

    sched._run_job_safe(job_b.id, profile.id)

    assert profile.id in sched._running_profiles


def test_run_job_safe_profil_verrouille_doit_marquer_le_job_failed(
    sched, factories, stub_pipeline, make_profile, reload_db
):
    """Chemin A — comportement CORRECT attendu (T3(a) du §7.4)."""
    profile = make_profile()
    job_b = factories.scrape_job(profile, status="queued")
    sched._acquire_profile(profile.id)

    sched._run_job_safe(job_b.id, profile.id)

    job_b = reload_db(job_b)
    assert job_b.status == "failed"
    assert job_b.completed_at is not None
    assert job_b.error_message


# ===========================================================================
# 3. _run_job_safe — le filet d'exception (§4.2 chemin B)
# ===========================================================================


def test_run_job_safe_marque_failed_une_exception_apres_le_passage_en_running(
    sched, factories, stub_pipeline, make_profile, reload_db, db_session
):
    """Comportement ACTUEL correct : le filet `:105-110` couvre `running`."""
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    def _boom(job_id):
        # Reproduit `_mark_job(db, job, "running")` (pipeline.py:145) puis une
        # exception postérieure (ex. OperationalError de l'étape 8).
        row = db_session.get(ScrapeJob, job_id)
        row.status = "running"
        row.started_at = FIXED_NOW
        db_session.commit()
        raise RuntimeError("boum en cours de scrape")

    stub_pipeline.install(_boom)

    sched._run_job_safe(job.id, profile.id)

    job = reload_db(job)
    assert job.status == "failed"
    assert job.error_message.startswith("Unhandled:")
    assert "boum en cours de scrape" in job.error_message
    assert job.completed_at is not None


def test_run_job_safe_relache_verrou_et_semaphore_meme_en_cas_dexception(
    sched, factories, stub_pipeline, make_profile
):
    """T3(c) : `finally` (`:115-118`) rend le sémaphore ET le profil."""
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    def _boom(job_id):
        raise RuntimeError("boum")

    stub_pipeline.install(_boom)

    sched._run_job_safe(job.id, profile.id)

    assert profile.id not in sched._running_profiles
    assert jetons_libres(sched) == _JETONS_ATTENDUS


def test_run_job_safe_exception_avant_running_nappelle_pas_deux_fois_le_pipeline(
    sched, factories, stub_pipeline, make_profile
):
    """Assertion stable : aucune relance implicite (vraie avant et après 1.3)."""
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    def _boom(job_id):
        raise RuntimeError("no such column: profiles.backfill_from")

    stub_pipeline.install(_boom)

    sched._run_job_safe(job.id, profile.id)

    assert stub_pipeline.calls == [job.id]


def test_run_job_safe_exception_avant_running_doit_marquer_le_job_failed(
    sched, factories, stub_pipeline, make_profile, reload_db
):
    """Chemin B — comportement CORRECT attendu (T3(b) du §7.4).

    Déclencheur concret cité par l'audit : le risque #1 lui-même
    (`no such column: profiles.backfill_from` sur `pipeline.py:109`).
    """
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    def _boom(job_id):
        raise RuntimeError("no such column: profiles.backfill_from")

    stub_pipeline.install(_boom)

    sched._run_job_safe(job.id, profile.id)

    job = reload_db(job)
    assert job.status == "failed"
    assert job.completed_at is not None


# ===========================================================================
# 4. check_due_profiles — sélection des profils dus
# ===========================================================================


def test_check_due_profiles_cree_un_job_pour_un_profil_jamais_scrape(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    now = frozen_now(FIXED_NOW)
    profile = factories.profile(last_scraped_at=None)

    sched.check_due_profiles()

    jobs = _jobs_of(db_session, profile.id)
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].triggered_by == "scheduler"
    assert fake_threads.started_job_ids == [jobs[0].id]
    assert fake_threads.started_profile_ids == [profile.id]
    # (l'ancienne assertion `now == FIXED_NOW` était tautologique : `now` est
    # la valeur qu'on vient de passer à la fixture. La lecture effective de
    # `_now_ts` est gardée par test_check_due_profiles_ignore_un_profil_pas_
    # encore_du, qui rougit si l'horodatage injecté n'est pas relu.)


def test_check_due_profiles_ignore_un_profil_pas_encore_du(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    now = frozen_now(FIXED_NOW)
    profile = factories.profile(
        scrape_interval_minutes=360, last_scraped_at=now - 60 * 60
    )

    sched.check_due_profiles()

    assert _jobs_of(db_session, profile.id) == []
    assert fake_threads.started == []


def test_check_due_profiles_inclut_le_profil_pile_a_lintervalle(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """`elapsed_minutes >= p.scrape_interval_minutes` (`:146`) : borne incluse."""
    now = frozen_now(FIXED_NOW)
    profile = factories.profile(
        scrape_interval_minutes=360, last_scraped_at=now - 360 * 60
    )

    sched.check_due_profiles()

    assert len(_jobs_of(db_session, profile.id)) == 1


def test_check_due_profiles_ignore_les_profils_inactifs(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=False, last_scraped_at=None)

    sched.check_due_profiles()

    assert _jobs_of(db_session, profile.id) == []


def test_check_due_profiles_saute_un_profil_deja_dans_running_profiles(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(last_scraped_at=None)
    sched._acquire_profile(profile.id)

    sched.check_due_profiles()

    assert _jobs_of(db_session, profile.id) == []
    assert fake_threads.started == []


@pytest.mark.parametrize("existing_status", ["queued", "running"])
def test_check_due_profiles_saute_un_profil_avec_un_job_actif(
    sched,
    factories,
    fake_threads,
    frozen_now,
    stub_pipeline,
    db_session,
    existing_status,
):
    """C'est CE garde (`:165-180`) que le job fantôme du §4.2 détourne."""
    frozen_now(FIXED_NOW)
    profile = factories.profile(last_scraped_at=None)
    existing = factories.scrape_job(profile, status=existing_status)

    sched.check_due_profiles()

    jobs = _jobs_of(db_session, profile.id)
    assert [j.id for j in jobs] == [existing.id]
    assert fake_threads.started == []


def test_check_due_profiles_traite_tous_les_profils_dus(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    frozen_now(FIXED_NOW)
    profiles = [factories.profile(last_scraped_at=None) for _ in range(3)]

    sched.check_due_profiles()

    assert sorted(fake_threads.started_profile_ids) == sorted(p.id for p in profiles)
    for profile in profiles:
        assert len(_jobs_of(db_session, profile.id)) == 1


# ===========================================================================
# 5. §4.2 chemin C — l'`except` global avorte le cycle (T3(e) du §7.4)
# ===========================================================================


def test_check_due_profiles_une_erreur_de_thread_ne_doit_pas_sauter_les_autres(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """Chemin C(b) — comportement CORRECT attendu.

    Trois profils dus ; le premier `t.start()` lève (`RuntimeError: can't
    start new thread`). Les deux profils suivants doivent quand même recevoir
    un job.
    """
    frozen_now(FIXED_NOW)
    profiles = [factories.profile(last_scraped_at=None) for _ in range(3)]
    fake_threads.fail_on_start_number = 1

    sched.check_due_profiles()

    victime = fake_threads.created[0].args[1]
    survivants = [p.id for p in profiles if p.id != victime]
    for pid in survivants:
        assert len(_jobs_of(db_session, pid)) == 1, (
            f"profil {pid} sauté après l'échec du thread du profil {victime}"
        )


def test_check_due_profiles_le_job_dun_thread_non_demarre_ne_doit_pas_rester_queued(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """Chemin C(a) — comportement CORRECT attendu.

    Le lot 1.3 propose deux implémentations acceptables : ne committer le job
    qu'après `t.start()` (aucun job en base), ou le marquer `failed`. Les deux
    satisfont l'assertion ci-dessous.
    """
    frozen_now(FIXED_NOW)
    factories.profile(last_scraped_at=None)
    fake_threads.fail_on_start_number = 1

    sched.check_due_profiles()

    victime = fake_threads.created[0].args[1]
    jobs = _jobs_of(db_session, victime)
    assert [j.status for j in jobs if j.status == "queued"] == []


def test_check_due_profiles_avale_lexception_du_thread(
    sched, factories, fake_threads, frozen_now, stub_pipeline
):
    """Assertion stable : `check_due_profiles` ne remonte JAMAIS d'exception.

    Elle restera vraie après le lot 1.3 (qui descend le `try/except` dans la
    boucle, sans le supprimer) : ce test ne contredit pas les deux xfail
    ci-dessus.
    """
    frozen_now(FIXED_NOW)
    factories.profile(last_scraped_at=None)
    fake_threads.fail_on_start_number = 1

    sched.check_due_profiles()  # ne doit rien lever


def test_check_due_profiles_une_erreur_db_ne_doit_pas_sauter_les_profils_suivants(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session, monkeypatch
):
    """Chemin C — le VRAI garde-fou du `try/except` DANS la boucle.

    Les autres tests du chemin C ne lèvent QUE depuis `t.start()`, or `t.start()`
    a son propre `try/except` dédié : ils restent verts même si le `try/except`
    par profil est remonté autour de la boucle entière. Ils ne gardent donc PAS
    la correction.

    Ici la panne vient de `db.commit()` — le déclencheur réaliste en production
    (« database is locked » sous SQLite/WAL, ou le `no such column` du risque #1).
    Cette exception naît DANS le corps de la boucle, hors de tout garde-fou
    local : seul le `try/except` par profil peut empêcher qu'elle avorte le
    cycle. 5 profils dus, le 2e échoue ; les 3 suivants doivent malgré tout
    recevoir leur job.
    """
    from sqlalchemy.exc import OperationalError

    frozen_now(FIXED_NOW)
    profiles = [factories.profile(last_scraped_at=None) for _ in range(5)]

    vraie_fabrique = sched.SessionLocal
    etat = {"commits": 0}

    class _SessionQuiCasseAuDeuxiemeCommit:
        def __init__(self, reel):
            self._reel = reel

        def __getattr__(self, nom):
            return getattr(self._reel, nom)

        def commit(self):
            etat["commits"] += 1
            if etat["commits"] == 2:
                raise OperationalError("INSERT INTO scrape_jobs", {}, Exception("database is locked"))
            return self._reel.commit()

    monkeypatch.setattr(
        sched, "SessionLocal", lambda: _SessionQuiCasseAuDeuxiemeCommit(vraie_fabrique())
    )

    sched.check_due_profiles()  # ne doit rien lever

    avec_job = [p.id for p in profiles if _jobs_of(db_session, p.id)]
    assert len(avec_job) == 4, (
        f"seuls les profils {avec_job} ont reçu un job : l'échec du commit du 2e "
        f"profil a avorté le cycle et sauté les suivants (chemin C, risque #54)"
    )


# ===========================================================================
# 6. _recover_stale_jobs — filet de boot (T3(d)) et §4.21
# ===========================================================================


@pytest.mark.parametrize("stale_status", ["queued", "running"])
def test_recover_stale_jobs_passe_queued_et_running_a_failed(
    sched, factories, frozen_now, db_session, reload_db, stale_status
):
    now = frozen_now(FIXED_NOW + 4242)
    profile = factories.profile()
    job = factories.scrape_job(profile, status=stale_status)

    sched._recover_stale_jobs()

    job = reload_db(job)
    assert job.status == "failed"
    assert job.error_message == "Interrupted by server restart"
    assert job.completed_at == now


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "empty", "partial"])
def test_recover_stale_jobs_ne_touche_pas_les_jobs_termines(
    sched, factories, frozen_now, reload_db, terminal_status
):
    frozen_now(FIXED_NOW)
    profile = factories.profile()
    job = factories.scrape_job(
        profile, status=terminal_status, error_message=None, completed_at=None
    )

    sched._recover_stale_jobs()

    job = reload_db(job)
    assert job.status == terminal_status
    assert job.error_message is None
    assert job.completed_at is None


def test_recover_stale_jobs_sans_job_stale_ne_leve_rien(sched, frozen_now):
    frozen_now(FIXED_NOW)

    sched._recover_stale_jobs()  # base vide : aucun commit, aucune exception


def test_recover_stale_jobs_traite_tous_les_profils(
    sched, factories, frozen_now, db_session
):
    frozen_now(FIXED_NOW)
    jobs = [factories.scrape_job(status="queued") for _ in range(3)]

    sched._recover_stale_jobs()

    db_session.expire_all()
    statuses = {
        db_session.get(ScrapeJob, j.id).status for j in jobs
    }
    assert statuses == {"failed"}


def test_recover_stale_jobs_ne_touche_pas_running_profiles(
    sched, factories, frozen_now
):
    """§4.21 — le filet est purement base de données.

    Il n'inspecte ni ne modifie `_running_profiles` (`scheduler.py:45-46`),
    l'ensemble LOCAL AU PROCESSUS : le job passe `failed` en base pendant que
    le verrou mémoire du profil, lui, reste tenu. Assertion stable, vraie
    quelle que soit la correction retenue côté propriété de processus.
    """
    frozen_now(FIXED_NOW)
    profile = factories.profile()
    factories.scrape_job(profile, status="running")
    sched._acquire_profile(profile.id)

    sched._recover_stale_jobs()

    assert profile.id in sched._running_profiles


@pytest.mark.xfail(
    strict=True,
    reason=(
        "risque #54 / §4.21 / lots 1.2 et 4.2 — `_recover_stale_jobs` n'a AUCUNE "
        "notion de propriété de processus : il passe `failed` TOUS les jobs "
        "`queued`/`running` sans filtre d'ancienneté, de PID ni de heartbeat "
        "(scheduler.py:443-451 ; `ScrapeJob` n'a aucune colonne d'appartenance, "
        "db.py:167-188). Sous les deux ordonnanceurs du reloader, le second boot "
        "tue les jobs VIVANTS du premier — puis en recrée : deux navigateurs sur "
        "le même profil. "
        "PORTÉE DU GARDE-FOU : ce test ne bascule en XPASS que pour un correctif "
        "fondé sur l'ANCIENNETÉ (comparaison à un horodatage de démarrage exposé "
        "sous le nom `_PROCESS_START_TS`) ou sur `_running_profiles`. Si le lot "
        "4.2 retient plutôt un PID ou un heartbeat en base, ce test devra être "
        "RÉÉCRIT en même temps — il resterait sinon silencieusement XFAIL."
    ),
)
def test_recover_stale_jobs_doit_epargner_un_job_du_processus_courant(
    sched, factories, frozen_now, reload_db, monkeypatch
):
    """Comportement CORRECT attendu : borner le filet aux jobs ANTÉRIEURS au
    démarrage du processus courant (correctif proposé en §4.21).

    Tout le temps est INJECTÉ (LOI ABSOLUE n°5) : `_now_ts` par la fixture, et
    la référence de démarrage du processus par `_PROCESS_START_TS`, le nom que
    le correctif « ancienneté » est censé exposer (`raising=False` : l'attribut
    n'existe pas encore aujourd'hui). Le job « vivant » est postérieur à cette
    référence et son profil est tenu dans `_running_profiles` — deux marqueurs
    d'appartenance au processus courant. Le job « fantôme » lui est antérieur
    et doit bien être récupéré.
    """
    frozen_now(FIXED_NOW)
    monkeypatch.setattr(sched, "_PROCESS_START_TS", FIXED_NOW - 10, raising=False)
    apres_le_boot = FIXED_NOW + 60

    profile_vivant = factories.profile()
    job_vivant = factories.scrape_job(
        profile_vivant,
        status="running",
        created_at=apres_le_boot,
        started_at=apres_le_boot,
    )
    sched._acquire_profile(profile_vivant.id)

    # Antérieur à `_PROCESS_START_TS` : c'est le job orphelin d'un processus mort.
    job_fantome = factories.scrape_job(status="queued", created_at=FIXED_NOW - 3600)

    sched._recover_stale_jobs()

    assert reload_db(job_fantome).status == "failed"
    assert reload_db(job_vivant).status == "running"


# ===========================================================================
# 7. retry_failed_media — remise en file des médias en échec
# ===========================================================================


def test_retry_failed_media_remet_un_download_failed_en_pending(
    sched, factories, fake_threads, frozen_now, stub_pipeline, reload_db
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    item = factories.media_item(
        profile,
        status="download_failed",
        error_message="boom",
        retry_count=0,
    )

    sched.retry_failed_media()

    item = reload_db(item)
    assert item.status == "pending"
    assert item.error_message is None
    # Lot 3.9 : la remise en file NE compte PLUS de tentative — l'échec réel a
    # déjà été compté par `pipeline.py:304`. Compter ici aussi plafonnait le
    # média à 3 tentatives réelles au lieu de 5 (risque #27, §4.12).
    assert item.retry_count == 0


def test_retry_failed_media_remet_un_upload_failed_en_downloaded(
    sched, factories, fake_threads, frozen_now, stub_pipeline, reload_db
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    item = factories.media_item(
        profile,
        status="upload_failed",
        error_message="drive HS",
        retry_count=1,
    )

    sched.retry_failed_media()

    item = reload_db(item)
    assert item.status == "downloaded"
    assert item.error_message is None
    # Lot 3.9 : idem côté upload — `pipeline.py:366` a déjà compté l'échec.
    assert item.retry_count == 1


@pytest.mark.parametrize("retry_count", [5, 6, 12])
def test_retry_failed_media_ignore_les_items_au_plafond(
    sched, factories, fake_threads, frozen_now, stub_pipeline, reload_db, retry_count
):
    """Filtre `retry_count < max_retries` avec `max_retries = 5` (`:226, 233`)."""
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    item = factories.media_item(
        profile, status="download_failed", retry_count=retry_count
    )

    sched.retry_failed_media()

    item = reload_db(item)
    assert item.status == "download_failed"
    assert item.retry_count == retry_count


@pytest.mark.parametrize("status", ["pending", "downloaded", "uploaded", "failed"])
def test_retry_failed_media_ne_touche_que_les_deux_statuts_dechec(
    sched, factories, fake_threads, frozen_now, stub_pipeline, reload_db, status
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    item = factories.media_item(profile, status=status, retry_count=0)

    sched.retry_failed_media()

    item = reload_db(item)
    assert item.status == status
    assert item.retry_count == 0


def test_retry_failed_media_cree_un_job_pour_le_profil_actif(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    factories.media_item(profile, status="download_failed", retry_count=0)

    sched.retry_failed_media()

    jobs = _jobs_of(db_session, profile.id)
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].triggered_by == "scheduler"
    assert fake_threads.started_profile_ids == [profile.id]


def test_retry_failed_media_le_job_dun_thread_non_demarre_ne_doit_pas_rester_queued(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """Même invariant que le chemin C, sur le 3e site d'appel de `t.start()`.

    `retry_failed_media` committe elle aussi un job `queued` AVANT de démarrer
    son thread. Si `t.start()` lève, le job reste `queued` sans porteur : le
    profil est gelé pour toujours puisque `check_due_profiles` saute tout
    profil ayant déjà un job actif (risque #54, §4.2). Les deux autres sites
    (`check_due_profiles`, `enqueue_manual_scrape`) sont gardés ; celui-ci
    doit l'être aussi.
    """
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    factories.media_item(profile, status="download_failed", retry_count=0)
    fake_threads.fail_on_start_number = 1

    sched.retry_failed_media()  # ne doit rien lever

    restes = [j for j in _jobs_of(db_session, profile.id) if j.status == "queued"]
    assert restes == [], (
        f"job {[j.id for j in restes]} laissé `queued` sans thread porteur : "
        f"le profil {profile.id} ne sera plus jamais scrapé automatiquement"
    )


def test_retry_failed_media_ne_double_pas_un_job_deja_actif(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """`if existing: continue` (`:301-302`) — sans conséquence d'après le
    risque #44 : l'étape 8 du pipeline reprendra tous les `pending`."""
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    existing = factories.scrape_job(profile, status="running")
    factories.media_item(profile, status="download_failed", retry_count=0)

    sched.retry_failed_media()

    assert [j.id for j in _jobs_of(db_session, profile.id)] == [existing.id]
    assert fake_threads.started == []


def test_retry_failed_media_ne_cree_aucun_job_pour_un_profil_inactif(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session
):
    """Assertion stable du risque #44 : `if not profile.is_active: continue`
    (`:289-290`). Reste vraie après le lot 3.2b, qui ne change QUE le sort de
    l'item, pas la création de job."""
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=False)
    factories.media_item(profile, status="download_failed", retry_count=0)

    sched.retry_failed_media()

    assert _jobs_of(db_session, profile.id) == []
    assert fake_threads.started == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "risque #44 / lot 3.2b — `retry_failed_media` commite le passage à "
        "`pending` (scheduler.py:238-241, :272) AVANT de constater qu'aucun job "
        "ne peut être créé pour un profil inactif (:289-290). L'item quitte les "
        "statuts d'échec, donc les cycles suivants ne le sélectionnent plus "
        "(:230-236) : il sort DÉFINITIVEMENT du retry automatique."
    ),
)
def test_retry_failed_media_un_item_de_profil_inactif_doit_rester_reessayable(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session, reload_db
):
    """Comportement CORRECT attendu : aucun item ne reste `pending` sans job.

    Deux implémentations satisfont l'assertion : laisser l'item en
    `download_failed` (correctif proposé du lot 3.2b) ou créer malgré tout un
    job. On n'impose donc pas laquelle.
    """
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=False)
    item = factories.media_item(profile, status="download_failed", retry_count=0)

    sched.retry_failed_media()

    item = reload_db(item)
    encore_reessayable = item.status in ("download_failed", "upload_failed")
    un_job_le_reprendra = bool(_jobs_of(db_session, profile.id))
    assert encore_reessayable or un_job_le_reprendra, (
        f"média {item.id} laissé en {item.status!r} sans aucun job : "
        "il ne sera plus jamais retenté automatiquement"
    )


def test_retry_failed_media_doit_autoriser_cinq_tentatives_reelles(
    sched, factories, fake_threads, frozen_now, stub_pipeline, db_session, reload_db
):
    """Comportement CORRECT attendu : 5 téléchargements réellement tentés.

    On simule le cycle complet sans réseau : `retry_failed_media()` remet
    l'item en file, puis on rejoue à la main l'échec de l'étape 8 du pipeline
    (`pipeline.py:302-304` : `status = "download_failed"` + `retry_count += 1`).
    """
    frozen_now(FIXED_NOW)
    profile = factories.profile(is_active=True)
    item = factories.media_item(profile, status="pending", retry_count=0)

    def _echec_reel_du_pipeline() -> None:
        """Reproduit fidèlement `pipeline.py:298-304`."""
        db_session.expire_all()
        row = db_session.get(MediaItem, item.id)
        row.status = "download_failed"
        row.error_message = "TransportError"
        row.retry_count += 1
        db_session.commit()

    tentatives = 1  # la 1re tentative a lieu dans le job initial
    _echec_reel_du_pipeline()

    for _ in range(10):  # borne dure : jamais de boucle infinie
        sched.retry_failed_media()
        if reload_db(item).status != "pending":
            break
        tentatives += 1
        _echec_reel_du_pipeline()

    assert tentatives == 5


# ===========================================================================
# 8. check_due_posts — posts planifiés et question du fuseau (§4.18)
# ===========================================================================


def test_check_due_posts_marque_ready_un_post_echu(
    sched, factories, frozen_now, reload_db
):
    now = frozen_now(FIXED_NOW)
    post = factories.scheduled_post(
        status="scheduled", scheduled_at=now - 60, updated_at=FIXED_NOW - 10_000
    )

    sched.check_due_posts()

    post = reload_db(post)
    assert post.status == "ready"
    assert post.updated_at == now


def test_check_due_posts_inclut_la_borne_exacte(
    sched, factories, frozen_now, reload_db
):
    """`ScheduledPost.scheduled_at <= now` (`:344`) : borne incluse."""
    now = frozen_now(FIXED_NOW)
    post = factories.scheduled_post(status="scheduled", scheduled_at=now)

    sched.check_due_posts()

    assert reload_db(post).status == "ready"


def test_check_due_posts_laisse_un_post_futur(sched, factories, frozen_now, reload_db):
    now = frozen_now(FIXED_NOW)
    post = factories.scheduled_post(status="scheduled", scheduled_at=now + 1)

    sched.check_due_posts()

    assert reload_db(post).status == "scheduled"


@pytest.mark.parametrize("status", ["draft", "published", "failed", "ready"])
def test_check_due_posts_ne_selectionne_que_le_statut_scheduled(
    sched, factories, frozen_now, reload_db, status
):
    now = frozen_now(FIXED_NOW)
    post = factories.scheduled_post(status=status, scheduled_at=now - 3600)

    sched.check_due_posts()

    assert reload_db(post).status == status


def test_check_due_posts_traite_tout_le_lot(
    sched, factories, frozen_now, db_session
):
    now = frozen_now(FIXED_NOW)
    posts = [
        factories.scheduled_post(status="scheduled", scheduled_at=now - i)
        for i in range(1, 4)
    ]

    sched.check_due_posts()

    db_session.expire_all()
    statuses = {db_session.get(ScheduledPost, p.id).status for p in posts}
    assert statuses == {"ready"}


@pytest.mark.parametrize("tz_name", ["UTC", "Europe/Paris", "Pacific/Kiritimati"])
def test_check_due_posts_ne_depend_pas_du_fuseau_du_serveur(
    sched, factories, frozen_now, reload_db, timezone, tz_name
):
    """§4.18 — la SÉLECTION est purement epoch, donc insensible au fuseau.

    `scheduled_at` et `_now_ts()` sont deux epochs : la comparaison de
    `scheduler.py:344` ne peut pas décaler. Le décalage horaire du calendrier
    naît AILLEURS — à la sérialisation (`calendar/api.py:88`, qui rend une ISO
    naïve). Ce test verrouille l'innocence du scheduler pour que la correction
    du lot 5.2 ne se trompe pas de fichier.
    """
    timezone(tz_name)
    now = frozen_now(FIXED_NOW)
    echu = factories.scheduled_post(status="scheduled", scheduled_at=now - 1)
    futur = factories.scheduled_post(status="scheduled", scheduled_at=now + 1)

    sched.check_due_posts()

    assert reload_db(echu).status == "ready"
    assert reload_db(futur).status == "scheduled"


@pytest.mark.parametrize("tz_name", ["UTC", "Europe/Paris", "Pacific/Kiritimati"])
def test_now_ts_est_un_epoch_reel_quel_que_soit_le_fuseau(
    sched, monkeypatch, timezone, tz_name
):
    """§4.18 — `_now_ts()` (`scheduler.py:73-74`) fait bien l'aller-retour.

    `datetime.now()` rend une heure locale NAÏVE, et `.timestamp()` la
    réinterprète dans le MÊME fuseau : l'epoch produit est correct partout.
    Le temps est injecté (aucune horloge murale) en remplaçant `datetime` dans
    le module — il y est importé par valeur (`scheduler.py:17`).
    """
    timezone(tz_name)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            # Heure locale correspondant à FIXED_NOW dans le fuseau courant.
            return datetime.fromtimestamp(FIXED_NOW, tz)

    monkeypatch.setattr(sched, "datetime", _FrozenDatetime)

    assert sched._now_ts() == FIXED_NOW


# ===========================================================================
# 9. enqueue_manual_scrape — le TROISIÈME créateur de jobs (scrape manuel)
# ===========================================================================
# `check_due_profiles` et `check_due_posts` sont couverts ci-dessus, mais le
# point d'entrée que l'UTILISATEUR déclenche lui-même ne l'était pas. Il est
# appelé par deux routes (`web/api.py:257` « Manual scrape triggered » et
# `web/api.py:395` « Job retry triggered ») et reproduit EXACTEMENT le motif
# du §4.2 chemin C : job committé côté vue, puis `t.start()` non gardé
# (`scheduler.py:590-595`).


def test_enqueue_manual_scrape_demarre_un_thread_vers_run_job_safe(
    sched, factories, fake_threads, make_profile
):
    """Chemin nominal : UN thread, visant `_run_job_safe(job_id, profile_id)`."""
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")

    sched.enqueue_manual_scrape(profile.id, job.id)

    assert len(fake_threads.started) == 1
    thread = fake_threads.started[0]
    assert thread.target is sched._run_job_safe
    assert thread.args == (job.id, profile.id)
    assert thread.name == f"manual-scrape-{profile.id}"


def test_enqueue_manual_scrape_un_thread_qui_ne_demarre_pas_remonte_lerreur(
    sched, factories, fake_threads, make_profile, db_session
):
    """Le lot 1.3 marque le job `failed` mais NE MANGE PAS l'exception.

    `RuntimeError: can't start new thread` doit continuer de remonter telle
    quelle dans la vue Flask (où elle est avalée par son `except Exception`,
    `web/api.py:260`, qui rend un 500 à l'utilisateur) : sans cela un échec de
    démarrage de thread passerait pour un succès côté UI.

    (Ce test remplace la caractérisation « le job reste `queued` » : ce bug est
    mort avec le lot 1.3, et le sort du job est désormais gardé par
    `test_enqueue_manual_scrape_un_thread_qui_ne_demarre_pas_doit_marquer_le_job_failed`.)
    """
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")
    fake_threads.fail_on_start_number = 1

    with pytest.raises(RuntimeError, match="can't start new thread"):
        sched.enqueue_manual_scrape(profile.id, job.id)

    db_session.expire_all()
    assert db_session.get(ScrapeJob, job.id).status != "queued"


def test_enqueue_manual_scrape_un_thread_qui_ne_demarre_pas_doit_marquer_le_job_failed(
    sched, factories, fake_threads, make_profile, db_session
):
    """COMPORTEMENT ATTENDU : aucun job ne doit rester `queued` sans porteur."""
    profile = make_profile()
    job = factories.scrape_job(profile, status="queued")
    fake_threads.fail_on_start_number = 1

    try:
        sched.enqueue_manual_scrape(profile.id, job.id)
    except RuntimeError:
        pass

    db_session.expire_all()
    assert db_session.get(ScrapeJob, job.id).status == "failed"


# ===========================================================================
# 10. Câblage de boot — assertions STATIQUES (APScheduler jamais démarré)
# ===========================================================================
# §7.4 interdit de déclencher `start_scheduler()`. On vérifie donc par lecture
# de source que les garanties du résumé exécutif d'AUDIT.md §1 point 5 sont
# bien CÂBLÉES — le comportement de `_recover_stale_jobs` est testé plus haut,
# mais rien ne gardait le fait qu'il soit appelé.


def test_start_scheduler_appelle_recover_stale_jobs_avant_de_planifier(sched):
    """Le correctif du commit 59d3ee2 doit rester branché : `_recover_stale_jobs()`
    est appelé, et AVANT le premier `add_job` (sans quoi `initial_check`
    recréerait des jobs sur des profils encore gelés — risque #5)."""
    source = inspect.getsource(sched.start_scheduler)

    assert "_recover_stale_jobs()" in source, (
        "start_scheduler n'appelle plus _recover_stale_jobs : le risque #5 "
        "redevient permanent (jobs orphelins au reboot)"
    )
    assert source.index("_recover_stale_jobs()") < source.index("add_job"), (
        "_recover_stale_jobs doit précéder la planification des jobs"
    )


def test_start_scheduler_est_idempotent_par_construction(sched):
    """Garde d'idempotence `if scheduler.running: return` (`scheduler.py:470-472`) :
    sous le reloader Werkzeug, un second appel ne doit pas doubler les jobs."""
    source = inspect.getsource(sched.start_scheduler)
    corps_avant_recover = source[: source.index("_recover_stale_jobs()")]

    assert "scheduler.running" in corps_avant_recover
    assert "return" in corps_avant_recover
