"""Wrapper autour du SDK `mistralai` : client singleton, retry, appel OCR bas niveau.

Toute la logique métier (routage, cache, décisions de confiance) vit dans app/ocr/ et
app/ingestion/ ; ce module ne fait que parler au SDK de façon fiable.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

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

# --- Clé principale et clés de secours (§4 OPTIMISATION.md) ------------------------------------
#
# Plusieurs clés API peuvent être déclarées. Elles ne servent PAS à paralléliser : elles se
# relaient. La première clé déclarée est la clé principale et prend tous les appels ; une clé de
# secours n'est sollicitée que lorsque celle du dessus ne fonctionne plus (quota épuisé, clé
# révoquée, rate limit).
#
# Pourquoi pas de répartition de charge : le stimulateur `min_interval_seconds` n'est pas le
# facteur limitant. Mesuré sur les runs e2e du 2026-07-29 (data/resultats_tests_2026-07-29/) :
# 5 à 6 requêtes par minute pour un plafond auto-imposé de 60/min, une concurrence réelle de 2 à 3
# appels déjà en vol, et 98 % du temps de traitement avec au moins un appel en cours. Le temps est
# consommé par la latence des appels (médiane ~14 s), pas par l'attente d'un créneau. Étaler les
# appels sur plusieurs clés n'y changerait quasiment rien, et diviserait la lisibilité des quotas.
#
# Avec une seule clé, tout ce qui suit se comporte exactement comme avant.
#
# Synchrone (pas asyncio) car ces fonctions tournent déjà sur de vrais threads via
# `asyncio.to_thread` dans les pipelines.

_slots_lock = threading.Lock()
# Par clé : instant (monotonic) à partir duquel un nouvel appel LLM est autorisé.
_llm_next_available: list[float] = []
# Par clé : jetons de concurrence OCR (upload + /v1/ocr).
_ocr_semaphores: list[threading.Semaphore] = []
_ocr_semaphore_size: int | None = None

# --- Santé des clés : bascule sur le secours quand une clé ne fonctionne plus -------------------
#
# Une clé peut être invalide, révoquée, à quota épuisé ou momentanément rate-limitée. Sans suivi,
# on la choisirait à chaque appel et chaque appel partirait dans le mur. Chaque clé porte donc un
# compteur d'échecs consécutifs et une date de fin de quarantaine ; une clé en quarantaine est
# sautée au profit de la suivante. Le premier succès lève la quarantaine — une clé rate-limitée,
# ou dont le quota s'est renouvelé, est reprise sans intervention.
_slot_failures: list[int] = []
_slot_blocked_until: list[float] = []

# Clé refusée par l'API (401/403) ou quota épuisé (402) : le problème ne se résoudra pas tout seul,
# inutile de la réessayer souvent — mais la quarantaine reste bornée pour qu'une clé réactivée ou
# un quota renouvelé soient repris automatiquement.
_QUARANTINE_KEY_REJECTED_SECONDS = 300.0
# Rate limit (429) : le plus souvent la clé est bonne et a juste besoin de souffler. Mais c'est
# aussi le code que renvoie une clé arrivée au bout de son quota — indiscernable sur un seul appel.
# D'où la progressivité ci-dessous : une clé qui rate-limite une fois est réessayée vite, une clé
# qui rate-limite en boucle (quota épuisé) est écartée de plus en plus longtemps.
_QUARANTINE_RATE_LIMITED_SECONDS = 30.0
# Panne non spécifique à la clé (5xx, réseau) : on ne l'écarte qu'après plusieurs échecs d'affilée,
# sinon une indisponibilité passagère de l'API mettrait toutes les clés en quarantaine d'un coup.
_QUARANTINE_GENERIC_SECONDS = 60.0
_SLOT_FAILURE_THRESHOLD = 3
# La quarantaine est multipliée par le nombre d'échecs consécutifs, dans cette limite : une clé
# définitivement morte finit écartée pour longtemps au lieu d'être re-testée toutes les 30 s.
_QUARANTINE_BACKOFF_MAX_FACTOR = 10

# Cas réel (run e2e du 2026-07-31, dossier dce_grand_pic2) : la Phase 1 lance 4 thèmes en parallèle
# (`_SYNTHESIS_LLM_CONCURRENCY`), et une rafale simultanée a fait passer les 2 clés de secours en
# 429 en même temps pendant que la principale était déjà écartée (401). Le code retentait alors
# IMMÉDIATEMENT sur la clé la moins quarantainée plutôt que d'attendre sa levée — elle échouait de
# nouveau sur-le-champ puisque son quota n'avait pas eu le temps de se libérer, épuisant les 3
# tentatives de `_retry` (2 s puis 4 s de backoff) en quelques secondes, bien plus vite que les 30 s
# d'une quarantaine de rate limit. Résultat : 4 sections du rapport perdues pour de bon
# (data/resultats_tests_2026-07-31/grand_pic_v2_cles_secours/phase1_synthese.md). Mieux vaut
# attendre que cette clé se libère (borné, pour ne jamais figer un dossier indéfiniment si toutes
# les clés sont réellement mortes) que d'y retourner à coup sûr pour rien.
_ALL_KEYS_QUARANTINED_MAX_WAIT_SECONDS = 35.0


class MistralNotConfiguredError(RuntimeError):
    """Levée quand aucune clé n'est configurée : on ne devine jamais, on échoue clairement."""


