"""Tests unitaires de app/pipeline_support.py — le bracket commun (statut + diffusion, filet
de sécurité) mutualisé entre les 3 pipelines LLM (§8 AUDIT_BACKEND.md). Avant factorisation,
ce comportement n'était couvert qu'indirectement, à travers les tests d'intégration API lourds
(TestClient + polling) de chacun des 3 pipelines — jamais testé isolément."""
from __future__ import annotations

import pytest

from app.pipeline_support import finalize_stage, run_pipeline_safely, start_stage
from app.progress import progress_manager
from app.store.db import session_scope
from app.store.models import DossierStatus
from app.store.repository import create_dossier, get_dossier


@pytest.fixture
def dossier_id(isolated_workspace):
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        return dossier.id


@pytest.mark.asyncio
async def test_start_stage_sets_status_and_broadcasts(dossier_id):
    events: list[dict] = []
    orig_broadcast = progress_manager.broadcast

    async def _capture(*args, **kwargs):
        events.append(kwargs)
        await orig_broadcast(*args, **kwargs)

    progress_manager.broadcast = _capture
    try:
        await start_stage(
            dossier_id, status=DossierStatus.CLASSIFYING, stage="classify", message="en cours…"
        )
    finally:
        progress_manager.broadcast = orig_broadcast

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.CLASSIFYING.value

    assert len(events) == 1
    assert events[0]["status"] == DossierStatus.CLASSIFYING.value
    assert events[0]["message"] == "en cours…"


@pytest.mark.asyncio
async def test_finalize_stage_recomputes_counters_sets_status_and_broadcasts(dossier_id):
    recompute_calls = []

    def _recompute(session, dossier) -> None:
        recompute_calls.append(dossier.id)
        dossier.total_files = 7

    events: list[dict] = []
    orig_broadcast = progress_manager.broadcast

    async def _capture(*args, **kwargs):
        events.append(kwargs)
        await orig_broadcast(*args, **kwargs)

    progress_manager.broadcast = _capture
    try:
        final_counters = await finalize_stage(
            dossier_id,
            status=DossierStatus.CLASSIFIED,
            stage="classify",
            message="terminé",
            counters=lambda d: {"total_files": d.total_files},
            recompute=_recompute,
            before_status_change=lambda s, d: setattr(d, "current_step", 1),
        )
    finally:
        progress_manager.broadcast = orig_broadcast

    assert recompute_calls == [dossier_id]
    assert final_counters == {"total_files": 7}

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.CLASSIFIED.value
        assert dossier.current_step == 1
        assert dossier.total_files == 7

    assert len(events) == 1
    assert events[0]["counters"] == {"total_files": 7}
    assert events[0]["status"] == DossierStatus.CLASSIFIED.value


@pytest.mark.asyncio
async def test_run_pipeline_safely_lets_success_through_untouched(dossier_id):
    called = False

    async def _run() -> None:
        nonlocal called
        called = True

    await run_pipeline_safely(dossier_id, _run, what="test")

    assert called is True
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        # run() n'a rien changé lui-même ; le filet de sécurité ne doit rien modifier non plus
        # tant qu'aucune exception n'est levée.
        assert dossier.status == DossierStatus.UPLOADED.value


@pytest.mark.asyncio
async def test_run_pipeline_safely_marks_dossier_error_and_broadcasts_on_exception(dossier_id):
    """Cas central du filet de sécurité générique : une exception non prévue dans le corps du
    pipeline doit basculer le dossier en erreur et diffuser l'échec, au lieu de le laisser
    bloqué silencieusement à mi-chemin (comportement historiquement dupliqué 3 fois avant
    factorisation, jamais testé isolément)."""

    async def _run() -> None:
        raise RuntimeError("panne simulée du moteur LLM")

    events: list[dict] = []
    orig_broadcast = progress_manager.broadcast

    async def _capture(*args, **kwargs):
        events.append(kwargs)
        await orig_broadcast(*args, **kwargs)

    progress_manager.broadcast = _capture
    try:
        # Ne doit PAS lever : le filet de sécurité absorbe l'exception.
        await run_pipeline_safely(dossier_id, _run, what="le pipeline de test")
    finally:
        progress_manager.broadcast = orig_broadcast

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.ERROR.value
        assert dossier.error_message == "panne simulée du moteur LLM"

    assert len(events) == 1
    assert events[0]["stage"] == "error"
    assert events[0]["status"] == DossierStatus.ERROR.value
    assert events[0]["message"] == "panne simulée du moteur LLM"


@pytest.mark.asyncio
async def test_run_pipeline_safely_handles_dossier_already_deleted(dossier_id):
    """Un dossier supprimé pendant qu'un pipeline tourne dessus (route DELETE, cf.
    AUDIT_BACKEND.md §6) ne doit pas faire planter le filet de sécurité lui-même : il doit
    simplement ne rien pouvoir mettre à jour, sans lever de nouvelle exception."""
    from app.store.repository import delete_dossier

    async def _run() -> None:
        with session_scope() as s:
            delete_dossier(s, dossier_id)
        raise RuntimeError("échec après suppression concurrente")

    # Ne doit pas lever, même si le dossier n'existe plus au moment de marquer l'erreur.
    await run_pipeline_safely(dossier_id, _run, what="le pipeline de test")

    with session_scope() as s:
        assert get_dossier(s, dossier_id) is None
