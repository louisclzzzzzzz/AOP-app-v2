"""Orchestration de l'étape 1 — classification (§4 du PLAN).

Enchaînée automatiquement après la fin de l'ingestion (étape 0), comme le sont déjà le
dézippage -> inventaire -> extraction de texte au sein de l'ingestion elle-même : classer
chaque document ne nécessite aucun jugement humain, seule la VALIDATION du plan proposé en
nécessite. Le statut `classified` est donc le premier vrai checkpoint (§0, §4.4) : la copie
triée elle-même (app/classify/reorg.py) n'est déclenchée que par une action explicite de
l'utilisateur, jamais automatiquement.
"""
from __future__ import annotations

import asyncio
import logging

from app.classify.engine import classify_document
from app.ocr.cache import read_text_cache
from app.progress import progress_manager
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, TextCache
from app.store.repository import (
    get_dossier,
    get_document,
    list_documents,
    recompute_dossier_counters,
    set_document_classification_result,
    set_dossier_status,
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
    def _set_status(status: DossierStatus) -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, status)

    await asyncio.to_thread(_set_status, DossierStatus.CLASSIFYING)
    await progress_manager.broadcast(
        dossier_id,
        stage="classify",
        status=DossierStatus.CLASSIFYING.value,
        message="Classification des documents (nom + contenu + LLM)…",
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

    for document_id in document_ids:
        doc_event = await asyncio.to_thread(_classify_one, document_id)
        counters = await asyncio.to_thread(_read_counters)
        await progress_manager.broadcast(
            dossier_id,
            stage="classify",
            status=DossierStatus.CLASSIFYING.value,
            counters=counters,
            document=doc_event,
        )

    def _finalize() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_dossier_counters(s, dossier)
            dossier.current_step = 1
            set_dossier_status(s, dossier, DossierStatus.CLASSIFIED)
            return _counters(dossier)

    final_counters = await asyncio.to_thread(_finalize)
    await progress_manager.broadcast(
        dossier_id,
        stage="classify",
        status=DossierStatus.CLASSIFIED.value,
        counters=final_counters,
        message="Classification terminée — plan de réorganisation prêt à valider",
    )


def _classify_one(document_id: str) -> dict:
    with session_scope() as s:
        document = get_document(s, document_id)
        assert document is not None
        relative_path = document.relative_path
        filename = document.filename
        file_category = document.category
        non_analyzable_reason = document.non_analyzable_reason
        text_cache_id = document.text_cache_id

    content_excerpt = ""
    if text_cache_id:
        with session_scope() as s:
            cache = s.get(TextCache, text_cache_id)
            text_path = cache.text_path if cache else None
        if text_path:
            content_excerpt = read_text_cache(text_path)

    outcome = classify_document(
        relative_path=relative_path,
        filename=filename,
        file_category=file_category,
        non_analyzable_reason=non_analyzable_reason,
        content_excerpt=content_excerpt,
    )

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

    return {
        "id": document_id,
        "filename": filename,
        "relative_path": relative_path,
        "category": outcome.category,
        "confidence": outcome.confidence,
        "error": outcome.error,
    }
