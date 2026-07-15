"""Wrapper autour du SDK `mistralai` : client singleton, retry, appel OCR bas niveau.

Toute la logique métier (routage, cache, décisions de confiance) vit dans app/ocr/ et
app/ingestion/ ; ce module ne fait que parler au SDK de façon fiable.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

from mistralai.client import Mistral
from mistralai.client.errors.mistralerror import MistralError
from mistralai.client.models.ocrresponse import OCRResponse

from app.settings import get_models_config, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MistralNotConfiguredError(RuntimeError):
    """Levée quand MISTRAL_API_KEY est absente : on ne devine jamais, on échoue clairement."""


@lru_cache
def get_client() -> Mistral:
    settings = get_settings()
    if not settings.mistral_api_key:
        raise MistralNotConfiguredError(
            "MISTRAL_API_KEY manquante. Renseignez-la dans .env (voir .env.example)."
        )
    return Mistral(api_key=settings.mistral_api_key)


def _retry(fn: Callable[[], T], *, what: str) -> T:
    cfg = get_models_config()["llm"]
    max_retries = int(cfg.get("max_retries", 3))
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except MistralError as exc:
            last_error = exc
            wait = min(2 ** attempt, 30)
            logger.warning(
                "Mistral API error during %s (tentative %d/%d): %s — retry dans %ds",
                what,
                attempt,
                max_retries,
                exc,
                wait,
            )
            if attempt < max_retries:
                time.sleep(wait)
    assert last_error is not None
    raise last_error


def upload_file_for_ocr(path: Path) -> str:
    """Upload un fichier local vers l'API Mistral (purpose=ocr) et retourne son file_id."""
    client = get_client()
    with open(path, "rb") as f:
        content = f.read()

    def _do() -> Any:
        return client.files.upload(
            file={"file_name": path.name, "content": content},
            purpose="ocr",
        )

    response = _retry(_do, what=f"upload de {path.name}")
    return response.id


def call_ocr(
    *,
    file_id: str,
    pages: list[int] | None = None,
) -> OCRResponse:
    """Appelle /v1/ocr sur un fichier déjà uploadé. `pages` restreint l'OCR à des pages
    précises (0-indexées) — utilisé pour l'OCR de contrôle sur pages à faible densité."""
    client = get_client()
    cfg = get_models_config()["ocr"]
    model = cfg["model"]

    kwargs: dict[str, Any] = {}
    if pages is not None:
        kwargs["pages"] = pages

    def _do() -> OCRResponse:
        return client.ocr.process(
            model=model,
            document={"type": "file", "file_id": file_id},
            confidence_scores_granularity="page",
            include_blocks=True,
            **kwargs,
        )

    return _retry(_do, what="appel OCR")
