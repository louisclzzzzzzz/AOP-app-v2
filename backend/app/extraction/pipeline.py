"""Orchestration de l'étape 3 — extraction d'informations (§6 du PLAN).

Pas d'écran de sélection (contrairement à la complétude) : `donnees_de_ref.md` ne décrit pas
de cases à cocher, le schéma d'extraction est fixe — tous les champs sont toujours analysés.
Le lancement reste néanmoins déclenché explicitement par l'utilisateur (`POST .../extraction/run`)
depuis `completeness_validated`, jamais enchaîné automatiquement — même principe que les 2
étapes précédentes.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.extraction.engine import ExtractionOutcome, analyze_field
from app.extraction.extraction_schema import ExtractionField, ExtractionSchema, load_extraction_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal
from app.progress import progress_manager
from app.settings import get_models_config
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, ExtractionResult
from app.store.repository import (
    create_extraction_result,
    get_dossier,
    get_extraction_result_by_field,
    list_documents,
    list_extraction_results,
    recompute_extraction_counters,
    set_dossier_status,
    set_extraction_result,
)

logger = logging.getLogger(__name__)


def ensure_results_initialized(session: Session, dossier_id: str) -> list[ExtractionResult]:
    """Crée les lignes ExtractionResult manquantes pour tous les champs du schéma — idempotent,
    appelé au premier accès à l'écran de résultats (pas de sélection : tous les champs)."""
    schema = load_extraction_schema()
    existing_ids = {r.field_id for r in list_extraction_results(session, dossier_id)}
    for f in schema.fields:
        if f.id not in existing_ids:
            create_extraction_result(session, dossier_id=dossier_id, field_id=f.id)
    return list_extraction_results(session, dossier_id)


def _counters(dossier: Dossier) -> dict[str, int]:
    return {
        "total_files": dossier.total_files,
        "text_extracted": dossier.files_text_extracted,
        "non_analyzable": dossier.files_non_analyzable,
        "error": dossier.files_error,
        "classified": dossier.files_classified,
        "pieces_selected": dossier.pieces_selected,
        "pieces_checked": dossier.pieces_checked,
        "pieces_present": dossier.pieces_present,
        "pieces_absent": dossier.pieces_absent,
        "pieces_error": dossier.pieces_error,
        "fields_total": dossier.fields_total,
        "fields_extracted": dossier.fields_extracted,
        "fields_present": dossier.fields_present,
        "fields_absent": dossier.fields_absent,
        "fields_incoherent": dossier.fields_incoherent,
        "fields_error": dossier.fields_error,
    }


async def run_extraction_pipeline(dossier_id: str) -> None:
    def _set_status(status: DossierStatus) -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, status)

    await asyncio.to_thread(_set_status, DossierStatus.EXTRACTING)
    await progress_manager.broadcast(
        dossier_id,
        stage="extraction",
        status=DossierStatus.EXTRACTING.value,
        message="Extraction des données (fichiers de référence + recherche élargie + recoupement)…",
    )

    def _prepare() -> tuple[list[str], list[DocumentSignal]]:
        with session_scope() as s:
            ensure_results_initialized(s, dossier_id)
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)
            field_ids = [f.id for f in load_extraction_schema().fields]
            documents = list_documents(s, dossier_id)
            doc_snapshots = [
                {
                    "id": d.id,
                    "filename": d.filename,
                    "final_category": d.final_category,
                    "final_lot": d.final_lot,
                    "classification_confidence": d.classification_confidence,
                    "text_cache_id": d.text_cache_id,
                }
                for d in documents
            ]
        signals = [build_document_signal(snap) for snap in doc_snapshots]
        return field_ids, signals

    field_ids, signals = await asyncio.to_thread(_prepare)
    schema = load_extraction_schema()
    cross_check_required_fields = set(
        get_models_config()["extraction"].get("cross_check_required_fields", [])
    )

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    for field_id in field_ids:
        doc_event = await asyncio.to_thread(
            _analyze_one, dossier_id, field_id, schema, signals, field_id in cross_check_required_fields
        )
        counters = await asyncio.to_thread(_read_counters)
        await progress_manager.broadcast(
            dossier_id,
            stage="extraction",
            status=DossierStatus.EXTRACTING.value,
            counters=counters,
            document=doc_event,
        )

    def _finalize() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)
            set_dossier_status(s, dossier, DossierStatus.EXTRACTION_REVIEW)
            return _counters(dossier)

    final_counters = await asyncio.to_thread(_finalize)
    await progress_manager.broadcast(
        dossier_id,
        stage="extraction",
        status=DossierStatus.EXTRACTION_REVIEW.value,
        counters=final_counters,
        message="Extraction terminée — résultats prêts à valider",
    )


def _analyze_one(
    dossier_id: str,
    field_id: str,
    schema: ExtractionSchema,
    signals: list[DocumentSignal],
    cross_check_required: bool,
) -> dict:
    field: ExtractionField | None = schema.by_id(field_id)
    assert field is not None

    outcome: ExtractionOutcome = analyze_field(
        field=field, documents=signals, cross_check_required=cross_check_required
    )

    with session_scope() as s:
        result = get_extraction_result_by_field(s, dossier_id, field_id)
        assert result is not None
        set_extraction_result(
            s,
            result,
            match_layer=outcome.match_layer,
            value=outcome.value,
            confidence=outcome.confidence,
            justification=outcome.justification,
            citation=outcome.citation,
            sources=outcome.sources,
            cross_check_status=outcome.cross_check_status,
            model_name=outcome.model_name,
            model_version=outcome.model_version,
            error=outcome.error,
        )
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        recompute_extraction_counters(s, dossier)

    return {
        "id": field_id,
        "filename": field.libelle,
        "relative_path": field.libelle,
        "value": outcome.value,
        "cross_check_status": outcome.cross_check_status,
        "error": outcome.error,
    }
