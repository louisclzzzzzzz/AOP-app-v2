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

Les 13 thèmes sont générés en trois phases, toutes dédupliquées par DOCUMENT (jamais par couple
document × thème) et toutes en concurrence bornée (`_SYNTHESIS_LLM_CONCURRENCY`) plutôt qu'en
séquence stricte — mesuré sur les dossiers de test, le temps de synthèse était jusque-là la somme
de 12 appels LLM indépendants (190-400s par dossier) sans aucune raison de ne pas les
paralléliser :

1. OCR à la demande, dédupliqué sur l'ensemble des documents pivots candidats de tous les thèmes ;
2. "map" — un appel LLM par document pivot (§summarize_document), qui produit d'un coup le relevé
   factuel de ce document pour chacun des thèmes dont il est pivot. Le RICT, pivot de 4 thèmes,
   n'est lu qu'une fois : même dédup que l'OCR ci-dessus ;
3. "reduce" — un appel LLM par thème (§generate_topic), alimenté par les relevés de l'étape 2 au
   lieu des textes bruts tronqués d'avant.

Le nombre d'appels LLM augmente donc (N documents pivots + 12 thèmes, au lieu de 12) : c'est le
prix à payer pour ne plus jamais tronquer ni exclure un document pivot faute de budget de contexte
(§app/synthesis/engine.py).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal, ensure_document_ocr
from app.store.db import session_scope
from app.store.repository import get_dossier, list_documents, list_extraction_results
from app.synthesis.engine import (
    DocumentSummary,
    FieldValues,
    TopicOutcome,
    assemble_report,
    build_documents_cartography,
    generate_topic,
    summarize_document,
    topics_for_document,
)
from app.synthesis.schema import SynthesisTopic, load_synthesis_schema

logger = logging.getLogger(__name__)

