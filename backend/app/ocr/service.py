"""Appel OCR Mistral de haut niveau : upload + process, mis en forme du résultat.

Exploite les scores de confiance (par page) et les bounding boxes (blocks) renvoyés par
l'API, conformément à la contrainte non négociable §1 du PLAN. Le JSON brut est conservé
en sidecar pour permettre plus tard une citation précise (page + position) dans l'UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.mistral.client import call_ocr, upload_file_for_ocr
from app.settings import get_models_config


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
    file_id = upload_file_for_ocr(path)
    response = call_ocr(file_id=file_id, pages=pages)

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

    return OcrCallOutcome(
        model=response.model,
        pages=page_outcomes,
        combined_markdown=combined_markdown,
        avg_confidence=avg_confidence,
        raw_json=response.model_dump_json(),
    )


def get_ocr_model_version() -> str:
    return get_models_config()["ocr"]["model"]
