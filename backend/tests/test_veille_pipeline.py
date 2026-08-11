"""Balayage de veille de bout en bout : filtrage, idempotence, création du dossier.

Les sources HTTP sont substituées : ce qui est vérifié ici est l'orchestration (ce qui est
retenu, ce qui est enregistré, ce qui est créé), pas la disponibilité d'API externes.
"""
from __future__ import annotations

import datetime as dt
import zipfile

import pytest

from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, VeilleNotice, VeilleNoticeStatus
from app.store.veille_repository import find_notice, last_scan, list_notices
from app.veille.notice import Notice
from app.veille.retrieval import RetrievalOutcome, RetrievalStatus


@pytest.fixture
def veille_env(isolated_workspace, monkeypatch):
    """Workspace isolé + critères de ciblage rechargés (le YAML est mis en cache)."""
    from app.veille.criteria import get_veille_criteria

    get_veille_criteria.cache_clear()
    monkeypatch.setenv("AOP_VEILLE_AUTO_RETRIEVAL", "false")
    from app.settings import get_settings

    get_settings.cache_clear()
    yield isolated_workspace
    get_veille_criteria.cache_clear()
    get_settings.cache_clear()


def _notice(source: str, source_id: str, objet: str, *, buyer="Ville de Rennes", deadline=None, dce=None):
    return Notice(
        source=source,
        source_id=source_id,
        objet=objet,
        buyer_name=buyer,
        deadline_at=deadline or dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc),
        notice_url=f"https://exemple/{source_id}",
        dce_url=dce,
    )


def _patch_sources(monkeypatch, boamp: list[Notice], ted: list[Notice], errors: list[str] | None = None):
    import app.veille.pipeline as pipeline

    monkeypatch.setattr(pipeline, "search_boamp", lambda criteria, since: (boamp, errors or []))
    monkeypatch.setattr(pipeline, "search_ted", lambda criteria, since: (ted, []))


async def test_scan_ne_retient_que_les_avis_d_assurance_construction(veille_env, monkeypatch):
    from app.veille.pipeline import run_scan

    _patch_sources(
        monkeypatch,
        boamp=[
            _notice("boamp", "26-1", "Assurance dommages ouvrage — groupe scolaire"),
            _notice("boamp", "26-2", "Assurance flotte automobile de la collectivité"),
            _notice("boamp", "26-3", "Fourniture de repas en liaison froide"),
        ],
        ted=[],
    )

    report = await run_scan()
    assert report.notices_seen == 3
    assert report.notices_retained == 1
    assert report.notices_new == 1

    with session_scope() as session:
        notices = list_notices(session)
        assert [n.source_id for n in notices] == ["26-1"]
        assert notices[0].status == VeilleNoticeStatus.MANUAL_REQUIRED.value  # aucun lien DCE


async def test_scan_fusionne_les_publications_boamp_et_ted(veille_env, monkeypatch):
    from app.veille.pipeline import run_scan

    deadline = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
    _patch_sources(
        monkeypatch,
        boamp=[_notice("boamp", "26-1", "Assurance dommages ouvrage", deadline=deadline)],
        ted=[
            _notice(
                "ted",
                "44-2026",
                "Assurance dommages ouvrage",
                deadline=deadline,
                dce="https://portail.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X",
            )
        ],
    )

    report = await run_scan()
    assert report.notices_retained == 1

    with session_scope() as session:
        notices = list_notices(session)
        assert len(notices) == 1
        notice = notices[0]
        assert notice.source == "ted"
        assert "26-1" in notice.also_published_json
        # Le lien de TED rend le retrait automatisable : l'avis naît « à récupérer ».
        assert notice.status == VeilleNoticeStatus.NEW.value
        assert notice.retrieval_platform == "Atexo / PLACE"


async def test_un_second_scan_ne_duplique_pas_et_preserve_une_decision_humaine(veille_env, monkeypatch):
    """Un avis écarté ne doit pas revenir « neuf » parce que la source l'a republié."""
    from app.store.veille_repository import set_notice_status
    from app.veille.pipeline import run_scan

    avis = _notice("boamp", "26-1", "Assurance dommages ouvrage — groupe scolaire")
    _patch_sources(monkeypatch, boamp=[avis], ted=[])

    await run_scan()
    with session_scope() as session:
        notice = find_notice(session, "boamp", "26-1")
        set_notice_status(session, notice, VeilleNoticeStatus.DISMISSED)

    # Même avis republié avec une date limite repoussée (cas réel : avis rectificatif).
    avis.deadline_at = dt.datetime(2027, 1, 15, tzinfo=dt.timezone.utc)
    report = await run_scan()

    assert report.notices_retained == 1
    assert report.notices_new == 0
    with session_scope() as session:
        assert session.query(VeilleNotice).count() == 1
        notice = find_notice(session, "boamp", "26-1")
        assert notice.status == VeilleNoticeStatus.DISMISSED.value  # décision préservée
        assert notice.deadline_at.year == 2027  # donnée éditoriale rafraîchie


