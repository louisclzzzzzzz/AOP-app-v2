"""Wrapper autour du SDK `mistralai` : client singleton, retry, appel OCR bas niveau.

Toute la logique métier (routage, cache, décisions de confiance) vit dans app/ocr/ et
app/ingestion/ ; ce module ne fait que parler au SDK de façon fiable.
"""
from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

from mistralai.client import Mistral
from mistralai.client.errors.mistralerror import MistralError
from mistralai.client.models.ocrresponse import OCRResponse
from pydantic import BaseModel

from app.settings import get_models_config, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --- Ordonnancement (§4 OPTIMISATION.md) ------------------------------------------
#
# File LLM chat : un seul worker, espacé par un token-bucket simple (verrou + horodatage du
# dernier appel autorisé). Synchrone (pas asyncio) car ces fonctions tournent déjà sur des
# threads réels via `asyncio.to_thread` dans les 3 pipelines.
_llm_throttle_lock = threading.Lock()
_llm_last_call_at = 0.0

# File OCR : concurrence bornée séparée (upload + /v1/ocr), cadencée indépendamment de la file
# LLM chat. Le sémaphore est recréé si la config change (tests avec des workspaces différents).
_ocr_semaphore: threading.Semaphore | None = None
_ocr_semaphore_size: int | None = None


def _throttle_llm_call() -> None:
    global _llm_last_call_at
    min_interval = float(get_models_config()["llm"].get("min_interval_seconds", 0.0))
    if min_interval <= 0:
        return
    with _llm_throttle_lock:
        now = time.monotonic()
        wait = _llm_last_call_at + min_interval - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _llm_last_call_at = now


def _get_ocr_semaphore() -> threading.Semaphore:
    global _ocr_semaphore, _ocr_semaphore_size
    size = int(get_models_config()["ocr"].get("max_concurrency", 3))
    if _ocr_semaphore is None or _ocr_semaphore_size != size:
        _ocr_semaphore = threading.Semaphore(size)
        _ocr_semaphore_size = size
    return _ocr_semaphore


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


def _ocr_timeout_ms() -> int | None:
    timeout = get_models_config()["ocr"].get("timeout_seconds")
    return int(timeout) * 1000 if timeout else None


def upload_file_for_ocr(path: Path) -> str:
    """Upload un fichier local vers l'API Mistral (purpose=ocr) et retourne son file_id."""
    client = get_client()
    with open(path, "rb") as f:
        content = f.read()

    def _do() -> Any:
        return client.files.upload(
            file={"file_name": path.name, "content": content},
            purpose="ocr",
            timeout_ms=_ocr_timeout_ms(),
        )

    with _get_ocr_semaphore():
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
            timeout_ms=_ocr_timeout_ms(),
            **kwargs,
        )

    with _get_ocr_semaphore():
        return _retry(_do, what="appel OCR")


ModelT = TypeVar("ModelT", bound=BaseModel)


def call_structured_chat(
    *,
    system_prompt: str,
    user_prompt: str,
    response_model: type[ModelT],
    what: str,
    model: str | None = None,
) -> tuple[ModelT, str | None]:
    """Appel LLM avec Structured Outputs (JSON Schema strict dérivé du modèle Pydantic fourni).
    Utilisé par la classification (étape 1, `mistral-small` batché), la complétude (étape 2) et
    l'extraction (étape 3, `mistral-large`). `model` permet à un appelant de préciser son propre
    modèle (ex. classification) ; par défaut retombe sur `llm.model` (mistral-large)."""
    client = get_client()
    cfg = get_models_config()["llm"]
    model = model or cfg["model"]
    temperature = float(cfg.get("temperature", 0.0))
    timeout = cfg.get("timeout_seconds")

    def _do():
        _throttle_llm_call()
        return client.chat.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_model,
            temperature=temperature,
            timeout_ms=int(timeout) * 1000 if timeout else None,
        )

    response = _retry(_do, what=what)
    if not response.choices:
        raise RuntimeError(f"Réponse LLM vide pour : {what}")
    parsed = response.choices[0].message.parsed if response.choices[0].message else None
    if parsed is None:
        raise RuntimeError(f"Réponse LLM structurée invalide (aucun contenu parsé) pour : {what}")
    if response.usage:
        logger.info(
            "USAGE llm what=%r model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            what, model, response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens,
        )
    return parsed, getattr(response, "model", None)
