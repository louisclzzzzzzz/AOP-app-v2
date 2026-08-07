"""Journalisation complète et optionnelle des appels Mistral (entrée/sortie/usage/troncatures).

Désactivée par défaut (aucun impact en prod) : n'écrit que si `AOP_API_CALL_LOG_DIR` est
renseigné (settings.api_call_log_dir). Prévue pour instrumenter des runs e2e (une ligne JSON par
appel API ou par troncature de contenu, dans `<dir>/api_calls.jsonl`) afin de reconstituer après
coup une vue complète de ce qui a été envoyé/reçu et de calculer un coût.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock

from app.settings import get_settings

_lock = Lock()


def _log_path() -> Path | None:
    log_dir = get_settings().api_call_log_dir
    if log_dir is None:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "api_calls.jsonl"


def is_enabled() -> bool:
    return _log_path() is not None


def _write(record: dict) -> None:
    path = _log_path()
    if path is None:
        return
    full = {"ts": time.time(), **record}
    line = json.dumps(full, ensure_ascii=False, default=str)
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_llm_call(
    *,
    what: str,
    model_requested: str,
    model_served: str | None,
    system_prompt: str,
    user_prompt: str,
    output: dict | list | None,
    usage: dict | None,
    latency_ms: float,
    attempts: int,
    error: str | None = None,
) -> None:
    _write(
        {
            "event": "llm_call",
            "what": what,
            "model_requested": model_requested,
            "model_served": model_served,
            "input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "system_chars": len(system_prompt),
                "user_chars": len(user_prompt),
            },
            "output": output,
            "usage": usage,
            "latency_ms": round(latency_ms, 1),
            "attempts": attempts,
            "error": error,
        }
    )


def log_ocr_call(
    *,
    document: str,
    pages_requested: list[int] | None,
    model: str,
    pages_processed: int | None,
    doc_size_bytes: int | None,
    avg_confidence: float | None,
    pages_output: list[dict],
    combined_markdown: str,
    latency_ms: float,
) -> None:
    _write(
        {
            "event": "ocr_call",
            "document": document,
            "pages_requested": pages_requested,
            "model": model,
            "usage": {"pages_processed": pages_processed, "doc_size_bytes": doc_size_bytes},
            "avg_confidence": avg_confidence,
            "output": {"combined_markdown": combined_markdown, "chars": len(combined_markdown)},
            "pages": pages_output,
            "latency_ms": round(latency_ms, 1),
        }
    )


def log_truncation(
    *,
    source: str,
    document: str,
    original_chars: int,
    kept_chars: int,
    limit_name: str,
    limit_value: int,
    extra: dict | None = None,
) -> None:
    _write(
        {
            "event": "truncation",
            "source": source,
            "document": document,
            "original_chars": original_chars,
            "kept_chars": kept_chars,
            "was_truncated": kept_chars < original_chars,
            "limit_name": limit_name,
            "limit_value": limit_value,
            **(extra or {}),
        }
    )
