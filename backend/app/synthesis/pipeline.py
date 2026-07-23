"""Orchestration de la génération de la synthèse projet (Phase 1 du protocole d'analyse).

Déclenchée explicitement par l'expert (`POST .../synthese-projet/generate`), jamais enchaînée
automatiquement à la fin de l'étape 3 : contrairement à `generate_synthesis` (§extraction/
engine.py, un appel LLM bon marché sur des valeurs déjà résolues), ce pipeline relit le texte
complet de plusieurs documents pivots par thème — plus long et plus coûteux, donc une action
volontaire plutôt qu'un ajout systématique au run standard de l'étape 3.

Best-effort et jamais bloquant : un échec ne touche jamais `Dossier.status` (le dossier reste
utilisable normalement), seul `Dossier.synthese_projet_status` reflète l'état de cette
génération annexe. Volontairement pas diffusé sur le WebSocket de progression partagé
(`app/progress.py`) : ce canal réassigne `Dossier.status`/`counters` en bloc côté frontend à
chaque évènement (§DossierProgress.tsx), ce qui écraserait le statut réel du dossier avec une
valeur hors énumération ("generating") — le frontend fait un simple polling de
`GET /api/dossiers/{id}` tant que `synthese_projet_status == "generating"`.

Les 13 thèmes sont générés en deux phases : (1) OCR à la demande, dédupliqué sur l'ensemble des
documents pivots candidats de tous les thèmes ; (2) génération LLM des thèmes en concurrence
bornée (`_SYNTHESIS_LLM_CONCURRENCY`) plutôt qu'en séquence stricte — mesuré sur les dossiers de
test, le temps de synthèse était jusque-là la somme de 12 appels LLM indépendants (190-400s par
dossier) sans aucune raison de ne pas les paralléliser.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal, ensure_document_ocr
from app.store.db import session_scope
from app.store.repository import get_dossier, list_documents, list_extraction_results
from app.synthesis.engine import FieldValues, TopicOutcome, build_documents_cartography, assemble_report, generate_topic
from app.synthesis.schema import SynthesisTopic, load_synthesis_schema

logger = logging.getLogger(__name__)

# Concurrence bornée sur les appels LLM des thèmes (§P0 de l'analyse timing) : les 12 thèmes
# "documents" sont indépendants entre eux, donc rien n'impose de les exécuter en séquence — mais
# on ne les lance pas tous d'un coup pour rester prudent sur un éventuel rate limit tokens/minute
# côté Mistral (chaque appel envoie désormais un contexte bien plus généreux, cf.
# SYNTHESIS_TOTAL_CONTEXT_MAX_CHARS). Même ordre de grandeur que le sémaphore OCR existant
# (`models.yaml` ocr.max_concurrency=3). `_retry` (app/mistral/client.py) absorbe déjà un 429
# isolé avec backoff exponentiel si la concurrence s'avère malgré tout trop agressive.
_SYNTHESIS_LLM_CONCURRENCY = 4


def _document_signals(dossier_id: str) -> list[DocumentSignal]:
    with session_scope() as s:
        documents = list_documents(s, dossier_id)
        doc_snapshots = [
            {
                "id": d.id,
                "filename": d.filename,
                "final_category": d.final_category,
                "final_lot": d.final_lot,
                "classification_confidence": d.classification_confidence,
                "text_cache_id": d.text_cache_id,
            }
            for d in documents
        ]
    return [build_document_signal(snap) for snap in doc_snapshots]


def _field_values(dossier_id: str) -> FieldValues:
    schema = load_extraction_schema()
    with session_scope() as s:
        results = list_extraction_results(s, dossier_id)
    values: FieldValues = {}
    for r in results:
        if not r.final_value:
            continue
        f = schema.by_id(r.field_id)
        if f is None:
            continue
        values[r.field_id] = (f.libelle, r.final_value)
    return values


def _persist_status(dossier_id: str, *, status: str, error: str | None = None) -> None:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        dossier.synthese_projet_status = status
        dossier.synthese_projet_error = error


async def run_project_synthesis_pipeline(dossier_id: str) -> None:
    await asyncio.to_thread(_persist_status, dossier_id, status="generating", error=None)

    schema = load_synthesis_schema()
    taxonomy = load_taxonomy()
    signals = await asyncio.to_thread(_document_signals, dossier_id)
    signals_by_id = {s.document_id: s for s in signals}
    field_values = await asyncio.to_thread(_field_values, dossier_id)

    pipeline_started_at = time.monotonic()

    # Phase 1 — OCR à la demande, une fois par document (dédupliqué sur l'UNION des candidats de
    # TOUS les thèmes). Fait avant la génération concurrente : un document pivot partagé par
    # plusieurs thèmes (ex. RICT utilisé par 4 thèmes) ne doit être OCRisé qu'une seule fois, pas
    # une fois par thème en parallèle sur le même fichier.
    all_pivot_categories = {c for topic in schema.topics for c in topic.pivot_categories}
    candidate_doc_ids = [
        d.document_id for d in signals_by_id.values() if d.final_category in all_pivot_categories
    ]
    if candidate_doc_ids:
        ocr_results = await asyncio.gather(
            *(asyncio.to_thread(ensure_document_ocr, dossier_id, signals_by_id[doc_id]) for doc_id in candidate_doc_ids)
        )
        for doc in ocr_results:
            signals_by_id[doc.document_id] = doc

    # Phase 2 — génération des 13 thèmes en concurrence bornée (§_SYNTHESIS_LLM_CONCURRENCY) :
    # ils sont indépendants entre eux, `signals_by_id` est désormais stable (plus de mutation
    # concurrente possible puisque l'OCR est déjà fait), donc rien n'empêche de les lancer en
    # parallèle plutôt qu'en séquence.
    semaphore = asyncio.Semaphore(_SYNTHESIS_LLM_CONCURRENCY)
    documents = list(signals_by_id.values())

    async def _run_topic(topic: SynthesisTopic, index: int) -> TopicOutcome:
        async with semaphore:
            topic_started_at = time.monotonic()
            outcome = await asyncio.to_thread(
                generate_topic, topic, documents=documents, field_values=field_values
            )
            elapsed = time.monotonic() - topic_started_at
            logger.info(
                "Synthèse projet %s : thème %r terminé (%d/%d) en %.1fs (documents=%d/%d candidats, modele=%s)",
                dossier_id,
                topic.id,
                index,
                len(schema.topics),
                elapsed,
                len(outcome.documents_used),
                outcome.candidates_count,
                outcome.model_name,
            )
            return outcome

    outcomes = list(
        await asyncio.gather(*(_run_topic(topic, i) for i, topic in enumerate(schema.topics, start=1)))
    )
    total_elapsed = time.monotonic() - pipeline_started_at
    logger.info(
        "Synthèse projet %s : rapport complet généré en %.1fs (%d thèmes)",
        dossier_id,
        total_elapsed,
        len(schema.topics),
    )

    cartography_md = build_documents_cartography(list(signals_by_id.values()), taxonomy)
    report_md = assemble_report(outcomes, schema, cartography_md=cartography_md)
    model_names = {o.model_name for o in outcomes if o.model_name}

    # Statut best-effort : un thème en échec (§TopicOutcome.error) reste visible tel quel dans le
    # rapport assemblé ("Section non générée (erreur : …)") sans faire échouer toute la synthèse —
    # `synthese_projet_status="error"` est réservé à une exception non gérée par ce pipeline
    # lui-même (cf. filet de sécurité de l'endpoint API).
    def _persist_result() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.synthese_projet_md = report_md
            dossier.synthese_projet_model = ", ".join(sorted(model_names)) if model_names else None
            dossier.synthese_projet_status = "done"
            dossier.synthese_projet_error = None
            dossier.synthese_projet_generated_at = dt.datetime.now(dt.timezone.utc)

    await asyncio.to_thread(_persist_result)