@lru_cache
def get_clients() -> tuple[Mistral, ...]:
    """Un client par clé API configurée, dans l'ordre de déclaration : la première est la clé
    principale, les suivantes sont des clés de secours (§settings.py)."""
    settings = get_settings()
    keys = settings.mistral_api_keys or ([settings.mistral_api_key] if settings.mistral_api_key else [])
    if not keys:
        raise MistralNotConfiguredError(
            "MISTRAL_API_KEY manquante. Renseignez-la dans .env (voir .env.example). "
            "Vous pouvez déclarer une clé de secours, utilisée automatiquement si la principale "
            "est épuisée ou refusée : MISTRAL_API_KEY_2 (ou MISTRAL_API_KEYS=cle_a,cle_b)."
        )
    if len(keys) > 1:
        logger.info(
            "Mistral : 1 clé principale + %d clé(s) de secours — le secours ne prend le relais "
            "que si la clé principale est épuisée, révoquée ou rate-limitée",
            len(keys) - 1,
        )
    return tuple(Mistral(api_key=key) for key in keys)


def _configured_keys() -> list[str]:
    settings = get_settings()
    return settings.mistral_api_keys or ([settings.mistral_api_key] if settings.mistral_api_key else [])


def api_slot_count() -> int:
    """Nombre de créneaux d'appel = nombre de clés configurées.

    Lu depuis la configuration et NON depuis `get_clients()` : l'ordonnanceur doit pouvoir tourner
    sans qu'un client soit constructible (aucune clé configurée), auquel cas il se comporte comme
    s'il y en avait une — l'absence de clé est signalée au moment de l'appel réel, par
    `get_clients()`, avec un message clair."""
    return max(1, len(_configured_keys()))


def reset_slots_for_tests() -> None:
    """Vide le pool de clients et les états par créneau — les tests changent de configuration
    (workspace, clés) d'un cas à l'autre et ne doivent pas hériter du stimulateur précédent."""
    global _ocr_semaphore_size
    get_clients.cache_clear()
    with _slots_lock:
        _llm_next_available.clear()
        _slot_failures.clear()
        _slot_blocked_until.clear()
        _ocr_semaphores.clear()
        _ocr_semaphore_size = None


def get_client(slot: int = 0) -> Mistral:
    """Client d'un créneau donné. `slot` par défaut à 0 : les appels hors ordonnanceur (et les
    tests mono-clé) retombent sur la première clé, comme avant."""
    clients = get_clients()
    return clients[slot % len(clients)]


