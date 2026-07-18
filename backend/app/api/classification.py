"""Endpoints REST de l'étape 1 (§4, §8 du PLAN) : consultation du plan de classification
proposé, correction manuelle au checkpoint, application de la copie triée, rapport."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from app.api.dossiers import dossier_to_out
from app.api.schemas import (
    ClassificationCorrectionIn,
    ClassificationEntryOut,
    DossierOut,
    ReorgApplyOut,
    TaxonomyCategoryOut,
)
from app.classify.reorg import REPORT_JSON_FILENAME, apply_reorganization
from app.classify.taxonomy import load_taxonomy
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, Document
from app.store.repository import (
    get_dossier,
    get_document,
    list_documents,
    reopen_reorganization,
    set_document_classification_correction,
    set_dossier_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["classification"])
taxonomy_router = APIRouter(prefix="/api/taxonomy", tags=["classification"])


def _entry_to_out(doc: Document) -> ClassificationEntryOut:
    return ClassificationEntryOut(
        document_id=doc.id,
        relative_path=doc.relative_path,
        filename=doc.filename,
        is_analyzable=doc.is_analyzable,
        classification_status=doc.classification_status,
        classification_error=doc.classification_error,
        proposed_category=doc.proposed_category,
        proposed_lot=doc.proposed_lot,
        proposed_doc_type=doc.proposed_doc_type,
        proposed_filename=doc.proposed_filename,
        confidence=doc.classification_confidence,
        justification=doc.classification_justification,
        signals=json.loads(doc.classification_signals_json) if doc.classification_signals_json else None,
        model_name=doc.classification_model,
        model_version=doc.classification_model_version,
        final_category=doc.final_category,
        final_lot=doc.final_lot,
        final_doc_type=doc.final_doc_type,
        final_filename=doc.final_filename,
        is_manually_corrected=doc.is_manually_corrected,
        organized_relative_path=doc.organized_relative_path,
    )


@taxonomy_router.get("", response_model=list[TaxonomyCategoryOut])
async def get_taxonomy() -> list[TaxonomyCategoryOut]:
    taxonomy = load_taxonomy()
    return [
        TaxonomyCategoryOut(path=c.path, label=c.label, alt_names=c.alt_names, lot_aware=c.lot_aware)
        for c in taxonomy.categories
    ]


@router.get("/{dossier_id}/classification", response_model=list[ClassificationEntryOut])
async def get_classification(dossier_id: str) -> list[ClassificationEntryOut]:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        return [_entry_to_out(d) for d in list_documents(s, dossier_id)]


@router.patch("/{dossier_id}/documents/{document_id}/classification", response_model=ClassificationEntryOut)
async def correct_classification(
    dossier_id: str, document_id: str, correction: ClassificationCorrectionIn
) -> ClassificationEntryOut:
    taxonomy = load_taxonomy()
    if taxonomy.by_path(correction.category) is None:
        raise HTTPException(400, f"Catégorie inconnue de la taxonomie : {correction.category}")
    if not correction.filename.strip():
        raise HTTPException(400, "Le nom de fichier cible ne peut pas être vide")

    with session_scope() as s:
        doc = get_document(s, document_id)
        if doc is None or doc.dossier_id != dossier_id:
            raise HTTPException(404, "Document introuvable")
        set_document_classification_correction(
            s,
            doc,
            category=correction.category,
            lot=correction.lot,
            doc_type=correction.doc_type,
            filename=correction.filename,
        )
        return _entry_to_out(doc)


@router.post("/{dossier_id}/reorganize/apply", response_model=ReorgApplyOut)
async def apply_reorganization_endpoint(dossier_id: str) -> ReorgApplyOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in (DossierStatus.CLASSIFIED.value, DossierStatus.REORGANIZED.value):
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour la copie triée (statut actuel : {dossier.status}). "
                "La classification (étape 1) doit être terminée au préalable.",
            )

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id
    source_dir = dossier_dir / "source"
    organized_root = dossier_dir / "organized"

    def _apply() -> dict:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, DossierStatus.REORGANIZING)

        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            report = apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)
            set_dossier_status(s, dossier, DossierStatus.REORGANIZED)
            return report

    try:
        report = await asyncio.to_thread(_apply)
    except Exception as exc:
        logger.exception("Échec de l'application de la copie triée pour %s", dossier_id)
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            if dossier is not None:
                set_dossier_status(s, dossier, DossierStatus.ERROR, error_message=str(exc))
        await progress_manager.broadcast(
            dossier_id, stage="reorganize", status=DossierStatus.ERROR.value, message=str(exc)
        )
        raise HTTPException(500, f"Échec de l'application de la copie triée : {exc}") from exc

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        dossier_out = dossier_to_out(dossier)

    await progress_manager.broadcast(
        dossier_id,
        stage="reorganize",
        status=DossierStatus.REORGANIZED.value,
        message=f"Copie triée appliquée — {report['total_files']} fichiers copiés",
    )
    return ReorgApplyOut(dossier=dossier_out, report=report)


_REOPENABLE_REORG_STATUSES = (
    DossierStatus.REORGANIZED.value,
    DossierStatus.COMPLETENESS_REVIEW.value,
    DossierStatus.COMPLETENESS_VALIDATED.value,
    DossierStatus.EXTRACTION_REVIEW.value,
    DossierStatus.EXTRACTION_VALIDATED.value,
)


@router.post("/{dossier_id}/reorganize/reopen", response_model=DossierOut)
async def reopen_reorganization_endpoint(dossier_id: str) -> DossierOut:
    """Rouvre le plan de classement pour correction, même si les étapes 2/3 ont déjà été
    réalisées — le moteur de copie triée est déjà idempotent (§ reorg.py), seule l'UI
    verrouillait cette possibilité. Invalide les résultats des étapes 2/3 (cf.
    `reopen_reorganization`) : ils devront être relancés après correction."""
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _REOPENABLE_REORG_STATUSES:
            raise HTTPException(
                409,
                f"Ce dossier ne peut pas être rouvert pour correction du classement "
                f"(statut actuel : {dossier.status}).",
            )
        reopen_reorganization(s, dossier)
        dossier_out = dossier_to_out(dossier)

    await progress_manager.broadcast(
        dossier_id,
        stage="classify",
        status=DossierStatus.CLASSIFIED.value,
        message="Plan de classement rouvert pour correction — étapes 2/3 à relancer",
    )
    return dossier_out


@router.get("/{dossier_id}/reorganize/report")
async def get_reorganization_report(dossier_id: str) -> dict:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if not dossier.reorg_report_json_path:
            raise HTTPException(404, "Aucun rapport de réorganisation disponible pour ce dossier")

    settings = get_settings()
    report_path = settings.workspace_dir / dossier_id / REPORT_JSON_FILENAME
    if not report_path.exists():
        raise HTTPException(404, "Fichier de rapport introuvable sur disque")
    return json.loads(report_path.read_text(encoding="utf-8"))
