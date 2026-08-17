"""Orchestration de l'audit des risques (Phase 2 du protocole d'analyse).

Même philosophie que la synthèse projet (Phase 1, §synthesis/pipeline.py) : déclenchée
explicitement par l'expert (`POST .../audit-risques/generate`), best-effort et jamais bloquante
(un échec ne touche jamais `Dossier.status`, seul `Dossier.audit_risques_status` en rend compte),
pas diffusée sur le WebSocket de progression partagé.

Quatre phases, toutes dédupliquées par DOCUMENT (jamais par couple document × section) :

1. OCR à la demande, sur l'union des documents réellement dans le périmètre d'au moins une section
   (les CCTP hors sujet ne sont pas OCRisés inutilement) ;
2. géocodage de l'adresse du chantier + interrogation Géorisques (best-effort) ;
3. « map » — un appel LLM par document pivot (§summarize_document_for_audit), qui relève d'un coup
   ses prescriptions, réserves et lacunes pour chacune des sections dont il est pivot ;
4. « reduce » — un appel LLM par section (§generate_section), alimenté par ces relevés, puis
   assemblage du rapport (tableau synoptique + analyse détaillée).

Le map-reduce (phases 3-4) remplace la concaténation des textes bruts dans le prompt de chaque
section, qui tronquait et excluait : sur le run e2e du 2026-07-29, 369 661 caractères perdus, dont
le RICT rogné dans 5 sections et absent d'une sixième. Il envoie aussi MOINS de contexte au total
(-16 % sur ce dossier), le RICT n'étant plus renvoyé 5 fois tronqué mais lu une fois en entier.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

from app.audit.engine import (
    DocumentAuditSummary,
    SectionOutcome,
    assemble_report,
    extract_chantier_address,
    generate_section,
    sections_for_document,
    summarize_document_for_audit,
)
from app.audit.georisques import GeorisquesReport, build_georisques_report
from app.audit.schema import AuditSection, load_audit_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal, ensure_document_ocr
from app.store.db import session_scope
from app.store.repository import get_dossier, list_documents, list_extraction_results

logger = logging.getLogger(__name__)

# Concurrence bornée sur les appels LLM des sections (6 sections indépendantes) et des relevés
# « map » par document pivot (texte intégral, comme la Phase 1) — même valeur que
# `_SYNTHESIS_LLM_CONCURRENCY` et `_EXTRACTION_LLM_CONCURRENCY` (app/extraction/pipeline.py).
#
# Relevé de 8 à 16 le 2026-08-12, sur mesure du VRAI pipeline (pas un gabarit synthétique) sur
# `dce_chu_rouen.zip` (84 fichiers, 24 appels map) : à 16, 415,4s et 0 échec — nettement mieux que
# 8 (595,2s ET 1/24 appels map en échec) et que 24 (488,7s ET 2/24 en échec). Ces échecs ne sont
# JAMAIS un 429/rate limit (aucun observé à aucun palier, y compris au-delà de 16 côté Phase 1,
# §app/synthesis/pipeline.py) mais un timeout de LECTURE côté client (`llm.timeout_seconds=300`)
# sur des CCTP techniques volumineux (un document différent à chaque fois) — absorbé sans casser
# le pipeline (repli automatique sur extrait brut tronqué pour la section concernée,
# app/audit/engine.py). Échantillon trop petit (3 runs) pour confirmer que la concurrence CAUSE
# ces timeouts plutôt que du bruit — mais 16 reste le meilleur point mesuré sur les trois. Détail
# complet, tableaux et logs bruts :
# `test-runs/campagnes/2026-08-12_phase1-2-concurrence-limites/RAPPORT_CONCURRENCE.md`.
# `_retry` (app/mistral/client.py) absorbe un 429 isolé avec backoff exponentiel.
_AUDIT_LLM_CONCURRENCY = 16

# Champ d'extraction (étape 3) utilisé pour géocoder le chantier auprès de Géorisques.
_ADRESSE_CHANTIER_FIELD_ID = "adresse_chantier"


def _document_signals(dossier_id: str) -> list[DocumentSignal]:
    with session_scope() as s:
        documents = list_documents(s, dossier_id)
        doc_snapshots = [
            {
                "id": d.id,
                "filename": d.filename,
                "final_filename": d.final_filename,
                "final_category": d.final_category,
                "final_lot": d.final_lot,
                "classification_confidence": d.classification_confidence,
                "text_cache_id": d.text_cache_id,
            }
            for d in documents
        ]
    return [build_document_signal(snap) for snap in doc_snapshots]


def _chantier_address(dossier_id: str) -> str | None:
    with session_scope() as s:
        results = list_extraction_results(s, dossier_id)
    for r in results:
        if r.field_id == _ADRESSE_CHANTIER_FIELD_ID and r.final_value:
            return r.final_value
    return None


def _persist_status(dossier_id: str, *, status: str, error: str | None = None) -> None:
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier is not None
        dossier.audit_risques_status = status
        dossier.audit_risques_error = error


async def run_audit_pipeline(dossier_id: str) -> None:
    await asyncio.to_thread(_persist_status, dossier_id, status="generating", error=None)

    schema = load_audit_schema()
    signals = await asyncio.to_thread(_document_signals, dossier_id)
    signals_by_id = {s.document_id: s for s in signals}

    pipeline_started_at = time.monotonic()

    # Phase 1 — OCR à la demande, dédupliqué sur l'union des documents dans le périmètre d'au moins
    # une section (un document partagé par plusieurs sections n'est OCRisé qu'une fois).
    # `sections_for_document` n'apparie que sur le nom de fichier/lot, disponible AVANT l'OCR : les
    # CCTP de lots hors sujet ne sont donc jamais OCRisés.
    candidate_doc_ids = [
        d.document_id for d in signals_by_id.values() if sections_for_document(d, schema)
    ]
    if candidate_doc_ids:
        ocr_results = await asyncio.gather(
            *(asyncio.to_thread(ensure_document_ocr, dossier_id, signals_by_id[doc_id]) for doc_id in candidate_doc_ids)
        )
        for doc in ocr_results:
            signals_by_id[doc.document_id] = doc

    documents = list(signals_by_id.values())

    # Phase 2 — géocodage + Géorisques (best-effort, hors du chemin critique de l'audit). L'adresse
    # du chantier vient d'abord du champ d'extraction `adresse_chantier` (étape 3) ; si l'étape 3 l'a
    # manqué, on relit l'arrêté PC / la notice / le RICT via un petit appel LLM dédié (sans quoi les
    # dossiers au champ vide n'auraient jamais de contexte Géorisques).
    address = await asyncio.to_thread(_chantier_address, dossier_id)
    if not address:
        address = await asyncio.to_thread(extract_chantier_address, documents)
    georisques: GeorisquesReport | None = await asyncio.to_thread(build_georisques_report, address)
    if georisques is not None:
        logger.info(
            "Audit risques %s : Géorisques — adresse=%r géocodée=%s séisme=%s rga=%s radon=%s erreurs=%s",
            dossier_id, address, georisques.geocoded, georisques.seisme, georisques.rga, georisques.radon, georisques.errors,
        )
    else:
        logger.info("Audit risques %s : aucune adresse de chantier — Géorisques non interrogé", dossier_id)

    semaphore = asyncio.Semaphore(_AUDIT_LLM_CONCURRENCY)

    # Phase 3 — « map » : un appel LLM par DOCUMENT pivot, couvrant d'un coup toutes les sections
    # dont il est pivot. Même dédup que l'OCR ci-dessus : le RICT, pivot de 5 sections sur le
    # dossier de test, n'est lu qu'une fois au lieu d'être renvoyé 5 fois tronqué.
    map_jobs: list[tuple[DocumentSignal, list[AuditSection]]] = []
    for doc in documents:
        if not doc.content_excerpt:
            continue
        doc_sections = sections_for_document(doc, schema)
        if doc_sections:
            map_jobs.append((doc, doc_sections))

    async def _run_map(doc: DocumentSignal, doc_sections: list[AuditSection]) -> DocumentAuditSummary:
        async with semaphore:
            doc_started_at = time.monotonic()
            summary = await asyncio.to_thread(summarize_document_for_audit, doc, doc_sections)
            logger.info(
                "Audit risques %s : relevé du document %r terminé en %.1fs "
                "(sections=%d, documentées=%d, modele=%s, erreur=%s)",
                dossier_id, doc.filename, time.monotonic() - doc_started_at,
                len(doc_sections), len(summary.constats_by_section), summary.model_name, summary.error,
            )
            return summary

    map_started_at = time.monotonic()
    summaries_by_document: dict[str, DocumentAuditSummary] = {}
    if map_jobs:
        results = await asyncio.gather(*(_run_map(doc, secs) for doc, secs in map_jobs))
        summaries_by_document = {s.document_id: s for s in results}
        failed = [s.filename for s in results if s.error]
        logger.info(
            "Audit risques %s : %d relevé(s) de document produit(s) en %.1fs (%d en échec%s)",
            dossier_id, len(results), time.monotonic() - map_started_at, len(failed),
            f" : {failed}" if failed else "",
        )

    # Phase 4 — « reduce » : génération des sections A→G en concurrence bornée, sur les relevés.
    async def _run_section(section, index: int) -> SectionOutcome:
        async with semaphore:
            section_started_at = time.monotonic()
            outcome = await asyncio.to_thread(
                generate_section,
                section,
                documents=documents,
                georisques=georisques,
                summaries=summaries_by_document,
            )
            elapsed = time.monotonic() - section_started_at
            logger.info(
                "Audit risques %s : section %r terminée (%d/%d) en %.1fs "
                "(%d risques, documents exploités=%d/%d, dont %d en repli brut, modele=%s)",
                dossier_id, section.id, index, len(schema.sections), elapsed,
                len(outcome.risks), len(outcome.documents_used), outcome.candidates_count,
                len(outcome.documents_degraded), outcome.model_name,
            )
            return outcome

    outcomes = list(
        await asyncio.gather(*(_run_section(section, i) for i, section in enumerate(schema.sections, start=1)))
    )
    total_elapsed = time.monotonic() - pipeline_started_at
    total_risks = sum(len(o.risks) for o in outcomes)
    logger.info(
        "Audit risques %s : rapport complet généré en %.1fs (%d relevés de documents + %d sections, %d risques)",
        dossier_id, total_elapsed, len(map_jobs), len(schema.sections), total_risks,
    )

    report = assemble_report(outcomes, schema, georisques=georisques)
    # Les deux étapes comptent : `audit_risques_model` doit refléter tous les modèles ayant
    # réellement contribué au rapport, pas seulement ceux de l'appel final.
    model_names = {o.model_name for o in outcomes if o.model_name}
    model_names |= {s.model_name for s in summaries_by_document.values() if s.model_name}

    def _persist_result() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.audit_risques_md = report.markdown
            dossier.audit_risques_citations = json.dumps(report.citations, ensure_ascii=False)
            dossier.audit_risques_model = ", ".join(sorted(model_names)) if model_names else None
            dossier.audit_risques_status = "done"
            dossier.audit_risques_error = None
            dossier.audit_risques_generated_at = dt.datetime.now(dt.timezone.utc)

    await asyncio.to_thread(_persist_result)
