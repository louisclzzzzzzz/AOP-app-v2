"""Signal de document partagé entre les moteurs d'analyse post-classification (complétude
étape 2, extraction étape 3) : contenu texte (natif ou OCR) + confiance + catégorie finale.

Reçoit un instantané déjà détaché de sa session d'origine (dict de scalaires) pour ne jamais
garder deux sessions SQLAlchemy ouvertes simultanément — la fonction appelante lit la liste des
documents dans une session, la ferme, puis appelle `build_document_signal` par document, qui
ouvre sa propre session pour le seul lookup TextCache dont il a besoin.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ocr.cache import read_text_cache
from app.store.db import session_scope
from app.store.models import TextCache


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
