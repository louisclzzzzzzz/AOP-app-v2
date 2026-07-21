"""Endpoints REST de l'étape 3 (§6, §8 du PLAN) : schéma d'extraction, lancement, correction
manuelle au checkpoint, validation, rapport."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.dossiers import dossier_to_out, reopen_stage
from app.api.schemas import (
    ExtractionApplyOut,
    ExtractionCorrectionIn,
    ExtractionEntryOut,
    ExtractionFieldOut,
    ExtractionRunIn,
    DossierOut,
)
from app.extraction.extraction_schema import load_extraction_schema
from app.extraction.pipeline import deepen_missing_fields, ensure_results_initialized, run_extraction_pipeline
from app.extraction.report import REPORT_JSON_FILENAME, validate_extraction
from app.pipeline_support import run_pipeline_safely
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import DossierStatus, ExtractionResult
from app.store.repository import (
    get_dossier,
    get_extraction_result_by_field,
    recompute_extraction_counters,
    reopen_extraction,
    set_dossier_status,
    set_extraction_correction,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["extraction"])
extraction_schema_router = APIRouter(prefix="/api/extraction-schema", tags=["extraction"])

_RUNNABLE_STATUSES = (DossierStatus.COMPLETENESS_VALIDATED.value, DossierStatus.EXTRACTION_REVIEW.value)


def _entry_to_out(result: ExtractionResult) -> ExtractionEntryOut:
    f = load_extraction_schema().by_id(result.field_id)
    assert f is not None
    return ExtractionEntryOut(
        field_id=result.field_id,
        libelle=f.libelle,
        section=f.section,
        resultat_attendu=f.resultat_attendu,
        status=result.status,
        extraction_error=result.extraction_error,
        match_layer=result.match_layer,
        proposed_value=result.proposed_value,
        confidence=result.proposed_confidence,
        justification=result.proposed_justification,
        citation=result.proposed_citation,
        sources=json.loads(result.proposed_sources_json) if result.proposed_sources_json else [],
        cross_check_status=result.cross_check_status,
        model_name=result.extraction_model,
        model_version=result.extraction_model_version,
        final_value=result.final_value,
        is_manually_corrected=result.is_manually_corrected,
    )


async def _run_extraction_safely(dossier_id: str, document_ids: list[str] | None = None) -> None:
    await run_pipeline_safely(
        dossier_id,
        lambda: run_extraction_pipeline(dossier_id, document_ids=document_ids),
        what="le pipeline d'extraction",
    )


@extraction_schema_router.get("", response_model=list[ExtractionFieldOut])
async def get_extraction_schema() -> list[ExtractionFieldOut]:
    schema = load_extraction_schema()
    return [
        ExtractionFieldOut(
            id=f.id,
            libelle=f.libelle,
            section=f.section,
            resultat_attendu=f.resultat_attendu,
            reference_categories=f.reference_categories,
        )
        for f in schema.fields
    ]


@router.get("/{dossier_id}/extraction", response_model=list[ExtractionEntryOut])
async def get_extraction(dossier_id: str) -> list[ExtractionEntryOut]:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        results = ensure_results_initialized(s, dossier_id)
        return [_entry_to_out(r) for r in results]


@router.post("/{dossier_id}/extraction/run", response_model=DossierOut)
async def run_extraction(
    dossier_id: str, background_tasks: BackgroundTasks, body: ExtractionRunIn | None = None
) -> DossierOut:
    """`body.document_ids` (optionnel) : sélection manuelle de documents — restreint tout le run
    à cette liste au lieu du filtrage standard par catégorie de référence (§pipeline.py)."""
    document_ids = body.document_ids if body and body.document_ids else None
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _RUNNABLE_STATUSES:
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour l'extraction (statut actuel : "
                f"{dossier.status}). La complétude (étape 2) doit être validée au préalable.",
            )
        ensure_results_initialized(s, dossier_id)
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_extraction_safely, dossier_id, document_ids)
    return result


@router.patch("/{dossier_id}/extraction/{field_id}", response_model=ExtractionEntryOut)
async def correct_extraction(
    dossier_id: str, field_id: str, correction: ExtractionCorrectionIn
) -> ExtractionEntryOut:
    with session_scope() as s:
        result = get_extraction_result_by_field(s, dossier_id, field_id)
        if result is None:
            raise HTTPException(404, "Champ introuvable pour ce dossier")
        set_extraction_correction(s, result, final_value=correction.final_value)
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        recompute_extraction_counters(s, dossier)
        return _entry_to_out(result)


@router.post("/{dossier_id}/extraction/deepen", response_model=list[ExtractionEntryOut])
async def deepen_missing_extraction_fields(dossier_id: str) -> list[ExtractionEntryOut]:
    """Approfondissement de TOUS les champs restés absents en un seul passage : recherche
    élargie par mots-clés sur tout le dossier (§extraction/engine.py), déclenchée explicitement
    par l'expert — ne touche jamais aux champs déjà trouvés."""
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")

    try:
        await deepen_missing_fields(dossier_id)
    except Exception as exc:
        logger.exception("Échec de l'approfondissement des champs manquants pour %s", dossier_id)
        raise HTTPException(500, f"Échec de l'approfondissement : {exc}") from exc

    with session_scope() as s:
        results = ensure_results_initialized(s, dossier_id)
        return [_entry_to_out(r) for r in results]


@router.post("/{dossier_id}/extraction/validate", response_model=ExtractionApplyOut)
async def validate_extraction_endpoint(dossier_id: str) -> ExtractionApplyOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in (
            DossierStatus.EXTRACTION_REVIEW.value,
            DossierStatus.EXTRACTION_VALIDATED.value,
        ):
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour la validation de l'extraction (statut actuel : "
                f"{dossier.status}).",
            )

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        report = validate_extraction(s, dossier, dossier_dir=dossier_dir)
        set_dossier_status(s, dossier, DossierStatus.EXTRACTION_VALIDATED)
        dossier_out = dossier_to_out(dossier)

    await progress_manager.broadcast(
        dossier_id,
        stage="extraction",
        status=DossierStatus.EXTRACTION_VALIDATED.value,
        message=f"Extraction validée — {report['total_fields']} champ(s)",
    )
    return ExtractionApplyOut(dossier=dossier_out, report=report)


_REOPENABLE_EXTRACTION_STATUSES = (DossierStatus.EXTRACTION_VALIDATED.value,)


@router.post("/{dossier_id}/extraction/reopen", response_model=DossierOut)
async def reopen_extraction_endpoint(dossier_id: str) -> DossierOut:
    return await reopen_stage(
        dossier_id,
        allowed_statuses=_REOPENABLE_EXTRACTION_STATUSES,
        reopen_fn=reopen_extraction,
        not_ready_message="Ce dossier ne peut pas être rouvert pour correction de l'extraction (statut actuel : {status}).",
        stage="extraction",
        target_status=DossierStatus.EXTRACTION_REVIEW,
        broadcast_message="Extraction rouverte pour correction",
    )


@router.get("/{dossier_id}/extraction/report")
async def get_extraction_report(dossier_id: str) -> dict:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if not dossier.extraction_report_json_path:
            raise HTTPException(404, "Aucun rapport d'extraction disponible pour ce dossier")

    settings = get_settings()
    report_path = settings.workspace_dir / dossier_id / REPORT_JSON_FILENAME
    if not report_path.exists():
        raise HTTPException(404, "Fichier de rapport introuvable sur disque")
    return json.loads(report_path.read_text(encoding="utf-8"))
