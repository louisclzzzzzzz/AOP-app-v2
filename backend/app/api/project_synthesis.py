"""Endpoints REST de la synthèse projet (Phase 1 du protocole d'analyse) : génération à la
demande, en complément de l'étape 3 — jamais un checkpoint, pas de statut de dossier dédié."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.dossiers import dossier_to_out
from app.api.schemas import DossierOut
from app.store.db import session_scope
from app.store.models import DossierStatus
from app.store.repository import get_dossier
from app.synthesis.pipeline import run_project_synthesis_pipeline
from app.synthesis_perplexity.pipeline import run_project_synthesis_perplexity_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dossiers", tags=["synthese-projet"])

_RUNNABLE_STATUSES = (DossierStatus.EXTRACTION_REVIEW.value, DossierStatus.EXTRACTION_VALIDATED.value)


async def _run_safely(dossier_id: str) -> None:
    """Filet de sécurité dédié (plutôt que `run_pipeline_safely`, §pipeline_support.py) : une
    exception ici ne doit jamais faire basculer `Dossier.status` en erreur — cette génération est
    annexe à l'étape 3, jamais un checkpoint — seul `synthese_projet_status` en rend compte."""
    try:
        await run_project_synthesis_pipeline(dossier_id)
    except Exception as exc:
        logger.exception("Échec de la génération de la synthèse projet pour %s", dossier_id)
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            if dossier is not None:
                dossier.synthese_projet_status = "error"
                dossier.synthese_projet_error = str(exc)


@router.post("/{dossier_id}/synthese-projet/generate", response_model=DossierOut)
async def generate_project_synthesis(dossier_id: str, background_tasks: BackgroundTasks) -> DossierOut:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _RUNNABLE_STATUSES:
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour la synthèse projet (statut actuel : "
                f"{dossier.status}). L'extraction (étape 3) doit avoir été lancée au préalable.",
            )
        dossier.synthese_projet_status = "generating"
        dossier.synthese_projet_error = None
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_safely, dossier_id)
    return result


async def _run_safely_perplexity(dossier_id: str) -> None:
    """Filet de sécurité dédié, même principe que `_run_safely` ci-dessus : une exception ici ne
    doit jamais faire basculer `Dossier.status` en erreur, seul `synthese_projet_perplexity_status`
    en rend compte."""
    try:
        await run_project_synthesis_perplexity_pipeline(dossier_id)
    except Exception as exc:
        logger.exception("Échec de la génération de la synthèse projet (Perplexity) pour %s", dossier_id)
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            if dossier is not None:
                dossier.synthese_projet_perplexity_status = "error"
                dossier.synthese_projet_perplexity_error = str(exc)


@router.post("/{dossier_id}/synthese-projet/generate-perplexity", response_model=DossierOut)
async def generate_project_synthesis_perplexity(dossier_id: str, background_tasks: BackgroundTasks) -> DossierOut:
    """Variante expérimentale de la route ci-dessus : Perplexity Deep Research au lieu de
    Mistral, stockée dans des colonnes dédiées pour comparaison côte à côte sur le même dossier."""
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        if dossier is None:
            raise HTTPException(404, "Dossier introuvable")
        if dossier.status not in _RUNNABLE_STATUSES:
            raise HTTPException(
                409,
                f"Le dossier n'est pas prêt pour la synthèse projet (statut actuel : "
                f"{dossier.status}). L'extraction (étape 3) doit avoir été lancée au préalable.",
            )
        dossier.synthese_projet_perplexity_status = "generating"
        dossier.synthese_projet_perplexity_error = None
        result = dossier_to_out(dossier)

    background_tasks.add_task(_run_safely_perplexity, dossier_id)
    return result
