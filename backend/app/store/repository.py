"""Fonctions CRUD pour dossiers, documents et cache de texte."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import (
    CacheStatus,
    Dossier,
    DossierStatus,
    Document,
    DocumentStage,
    TextCache,
)


# --- Dossier ---------------------------------------------------------------

def create_dossier(session: Session, original_filename: str) -> Dossier:
    dossier = Dossier(original_filename=original_filename, status=DossierStatus.UPLOADED.value)
    session.add(dossier)
    session.flush()
    return dossier


def get_dossier(session: Session, dossier_id: str) -> Dossier | None:
    return session.get(Dossier, dossier_id)


def list_dossiers(session: Session) -> list[Dossier]:
    stmt = select(Dossier).order_by(Dossier.created_at.desc())
    return list(session.scalars(stmt))


def set_dossier_status(
    session: Session, dossier: Dossier, status: DossierStatus, error_message: str | None = None
) -> None:
    dossier.status = status.value
    if error_message is not None:
        dossier.error_message = error_message
    session.add(dossier)
    session.flush()


def recompute_dossier_counters(session: Session, dossier: Dossier) -> None:
    docs = session.scalars(select(Document).where(Document.dossier_id == dossier.id)).all()
    dossier.total_files = len(docs)
    dossier.files_text_extracted = sum(1 for d in docs if d.stage == "text_extracted")
    dossier.files_non_analyzable = sum(1 for d in docs if d.stage == "non_analyzable")
    dossier.files_error = sum(1 for d in docs if d.stage == "error")
    session.add(dossier)
    session.flush()


# --- Document ----------------------------------------------------------------

def create_document(session: Session, **kwargs) -> Document:
    doc = Document(**kwargs)
    session.add(doc)
    session.flush()
    return doc


def list_documents(session: Session, dossier_id: str) -> list[Document]:
    stmt = select(Document).where(Document.dossier_id == dossier_id).order_by(Document.relative_path)
    return list(session.scalars(stmt))


def get_document(session: Session, document_id: str) -> Document | None:
    return session.get(Document, document_id)


def set_document_text_result(
    session: Session,
    document: Document,
    *,
    text_cache_id: str | None,
    method: str | None,
    detected_title: str | None,
    preview_text: str | None,
    key_mentions: dict[str, Any] | None,
    error: str | None,
) -> None:
    document.text_cache_id = text_cache_id
    document.text_extraction_method = method
    document.detected_title = detected_title
    document.preview_text = preview_text
    document.key_mentions_json = json.dumps(key_mentions, ensure_ascii=False) if key_mentions else None
    if error:
        document.stage = DocumentStage.ERROR.value
        document.stage_error = error
    else:
        document.stage = DocumentStage.TEXT_EXTRACTED.value
        document.stage_error = None
    session.add(document)
    session.flush()


# --- TextCache ---------------------------------------------------------------

def get_text_cache_by_hash(session: Session, content_hash: str) -> TextCache | None:
    stmt = select(TextCache).where(TextCache.content_hash == content_hash)
    return session.scalars(stmt).first()


def get_or_create_pending_text_cache(
    session: Session, content_hash: str, extension: str
) -> tuple[TextCache, bool]:
    """Retourne (entrée, created). Si elle existe déjà (peu importe son statut), la réutilise."""
    existing = get_text_cache_by_hash(session, content_hash)
    if existing is not None:
        return existing, False
    entry = TextCache(
        content_hash=content_hash,
        extension=extension,
        method="none",
        status=CacheStatus.PENDING.value,
    )
    session.add(entry)
    session.flush()
    return entry, True


def update_text_cache_result(
    session: Session,
    cache_id: str,
    *,
    method: str,
    text_path: str | None,
    char_count: int,
    page_count: int | None,
    avg_confidence: float | None,
    model_name: str | None,
    model_version: str | None,
    pages_meta: list[dict[str, Any]] | None,
    error: str | None,
) -> TextCache:
    entry = session.get(TextCache, cache_id)
    assert entry is not None
    entry.method = method
    entry.status = CacheStatus.DONE.value if char_count > 0 else CacheStatus.FAILED.value
    entry.avg_confidence = avg_confidence
    entry.model_name = model_name
    entry.model_version = model_version
    entry.text_path = text_path
    entry.char_count = char_count
    entry.page_count = page_count
    entry.pages_meta_json = json.dumps(pages_meta, ensure_ascii=False) if pages_meta else None
    entry.error_message = error
    session.add(entry)
    session.flush()
    return entry
