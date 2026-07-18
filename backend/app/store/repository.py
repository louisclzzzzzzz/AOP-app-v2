"""Fonctions CRUD pour dossiers, documents et cache de texte."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.store.models import (
    CacheStatus,
    ClassificationStatus,
    CompletenessCheck,
    CompletenessStatus,
    Dossier,
    DossierStatus,
    Document,
    DocumentStage,
    ExtractionResult,
    ExtractionStatus,
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


def find_dossier_by_upload_hash(session: Session, sha256: str, *, exclude_id: str) -> Dossier | None:
    """Le plus récent autre dossier portant le même hash de zip uploadé — sert uniquement à
    avertir d'un probable doublon, jamais à bloquer l'upload."""
    stmt = (
        select(Dossier)
        .where(Dossier.upload_sha256 == sha256, Dossier.id != exclude_id)
        .order_by(Dossier.created_at.desc())
    )
    return session.scalars(stmt).first()


def set_dossier_upload_info(
    session: Session, dossier: Dossier, *, upload_sha256: str, duplicate_of: Dossier | None
) -> None:
    dossier.upload_sha256 = upload_sha256
    if duplicate_of is not None:
        dossier.duplicate_of_dossier_id = duplicate_of.id
        dossier.duplicate_of_filename = duplicate_of.original_filename
        dossier.duplicate_of_created_at = duplicate_of.created_at
    session.add(dossier)
    session.flush()


def reopen_reorganization(session: Session, dossier: Dossier) -> None:
    """Rouvre l'étape 1 pour correction. Les résultats des étapes 2/3, s'ils existent,
    référencent des documents dont le classement (catégorie/lot) va changer une fois
    reclassés : les garder laisserait un état silencieusement incohérent plutôt que de
    forcer une ré-analyse propre — cf. FRICTIONS_EXPERT_METIER.md §3."""
    session.execute(delete(ExtractionResult).where(ExtractionResult.dossier_id == dossier.id))
    session.execute(delete(CompletenessCheck).where(CompletenessCheck.dossier_id == dossier.id))
    dossier.reorg_applied_at = None
    dossier.reorg_report_json_path = None
    dossier.reorg_report_md_path = None
    dossier.completeness_validated_at = None
    dossier.completeness_report_json_path = None
    dossier.completeness_report_md_path = None
    dossier.extraction_validated_at = None
    dossier.extraction_report_json_path = None
    dossier.extraction_report_md_path = None
    dossier.pieces_selected = 0
    dossier.pieces_checked = 0
    dossier.pieces_present = 0
    dossier.pieces_absent = 0
    dossier.pieces_error = 0
    dossier.fields_total = 0
    dossier.fields_extracted = 0
    dossier.fields_present = 0
    dossier.fields_absent = 0
    dossier.fields_incoherent = 0
    dossier.fields_error = 0
    dossier.status = DossierStatus.CLASSIFIED.value
    session.add(dossier)
    session.flush()


def reopen_completeness(session: Session, dossier: Dossier) -> None:
    """Rouvre l'étape 2 pour correction. N'invalide pas l'extraction (étape 3) : elle relit
    l'intégralité des documents organisés, indépendamment des sélections/corrections de
    complétude — seulement potentiellement obsolète si l'étape 1 est elle-même rouverte."""
    dossier.completeness_validated_at = None
    dossier.completeness_report_json_path = None
    dossier.completeness_report_md_path = None
    dossier.status = DossierStatus.COMPLETENESS_REVIEW.value
    session.add(dossier)
    session.flush()


def reopen_extraction(session: Session, dossier: Dossier) -> None:
    """Rouvre l'étape 3 pour correction — dernière étape, aucune donnée en aval à invalider."""
    dossier.extraction_validated_at = None
    dossier.extraction_report_json_path = None
    dossier.extraction_report_md_path = None
    dossier.status = DossierStatus.EXTRACTION_REVIEW.value
    session.add(dossier)
    session.flush()


def delete_dossier(session: Session, dossier_id: str) -> None:
    """Supprime le dossier et toutes ses lignes dépendantes. Ne touche jamais `text_cache` :
    ces entrées sont partagées par hash de contenu entre dossiers (§ TextCache) et peuvent
    être référencées par d'autres dossiers encore présents."""
    session.execute(delete(ExtractionResult).where(ExtractionResult.dossier_id == dossier_id))
    session.execute(delete(CompletenessCheck).where(CompletenessCheck.dossier_id == dossier_id))
    session.execute(delete(Document).where(Document.dossier_id == dossier_id))
    session.execute(delete(Dossier).where(Dossier.id == dossier_id))
    session.flush()


