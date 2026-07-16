"""Endpoints REST : upload d'un dossier (zip), liste, détail, inventaire, texte extrait."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from app.api.schemas import CountersOut, DocumentOut, DocumentTextOut, DossierOut
from app.classify.pipeline import run_classification_pipeline
from app.ingestion.pipeline import run_ingestion_pipeline
from app.ocr.cache import read_text_cache
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, Document, TextCache
from app.store.repository import (
    create_dossier,
    get_dossier,
    get_document,
    list_documents,
    list_dossiers,
    set_dossier_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["dossiers"])


def dossier_to_out(d: Dossier) -> DossierOut:
    return DossierOut(
        id=d.id,
        original_filename=d.original_filename,
        status=d.status,
        current_step=d.current_step,
        error_message=d.error_message,
        counters=CountersOut(
            total_files=d.total_files,
            text_extracted=d.files_text_extracted,
            non_analyzable=d.files_non_analyzable,
            error=d.files_error,
            classified=d.files_classified,
            pieces_selected=d.pieces_selected,
            pieces_checked=d.pieces_checked,
            pieces_present=d.pieces_present,
            pieces_absent=d.pieces_absent,
            pieces_error=d.pieces_error,
        ),
        reorg_applied_at=d.reorg_applied_at,
        completeness_validated_at=d.completeness_validated_at,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _document_to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        relative_path=doc.relative_path,
        filename=doc.filename,
        extension=doc.extension,
        size_bytes=doc.size_bytes,
        sha256=doc.sha256,
        category=doc.category,
        is_analyzable=doc.is_analyzable,
        non_analyzable_reason=doc.non_analyzable_reason,
        parent_archive_id=doc.parent_archive_id,
        stage=doc.stage,
        stage_error=doc.stage_error,
        text_extraction_method=doc.text_extraction_method,
        detected_title=doc.detected_title,
        preview_text=doc.preview_text,
        key_mentions=json.loads(doc.key_mentions_json) if doc.key_mentions_json else None,
    )


async def _run_pipeline_safely(dossier_id: str, zip_path) -> None:
    """Filet de sécurité : toute exception non prévue par le pipeline lui-même bascule le
    dossier en erreur au lieu de le laisser bloqué silencieusement à mi-chemin.

    Enchaîne automatiquement l'étape 1 (classification) après l'ingestion : classer ne
    nécessite aucun jugement humain, seule la validation du plan proposé en nécessite un —
    c'est pourquoi `classified` est le premier vrai checkpoint (§0, §4.4 du PLAN)."""
    try:
        await run_ingestion_pipeline(dossier_id, zip_path)

        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            ingestion_ok = dossier is not None and dossier.status == DossierStatus.READY_STEP1.value

        if ingestion_ok:
            await run_classification_pipeline(dossier_id)
    except Exception as exc:  # pragma: no cover - filet de sécurité générique
        logger.exception("Erreur non gérée dans le pipeline d'ingestion pour %s", dossier_id)

        def _mark_error() -> None:
            with session_scope() as s:
                dossier = get_dossier(s, dossier_id)
                if dossier is not None:
                    set_dossier_status(s, dossier, DossierStatus.ERROR, error_message=str(exc))

        await asyncio.to_thread(_mark_error)
        await progress_manager.broadcast(
            dossier_id, stage="error", status=DossierStatus.ERROR.value, message=str(exc)
        )


@router.post("", response_model=DossierOut)
async def upload_dossier(file: UploadFile, background_tasks: BackgroundTasks) -> DossierOut:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Seuls les fichiers .zip sont acceptés")

    with session_scope() as s:
        dossier = create_dossier(s, file.filename)
        dossier_id = dossier.id
        result = dossier_to_out(dossier)

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id
    dossier_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dossier_dir / "upload.zip"

    with open(zip_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    background_tasks.add_task(_run_pipeline_safely, dossier_id, zip_path)
    return result


@router.get("", response_model=list[DossierOut])
async def list_all_dossiers() -> list[DossierOut]:
    with session_scope() as s:
        return [dossier_to_out(d) for d in list_dossiers(s)]


@router.get("/{dossier_id}", response_model=DossierOut)
async def get_dossier_detail(dossier_id: str) -> DossierOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        return dossier_to_out(dossier)


@router.get("/{dossier_id}/documents", response_model=list[DocumentOut])
async def get_dossier_documents(dossier_id: str) -> list[DocumentOut]:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        return [_document_to_out(d) for d in list_documents(s, dossier_id)]


@router.get("/{dossier_id}/documents/{document_id}/text", response_model=DocumentTextOut)
async def get_document_text(dossier_id: str, document_id: str) -> DocumentTextOut:
    with session_scope() as s:
        doc = get_document(s, document_id)
        if doc is None or doc.dossier_id != dossier_id:
            raise HTTPException(404, "Document introuvable")
        if doc.text_cache_id is None:
            raise HTTPException(404, "Aucun texte extrait pour ce document")
        cache = s.get(TextCache, doc.text_cache_id)
        if cache is None or not cache.text_path:
            raise HTTPException(404, "Cache de texte introuvable")
        text = read_text_cache(cache.text_path)
        return DocumentTextOut(
            document_id=doc.id,
            filename=doc.filename,
            method=cache.method,
            avg_confidence=cache.avg_confidence,
            model_name=cache.model_name,
            model_version=cache.model_version,
            page_count=cache.page_count,
            char_count=cache.char_count,
            text=text,
        )
