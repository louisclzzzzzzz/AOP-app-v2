"""Bracket commun aux 3 pipelines LLM (classification, complétude, extraction) : passage au
statut "en cours" + diffusion de démarrage, puis à la fin recalcul des compteurs + passage au
statut final + diffusion — la seule partie qui différait entre eux (§4/§8 AUDIT_BACKEND.md).
Le corps de chaque pipeline (la boucle de traitement proprement dite) reste propre à chacun :
c'est là que vit la vraie logique métier, volontairement non factorisée.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from app.progress import progress_manager
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus
from app.store.repository import get_dossier, set_dossier_status

logger = logging.getLogger(__name__)


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