def recompute_dossier_counters(session: Session, dossier: Dossier) -> None:
    docs = session.scalars(select(Document).where(Document.dossier_id == dossier.id)).all()
    dossier.total_files = len(docs)
    dossier.files_text_extracted = sum(1 for d in docs if d.stage == "text_extracted")
    dossier.files_non_analyzable = sum(1 for d in docs if d.stage == "non_analyzable")
    dossier.files_non_analyzable_at_risk = sum(
        1 for d in docs if d.stage == "non_analyzable" and d.non_analyzable_at_risk
    )
    dossier.files_error = sum(1 for d in docs if d.stage == "error")
    dossier.files_classified = sum(
        1
        for d in docs
        if d.classification_status
        in (
            ClassificationStatus.PROPOSED.value,
            ClassificationStatus.CORRECTED.value,
            ClassificationStatus.ERROR.value,
        )
    )
    session.add(dossier)
    session.flush()


def mark_reorg_applied(
    session: Session, dossier: Dossier, *, json_path: str, md_path: str
) -> None:
    dossier.reorg_report_json_path = json_path
    dossier.reorg_report_md_path = md_path
    dossier.reorg_applied_at = dt.datetime.now(dt.timezone.utc)
    session.add(dossier)
    session.flush()


def recompute_completeness_counters(session: Session, dossier: Dossier) -> None:
    all_checks = session.scalars(
        select(CompletenessCheck).where(CompletenessCheck.dossier_id == dossier.id)
    ).all()
    dossier.pieces_selected = sum(1 for c in all_checks if c.is_selected)
    checks = [c for c in all_checks if c.is_selected]
    dossier.pieces_checked = sum(
        1
        for c in checks
        if c.status
        in (CompletenessStatus.PROPOSED.value, CompletenessStatus.CORRECTED.value, CompletenessStatus.ERROR.value)
    )
    dossier.pieces_present = sum(1 for c in checks if c.final_presence == "present")
    dossier.pieces_absent = sum(1 for c in checks if c.final_presence == "absent")
    dossier.pieces_error = sum(1 for c in checks if c.status == CompletenessStatus.ERROR.value)
    session.add(dossier)
    session.flush()


def mark_completeness_validated(
    session: Session, dossier: Dossier, *, json_path: str, md_path: str
) -> None:
    dossier.completeness_report_json_path = json_path
    dossier.completeness_report_md_path = md_path
    dossier.completeness_validated_at = dt.datetime.now(dt.timezone.utc)
    session.add(dossier)
    session.flush()


def recompute_extraction_counters(session: Session, dossier: Dossier) -> None:
    results = session.scalars(
        select(ExtractionResult).where(ExtractionResult.dossier_id == dossier.id)
    ).all()
    dossier.fields_total = len(results)
    dossier.fields_extracted = sum(
        1
        for r in results
        if r.status in (ExtractionStatus.PROPOSED.value, ExtractionStatus.CORRECTED.value, ExtractionStatus.ERROR.value)
    )
    dossier.fields_present = sum(1 for r in results if r.final_value)
    dossier.fields_absent = sum(1 for r in results if r.status != ExtractionStatus.PENDING.value and not r.final_value)
    dossier.fields_incoherent = sum(1 for r in results if r.cross_check_status == "incoherent")
    dossier.fields_error = sum(1 for r in results if r.status == ExtractionStatus.ERROR.value)
    session.add(dossier)
    session.flush()


def mark_extraction_validated(
    session: Session, dossier: Dossier, *, json_path: str, md_path: str
) -> None:
    dossier.extraction_report_json_path = json_path
    dossier.extraction_report_md_path = md_path
    dossier.extraction_validated_at = dt.datetime.now(dt.timezone.utc)
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


def set_document_classification_result(
    session: Session,
    document: Document,
    *,
    category: str,
    lot: str | None,
    doc_type: str,
    filename: str,
    confidence: float,
    justification: str,
    signals: dict[str, Any],
    model_name: str,
    model_version: str,
    error: str | None,
) -> None:
    document.proposed_category = category
    document.proposed_lot = lot
    document.proposed_doc_type = doc_type
    document.proposed_filename = filename
    document.classification_confidence = confidence
    document.classification_justification = justification
    document.classification_signals_json = json.dumps(signals, ensure_ascii=False)
    document.classification_model = model_name
    document.classification_model_version = model_version
    document.classified_at = dt.datetime.now(dt.timezone.utc)
    document.classification_error = error
    document.classification_status = (
        ClassificationStatus.ERROR.value if error else ClassificationStatus.PROPOSED.value
    )
    # Les valeurs finales démarrent égales à la proposition (même en cas d'erreur, où le moteur
    # retombe déjà sur la catégorie de repli AUTRES — jamais un fichier sans destination) —
    # écrasées seulement par une correction humaine explicite (checkpoint), jamais silencieusement.
    document.final_category = category
    document.final_lot = lot
    document.final_doc_type = doc_type
    document.final_filename = filename

    session.add(document)
    session.flush()


def set_document_classification_correction(
    session: Session,
    document: Document,
    *,
    category: str,
    lot: str | None,
    doc_type: str,
    filename: str,
) -> None:
    document.final_category = category
    document.final_lot = lot
    document.final_doc_type = doc_type
    document.final_filename = filename
    document.is_manually_corrected = True
    document.classification_status = ClassificationStatus.CORRECTED.value
    session.add(document)
    session.flush()


def set_document_organized_path(session: Session, document: Document, relative_path: str) -> None:
    document.organized_relative_path = relative_path
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