def _ensure_llm_slots(count: int) -> None:
    """Appelé sous `_slots_lock`. Volontairement indépendant de la configuration OCR : un appel
    LLM n'a aucune raison d'aller lire `ocr.max_concurrency`."""
    if len(_llm_next_available) != count:
        _llm_next_available[:] = [0.0] * count
    if len(_slot_failures) != count:
        _slot_failures[:] = [0] * count
        _slot_blocked_until[:] = [0.0] * count


def _quarantine_seconds(exc: Exception, consecutive_failures: int) -> float:
    """Durée de mise à l'écart d'une clé après un échec, ou 0 pour ne pas l'écarter.

    La durée croît avec le nombre d'échecs consécutifs : un rate limit ponctuel se rattrape en
    30 s, tandis qu'une clé arrivée au bout de son quota — qui renvoie le même 429 à chaque
    tentative — est écartée de plus en plus longtemps au lieu d'être re-testée en boucle."""
    status = getattr(exc, "status_code", None)
    if status in (401, 402, 403):
        base = _QUARANTINE_KEY_REJECTED_SECONDS
    elif status == 429:
        base = _QUARANTINE_RATE_LIMITED_SECONDS
    elif consecutive_failures >= _SLOT_FAILURE_THRESHOLD:
        base = _QUARANTINE_GENERIC_SECONDS
    else:
        return 0.0
    return base * min(consecutive_failures, _QUARANTINE_BACKOFF_MAX_FACTOR)


def record_slot_failure(slot: int, exc: Exception) -> None:
    """Signale l'échec d'un appel sur ce créneau. Met la clé en quarantaine si l'erreur montre
    qu'elle est en cause (clé refusée, quota, rate limit) ou si elle enchaîne les échecs."""
    count = api_slot_count()
    with _slots_lock:
        _ensure_llm_slots(count)
        if slot >= len(_slot_failures):
            return
        _slot_failures[slot] += 1
        failures = _slot_failures[slot]
        seconds = _quarantine_seconds(exc, failures)
        if seconds <= 0:
            return
        _slot_blocked_until[slot] = time.monotonic() + seconds
    logger.warning(
        "Clé API n°%d écartée pour %.0fs après %d échec(s) consécutif(s) (%s) — bascule sur la "
        "clé de secours suivante (%d clé(s) déclarée(s) au total)",
        slot + 1,
        seconds,
        failures,
        exc,
        count,
    )


def record_slot_success(slot: int) -> None:
    """Un succès lève immédiatement la quarantaine : une clé rate-limitée qui repasse redevient
    utilisable sans intervention."""
    with _slots_lock:
        if slot < len(_slot_failures):
            _slot_failures[slot] = 0
            _slot_blocked_until[slot] = 0.0


def api_slots_health() -> list[dict[str, Any]]:
    """État par clé, pour le diagnostic (jamais la clé elle-même, seulement son rang)."""
    count = api_slot_count()
    now = time.monotonic()
    with _slots_lock:
        _ensure_llm_slots(count)
        return [
            {
                "slot": i + 1,
                "consecutive_failures": _slot_failures[i],
                "quarantined": _slot_blocked_until[i] > now,
                "quarantined_for_seconds": max(0.0, round(_slot_blocked_until[i] - now, 1)),
            }
            for i in range(count)
        ]


def _ensure_ocr_slots(count: int) -> None:
    """Appelé sous `_slots_lock`. Les sémaphores sont reconstruits si le nombre de clés ou la
    concurrence configurée change (tests successifs avec des workspaces différents)."""
    global _ocr_semaphore_size
    size = int(get_models_config()["ocr"].get("max_concurrency", 3))
    if len(_ocr_semaphores) != count or _ocr_semaphore_size != size:
        _ocr_semaphores[:] = [threading.Semaphore(size) for _ in range(count)]
        _ocr_semaphore_size = size


