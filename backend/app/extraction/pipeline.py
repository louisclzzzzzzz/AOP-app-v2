"""Orchestration de l'étape 3 — extraction d'informations (§6 du PLAN).

Pas d'écran de sélection (contrairement à la complétude) : `donnees_de_ref.md` ne décrit pas
de cases à cocher, le schéma d'extraction est fixe — tous les champs sont toujours analysés.
Le lancement reste néanmoins déclenché explicitement par l'utilisateur (`POST .../extraction/run`)
depuis `completeness_validated`, jamais enchaîné automatiquement — même principe que les 2
étapes précédentes.

Un appel LLM par DOCUMENT de référence (pas par champ, §3 OPTIMISATION.md) : la couche 1 appelle
chaque document de référence distinct une fois, couvrant tous les champs qu'il concerne ; les
champs encore sans valeur passent en couche 2 (recherche élargie, un appel par document
candidat) ; ce qui reste est déclaré absent sans appel LLM.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.extraction.engine import (
    DocumentExtractionResult,
    ExtractionOutcome,
    absent_outcome,
    analyze_document,
    layer2_candidates,
    plan_layer2_calls,
    plan_reference_document_calls,
    reference_candidates,
    resolve_field,
)
from app.extraction.extraction_schema import ExtractionField, load_extraction_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal
from app.progress import progress_manager
from app.settings import get_models_config
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, ExtractionResult, MatchLayer
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
        message="Extraction des données (un appel par document de référence, recoupement, recherche élargie)…",
    )

    def _prepare() -> list[DocumentSignal]:
        with session_scope() as s:
            ensure_results_initialized(s, dossier_id)
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)
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
        return [build_document_signal(snap) for snap in doc_snapshots]

    signals = await asyncio.to_thread(_prepare)
    schema = load_extraction_schema()
    extraction_cfg = get_models_config()["extraction"]
    cross_check_required_fields = set(extraction_cfg.get("cross_check_required_fields", []))
    max_cross_check_sources = int(extraction_cfg.get("cross_check_passes", 2))

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    def _persist(outcomes: dict[str, ExtractionOutcome]) -> None:
        with session_scope() as s:
            for field_id, outcome in outcomes.items():
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

    async def _run_calls(
        calls: list[tuple[DocumentSignal, list[ExtractionField]]]
    ) -> dict[str, DocumentExtractionResult]:
        results: dict[str, DocumentExtractionResult] = {}
        for doc, fields_for_doc in calls:
            result = await asyncio.to_thread(analyze_document, doc, fields_for_doc)
            results[doc.document_id] = result
            counters = await asyncio.to_thread(_read_counters)
            await progress_manager.broadcast(
                dossier_id,
                stage="extraction",
                status=DossierStatus.EXTRACTING.value,
                counters=counters,
                document={
                    "id": doc.document_id,
                    "filename": doc.filename,
                    "relative_path": doc.filename,
                    "fields_covered": len(fields_for_doc),
                    "error": result.error,
                },
            )
        return results

    # --- Couche 1 : un appel par document de référence --------------------------------------
    layer1_calls = plan_reference_document_calls(schema.fields, signals)
    layer1_results = await _run_calls(layer1_calls)

    layer1_outcomes: dict[str, ExtractionOutcome] = {}
    for f in schema.fields:
        outcome = resolve_field(
            f,
            candidates=reference_candidates(f, signals),
            results_by_document=layer1_results,
            match_layer=MatchLayer.FILE.value,
            cross_check_required=f.id in cross_check_required_fields,
            max_cross_check_sources=max_cross_check_sources,
        )
        if outcome is not None:
            layer1_outcomes[f.id] = outcome
    await asyncio.to_thread(_persist, layer1_outcomes)

    # --- Couche 2 : recherche élargie sur les champs encore manquants -----------------------
    missing_fields = [f for f in schema.fields if f.id not in layer1_outcomes]
    layer2_outcomes: dict[str, ExtractionOutcome] = {}
    if missing_fields:
        layer2_calls = plan_layer2_calls(missing_fields, signals)
        layer2_results = await _run_calls(layer2_calls)
        for f in missing_fields:
            outcome = resolve_field(
                f,
                candidates=layer2_candidates(f, signals),
                results_by_document=layer2_results,
                match_layer=MatchLayer.CONTENT.value,
                cross_check_required=False,
            )
            if outcome is not None:
                layer2_outcomes[f.id] = outcome
        await asyncio.to_thread(_persist, layer2_outcomes)

    # --- Couche 3 : absent, aucun appel LLM --------------------------------------------------
    resolved_ids = set(layer1_outcomes) | set(layer2_outcomes)
    absent_fields = [f for f in schema.fields if f.id not in resolved_ids]
    if absent_fields:
        absent_outcomes = {
            f.id: absent_outcome(
                "Aucune valeur trouvée : ni dans les documents de référence, ni par recherche de mots-clés."
            )
            for f in absent_fields
        }
        await asyncio.to_thread(_persist, absent_outcomes)

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
