"""Orchestration de l'audit des risques (Phase 2 du protocole d'analyse).

Même philosophie que la synthèse projet (Phase 1, §synthesis/pipeline.py) : déclenchée
explicitement par l'expert (`POST .../audit-risques/generate`), best-effort et jamais bloquante
(un échec ne touche jamais `Dossier.status`, seul `Dossier.audit_risques_status` en rend compte),
pas diffusée sur le WebSocket de progression partagé.

Trois phases : (1) OCR à la demande, dédupliqué sur l'union des documents réellement dans le
périmètre d'au moins une section (les CCTP hors sujet ne sont pas OCRisés inutilement) ; (2)
géocodage de l'adresse du chantier + interrogation Géorisques (best-effort) ; (3) génération LLM
des sections A→G en concurrence bornée, puis assemblage du rapport (tableau synoptique + analyse
détaillée).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from app.audit.engine import SectionOutcome, assemble_report, extract_chantier_address, generate_section
from app.audit.georisques import GeorisquesReport, build_georisques_report
from app.audit.schema import LOT_FILTERED_CATEGORIES, AuditSchema, load_audit_schema
from app.audit.engine import _normalize  # helper d'appariement de mots-clés (accents/casse)
from app.ingestion.document_signal import DocumentSignal, build_document_signal, ensure_document_ocr
from app.store.db import session_scope
from app.store.repository import get_dossier, list_documents, list_extraction_results

logger = logging.getLogger(__name__)

# Concurrence bornée sur les appels LLM des sections (6 sections indépendantes) — même ordre de
# grandeur que la Phase 1 (`_SYNTHESIS_LLM_CONCURRENCY`). `_retry` (app/mistral/client.py) absorbe
# un 429 isolé avec backoff exponentiel.
_AUDIT_LLM_CONCURRENCY = 4

# Champ d'extraction (étape 3) utilisé pour géocoder le chantier auprès de Géorisques.
_ADRESSE_CHANTIER_FIELD_ID = "adresse_chantier"


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


def _chantier_address(dossier_id: str) -> str | None:
    with session_scope() as s:
        results = list_extraction_results(s, dossier_id)
    for r in results:
        if r.field_id == _ADRESSE_CHANTIER_FIELD_ID and r.final_value:
            return r.final_value
    return None


def _document_in_scope(doc: DocumentSignal, schema: AuditSchema) -> bool:
    """True si ce document sera candidat pour au moins une section (donc à OCRiser). L'appariement
    de mots-clés se fait sur le nom de fichier/lot, disponible AVANT l'OCR — on évite ainsi
    d'OCRiser les CCTP de lots hors sujet."""
    for section in schema.sections:
        if doc.final_category not in section.pivot_categories:
            continue
        if doc.final_category in LOT_FILTERED_CATEGORIES and section.cctp_keywords:
            haystack = _normalize(f"{doc.filename} {doc.final_lot or ''}")
            if not any(_normalize(k) in haystack for k in section.cctp_keywords):
                continue
        return True
    return False


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
    candidate_doc_ids = [d.document_id for d in signals_by_id.values() if _document_in_scope(d, schema)]
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

    # Phase 3 — génération des sections A→G en concurrence bornée.
    semaphore = asyncio.Semaphore(_AUDIT_LLM_CONCURRENCY)

    async def _run_section(section, index: int) -> SectionOutcome:
        async with semaphore:
            section_started_at = time.monotonic()
            outcome = await asyncio.to_thread(
                generate_section, section, documents=documents, georisques=georisques
            )
            elapsed = time.monotonic() - section_started_at
            logger.info(
                "Audit risques %s : section %r terminée (%d/%d) en %.1fs (%d risques, documents=%d/%d, modele=%s)",
                dossier_id, section.id, index, len(schema.sections), elapsed,
                len(outcome.risks), len(outcome.documents_used), outcome.candidates_count, outcome.model_name,
            )
            return outcome

    outcomes = list(
        await asyncio.gather(*(_run_section(section, i) for i, section in enumerate(schema.sections, start=1)))
    )
    total_elapsed = time.monotonic() - pipeline_started_at
    total_risks = sum(len(o.risks) for o in outcomes)
    logger.info(
        "Audit risques %s : rapport complet généré en %.1fs (%d sections, %d risques)",
        dossier_id, total_elapsed, len(schema.sections), total_risks,
    )

    report_md = assemble_report(outcomes, schema, georisques=georisques)
    model_names = {o.model_name for o in outcomes if o.model_name}

    def _persist_result() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.audit_risques_md = report_md
            dossier.audit_risques_model = ", ".join(sorted(model_names)) if model_names else None
            dossier.audit_risques_status = "done"
            dossier.audit_risques_error = None
            dossier.audit_risques_generated_at = dt.datetime.now(dt.timezone.utc)

    await asyncio.to_thread(_persist_result)
