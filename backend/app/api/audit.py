"""Endpoints REST de l'audit des risques (Phase 2 du protocole d'analyse) : génération à la
demande, en complément de l'étape 3 — jamais un checkpoint, pas de statut de dossier dédié
(même modèle que la synthèse projet Phase 1, §api/project_synthesis.py)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.dossiers import dossier_to_out
from app.api.schemas import DossierOut
from app.audit.pipeline import run_audit_pipeline
from app.store.db import session_scope
from app.store.models import DossierStatus
from app.store.repository import get_dossier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["audit-risques"])

_RUNNABLE_STATUSES = (DossierStatus.EXTRACTION_REVIEW.value, DossierStatus.EXTRACTION_VALIDATED.value)


async def _run_safely(dossier_id: str) -> None:
    """Filet de sécurité dédié : une exception ici ne doit jamais faire basculer `Dossier.status`
    en erreur — cet audit est annexe à l'étape 3 — seul `audit_risques_status` en rend compte."""
    try:
        await run_audit_pipeline(dossier_id)
    except Exception as exc:
        logger.exception("Échec de la génération de l'audit des risques pour %s", dossier_id)
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            if dossier is not None:
                dossier.audit_risques_status = "error"
                dossier.audit_risques_error = str(exc)


@router.post("/{dossier_id}/audit-risques/generate", response_model=DossierOut)
async def generate_audit_risques(dossier_id: str, background_tasks: BackgroundTasks) -> DossierOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _RUNNABLE_STATUSES:
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour l'audit des risques (statut actuel : "
                f"{dossier.status}). L'extraction (étape 3) doit avoir été lancée au préalable.",
            )
        dossier.audit_risques_status = "generating"
        dossier.audit_risques_error = None
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_safely, dossier_id)
    return result
