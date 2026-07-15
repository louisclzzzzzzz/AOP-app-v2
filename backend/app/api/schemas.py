"""Schémas de réponse API (Pydantic) — contrat stable, indépendant des modèles ORM."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class CountersOut(BaseModel):
    total_files: int
    text_extracted: int
    non_analyzable: int
    error: int
    classified: int = 0


class DossierOut(BaseModel):
    id: str
    original_filename: str
    status: str
    current_step: int
    error_message: str | None
    counters: CountersOut
    reorg_applied_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class DocumentOut(BaseModel):
    id: str
    relative_path: str
    filename: str
    extension: str
    size_bytes: int
    sha256: str
    category: str
    is_analyzable: bool
    non_analyzable_reason: str | None
    parent_archive_id: str | None
    stage: str
    stage_error: str | None
    text_extraction_method: str | None
    detected_title: str | None
    preview_text: str | None
    key_mentions: dict | None


class DocumentTextOut(BaseModel):
    document_id: str
    filename: str
    method: str | None
    avg_confidence: float | None
    model_name: str | None
    model_version: str | None
    page_count: int | None
    char_count: int
    text: str


class ClassificationEntryOut(BaseModel):
    document_id: str
    relative_path: str
    filename: str
    is_analyzable: bool

    classification_status: str
    classification_error: str | None

    proposed_category: str | None
    proposed_lot: str | None
    proposed_doc_type: str | None
    proposed_filename: str | None
    confidence: float | None
    justification: str | None
    signals: dict | None
    model_name: str | None
    model_version: str | None

    final_category: str | None
    final_lot: str | None
    final_doc_type: str | None
    final_filename: str | None
    is_manually_corrected: bool
    organized_relative_path: str | None


class ClassificationCorrectionIn(BaseModel):
    category: str
    lot: str | None = None
    doc_type: str
    filename: str


class TaxonomyCategoryOut(BaseModel):
    path: str
    label: str
    alt_names: list[str]
    lot_aware: bool


class ReorgApplyOut(BaseModel):
    dossier: DossierOut
    report: dict
