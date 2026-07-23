"""Orchestration de la génération de la synthèse projet (Phase 1) via Perplexity Deep Research —
variante expérimentale de `app/synthesis/pipeline.py` (Mistral), déclenchée par un bouton distinct
et stockée dans des colonnes dédiées (`Dossier.synthese_projet_perplexity_*`) pour permettre une
comparaison côte à côte des deux rapports sur le même dossier.

Best-effort et jamais bloquant, même principe que le pipeline Mistral : un échec ne touche jamais
`Dossier.status`, seul `Dossier.synthese_projet_perplexity_status` en rend compte. Réutilise les
helpers de collecte de données du pipeline Mistral (`_document_signals`, `_field_values`) — même
lecture de la même base, aucune raison de la dupliquer pour ce test comparatif.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.classify.taxonomy import load_taxonomy
from app.ingestion.document_signal import ensure_document_ocr
from app.store.db import session_scope
from app.store.repository import get_dossier
from app.synthesis.pipeline import _document_signals, _field_values
from app.synthesis.schema import load_synthesis_schema
from app.synthesis_perplexity.engine import generate_project_synthesis

logger = logging.getLogger(__name__)


def _persist_status(dossier_id: str, *, status: str, error: str | None = None) -> None:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        dossier.synthese_projet_perplexity_status = status
        dossier.synthese_projet_perplexity_error = error


async def run_project_synthesis_perplexity_pipeline(dossier_id: str) -> None:
    await asyncio.to_thread(_persist_status, dossier_id, status="generating", error=None)

    schema = load_synthesis_schema()
    taxonomy = load_taxonomy()
    signals = await asyncio.to_thread(_document_signals, dossier_id)
    signals_by_id = {s.document_id: s for s in signals}
    field_values = await asyncio.to_thread(_field_values, dossier_id)

    # OCR à la demande sur l'union des documents pivots candidats (même logique que le pipeline
    # Mistral) — fait une seule fois avant l'appel, pas de mutation concurrente possible ensuite
    # puisqu'il n'y a ici qu'un seul appel Deep Research (pas de thèmes en concurrence).
    pivot_categories = {c for topic in schema.topics if topic.source == "documents" for c in topic.pivot_categories}
    candidate_doc_ids = [
        d.document_id for d in signals_by_id.values() if d.final_category in pivot_categories
    ]
    if candidate_doc_ids:
        ocr_results = await asyncio.gather(
            *(asyncio.to_thread(ensure_document_ocr, dossier_id, signals_by_id[doc_id]) for doc_id in candidate_doc_ids)
        )
        for doc in ocr_results:
            signals_by_id[doc.document_id] = doc

    result = await asyncio.to_thread(
        generate_project_synthesis,
        schema,
        taxonomy,
        documents=list(signals_by_id.values()),
        field_values=field_values,
    )

    def _persist_result() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.synthese_projet_perplexity_md = result.report_md
            dossier.synthese_projet_perplexity_model = result.model_name
            dossier.synthese_projet_perplexity_status = "done"
            dossier.synthese_projet_perplexity_error = None
            dossier.synthese_projet_perplexity_generated_at = dt.datetime.now(dt.timezone.utc)

    await asyncio.to_thread(_persist_result)