async def test_scan_remonte_l_erreur_d_une_source_sans_perdre_l_autre(veille_env, monkeypatch):
    from app.veille.pipeline import run_scan

    _patch_sources(
        monkeypatch,
        boamp=[],
        ted=[_notice("ted", "44-2026", "Assurance tous risques chantier")],
        errors=["BOAMP indisponible : timeout"],
    )
    report = await run_scan()
    assert report.notices_retained == 1
    assert report.errors == ["BOAMP indisponible : timeout"]

    with session_scope() as session:
        scan = last_scan(session)
        assert scan.finished_at is not None
        assert "BOAMP indisponible" in scan.errors


async def test_retrait_reussi_cree_un_dossier_non_lance(veille_env, monkeypatch, tmp_path):
    """Le dossier est créé au statut `uploaded` : aucun appel LLM n'est engagé par la veille,
    l'analyse ne démarre que sur décision humaine."""
    import app.veille.pipeline as pipeline
    from app.veille.pipeline import retrieve_notice_dce, run_scan

    _patch_sources(
        monkeypatch,
        boamp=[],
        ted=[
            _notice(
                "ted",
                "44-2026",
                "Assurance dommages ouvrage",
                dce="https://portail.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X",
            )
        ],
    )
    await run_scan()

    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CCTP.txt", "contenu")
    archive_bytes = archive.read_bytes()

    def fake_fetch(url, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive_bytes)
        return RetrievalOutcome(
            status=RetrievalStatus.DOWNLOADED,
            platform="Atexo / PLACE",
            message="ok",
            archive_path=destination,
            filename="DCE-2026.zip",
        )

    monkeypatch.setattr(pipeline, "fetch_dce", fake_fetch)

    with session_scope() as session:
        notice_id = list_notices(session)[0].id

    assert retrieve_notice_dce(notice_id) == RetrievalStatus.DOWNLOADED

    with session_scope() as session:
        notice = session.get(VeilleNotice, notice_id)
        assert notice.status == VeilleNoticeStatus.RETRIEVED.value
        assert notice.dossier_id is not None

        dossier_id = notice.dossier_id
        dossier = session.get(Dossier, dossier_id)
        assert dossier.original_filename == "DCE-2026.zip"
        assert dossier.status == DossierStatus.UPLOADED.value
        assert dossier.upload_sha256 is not None

    # Le zip atterrit là où le pipeline d'ingestion l'attend : un dossier issu de la veille est
    # ensuite indiscernable d'un dossier déposé à la main.
    zip_path = veille_env / dossier_id / "upload.zip"
    assert zip_path.exists()
    assert zipfile.ZipFile(zip_path).namelist() == ["CCTP.txt"]
    # Le dépôt temporaire est nettoyé, quel que soit le résultat.
    assert not (veille_env / ".veille" / notice_id).exists()


async def test_retrait_impossible_est_signale_sans_creer_de_dossier(veille_env, monkeypatch):
    import app.veille.pipeline as pipeline
    from app.veille.pipeline import retrieve_notice_dce, run_scan

    _patch_sources(monkeypatch, boamp=[_notice("boamp", "26-1", "Assurance dommages ouvrage")], ted=[])
    await run_scan()

    monkeypatch.setattr(
        pipeline,
        "fetch_dce",
        lambda url, destination: RetrievalOutcome(
            status=RetrievalStatus.MANUAL_REQUIRED,
            platform="AWS / marches-publics.info",
            message="captcha",
        ),
    )

    with session_scope() as session:
        notice_id = list_notices(session)[0].id

    assert retrieve_notice_dce(notice_id) == RetrievalStatus.MANUAL_REQUIRED
    with session_scope() as session:
        notice = session.get(VeilleNotice, notice_id)
        assert notice.status == VeilleNoticeStatus.MANUAL_REQUIRED.value
        assert notice.dossier_id is None
        assert notice.retrieval_attempted_at is not None
        assert session.query(Dossier).count() == 0


async def test_les_avis_expires_ne_remontent_pas_en_tete(veille_env, monkeypatch):
    """Un tri naïf par date limite croissante mettrait les avis EXPIRÉS en premier, puisque
    leur date est la plus ancienne. L'ordre attendu est : à venir (le plus proche d'abord),
    puis échéance inconnue, puis expirés."""
    from app.veille.pipeline import run_scan

    now = dt.datetime.now(dt.timezone.utc)

    def avis(source_id: str, buyer: str, deadline: dt.datetime | None):
        notice = _notice("boamp", source_id, f"Assurance dommages ouvrage — {source_id}", buyer=buyer)
        notice.deadline_at = deadline  # `_notice` impose une date par défaut
        return notice

    _patch_sources(
        monkeypatch,
        boamp=[
            avis("expire", "A", now - dt.timedelta(days=5)),
            avis("lointain", "B", now + dt.timedelta(days=40)),
            avis("proche", "C", now + dt.timedelta(days=3)),
            avis("inconnu", "D", None),
        ],
        ted=[],
    )

    await run_scan()
    with session_scope() as session:
        assert [n.source_id for n in list_notices(session)] == [
            "proche",
            "lointain",
            "inconnu",
            "expire",
        ]
