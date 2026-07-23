"""Wrapper autour du SDK `perplexityai` : client singleton, soumission + scrutation d'un job
Deep Research asynchrone (`sonar-deep-research`, endpoint `/async/chat/completions`).

Contrairement à `app/mistral/client.py` (un appel HTTP synchrone qui répond directement),
Deep Research est un job long (recherches web + raisonnement multi-étapes, plusieurs minutes) :
on soumet le job puis on scrute périodiquement son statut jusqu'à COMPLETED/FAILED, plutôt que
d'attendre une réponse HTTP unique.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from perplexity import Perplexity, PerplexityError, RateLimitError

from app.settings import get_models_config, get_settings

logger = logging.getLogger(__name__)


class PerplexityNotConfiguredError(RuntimeError):
    """Levée quand PERPLEXITY_API_KEY est absente : on ne devine jamais, on échoue clairement."""


class DeepResearchFailedError(RuntimeError):
    """Levée quand le job Deep Research se termine en statut FAILED côté API."""


class DeepResearchTimeoutError(RuntimeError):
    """Levée quand le job dépasse `perplexity.max_wait_seconds` sans avoir terminé."""


@lru_cache
def get_client() -> Perplexity:
    settings = get_settings()
    if not settings.perplexity_api_key:
        raise PerplexityNotConfiguredError(
            "PERPLEXITY_API_KEY manquante. Renseignez-la dans .env (voir .env.example)."
        )
    return Perplexity(api_key=settings.perplexity_api_key)


def _retry(fn, *, what: str, max_retries: int):
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except RateLimitError as exc:
            last_error = exc
            wait = 30.0 * attempt
            logger.warning(
                "Perplexity rate limit pendant %s (tentative %d/%d) — retry dans %.0fs",
                what, attempt, max_retries, wait,
            )
        except PerplexityError as exc:
            last_error = exc
            wait = min(2**attempt, 30)
            logger.warning(
                "Erreur API Perplexity pendant %s (tentative %d/%d): %s — retry dans %ds",
                what, attempt, max_retries, exc, wait,
            )
        if attempt < max_retries:
            time.sleep(wait)
    assert last_error is not None
    raise last_error


def _extract_text(content: Any) -> str:
    """`message.content` peut être une chaîne simple ou une liste de chunks structurés
    (`ContentStructuredContent...TextChunk` notamment) — on ne garde que le texte."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = [text for chunk in content if (text := getattr(chunk, "text", None))]
    return "\n".join(parts)


def run_deep_research(*, system_prompt: str, user_prompt: str, what: str) -> tuple[str, list[str], str | None]:
    """Soumet un job Deep Research et bloque (scrutation par `time.sleep`) jusqu'à son terme.

    Retourne (contenu_markdown, citations_web, nom_du_modèle_ayant_répondu). `citations_web`
    reflète les seules sources web trouvées par Perplexity lui-même (recherche web éventuelle) —
    distinct des citations de documents internes, que le prompt demande au modèle d'écrire
    lui-même dans le texte (ces documents ne sont jamais des pages web indexées).
    """
    client = get_client()
    cfg = get_models_config()["perplexity"]
    model = cfg["model"]
    poll_interval = float(cfg.get("poll_interval_seconds", 15))
    max_wait = float(cfg.get("max_wait_seconds", 1800))
    max_retries = int(cfg.get("max_retries", 3))
    temperature = float(cfg.get("temperature", 0.2))

    def _submit():
        return client.async_.chat.completions.create(
            request={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
            }
        )

    job = _retry(_submit, what=f"soumission Deep Research ({what})", max_retries=max_retries)
    logger.info("Deep Research soumis (%s) : id=%s modèle=%s", what, job.id, model)

    started = time.monotonic()
    result = job
    while result.status in ("CREATED", "IN_PROGRESS"):
        elapsed = time.monotonic() - started
        if elapsed > max_wait:
            raise DeepResearchTimeoutError(
                f"Deep Research ({what}) toujours en cours après {max_wait:.0f}s (id={job.id})"
            )
        time.sleep(poll_interval)
        result = _retry(
            lambda: client.async_.chat.completions.get(job.id),
            what=f"scrutation Deep Research ({what})",
            max_retries=max_retries,
        )
        logger.debug("Deep Research %s (%s) : statut=%s (%.0fs écoulées)", job.id, what, result.status, elapsed)

    if result.status == "FAILED":
        raise DeepResearchFailedError(
            f"Deep Research ({what}) en échec côté API (id={job.id}) : "
            f"{result.error_message or 'sans détail'}"
        )

    response = result.response
    if response is None or not response.choices:
        raise RuntimeError(f"Réponse Deep Research vide pour : {what} (id={job.id})")

    content = _extract_text(response.choices[0].message.content)
    citations = list(response.citations or [])
    total_elapsed = time.monotonic() - started
    if response.usage:
        logger.info(
            "USAGE deep-research what=%r model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            what, response.model, response.usage.prompt_tokens, response.usage.completion_tokens,
            response.usage.total_tokens,
        )
    logger.info(
        "Deep Research %s (%s) terminé en %.0fs (modèle=%s, citations=%d)",
        job.id, what, total_elapsed, response.model, len(citations),
    )
    return content, citations, response.model