# --- CompletenessCheck ---------------------------------------------------------

def create_completeness_check(session: Session, **kwargs) -> CompletenessCheck:
    check = CompletenessCheck(**kwargs)
    session.add(check)
    session.flush()
    return check


def list_completeness_checks(session: Session, dossier_id: str) -> list[CompletenessCheck]:
    stmt = (
        select(CompletenessCheck)
        .where(CompletenessCheck.dossier_id == dossier_id)
        .order_by(CompletenessCheck.piece_id)
    )
    return list(session.scalars(stmt))


def get_completeness_check_by_piece(
    session: Session, dossier_id: str, piece_id: str
) -> CompletenessCheck | None:
    stmt = select(CompletenessCheck).where(
        CompletenessCheck.dossier_id == dossier_id, CompletenessCheck.piece_id == piece_id
    )
    return session.scalars(stmt).first()


def set_completeness_selection(session: Session, check: CompletenessCheck, *, is_selected: bool) -> None:
    check.is_selected = is_selected
    session.add(check)
    session.flush()


def set_completeness_result(
    session: Session,
    check: CompletenessCheck,
    *,
    match_layer: str,
    presence: str,
    certainty: str | None,
    confidence: float | None,
    justification: str,
    matched_document_ids: list[str],
    matched_lots: dict[str, Any] | None,
    model_name: str | None,
    model_version: str | None,
    error: str | None,
) -> None:
    check.match_layer = match_layer
    check.proposed_presence = presence
    check.proposed_certainty = certainty
    check.proposed_confidence = confidence
    check.proposed_justification = justification
    check.proposed_matched_document_ids_json = json.dumps(matched_document_ids, ensure_ascii=False)
    check.proposed_matched_lots_json = (
        json.dumps(matched_lots, ensure_ascii=False) if matched_lots is not None else None
    )
    check.completeness_model = model_name
    check.completeness_model_version = model_version
    check.analyzed_at = dt.datetime.now(dt.timezone.utc)
    check.completeness_error = error
    check.status = CompletenessStatus.ERROR.value if error else CompletenessStatus.PROPOSED.value
    # Les valeurs finales démarrent égales à la proposition — écrasées seulement par une
    # correction humaine explicite (checkpoint), jamais silencieusement.
    check.final_presence = presence
    check.final_certainty = certainty
    session.add(check)
    session.flush()


def set_completeness_correction(
    session: Session, check: CompletenessCheck, *, presence: str, certainty: str | None
) -> None:
    check.final_presence = presence
    check.final_certainty = certainty
    check.is_manually_corrected = True
    check.corrected_at = dt.datetime.now(dt.timezone.utc)
    check.status = CompletenessStatus.CORRECTED.value
    session.add(check)
    session.flush()


# --- ExtractionResult ----------------------------------------------------------

def create_extraction_result(session: Session, **kwargs) -> ExtractionResult:
    result = ExtractionResult(**kwargs)
    session.add(result)
    session.flush()
    return result


def list_extraction_results(session: Session, dossier_id: str) -> list[ExtractionResult]:
    stmt = (
        select(ExtractionResult)
        .where(ExtractionResult.dossier_id == dossier_id)
        .order_by(ExtractionResult.field_id)
    )
    return list(session.scalars(stmt))


def get_extraction_result_by_field(
    session: Session, dossier_id: str, field_id: str
) -> ExtractionResult | None:
    stmt = select(ExtractionResult).where(
        ExtractionResult.dossier_id == dossier_id, ExtractionResult.field_id == field_id
    )
    return session.scalars(stmt).first()


def set_extraction_result(
    session: Session,
    result: ExtractionResult,
    *,
    match_layer: str,
    value: str | None,
    confidence: float | None,
    justification: str | None,
    citation: str | None,
    sources: list[dict[str, Any]],
    cross_check_status: str | None,
    model_name: str | None,
    model_version: str | None,
    error: str | None,
) -> None:
    result.match_layer = match_layer
    result.proposed_value = value
    result.proposed_confidence = confidence
    result.proposed_justification = justification
    result.proposed_citation = citation
    result.proposed_sources_json = json.dumps(sources, ensure_ascii=False)
    result.cross_check_status = cross_check_status
    result.extraction_model = model_name
    result.extraction_model_version = model_version
    result.analyzed_at = dt.datetime.now(dt.timezone.utc)
    result.extraction_error = error
    result.status = ExtractionStatus.ERROR.value if error else ExtractionStatus.PROPOSED.value
    # La valeur finale démarre égale à la proposition — écrasée seulement par une correction
    # humaine explicite (checkpoint), jamais silencieusement.
    result.final_value = value
    session.add(result)
    session.flush()


def set_extraction_correction(session: Session, result: ExtractionResult, *, final_value: str) -> None:
    result.final_value = final_value
    result.is_manually_corrected = True
    result.corrected_at = dt.datetime.now(dt.timezone.utc)
    result.status = ExtractionStatus.CORRECTED.value
    session.add(result)
    session.flush()