def _acquire_llm_slot() -> int:
    """Choisit la clé à utiliser et applique le stimulateur d'appels.

    Sélection par ORDRE DE DÉCLARATION : la première clé saine prend l'appel. Il n'y a pas de
    répartition de charge — une clé de secours ne sert que si toutes celles qui la précèdent sont
    en quarantaine. Tant que la clé principale fonctionne, le comportement est identique à celui
    d'une configuration mono-clé.

    Le stimulateur (`min_interval_seconds`) est tenu PAR CLÉ : après une bascule, la clé de secours
    démarre avec son propre budget au lieu d'hériter de l'historique de la clé défaillante.

    Si TOUTES les clés sont en quarantaine, on attend la levée de la moins pénalisée plutôt que d'y
    retourner immédiatement (§_ALL_KEYS_QUARANTINED_MAX_WAIT_SECONDS) — borné, pour ne jamais figer
    un dossier si toutes les clés sont réellement mortes.

    L'attente a lieu HORS du verrou, pour ne pas bloquer les autres appels pendant la temporisation.
    """
    count = api_slot_count()
    min_interval = float(get_models_config()["llm"].get("min_interval_seconds", 0.0))
    all_quarantined = False
    with _slots_lock:
        _ensure_llm_slots(count)
        now = time.monotonic()
        slot = next((i for i in range(count) if _slot_blocked_until[i] <= now), None)
        quarantine_wait = 0.0
        if slot is None:
            all_quarantined = True
            slot = min(range(count), key=lambda i: _slot_blocked_until[i])
            quarantine_wait = min(
                max(0.0, _slot_blocked_until[slot] - now), _ALL_KEYS_QUARANTINED_MAX_WAIT_SECONDS
            )
        ready_at = _llm_next_available[slot]
        wait = max(ready_at - now, quarantine_wait)
        _llm_next_available[slot] = now + wait + min_interval
    if all_quarantined:
        logger.warning(
            "Toutes les clés API (%d) sont en quarantaine — attente de %.0fs pour la clé n°%d avant nouvelle tentative",
            count,
            wait,
            slot + 1,
        )
    if wait > 0:
        time.sleep(wait)
    return slot


@contextmanager
def ocr_slot() -> Iterator[int]:
    """Réserve un créneau OCR (une clé + un jeton de concurrence) pour TOUTE la durée d'une
    opération OCR.

    Le créneau doit couvrir l'upload ET le traitement : le `file_id` rendu par l'upload est lié au
    compte qui l'a uploadé, le traiter avec une autre clé donnerait un 404. C'est la raison d'être
    de ce contexte, plutôt qu'une prise de jeton indépendante dans chaque fonction."""
    count = api_slot_count()
    quarantine_wait = 0.0
    with _slots_lock:
        _ensure_ocr_slots(count)
        _ensure_llm_slots(count)
        semaphores = list(_ocr_semaphores)
        now = time.monotonic()
        healthy = [i for i in range(count) if _slot_blocked_until[i] <= now]
        if healthy:
            # Même politique que pour le LLM : ordre de déclaration, une clé écartée est sautée.
            slot = healthy[0]
        else:
            # Toutes les clés sont écartées : on retient celle qui se libère le plus tôt — pas la
            # première déclarée, qui est souvent la principale, la plus longuement quarantainée
            # (401/402/403) — et on attend sa levée, bornée (§_acquire_llm_slot, même raison :
            # y retourner tout de suite échouerait à coup sûr).
            slot = min(range(count), key=lambda i: _slot_blocked_until[i])
            quarantine_wait = min(
                max(0.0, _slot_blocked_until[slot] - now), _ALL_KEYS_QUARANTINED_MAX_WAIT_SECONDS
            )

    if quarantine_wait > 0:
        logger.warning(
            "Toutes les clés API (%d) sont en quarantaine — attente de %.0fs pour la clé n°%d "
            "avant nouvel appel OCR",
            count,
            quarantine_wait,
            slot + 1,
        )
        time.sleep(quarantine_wait)
    semaphores[slot].acquire()
    try:
        yield slot
    finally:
        semaphores[slot].release()


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


