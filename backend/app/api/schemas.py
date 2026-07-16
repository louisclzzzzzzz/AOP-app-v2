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
    pieces_selected: int = 0
    pieces_checked: int = 0
    pieces_present: int = 0
    pieces_absent: int = 0
    pieces_error: int = 0
    fields_total: int = 0
    fields_extracted: int = 0
    fields_present: int = 0
    fields_absent: int = 0
    fields_incoherent: int = 0
    fields_error: int = 0


class DossierOut(BaseModel):
    id: str
    original_filename: str
    status: str
    current_step: int
    error_message: str | None
    counters: CountersOut
    reorg_applied_at: dt.datetime | None = None
    completeness_validated_at: dt.datetime | None = None
    extraction_validated_at: dt.datetime | None = None
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


class PieceOut(BaseModel):
    id: str
    libelle: str
    phase: str
    alias: list[str]
    categorie_attendue: str | None
    obligatoire: bool
    par_lot: bool


class CompletenessEntryOut(BaseModel):
    piece_id: str
    libelle: str
    phase: str
    alias: list[str]
    obligatoire: bool
    is_selected: bool

    status: str
    completeness_error: str | None
    match_layer: str | None

    proposed_presence: str | None
    proposed_certainty: str | None
    confidence: float | None
    justification: str | None
    matched_document_ids: list[str]
    matched_lots: dict | None
    model_name: str | None
    model_version: str | None

    final_presence: str | None
    final_certainty: str | None
    is_manually_corrected: bool


class CompletenessSelectionItem(BaseModel):
    piece_id: str
    is_selected: bool


class CompletenessSelectionIn(BaseModel):
    selection: list[CompletenessSelectionItem]


class CompletenessCorrectionIn(BaseModel):
    presence: str
    certainty: str | None = None


class CompletenessApplyOut(BaseModel):
    dossier: DossierOut
    report: dict


class ExtractionFieldOut(BaseModel):
    id: str
    libelle: str
    section: str
    resultat_attendu: str | None
    reference_categories: list[str]


class ExtractionSourceOut(BaseModel):
    document_id: str
    filename: str
    value: str
    confidence: float | None


class ExtractionEntryOut(BaseModel):
    field_id: str
    libelle: str
    section: str
    resultat_attendu: str | None

    status: str
    extraction_error: str | None
    match_layer: str | None

    proposed_value: str | None
    confidence: float | None
    justification: str | None
    citation: str | None
    sources: list[ExtractionSourceOut]
    cross_check_status: str | None
    model_name: str | None
    model_version: str | None

    final_value: str | None
    is_manually_corrected: bool


class ExtractionCorrectionIn(BaseModel):
    final_value: str


class ExtractionApplyOut(BaseModel):
    dossier: DossierOut
    report: dict
