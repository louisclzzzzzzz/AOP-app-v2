"""Appel OCR Mistral de haut niveau : upload + process, mis en forme du résultat.

Exploite les scores de confiance (par page) et les bounding boxes (blocks) renvoyés par
l'API, conformément à la contrainte non négociable §1 du PLAN. Le JSON brut est conservé
en sidecar pour permettre plus tard une citation précise (page + position) dans l'UI.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from app.mistral import call_log
from app.mistral.client import call_ocr, ocr_slot, upload_file_for_ocr
from app.settings import get_models_config

logger = logging.getLogger(__name__)


@dataclass
class OcrPageOutcome:
    index: int
    markdown: str
    avg_confidence: float | None
    min_confidence: float | None
    char_count: int


@dataclass
class OcrCallOutcome:
    model: str
    pages: list[OcrPageOutcome]
    combined_markdown: str
    avg_confidence: float | None
    raw_json: str


def run_ocr(path: Path, *, pages: list[int] | None = None) -> OcrCallOutcome:
    """OCRise un fichier local (PDF ou image). `pages` (0-indexées) restreint l'appel à
    un sous-ensemble de pages — utilisé pour l'OCR de contrôle sur PDF partiellement natif."""
    # Un seul créneau (donc une seule clé API) pour l'upload ET le traitement : le file_id rendu
    # par l'upload n'existe que dans le compte qui l'a uploadé.
    t0 = time.monotonic()
    with ocr_slot() as slot:
        file_id = upload_file_for_ocr(path, slot=slot)
        response = call_ocr(file_id=file_id, pages=pages, slot=slot)
    latency_ms = (time.monotonic() - t0) * 1000
    if response.usage_info:
        logger.info(
            "USAGE ocr file=%s pages_processed=%s doc_size_bytes=%s",
            path.name, response.usage_info.pages_processed, response.usage_info.doc_size_bytes,
        )

    page_outcomes: list[OcrPageOutcome] = []
    for p in response.pages:
        scores = p.confidence_scores
        avg_c = scores.average_page_confidence_score if scores else None
        min_c = scores.minimum_page_confidence_score if scores else None
        page_outcomes.append(
            OcrPageOutcome(
                index=p.index,
                markdown=p.markdown,
                avg_confidence=avg_c,
                min_confidence=min_c,
                char_count=len(p.markdown or ""),
            )
        )

    combined_markdown = "\n\n".join(
        f"<!-- page {p.index} -->\n{p.markdown}" for p in page_outcomes
    )
    confidences = [p.avg_confidence for p in page_outcomes if p.avg_confidence is not None]
    avg_confidence = sum(confidences) / len(confidences) if confidences else None

    if call_log.is_enabled():
        call_log.log_ocr_call(
            document=path.name,
            pages_requested=pages,
            model=response.model,
            pages_processed=response.usage_info.pages_processed if response.usage_info else None,
            doc_size_bytes=response.usage_info.doc_size_bytes if response.usage_info else None,
            avg_confidence=avg_confidence,
            pages_output=[
                {
                    "index": p.index,
                    "char_count": p.char_count,
                    "avg_confidence": p.avg_confidence,
                    "min_confidence": p.min_confidence,
                    "markdown": p.markdown,
                }
                for p in page_outcomes
            ],
            combined_markdown=combined_markdown,
            latency_ms=latency_ms,
        )

    return OcrCallOutcome(
        model=response.model,
        pages=page_outcomes,
        combined_markdown=combined_markdown,
        avg_confidence=avg_confidence,
        raw_json=response.model_dump_json(),
    )


def get_ocr_model_version() -> str:
    return get_models_config()["ocr"]["model"]
