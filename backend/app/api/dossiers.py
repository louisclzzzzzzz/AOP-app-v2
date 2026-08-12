"""Endpoints REST : upload d'un dossier (zip), liste, détail, inventaire, texte extrait."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.schemas import (
    CitationLocationOut,
    CitationRectOut,
    CountersOut,
    DocumentOut,
    DocumentTextOut,
    DossierOut,
)
from app.auth.dependencies import get_current_user_id
from app.classify.pipeline import run_classification_pipeline
from app.extraction.citation_preview import CitationLocation, locate_in_ocr_pages, locate_in_pdf
from app.ingestion.pipeline import run_ingestion_pipeline
from app.ocr.cache import delete_text_cache_files, read_text_cache
from app.pipeline_support import owner_api_key, run_pipeline_safely
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
    get_user_api_key_row,
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
        audit_risques_md=d.audit_risques_md,
        audit_risques_model=d.audit_risques_model,
        audit_risques_status=d.audit_risques_status,
        audit_risques_error=d.audit_risques_error,
        audit_risques_generated_at=d.audit_risques_generated_at,
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
        async with owner_api_key(dossier_id):
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
async def upload_dossier(file: UploadFile, background_tasks: BackgroundTasks, request: Request) -> DossierOut:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Seuls les fichiers .zip sont acceptés")

    settings = get_settings()
    owner_user_id = get_current_user_id(request)
    if settings.require_auth:
        # Garanti non-None ici : le router impose déjà require_auth (app/main.py) — la requête
        # n'atteint ce point qu'avec une session valide.
        assert owner_user_id is not None
        with session_scope() as s:
            row = get_user_api_key_row(s, owner_user_id)
            has_key = bool(row and row.mistral_api_key_encrypted)
        if not has_key:
            raise HTTPException(
                400,
                "Configurez votre clé API Mistral personnelle avant d'analyser un dossier "
                "(bouton « Clé API » dans le menu).",
            )

    with session_scope() as s:
        dossier = create_dossier(s, file.filename, owner_user_id=owner_user_id)
        dossier_id = dossier.id

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


@router.post("/{dossier_id}/lancer", response_model=DossierOut)
async def start_dossier_pipeline(dossier_id: str, background_tasks: BackgroundTasks) -> DossierOut:
    """Démarre l'ingestion d'un dossier déposé mais non encore traité.

    Existe pour les dossiers créés par la veille automatique (§app/veille/pipeline.py) : leur
    zip est rapatrié sans qu'aucun traitement ne démarre, précisément pour qu'un balayage
    nocturne n'engage jamais de dépense d'API sans arbitrage humain. C'est cet appel qui
    déclenche l'analyse, une fois l'avis jugé digne d'intérêt."""
    settings = get_settings()
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status != DossierStatus.UPLOADED.value:
            raise HTTPException(409, f"Le traitement de ce dossier a déjà démarré (statut : {dossier.status}).")
        result = dossier_to_out(dossier)

    zip_path = settings.workspace_dir / dossier_id / "upload.zip"
    if not zip_path.exists():
        raise HTTPException(409, "L'archive de ce dossier est introuvable sur le disque.")

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
    file_path, filename = _resolve_document_path(dossier_id, document_id)
    return FileResponse(file_path, filename=filename, content_disposition_type="inline")


def _resolve_document_path(dossier_id: str, document_id: str) -> tuple[Path, str]:
    """Chemin disque du fichier original + son nom, avec la même garde anti-traversée que
    `get_document_file` — factorisée depuis celui-ci et réutilisée par la prévisualisation."""
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
    return file_path, filename


def _locate_citation(file_path: Path, document_id: str, citation: str) -> CitationLocation | None:
    """Localise la citation, avec repli sur la page seule pour les PDF sans couche de texte
    (documents scannés, lus par OCR)."""
    try:
        location = locate_in_pdf(file_path, citation)
    except Exception:
        logger.warning("Localisation de citation impossible dans %s", file_path.name, exc_info=True)
        return None
    if location is not None:
        return location
    # Pas de couche de texte exploitable : le texte OCR mis en cache sait au moins dire la page.
    pages = _ocr_page_texts(document_id)
    return locate_in_ocr_pages(pages, citation) if pages else None


def _ocr_page_texts(document_id: str) -> list[str]:
    """Texte OCR page par page, depuis le sidecar `<hash>.ocr.json` du cache — déduit du
    `text_path` du cache (même hash, autre suffixe, §app/ocr/cache.py). [] s'il n'existe pas,
    ce qui est le cas de tout document lu en texte natif."""
    with session_scope() as s:
        document = get_document(s, document_id)
        cache = s.get(TextCache, document.text_cache_id) if document and document.text_cache_id else None
        text_path = cache.text_path if cache else None
    if not text_path:
        return []
    json_path = get_settings().workspace_dir / text_path
    json_path = json_path.with_name(json_path.name.removesuffix(".md") + ".ocr.json")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [page.get("markdown") or "" for page in payload.get("pages", [])]


@router.get("/{dossier_id}/documents/{document_id}/citation")
async def locate_document_citation(dossier_id: str, document_id: str, citation: str) -> CitationLocationOut:
    """Où se trouve, dans le document, le passage cité à l'appui d'une valeur extraite.

    Rend la page seul le frontend (PDF.js, sur le fichier servi par `/file`) : ce endpoint ne
    renvoie que ce que le serveur seul sait calculer — la correspondance entre la citation
    (reformulée par le LLM) et les coordonnées réelles du texte dans le PDF."""
    file_path, filename = _resolve_document_path(dossier_id, document_id)
    if file_path.suffix.lower() != ".pdf":
        # Les .docx/.xlsx du dossier n'ont pas de rendu paginé : l'expert garde le lien
        # « ouvrir le document », mais aucune preuve visuelle n'est promise.
        return CitationLocationOut(found=False, page=None, page_count=None, reason="not_a_pdf", filename=filename)

    location = _locate_citation(file_path, document_id, citation)
    if location is None:
        return CitationLocationOut(found=False, page=None, page_count=None, reason="not_found", filename=filename)
    return CitationLocationOut(
        found=True,
        page=location.page,
        page_count=None,
        highlighted=bool(location.rects),
        reason=None if location.rects else "scanned_page_only",
        filename=filename,
        rects=[CitationRectOut(x0=r.x0, top=r.top, x1=r.x1, bottom=r.bottom) for r in location.rects],
    )
