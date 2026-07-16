from __future__ import annotations

import app.ingestion.document_signal as ds
from app.ingestion.document_signal import DocumentSignal, ensure_document_ocr
from app.ingestion.text_extraction import ExtractionOutcome
from app.store.db import session_scope
from app.store.models import FileCategory, TextExtractionMethod
from app.store.repository import (
    create_dossier,
    create_document,
    get_or_create_pending_text_cache,
    update_text_cache_result,
)


def _setup_document(*, method: str, char_count: int = 0):
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        cache, _created = get_or_create_pending_text_cache(s, "hash-rc", ".pdf")
        cache_id = cache.id
        update_text_cache_result(
            s,
            cache_id,
            method=method,
            text_path=None,
            char_count=char_count,
            page_count=None,
            avg_confidence=None,
            model_name=None,
            model_version=None,
            pages_meta=None,
            error=None,
        )
        document = create_document(
            s,
            dossier_id=dossier_id,
            relative_path="ADMIN/RC.pdf",
            filename="RC.pdf",
            extension=".pdf",
            size_bytes=8,
            sha256="hash-rc",
            category=FileCategory.PDF.value,
            is_analyzable=True,
            text_cache_id=cache_id,
            final_category="ADMIN/RC",
        )
        document_id = document.id
    return dossier_id, document_id, cache_id


def test_ensure_document_ocr_upgrades_deferred_cache(isolated_workspace, monkeypatch):
    dossier_id, document_id, cache_id = _setup_document(method=TextExtractionMethod.DEFERRED.value)

    calls = []

    def _fake_extract(path, category, *, allow_ocr):
        calls.append((path, category, allow_ocr))
        return ExtractionOutcome(
            method=TextExtractionMethod.OCR.value,
            combined_text="Texte OCRisé complet.",
            avg_confidence=0.9,
            page_count=1,
            char_count=22,
            model_name="mistral-ocr-test",
            model_version="v1",
        )

    monkeypatch.setattr(ds, "extract_text_for_file", _fake_extract)

    doc = DocumentSignal(
        document_id=document_id,
        filename="RC.pdf",
        final_category="ADMIN/RC",
        final_lot=None,
        classification_confidence=0.8,
        content_excerpt="",
        ocr_confidence=None,
    )

    refreshed = ensure_document_ocr(dossier_id, doc)

    assert len(calls) == 1
    _, _, allow_ocr = calls[0]
    assert allow_ocr is True
    assert refreshed.content_excerpt == "Texte OCRisé complet."
    assert refreshed.ocr_confidence == 0.9

    with session_scope() as s:
        from app.store.models import Document, TextCache

        cache = s.get(TextCache, cache_id)
        assert cache.method == TextExtractionMethod.OCR.value
        assert cache.status == "done"
        document = s.get(Document, document_id)
        assert document.text_extraction_method == TextExtractionMethod.OCR.value
        assert document.stage == "text_extracted"


def test_ensure_document_ocr_is_noop_when_already_extracted(isolated_workspace, monkeypatch):
    dossier_id, document_id, _cache_id = _setup_document(
        method=TextExtractionMethod.NATIVE_PDF.value, char_count=10
    )

    def _boom(*args, **kwargs):
        raise AssertionError("aucune ré-extraction ne doit être tentée si le texte est déjà définitif")

    monkeypatch.setattr(ds, "extract_text_for_file", _boom)

    doc = DocumentSignal(
        document_id=document_id,
        filename="RC.pdf",
        final_category="ADMIN/RC",
        final_lot=None,
        classification_confidence=0.8,
        content_excerpt="Texte natif déjà là.",
        ocr_confidence=None,
    )

    refreshed = ensure_document_ocr(dossier_id, doc)

    assert refreshed == doc
