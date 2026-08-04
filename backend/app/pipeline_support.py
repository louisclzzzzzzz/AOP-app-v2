"""Bracket commun aux 3 pipelines LLM (classification, complétude, extraction) : passage au
statut "en cours" + diffusion de démarrage, puis à la fin recalcul des compteurs + passage au
statut final + diffusion — la seule partie qui différait entre eux (§4/§8 AUDIT_BACKEND.md).
Le corps de chaque pipeline (la boucle de traitement proprement dite) reste propre à chacun :
c'est là que vit la vraie logique métier, volontairement non factorisée.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from sqlalchemy.orm import Session

from app.auth.crypto import decrypt_secret
from app.mistral.client import MistralNotConfiguredError, use_user_api_key
from app.progress import progress_manager
from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus
from app.store.repository import get_dossier, get_user_api_key_row, set_dossier_status

logger = logging.getLogger(__name__)


@asynccontextmanager
async def owner_api_key(dossier_id: str) -> AsyncIterator[None]:
    """Bascule tous les appels Mistral du bloc sur la clé API PERSONNELLE du propriétaire du
    dossier plutôt que sur la ou les clés globales de `settings` — chaque utilisateur consomme
    son propre quota (§AOP_REQUIRE_AUTH, app/api/me.py, app/mistral/client.py
    `use_user_api_key`). No-op si l'authentification est désactivée (usage local/exécutable
    Windows, comportement historique inchangé). Lève `MistralNotConfiguredError` — attrapée par
    le filet de sécurité de chaque appelant (`run_pipeline_safely` ou équivalent) et affichée
    comme une erreur de dossier normale — si le propriétaire n'a pas encore enregistré sa clé
    (garde-fou : l'upload la refuse déjà en amont, §app/api/dossiers.py, mais une clé peut avoir
    été effacée entre-temps depuis un autre onglet)."""
    settings = get_settings()
    if not settings.require_auth:
        yield
        return

    def _resolve() -> tuple[str, str] | None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            owner_user_id = dossier.owner_user_id if dossier else None
            if owner_user_id is None:
                return None
            row = get_user_api_key_row(s, owner_user_id)
            encrypted = row.mistral_api_key_encrypted if row else None
        if not encrypted:
            return None
        api_key = decrypt_secret(encrypted, secret_key=settings.secret_key)
        return (owner_user_id, api_key) if api_key else None

    resolved = await asyncio.to_thread(_resolve)
    if resolved is None:
        raise MistralNotConfiguredError(
            "Aucune clé API Mistral personnelle configurée pour ce compte — ouvrez « Clé API » "
            "dans le menu pour en enregistrer une avant de lancer une analyse."
        )
    user_id, api_key = resolved
    with use_user_api_key(user_id, api_key):
        yield


async def start_stage(dossier_id: str, *, status: DossierStatus, stage: str, message: str) -> None:
    def _set_status() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, status)

    await asyncio.to_thread(_set_status)
    await progress_manager.broadcast(dossier_id, stage=stage, status=status.value, message=message)


async def finalize_stage(
    dossier_id: str,
    *,
    status: DossierStatus,
    stage: str,
    message: str,
    counters: Callable[[Dossier], dict[str, int]],
    recompute: Callable[[Session, Dossier], None],
    before_status_change: Callable[[Session, Dossier], None] | None = None,
) -> dict[str, int]:
    """Recalcule les compteurs, applique `before_status_change` (ex. incrémenter
    `current_step`), passe au statut final, puis diffuse le tout — dans cet ordre, comme le
    faisaient les 3 pipelines avant factorisation."""

    def _finalize() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute(s, dossier)
            if before_status_change is not None:
                before_status_change(s, dossier)
            set_dossier_status(s, dossier, status)
            return counters(dossier)

    final_counters = await asyncio.to_thread(_finalize)
    await progress_manager.broadcast(
        dossier_id, stage=stage, status=status.value, counters=final_counters, message=message
    )
    return final_counters


async def run_pipeline_safely(dossier_id: str, run: Callable[[], Awaitable[None]], *, what: str) -> None:
    """Filet de sécurité générique : toute exception non prévue par `run()` lui-même bascule le
    dossier en erreur au lieu de le laisser bloqué silencieusement à mi-chemin, puis diffuse
    l'échec. Mutualise ce qui était dupliqué à l'identique dans `api/dossiers.py`,
    `api/completeness.py` et `api/extraction.py` (§8 AUDIT_BACKEND.md)."""
    try:
        await run()
    except Exception as exc:  # pragma: no cover - filet de sécurité générique
        logger.exception("Erreur non gérée dans %s pour %s", what, dossier_id)

        def _mark_error() -> None:
            with session_scope() as s:
                dossier = get_dossier(s, dossier_id)
                if dossier is not None:
                    set_dossier_status(s, dossier, DossierStatus.ERROR, error_message=str(exc))

        await asyncio.to_thread(_mark_error)
        await progress_manager.broadcast(
            dossier_id, stage="error", status=DossierStatus.ERROR.value, message=str(exc)
        )
