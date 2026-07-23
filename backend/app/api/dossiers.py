"""Endpoints REST : upload d'un dossier (zip), liste, détail, inventaire, texte extrait."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.schemas import CountersOut, DocumentOut, DocumentTextOut, DossierOut
from app.classify.pipeline import run_classification_pipeline
from app.ingestion.pipeline import run_ingestion_pipeline
from app.ocr.cache import delete_text_cache_files, read_text_cache
from app.pipeline_support import run_pipeline_safely
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, Document, TextCache
from app.store.repository import (
    create_dossier,
    delete_dossier,
    find_dossier_by_upload_hash,
    get_dossier,
    get_document,
    list_documents,
    list_dossiers,
    set_dossier_upload_info,
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
            non_analyzable_at_risk=d.files_non_analyzable_at_risk,
            error=d.files_error,
            classified=d.files_classified,
            pieces_selected=d.pieces_selected,
            pieces_checked=d.pieces_checked,
            pieces_present=d.pieces_present,
            pieces_absent=d.pieces_absent,
            pieces_error=d.pieces_error,
            fields_total=d.fields_total,
            fields_extracted=d.fields_extracted,
            fields_present=d.fields_present,
            fields_absent=d.fields_absent,
            fields_incoherent=d.fields_incoherent,
            fields_error=d.fields_error,
        ),
        reorg_applied_at=d.reorg_applied_at,
        completeness_validated_at=d.completeness_validated_at,
        extraction_validated_at=d.extraction_validated_at,
        synthese_ia=d.synthese_ia,
        synthese_projet_md=d.synthese_projet_md,
        synthese_projet_model=d.synthese_projet_model,
        synthese_projet_status=d.synthese_projet_status,
        synthese_projet_error=d.synthese_projet_error,
        synthese_projet_generated_at=d.synthese_projet_generated_at,
        synthese_projet_perplexity_md=d.synthese_projet_perplexity_md,
        synthese_projet_perplexity_model=d.synthese_projet_perplexity_model,
        synthese_projet_perplexity_status=d.synthese_projet_perplexity_status,
        synthese_projet_perplexity_error=d.synthese_projet_perplexity_error,
        synthese_projet_perplexity_generated_at=d.synthese_projet_perplexity_generated_at,
        duplicate_of_dossier_id=d.duplicate_of_dossier_id,
        duplicate_of_filename=d.duplicate_of_filename,
        duplicate_of_created_at=d.duplicate_of_created_at,
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
        non_analyzable_at_risk=doc.non_analyzable_at_risk,
        parent_archive_id=doc.parent_archive_id,
        stage=doc.stage,
        stage_error=doc.stage_error,
        text_extraction_method=doc.text_extraction_method,
        detected_title=doc.detected_title,
        preview_text=doc.preview_text,
        key_mentions=json.loads(doc.key_mentions_json) if doc.key_mentions_json else None,
    )


async def _run_pipeline_safely(dossier_id: str, zip_path) -> None:
    """Enchaîne automatiquement l'étape 1 (classification) après l'ingestion : classer ne
    nécessite aucun jugement humain, seule la validation du plan proposé en nécessite un —
    c'est pourquoi `classified` est le premier vrai checkpoint (§0, §4.4 du PLAN). Le filet de
    sécurité générique (`run_pipeline_safely`) bascule le dossier en erreur si l'un ou l'autre
    lève une exception non prévue, au lieu de le laisser bloqué silencieusement à mi-chemin."""

    async def _run() -> None:
        await run_ingestion_pipeline(dossier_id, zip_path)

        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            ingestion_ok = dossier is not None and dossier.status == DossierStatus.READY_STEP1.value

        if ingestion_ok:
            await run_classification_pipeline(dossier_id)

    await run_pipeline_safely(dossier_id, _run, what="le pipeline d'ingestion")


async def reopen_stage(
    dossier_id: str,
    *,
    allowed_statuses: tuple[str, ...],
    reopen_fn: Callable[[Session, Dossier], None],
    not_ready_message: str,
    stage: str,
    target_status: DossierStatus,
    broadcast_message: str,
) -> DossierOut:
    """Filet commun aux 3 endpoints "reopen" (classification/complétude/extraction) : même
    structure à l'identique pour les 3 avant factorisation — vérifier que le statut actuel
    autorise la réouverture, rouvrir, puis diffuser (§8 AUDIT_BACKEND.md). `not_ready_message`
    reçoit le statut actuel via `.format(status=...)`."""
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in allowed_statuses:
            raise HTTPException(409, not_ready_message.format(status=dossier.status))
        reopen_fn(s, dossier)
        dossier_out = dossier_to_out(dossier)

    await progress_manager.broadcast(
        dossier_id, stage=stage, status=target_status.value, message=broadcast_message
    )
    return dossier_out


@router.post("", response_model=DossierOut)
async def upload_dossier(file: UploadFile, background_tasks: BackgroundTasks) -> DossierOut:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Seuls les fichiers .zip sont acceptés")

    with session_scope() as s:
        dossier = create_dossier(s, file.filename)
        dossier_id = dossier.id

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id
    dossier_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dossier_dir / "upload.zip"

    hasher = hashlib.sha256()
    with open(zip_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            hasher.update(chunk)
            out.write(chunk)
    upload_sha256 = hasher.hexdigest()

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        # Avertissement non bloquant seulement : un même DCE peut légitimement être ré-analysé
        # (ex. après une mise à jour de la taxonomie) — jamais un refus d'upload.
        duplicate = find_dossier_by_upload_hash(s, upload_sha256, exclude_id=dossier_id)
        set_dossier_upload_info(s, dossier, upload_sha256=upload_sha256, duplicate_of=duplicate)
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_pipeline_safely, dossier_id, zip_path)
    return result


@router.delete("/{dossier_id}", status_code=204)
async def delete_dossier_endpoint(dossier_id: str) -> Response:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        orphaned_hashes = delete_dossier(s, dossier_id)

    for content_hash in orphaned_hashes:
        delete_text_cache_files(content_hash)

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id
    if dossier_dir.exists():
        shutil.rmtree(dossier_dir, ignore_errors=True)

    await progress_manager.forget(dossier_id)

    return Response(status_code=204)


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


@router.get("/{dossier_id}/documents/{document_id}/file")
async def get_document_file(dossier_id: str, document_id: str) -> FileResponse:
    """Sert le fichier original tel qu'uploadé (jamais une version modifiée), pour permettre à
    l'expert métier de vérifier une valeur extraite en un clic plutôt que de devoir retrouver
    le document par ses propres moyens (cf. FRICTIONS_EXPERT_METIER.md §5)."""
    with session_scope() as s:
        doc = get_document(s, document_id)
        if doc is None or doc.dossier_id != dossier_id:
            raise HTTPException(404, "Document introuvable")
        relative_path = doc.relative_path
        filename = doc.filename

    settings = get_settings()
    source_dir = (settings.workspace_dir / dossier_id / "source").resolve()
    file_path = (source_dir / relative_path).resolve()
    try:
        file_path.relative_to(source_dir)
    except ValueError:
        raise HTTPException(400, "Chemin de document invalide") from None
    if not file_path.is_file():
        raise HTTPException(404, "Fichier introuvable sur disque")

    return FileResponse(file_path, filename=filename, content_disposition_type="inline")
