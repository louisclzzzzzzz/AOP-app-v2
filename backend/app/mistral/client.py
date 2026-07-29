"""Wrapper autour du SDK `mistralai` : client singleton, retry, appel OCR bas niveau.

Toute la logique métier (routage, cache, décisions de confiance) vit dans app/ocr/ et
app/ingestion/ ; ce module ne fait que parler au SDK de façon fiable.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

from mistralai.client import Mistral
from mistralai.client.errors.mistralerror import MistralError
from mistralai.client.models.ocrresponse import OCRResponse
from mistralai.extra.utils.response_format import (
    pydantic_model_from_json,
    response_format_from_pydantic_model,
)
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

# Consigne ajoutée au prompt utilisateur lors des tentatives de rattrapage d'un JSON non parsable.
# Rejouer la MÊME consigne à température plus haute ne sert à rien quand la cause est structurelle :
# mesuré sur le run e2e du 2026-07-29, 9 appels "map" de la Phase 1 ont perdu leurs 3 tentatives
# sans jamais rattraper (et le même document rejoué à T=0.7 bouclait encore). La tentative de
# rattrapage doit donc changer la CONSIGNE, pas seulement le hasard du tirage.
_JSON_REPAIR_INSTRUCTION = """

ATTENTION — ta réponse précédente n'était pas un JSON syntaxiquement valide. Réponds à nouveau, \
identique sur le fond, en respectant strictement ces trois points :
- aucun retour à la ligne ni suite d'espaces/tabulations à l'intérieur d'une valeur texte ;
- aucun guillemet droit (") à l'intérieur d'une valeur texte — pour citer, utilise « … » ;
- après chaque valeur, enchaîne immédiatement sur la virgule ou l'accolade fermante, sans insérer \
d'espaces ni de tabulations de remplissage."""


def _message_content(response) -> str | None:
    """Texte brut renvoyé par le modèle, avant tout parsing — c'est précisément ce que
    `client.chat.parse` du SDK ne laisse jamais voir quand son `json.loads()` interne échoue."""
    if not response.choices:
        return None
    message = response.choices[0].message
    if message is None:
        return None
    content = message.content
    return content if isinstance(content, str) else None


def _malformed_json_excerpt(content: str, exc: Exception, *, window: int = 250) -> str:
    """Fenêtre du texte brut autour du point de rupture, avec un marqueur ⟦ICI⟧ — sans ça, on ne
    dispose que de la position de l'erreur et la cause reste une hypothèse (§bug du 2026-07-29)."""
    pos = getattr(exc, "pos", None)
    if not isinstance(pos, int):
        return content[: 2 * window]
    return content[max(0, pos - window) : pos] + " ⟦ICI⟧ " + content[pos : pos + window]


def call_structured_chat(
    *,
    system_prompt: str,
    user_prompt: str,
    response_model: type[ModelT],
    what: str,
    model: str | None = None,
) -> tuple[ModelT, str | None]:
    """Appel LLM avec Structured Outputs (JSON Schema strict dérivé du modèle Pydantic fourni).
    Utilisé par la classification (étape 1, `mistral-small` batché), la complétude (étape 2),
    l'extraction (étape 3, `mistral-large`) et les deux phases d'analyse. `model` permet à un
    appelant de préciser son propre modèle (ex. classification) ; par défaut retombe sur
    `llm.model` (mistral-large).

    On n'utilise volontairement PAS `client.chat.parse` du SDK, alors que c'est exactement ce
    qu'il fait (`response_format_from_pydantic_model` → `chat.complete` → `json.loads`) : son
    `json.loads()` est appelé APRÈS le 200 OK et laisse remonter l'exception SANS jamais exposer le
    `message.content` qui l'a provoquée. On ne disposait donc que de la position de l'erreur, et la
    cause d'un JSON cassé restait une hypothèse (§data/resultats_tests_2026-07-29/grand_pic_map_reduce
    /BUG_map_reduce_json_parse_failures.md). Recomposer les trois étapes nous rend le texte brut et
    le `finish_reason` — de quoi distinguer un guillemet non échappé d'une troncature à max_tokens."""
    client = get_client()
    cfg = get_models_config()["llm"]
    model = model or cfg["model"]
    base_temperature = float(cfg.get("temperature", 0.0))
    timeout = cfg.get("timeout_seconds")
    max_tokens = cfg.get("max_tokens")
    parse_retries = int(cfg.get("parse_retries", 0))
    json_response_format = response_format_from_pydantic_model(response_model)

    def _do(temperature: float, prompt: str):
        _throttle_llm_call()
        return client.chat.complete(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format=json_response_format,
            temperature=temperature,
            max_tokens=int(max_tokens) if max_tokens else None,
            timeout_ms=int(timeout) * 1000 if timeout else None,
        )

    # Le mode Structured Outputs de Mistral *guide* la génération avec un JSON Schema, il ne
    # garantit pas mécaniquement un JSON syntaxiquement valide : sur de longues réponses, un
    # guillemet droit non échappé au milieu d'une valeur texte suffit à tout casser. Cette erreur
    # survient APRÈS le succès HTTP, donc hors du champ de `_retry` (qui ne couvre que les erreurs
    # réseau/API MistralError) — d'où cette seconde boucle. Chaque rattrapage relève légèrement la
    # température ET ajoute une consigne de réparation explicite : à consigne inchangée, le seul
    # effet de la température est de recasser le JSON ailleurs (0% de rattrapage mesuré).
    response = None
    parsed: ModelT | None = None
    last_parse_error: Exception | None = None
    for attempt in range(parse_retries + 1):
        temperature = base_temperature if attempt == 0 else min(0.4, base_temperature + 0.2 * attempt)
        prompt = user_prompt if attempt == 0 else user_prompt + _JSON_REPAIR_INSTRUCTION
        response = _retry(lambda: _do(temperature, prompt), what=what)

        content = _message_content(response)
        if content is None:
            raise RuntimeError(f"Réponse LLM vide pour : {what}")
        try:
            parsed = pydantic_model_from_json(json.loads(content), response_model)
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_parse_error = exc
            finish_reason = response.choices[0].finish_reason if response.choices else None
            logger.warning(
                "Réponse structurée non parsable pour %s (tentative %d/%d, T=%.1f, finish_reason=%s, "
                "%d caractères reçus) : %s\nTexte brut autour du point de rupture : %s",
                what,
                attempt + 1,
                parse_retries + 1,
                temperature,
                finish_reason,
                len(content),
                exc,
                _malformed_json_excerpt(content, exc),
            )
            if finish_reason in ("length", "error"):
                # Symptôme d'une boucle dégénérée du décodage contraint : le modèle enchaîne des
                # espaces/tabulations (toujours licites entre deux tokens JSON, donc jamais
                # interrompues par la grammaire) et n'atteint jamais le token suivant ; la
                # génération finit avortée par le serveur ou coupée par max_tokens, laissant un
                # JSON tronqué. Ça se corrige côté schéma — préférer une liste de chaînes courtes à
                # une longue chaîne multi-lignes (§app/synthesis/engine.py `constats`) —, pas en
                # relançant l'appel.
                logger.warning(
                    "Génération interrompue (finish_reason=%s, max_tokens=%s) pour %s : réponse tronquée, "
                    "%d caractères dont %d de whitespace final — vérifier que le schéma de réponse ne "
                    "demande pas un long texte multi-lignes dans une seule valeur JSON",
                    finish_reason,
                    max_tokens,
                    what,
                    len(content),
                    len(content) - len(content.rstrip()),
                )
    if parsed is None:
        raise RuntimeError(
            f"Réponse structurée non parsable après {parse_retries + 1} tentative(s) pour {what} : {last_parse_error}"
        )

    assert response is not None
    if response.usage:
        logger.info(
            "USAGE llm what=%r model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            what, model, response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens,
        )
    return parsed, getattr(response, "model", None)