# Concurrence bornée sur les appels LLM (§P0 de l'analyse timing) : les relevés de documents sont
# indépendants entre eux, les thèmes aussi, donc rien n'impose de les exécuter en séquence.
# Mesuré empiriquement sur le vrai compte Mistral avant d'être relevé à 8 (comme
# `_EXTRACTION_LLM_CONCURRENCY`, app/extraction/pipeline.py — mêmes chiffres de base : 0 échec
# jusqu'à 16 appels simultanés sur mistral-large-2512) : contrairement à l'extraction, un appel
# « map » ici envoie le texte INTÉGRAL d'un document (pas un extrait borné), donc un volume de
# tokens/appel bien plus élevé — reste dans le rate limit tokens/minute (validé avec un gabarit de
# test ~26k caractères + max_tokens=16000, 0 échec à concurrence=8). `_retry`
# (app/mistral/client.py) absorbe déjà un 429 isolé avec backoff exponentiel si la concurrence
# s'avère malgré tout trop agressive sur un dossier aux documents pivots particulièrement longs.
#
# Relevé de 8 à 16 le 2026-08-12, sur mesure du VRAI pipeline (pas un gabarit synthétique) sur
# `dce_chu_rouen.zip` (84 fichiers, 51 appels map) : 0 échec/429 à AUCUN palier testé, y compris à
# 40 (quasi non-borné pour ce dossier) — 16 est le point le plus rapide mesuré (317,8s contre
# 410,1s à 8, la phase map à elle seule passant de 367,2s à 232,7s), sans gain fiable au-delà
# (bruit de mesure, pas de dégradation). Détail complet, tableaux et logs bruts :
# `test-runs/campagnes/2026-08-12_phase1-2-concurrence-limites/RAPPORT_CONCURRENCE.md`.
_SYNTHESIS_LLM_CONCURRENCY = 16


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
    """Valeurs de l'étape 3, indexées par field_id.

    Les champs SANS valeur sont inclus, avec une valeur vide : la carte d'identité de la Phase 1
    (`_format_extraction_fields_topic`) doit pouvoir afficher une ligne « non trouvé » plutôt que
    d'omettre la donnée — une absence est une information de souscription, et une ligne manquante
    en silence se confond avec un champ qu'on aurait oublié de demander. Les consommateurs qui ne
    veulent que les valeurs renseignées (`_format_grounding_block`) testent déjà la valeur vide."""
    schema = load_extraction_schema()
    with session_scope() as s:
        results = list_extraction_results(s, dossier_id)
    by_field_id = {r.field_id: r for r in results}
    values: FieldValues = {}
    for f in schema.fields:
        result = by_field_id.get(f.id)
        values[f.id] = (f.libelle, result.final_value if result and result.final_value else "")
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
    # plusieurs thèmes (ex. RICT, pivot de 7 thèmes) ne doit être OCRisé qu'une seule fois, pas
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

    # `signals_by_id` est désormais stable (plus de mutation concurrente possible puisque l'OCR est
    # déjà fait), donc les deux phases LLM qui suivent peuvent lire librement en parallèle.
    semaphore = asyncio.Semaphore(_SYNTHESIS_LLM_CONCURRENCY)
    documents = list(signals_by_id.values())

    # Phase 2 — "map" : un appel LLM par DOCUMENT pivot, qui produit d'un coup son relevé pour tous
    # les thèmes dont il est pivot. Même dédup que l'OCR ci-dessus : le RICT, pivot de 7 thèmes,
    # n'est lu qu'une fois. C'est ce qui remplace l'ancienne concaténation des textes bruts dans le
    # prompt de chaque thème, où un document au-delà du budget était purement absent du prompt.
    map_jobs: list[tuple[DocumentSignal, list[SynthesisTopic]]] = []
    for doc in documents:
        doc_topics = topics_for_document(doc, schema)
        if doc_topics:
            map_jobs.append((doc, doc_topics))

    async def _run_map(doc: DocumentSignal, doc_topics: list[SynthesisTopic]) -> DocumentSummary:
        async with semaphore:
            doc_started_at = time.monotonic()
            summary = await asyncio.to_thread(summarize_document, doc, doc_topics)
            logger.info(
                "Synthèse projet %s : relevé du document %r terminé en %.1fs "
                "(themes=%d, avec info=%d, modele=%s, erreur=%s)",
                dossier_id,
                doc.filename,
                time.monotonic() - doc_started_at,
                len(doc_topics),
                len(summary.summaries_by_topic),
                summary.model_name,
                summary.error,
            )
            return summary

    map_started_at = time.monotonic()
    summaries_by_document: dict[str, DocumentSummary] = {}
    if map_jobs:
        results = await asyncio.gather(*(_run_map(doc, doc_topics) for doc, doc_topics in map_jobs))
        summaries_by_document = {s.document_id: s for s in results}
        failed = [s.filename for s in results if s.error]
        logger.info(
            "Synthèse projet %s : %d relevé(s) de document produit(s) en %.1fs (%d en échec%s)",
            dossier_id,
            len(results),
            time.monotonic() - map_started_at,
            len(failed),
            f" : {failed}" if failed else "",
        )

    # Phase 3 — "reduce" : génération des 13 thèmes en concurrence bornée
    # (§_SYNTHESIS_LLM_CONCURRENCY), chacun alimenté par les relevés de la phase 2. Les thèmes sont
    # indépendants entre eux, donc rien n'empêche de les lancer en parallèle plutôt qu'en séquence.
    async def _run_topic(topic: SynthesisTopic, index: int) -> TopicOutcome:
        async with semaphore:
            topic_started_at = time.monotonic()
            outcome = await asyncio.to_thread(
                generate_topic,
                topic,
                documents=documents,
                field_values=field_values,
                summaries=summaries_by_document,
            )
            elapsed = time.monotonic() - topic_started_at
            logger.info(
                "Synthèse projet %s : thème %r terminé (%d/%d) en %.1fs "
                "(documents exploités=%d/%d candidats, dont %d en repli brut, modele=%s)",
                dossier_id,
                topic.id,
                index,
                len(schema.topics),
                elapsed,
                len(outcome.documents_used),
                outcome.candidates_count,
                len(outcome.documents_degraded),
                outcome.model_name,
            )
            return outcome

    outcomes = list(
        await asyncio.gather(*(_run_topic(topic, i) for i, topic in enumerate(schema.topics, start=1)))
    )
    total_elapsed = time.monotonic() - pipeline_started_at
    logger.info(
        "Synthèse projet %s : rapport complet généré en %.1fs (%d relevés de documents + %d thèmes)",
        dossier_id,
        total_elapsed,
        len(map_jobs),
        len(schema.topics),
    )

    cartography_md = build_documents_cartography(list(signals_by_id.values()), taxonomy)
    report = assemble_report(outcomes, schema, cartography_md=cartography_md)
    # Les deux étapes (map et reduce) comptent : `synthese_projet_model` doit refléter tous les
    # modèles qui ont réellement contribué au rapport, pas seulement ceux de l'appel final.
    model_names = {o.model_name for o in outcomes if o.model_name}
    model_names |= {s.model_name for s in summaries_by_document.values() if s.model_name}

    # Statut best-effort : un thème en échec (§TopicOutcome.error) reste visible tel quel dans le
    # rapport assemblé ("Section non générée (erreur : …)") sans faire échouer toute la synthèse —
    # `synthese_projet_status="error"` est réservé à une exception non gérée par ce pipeline
    # lui-même (cf. filet de sécurité de l'endpoint API).
    def _persist_result() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.synthese_projet_md = report.markdown
            dossier.synthese_projet_citations = json.dumps(report.citations, ensure_ascii=False)
            dossier.synthese_projet_model = ", ".join(sorted(model_names)) if model_names else None
            dossier.synthese_projet_status = "done"
            dossier.synthese_projet_error = None
            dossier.synthese_projet_generated_at = dt.datetime.now(dt.timezone.utc)

    await asyncio.to_thread(_persist_result)
