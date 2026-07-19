"""Orchestration de l'étape 1 — classification (§4 du PLAN).

Enchaînée automatiquement après la fin de l'ingestion (étape 0), comme le sont déjà le
dézippage -> inventaire -> extraction de texte au sein de l'ingestion elle-même : classer
chaque document ne nécessite aucun jugement humain, seule la VALIDATION du plan proposé en
nécessite. Le statut `classified` est donc le premier vrai checkpoint (§0, §4.4) : la copie
triée elle-même (app/classify/reorg.py) n'est déclenchée que par une action explicite de
l'utilisateur, jamais automatiquement.

Deux passes (§2 OPTIMISATION.md) : d'abord les règles (nom + contenu), zéro appel LLM, un
évènement par document classé instantanément ; puis les documents ambigus restants, regroupés
en lots (`classification.batch_size`) et soumis en UN appel LLM par lot au lieu d'un par
document — la diffusion de progression reste néanmoins un évènement par document.
"""
from __future__ import annotations

import asyncio
import logging

from app.classify.engine import (
    AmbiguousDocument,
    ClassificationOutcome,
    classify_document_by_rules,
    classify_documents_batch,
    extract_lot_signal,
    score_content,
    score_filename,
)
from app.ocr.cache import read_text_cache
from app.pipeline_support import finalize_stage, start_stage
from app.progress import progress_manager
from app.settings import get_models_config
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, TextCache
from app.store.repository import (
    get_dossier,
    get_document,
    list_documents,
    recompute_dossier_counters,
    set_document_classification_result,
)

logger = logging.getLogger(__name__)


def _counters(dossier: Dossier) -> dict[str, int]:
    return {
        "total_files": dossier.total_files,
        "text_extracted": dossier.files_text_extracted,
        "non_analyzable": dossier.files_non_analyzable,
        "error": dossier.files_error,
        "classified": dossier.files_classified,
    }


async def run_classification_pipeline(dossier_id: str) -> None:
    await start_stage(
        dossier_id,
        status=DossierStatus.CLASSIFYING,
        stage="classify",
        message="Classification des documents (règles nom+contenu, puis LLM batché sur les ambigus)…",
    )

    def _document_ids() -> list[str]:
        with session_scope() as s:
            return [d.id for d in list_documents(s, dossier_id)]

    document_ids = await asyncio.to_thread(_document_ids)

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    # --- Passe 1 : règles (zéro appel LLM) --------------------------------------------------
    ambiguous_ids: list[str] = []
    for document_id in document_ids:
        doc_event = await asyncio.to_thread(_classify_by_rules_one, document_id)
        if doc_event is None:
            ambiguous_ids.append(document_id)
            continue
        counters = await asyncio.to_thread(_read_counters)
        await progress_manager.broadcast(
            dossier_id,
            stage="classify",
            status=DossierStatus.CLASSIFYING.value,
            counters=counters,
            document=doc_event,
        )

    # --- Passe 2 : documents ambigus, un appel LLM par lot ----------------------------------
    batch_size = max(1, int(get_models_config().get("classification", {}).get("batch_size", 10)))
    for i in range(0, len(ambiguous_ids), batch_size):
        batch_ids = ambiguous_ids[i : i + batch_size]
        doc_events = await asyncio.to_thread(_classify_batch, batch_ids)
        for doc_event in doc_events:
            counters = await asyncio.to_thread(_read_counters)
            await progress_manager.broadcast(
                dossier_id,
                stage="classify",
                status=DossierStatus.CLASSIFYING.value,
                counters=counters,
                document=doc_event,
            )

    await finalize_stage(
        dossier_id,
        status=DossierStatus.CLASSIFIED,
        stage="classify",
        message="Classification terminée — plan de réorganisation prêt à valider",
        counters=_counters,
        recompute=lambda s, dossier: recompute_dossier_counters(s, dossier),
        before_status_change=lambda s, dossier: setattr(dossier, "current_step", 1),
    )


def _read_content_excerpt(text_cache_id: str | None) -> str:
    if not text_cache_id:
        return ""
    with session_scope() as s:
        cache = s.get(TextCache, text_cache_id)
        text_path = cache.text_path if cache else None
    return read_text_cache(text_path) if text_path else ""


def _apply_outcome(document_id: str, outcome: ClassificationOutcome) -> None:
    with session_scope() as s:
        document = get_document(s, document_id)
        assert document is not None
        set_document_classification_result(
            s,
            document,
            category=outcome.category,
            lot=outcome.lot,
            doc_type=outcome.doc_type,
            filename=outcome.normalized_filename,
            confidence=outcome.confidence,
            justification=outcome.justification,
            signals=outcome.signals,
            model_name=outcome.model_name,
            model_version=outcome.model_version,
            error=outcome.error,
        )


def _classify_by_rules_one(document_id: str) -> dict | None:
    """Tente une classification sans LLM. Retourne l'évènement à diffuser si réglé par règles,
    ou None si le document est ambigu (à traiter en lot dans `_classify_batch`)."""
    with session_scope() as s:
        document = get_document(s, document_id)
        assert document is not None
        relative_path = document.relative_path
        filename = document.filename
        file_category = document.category
        non_analyzable_reason = document.non_analyzable_reason
        text_cache_id = document.text_cache_id

    content_excerpt = _read_content_excerpt(text_cache_id)

    outcome = classify_document_by_rules(
        relative_path=relative_path,
        filename=filename,
        file_category=file_category,
        non_analyzable_reason=non_analyzable_reason,
        content_excerpt=content_excerpt,
    )
    if outcome is None:
        return None

    _apply_outcome(document_id, outcome)
    return {
        "id": document_id,
        "filename": filename,
        "relative_path": relative_path,
        "category": outcome.category,
        "confidence": outcome.confidence,
        "error": outcome.error,
    }


def _classify_batch(document_ids: list[str]) -> list[dict]:
    """Un seul appel LLM structuré pour tout le lot (§2 OPTIMISATION.md)."""
    snapshots: list[dict] = []
    with session_scope() as s:
        for document_id in document_ids:
            document = get_document(s, document_id)
            assert document is not None
            snapshots.append(
                {
                    "document_id": document_id,
                    "relative_path": document.relative_path,
                    "filename": document.filename,
                    "text_cache_id": document.text_cache_id,
                }
            )

    items: list[AmbiguousDocument] = []
    excerpts: list[str] = []
    for snap in snapshots:
        content_excerpt = _read_content_excerpt(snap["text_cache_id"])
        excerpts.append(content_excerpt)
        items.append(
            AmbiguousDocument(
                relative_path=snap["relative_path"],
                filename=snap["filename"],
                content_excerpt=content_excerpt,
                filename_matches=score_filename(snap["filename"]),
                content_matches=score_content(content_excerpt) if content_excerpt else [],
                lot_signal=extract_lot_signal(snap["filename"]) or extract_lot_signal(content_excerpt or ""),
            )
        )

    outcomes = classify_documents_batch(items)

    events: list[dict] = []
    for snap, outcome in zip(snapshots, outcomes):
        _apply_outcome(snap["document_id"], outcome)
        events.append(
            {
                "id": snap["document_id"],
                "filename": snap["filename"],
                "relative_path": snap["relative_path"],
                "category": outcome.category,
                "confidence": outcome.confidence,
                "error": outcome.error,
            }
        )
    return events
