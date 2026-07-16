"""Signal de document partagé entre les moteurs d'analyse post-classification (complétude
étape 2, extraction étape 3) : contenu texte (natif ou OCR) + confiance + catégorie finale.

Reçoit un instantané déjà détaché de sa session d'origine (dict de scalaires) pour ne jamais
garder deux sessions SQLAlchemy ouvertes simultanément — la fonction appelante lit la liste des
documents dans une session, la ferme, puis appelle `build_document_signal` par document, qui
ouvre sa propre session pour le seul lookup TextCache dont il a besoin.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.metadata import detect_key_mentions, first_nonempty_line
from app.ingestion.text_extraction import extract_text_for_file
from app.ocr.cache import read_text_cache, write_text_cache_files
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import TextCache, TextExtractionMethod
from app.store.repository import get_document, set_document_text_result, update_text_cache_result


@dataclass(frozen=True)
class DocumentSignal:
    document_id: str
    filename: str
    final_category: str | None
    final_lot: str | None
    classification_confidence: float | None
    content_excerpt: str
    ocr_confidence: float | None


def build_document_signal(doc_snapshot: dict) -> DocumentSignal:
    content_excerpt = ""
    ocr_confidence: float | None = None
    text_cache_id = doc_snapshot["text_cache_id"]
    if text_cache_id:
        with session_scope() as s:
            cache = s.get(TextCache, text_cache_id)
            text_path = cache.text_path if cache else None
            ocr_confidence = cache.avg_confidence if cache else None
        if text_path:
            content_excerpt = read_text_cache(text_path)
    return DocumentSignal(
        document_id=doc_snapshot["id"],
        filename=doc_snapshot["filename"],
        final_category=doc_snapshot["final_category"],
        final_lot=doc_snapshot["final_lot"],
        classification_confidence=doc_snapshot["classification_confidence"],
        content_excerpt=content_excerpt,
        ocr_confidence=ocr_confidence,
    )


def ensure_document_ocr(dossier_id: str, doc: DocumentSignal) -> DocumentSignal:
    """OCR à la demande (§5 OPTIMISATION.md, phase 4) : si l'ingestion a différé l'OCR de ce
    document (`text_extraction.defer_ocr_to_extraction`), le ré-extrait maintenant avec OCR
    complet, met à jour le cache partagé (par hash) + le document, et retourne un signal
    rafraîchi. No-op (aucun appel OCR) si le document a déjà un texte définitif — donc coût nul
    quand l'option est désactivée."""
    with session_scope() as s:
        document = get_document(s, doc.document_id)
        assert document is not None
        cache = s.get(TextCache, document.text_cache_id) if document.text_cache_id else None
        if cache is None or cache.method != TextExtractionMethod.DEFERRED.value:
            return doc
        relative_path = document.relative_path
        category = document.category
        sha256 = document.sha256
        cache_id = document.text_cache_id

    settings = get_settings()
    path = settings.workspace_dir / dossier_id / "source" / relative_path
    outcome = extract_text_for_file(path, category, allow_ocr=True)
    text_path_rel, _json_rel = write_text_cache_files(sha256, outcome.combined_text, outcome.raw_json)

    with session_scope() as s:
        update_text_cache_result(
            s,
            cache_id,
            method=outcome.method,
            text_path=text_path_rel,
            char_count=outcome.char_count,
            page_count=outcome.page_count,
            avg_confidence=outcome.avg_confidence,
            model_name=outcome.model_name,
            model_version=outcome.model_version,
            pages_meta=outcome.pages_meta,
            error=outcome.error,
        )
        document = get_document(s, doc.document_id)
        assert document is not None
        set_document_text_result(
            s,
            document,
            text_cache_id=cache_id,
            method=outcome.method,
            detected_title=first_nonempty_line(outcome.combined_text) if outcome.combined_text else None,
            preview_text=outcome.combined_text[:1000] if outcome.combined_text else None,
            key_mentions=detect_key_mentions(outcome.combined_text) if outcome.combined_text else None,
            error=outcome.error,
        )

    return DocumentSignal(
        document_id=doc.document_id,
        filename=doc.filename,
        final_category=doc.final_category,
        final_lot=doc.final_lot,
        classification_confidence=doc.classification_confidence,
        content_excerpt=outcome.combined_text,
        ocr_confidence=outcome.avg_confidence,
    )
