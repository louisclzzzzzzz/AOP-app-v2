"""Endpoints REST de l'étape 2 (§5, §8 du PLAN) : checklist de pièces, sélection par dossier,
lancement de l'analyse de complétude, correction manuelle au checkpoint, validation, rapport."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.dossiers import dossier_to_out, reopen_stage
from app.api.schemas import (
    CompletenessApplyOut,
    CompletenessCorrectionIn,
    CompletenessEntryOut,
    CompletenessSelectionIn,
    DossierOut,
    PieceOut,
)
from app.completeness.pieces_checklist import load_pieces_checklist
from app.completeness.pipeline import ensure_checks_initialized, run_completeness_pipeline
from app.completeness.report import REPORT_JSON_FILENAME, validate_completeness
from app.pipeline_support import run_pipeline_safely
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import CompletenessCheck, DossierStatus, Presence, Certainty
from app.store.repository import (
    get_completeness_check_by_piece,
    get_dossier,
    list_completeness_checks,
    recompute_completeness_counters,
    reopen_completeness,
    set_completeness_correction,
    set_completeness_selection,
    set_dossier_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["completeness"])
pieces_checklist_router = APIRouter(prefix="/api/pieces-checklist", tags=["completeness"])

_VALID_PRESENCE = {p.value for p in Presence}
_VALID_CERTAINTY = {c.value for c in Certainty}

_RUNNABLE_STATUSES = (DossierStatus.REORGANIZED.value, DossierStatus.COMPLETENESS_REVIEW.value)


def _entry_to_out(check: CompletenessCheck) -> CompletenessEntryOut:
    piece = load_pieces_checklist().by_id(check.piece_id)
    assert piece is not None
    return CompletenessEntryOut(
        piece_id=check.piece_id,
        libelle=piece.libelle,
        phase=piece.phase,
        alias=piece.alias,
        obligatoire=piece.obligatoire,
        is_selected=check.is_selected,
        status=check.status,
        completeness_error=check.completeness_error,
        match_layer=check.match_layer,
        proposed_presence=check.proposed_presence,
        proposed_certainty=check.proposed_certainty,
        confidence=check.proposed_confidence,
        justification=check.proposed_justification,
        matched_document_ids=(
            json.loads(check.proposed_matched_document_ids_json)
            if check.proposed_matched_document_ids_json
            else []
        ),
        matched_lots=(
            json.loads(check.proposed_matched_lots_json) if check.proposed_matched_lots_json else None
        ),
        model_name=check.completeness_model,
        model_version=check.completeness_model_version,
        final_presence=check.final_presence,
        final_certainty=check.final_certainty,
        is_manually_corrected=check.is_manually_corrected,
    )


async def _run_completeness_safely(dossier_id: str) -> None:
    await run_pipeline_safely(
        dossier_id, lambda: run_completeness_pipeline(dossier_id), what="le pipeline de complétude"
    )


@pieces_checklist_router.get("", response_model=list[PieceOut])
async def get_pieces_checklist() -> list[PieceOut]:
    checklist = load_pieces_checklist()
    return [
        PieceOut(
            id=p.id,
            libelle=p.libelle,
            phase=p.phase,
            alias=p.alias,
            categorie_attendue=p.categorie_attendue,
            obligatoire=p.obligatoire,
            par_lot=p.par_lot,
        )
        for p in checklist.pieces
    ]


@router.get("/{dossier_id}/completeness", response_model=list[CompletenessEntryOut])
async def get_completeness(dossier_id: str) -> list[CompletenessEntryOut]:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        checks = ensure_checks_initialized(s, dossier_id)
        return [_entry_to_out(c) for c in checks]


@router.patch("/{dossier_id}/completeness/selection", response_model=list[CompletenessEntryOut])
async def update_completeness_selection(
    dossier_id: str, selection: CompletenessSelectionIn
) -> list[CompletenessEntryOut]:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        ensure_checks_initialized(s, dossier_id)
        for item in selection.selection:
            check = get_completeness_check_by_piece(s, dossier_id, item.piece_id)
            if check is None:
                raise HTTPException(400, f"Pièce inconnue : {item.piece_id}")
            set_completeness_selection(s, check, is_selected=item.is_selected)
        recompute_completeness_counters(s, dossier)
        return [_entry_to_out(c) for c in list_completeness_checks(s, dossier_id)]


@router.post("/{dossier_id}/completeness/run", response_model=DossierOut)
async def run_completeness(dossier_id: str, background_tasks: BackgroundTasks) -> DossierOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _RUNNABLE_STATUSES:
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour l'analyse de complétude (statut actuel : "
                f"{dossier.status}). La réorganisation (étape 1) doit être terminée au préalable.",
            )
        ensure_checks_initialized(s, dossier_id)
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_completeness_safely, dossier_id)
    return result


@router.patch("/{dossier_id}/completeness/{piece_id}", response_model=CompletenessEntryOut)
async def correct_completeness(
    dossier_id: str, piece_id: str, correction: CompletenessCorrectionIn
) -> CompletenessEntryOut:
    if correction.presence not in _VALID_PRESENCE:
        raise HTTPException(400, f"Statut de présence inconnu : {correction.presence}")
    if correction.certainty is not None and correction.certainty not in _VALID_CERTAINTY:
        raise HTTPException(400, f"Niveau de sûreté inconnu : {correction.certainty}")

    with session_scope() as s:
        check = get_completeness_check_by_piece(s, dossier_id, piece_id)
        if check is None:
            raise HTTPException(404, "Pièce introuvable pour ce dossier")
        set_completeness_correction(
            s, check, presence=correction.presence, certainty=correction.certainty
        )
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        recompute_completeness_counters(s, dossier)
        return _entry_to_out(check)


@router.post("/{dossier_id}/completeness/validate", response_model=CompletenessApplyOut)
async def validate_completeness_endpoint(dossier_id: str) -> CompletenessApplyOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in (
            DossierStatus.COMPLETENESS_REVIEW.value,
            DossierStatus.COMPLETENESS_VALIDATED.value,
        ):
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour la validation de complétude (statut actuel : "
                f"{dossier.status}).",
            )

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        report = validate_completeness(s, dossier, dossier_dir=dossier_dir)
        set_dossier_status(s, dossier, DossierStatus.COMPLETENESS_VALIDATED)
        dossier_out = dossier_to_out(dossier)

    await progress_manager.broadcast(
        dossier_id,
        stage="completeness",
        status=DossierStatus.COMPLETENESS_VALIDATED.value,
        message=f"Complétude validée — {report['total_pieces_selected']} pièce(s)",
    )
    return CompletenessApplyOut(dossier=dossier_out, report=report)


_REOPENABLE_COMPLETENESS_STATUSES = (
    DossierStatus.COMPLETENESS_VALIDATED.value,
    DossierStatus.EXTRACTION_REVIEW.value,
    DossierStatus.EXTRACTION_VALIDATED.value,
)


@router.post("/{dossier_id}/completeness/reopen", response_model=DossierOut)
async def reopen_completeness_endpoint(dossier_id: str) -> DossierOut:
    return await reopen_stage(
        dossier_id,
        allowed_statuses=_REOPENABLE_COMPLETENESS_STATUSES,
        reopen_fn=reopen_completeness,
        not_ready_message="Ce dossier ne peut pas être rouvert pour correction de la complétude (statut actuel : {status}).",
        stage="completeness",
        target_status=DossierStatus.COMPLETENESS_REVIEW,
        broadcast_message="Complétude rouverte pour correction",
    )


@router.get("/{dossier_id}/completeness/report")
async def get_completeness_report(dossier_id: str) -> dict:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if not dossier.completeness_report_json_path:
            raise HTTPException(404, "Aucun rapport de complétude disponible pour ce dossier")

    settings = get_settings()
    report_path = settings.workspace_dir / dossier_id / REPORT_JSON_FILENAME
    if not report_path.exists():
        raise HTTPException(404, "Fichier de rapport introuvable sur disque")
    return json.loads(report_path.read_text(encoding="utf-8"))