def upload_file_for_ocr(path: Path, *, slot: int = 0) -> str:
    """Upload un fichier local vers l'API Mistral (purpose=ocr) et retourne son file_id.

    `slot` doit provenir d'un `ocr_slot()` ouvert par l'appelant et être réutilisé pour le
    `call_ocr` qui suit : le file_id n'existe que dans le compte qui l'a uploadé."""
    client = get_client(slot)
    with open(path, "rb") as f:
        content = f.read()

    def _do() -> Any:
        try:
            response = client.files.upload(
                file={"file_name": path.name, "content": content},
                purpose="ocr",
                timeout_ms=_ocr_timeout_ms(),
            )
        except Exception as exc:
            record_slot_failure(slot, exc)
            raise
        record_slot_success(slot)
        return response

    return _retry(_do, what=f"upload de {path.name}").id


def call_ocr(
    *,
    file_id: str,
    pages: list[int] | None = None,
    slot: int = 0,
) -> OCRResponse:
    """Appelle /v1/ocr sur un fichier déjà uploadé. `pages` restreint l'OCR à des pages
    précises (0-indexées) — utilisé pour l'OCR de contrôle sur pages à faible densité.

    `slot` doit être celui utilisé pour l'upload de ce `file_id` (§`ocr_slot`)."""
    client = get_client(slot)
    cfg = get_models_config()["ocr"]
    model = cfg["model"]

    kwargs: dict[str, Any] = {}
    if pages is not None:
        kwargs["pages"] = pages

    def _do() -> OCRResponse:
        try:
            response = client.ocr.process(
                model=model,
                document={"type": "file", "file_id": file_id},
                confidence_scores_granularity="page",
                include_blocks=True,
                timeout_ms=_ocr_timeout_ms(),
                **kwargs,
            )
        except Exception as exc:
            record_slot_failure(slot, exc)
            raise
        record_slot_success(slot)
        return response

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


def document_summary_max_tokens() -> int | None:
    """Plafond de sortie des étapes « map » des deux phases d'analyse (§models.yaml
    `llm.max_tokens_document_summary`) : un relevé couvrant tous les thèmes/sections d'un document
    produit bien plus de tokens qu'un thème ou une section isolés."""
    cfg = get_models_config()["llm"]
    return cfg.get("max_tokens_document_summary") or cfg.get("max_tokens")


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
    max_tokens: int | None = None,
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
    cfg = get_models_config()["llm"]
    model = model or cfg["model"]
    base_temperature = float(cfg.get("temperature", 0.0))
    timeout = cfg.get("timeout_seconds")
    # `max_tokens` explicite : un appelant dont la sortie est structurellement plus longue que la
    # moyenne (§étapes « map » des deux phases) relève son propre plafond, sans changer celui des
    # autres appels — une réponse coupée en plein JSON est irrécupérable et la re-tentative rend
    # alors un résultat silencieusement appauvri.
    max_tokens = max_tokens or cfg.get("max_tokens")
    parse_retries = int(cfg.get("parse_retries", 0))
    json_response_format = response_format_from_pydantic_model(response_model)

    def _do(temperature: float, prompt: str):
        # Le créneau est choisi à CHAQUE tentative, jamais une fois pour toute la fonction : si une
        # clé sature (rate limit, quota épuisé), les tentatives suivantes basculent d'elles-mêmes
        # sur une autre clé au lieu de s'acharner sur la même.
        slot = _acquire_llm_slot()
        client = get_client(slot)
        try:
            response = client.chat.complete(
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
        except Exception as exc:
            record_slot_failure(slot, exc)
            raise
        record_slot_success(slot)
        return response

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
