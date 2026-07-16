"""Orchestration de l'étape 2 — analyse de complétude (§5 du PLAN).

Contrairement à l'ingestion -> classification (enchaînées automatiquement, classer ne
nécessite aucun jugement humain), le lancement de l'analyse de complétude est déclenché
explicitement par l'utilisateur (`POST .../completeness/run`) après qu'il a sélectionné, sur
l'écran de sélection (§5.2), les pièces recherchées pour CE dossier — exactement comme
l'application de la copie triée n'est pas enchaînée automatiquement après `classified`.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.completeness.engine import analyze_piece
from app.completeness.pieces_checklist import Piece, PiecesChecklist, load_pieces_checklist
from app.ingestion.document_signal import DocumentSignal, build_document_signal
from app.progress import progress_manager
from app.store.db import session_scope
from app.store.models import CompletenessCheck, Dossier, DossierStatus
from app.store.repository import (
    create_completeness_check,
    get_completeness_check_by_piece,
    get_dossier,
    list_completeness_checks,
    list_documents,
    recompute_completeness_counters,
    set_completeness_result,
    set_dossier_status,
)

logger = logging.getLogger(__name__)


def ensure_checks_initialized(session: Session, dossier_id: str) -> list[CompletenessCheck]:
    """Crée les lignes CompletenessCheck manquantes à partir de la config, pré-cochées pour
    les pièces obligatoires — idempotent, appelé au premier accès à l'écran de sélection."""
    checklist = load_pieces_checklist()
    existing_ids = {c.piece_id for c in list_completeness_checks(session, dossier_id)}
    for piece in checklist.pieces:
        if piece.id not in existing_ids:
            create_completeness_check(
                session,
                dossier_id=dossier_id,
                piece_id=piece.id,
                is_selected=piece.obligatoire,
            )
    return list_completeness_checks(session, dossier_id)


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
    }


async def run_completeness_pipeline(dossier_id: str) -> None:
    def _set_status(status: DossierStatus) -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, status)

    await asyncio.to_thread(_set_status, DossierStatus.ANALYZING_COMPLETENESS)
    await progress_manager.broadcast(
        dossier_id,
        stage="completeness",
        status=DossierStatus.ANALYZING_COMPLETENESS.value,
        message="Analyse de complétude (fichier direct + recherche intra-document + LLM)…",
    )

    def _prepare() -> tuple[list[str], list[DocumentSignal], list[str]]:
        with session_scope() as s:
            checks = [c for c in list_completeness_checks(s, dossier_id) if c.is_selected]
            piece_ids = [c.piece_id for c in checks]
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
            all_lots = sorted({d.final_lot for d in documents if d.final_lot})
        signals = [build_document_signal(snap) for snap in doc_snapshots]
        return piece_ids, signals, all_lots

    piece_ids, signals, all_lots = await asyncio.to_thread(_prepare)
    checklist = load_pieces_checklist()

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    for piece_id in piece_ids:
        doc_event = await asyncio.to_thread(_analyze_one, dossier_id, piece_id, checklist, signals, all_lots)
        counters = await asyncio.to_thread(_read_counters)
        await progress_manager.broadcast(
            dossier_id,
            stage="completeness",
            status=DossierStatus.ANALYZING_COMPLETENESS.value,
            counters=counters,
            document=doc_event,
        )

    def _finalize() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_completeness_counters(s, dossier)
            set_dossier_status(s, dossier, DossierStatus.COMPLETENESS_REVIEW)
            return _counters(dossier)

    final_counters = await asyncio.to_thread(_finalize)
    await progress_manager.broadcast(
        dossier_id,
        stage="completeness",
        status=DossierStatus.COMPLETENESS_REVIEW.value,
        counters=final_counters,
        message="Analyse de complétude terminée — résultats prêts à valider",
    )


def _analyze_one(
    dossier_id: str,
    piece_id: str,
    checklist: PiecesChecklist,
    signals: list[DocumentSignal],
    all_lots: list[str],
) -> dict:
    piece: Piece | None = checklist.by_id(piece_id)
    assert piece is not None

    outcome = analyze_piece(piece=piece, documents=signals, all_lots=all_lots)

    with session_scope() as s:
        check = get_completeness_check_by_piece(s, dossier_id, piece_id)
        assert check is not None
        set_completeness_result(
            s,
            check,
            match_layer=outcome.match_layer,
            presence=outcome.presence,
            certainty=outcome.certainty,
            confidence=outcome.confidence,
            justification=outcome.justification,
            matched_document_ids=outcome.matched_document_ids,
            matched_lots=outcome.matched_lots,
            model_name=outcome.model_name,
            model_version=outcome.model_version,
            error=outcome.error,
        )

    return {
        "id": piece_id,
        "filename": piece.libelle,
        "relative_path": piece.libelle,
        "presence": outcome.presence,
        "certainty": outcome.certainty,
        "error": outcome.error,
    }
