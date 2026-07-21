"""Orchestration de l'étape 3 — extraction d'informations (§6 du PLAN).

Pas d'écran de sélection (contrairement à la complétude) : `donnees_de_ref.md` ne décrit pas
de cases à cocher, le schéma d'extraction est fixe — tous les champs sont toujours analysés.
Le lancement reste néanmoins déclenché explicitement par l'utilisateur (`POST .../extraction/run`)
depuis `completeness_validated`, jamais enchaîné automatiquement — même principe que les 2
étapes précédentes.

Un appel LLM par DOCUMENT de référence (pas par champ, §3 OPTIMISATION.md) : on appelle chaque
document de référence distinct une fois, couvrant tous les champs qu'il concerne. Un champ
introuvable dans ses documents de référence (absents du dossier, ou présents mais sans la
valeur) est déclaré absent directement, sans recherche élargie automatique sur le reste du
dossier. Deux mécanismes complémentaires, tous deux déclenchés explicitement par l'expert :
`run_extraction_pipeline(document_ids=...)` restreint tout le run à une sélection manuelle de
documents (couche 1 sans filtrage par catégorie), et `deepen_field` relance la recherche élargie
(mots-clés) pour un seul champ resté absent, sans toucher aux autres champs.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.extraction.engine import (
    DocumentExtractionResult,
    ExtractionOutcome,
    absent_outcome,
    analyze_document,
    generate_synthesis,
    plan_layer2_calls,
    plan_manual_calls,
    plan_reference_document_calls,
    reference_candidates,
    resolve_field,
)
from app.extraction.extraction_schema import ExtractionField, load_extraction_schema
from app.ingestion.document_signal import DocumentSignal, build_document_signal, ensure_document_ocr
from app.pipeline_support import finalize_stage, start_stage
from app.progress import progress_manager
from app.settings import get_models_config
from app.store.db import session_scope
from app.store.models import Dossier, DossierStatus, ExtractionResult, MatchLayer
from app.store.repository import (
    create_extraction_result,
    get_dossier,
    get_extraction_result_by_field,
    list_documents,
    list_extraction_results,
    recompute_extraction_counters,
    set_extraction_result,
)

logger = logging.getLogger(__name__)


def ensure_results_initialized(session: Session, dossier_id: str) -> list[ExtractionResult]:
    """Crée les lignes ExtractionResult manquantes pour tous les champs du schéma — idempotent,
    appelé au premier accès à l'écran de résultats (pas de sélection : tous les champs)."""
    schema = load_extraction_schema()
    existing_ids = {r.field_id for r in list_extraction_results(session, dossier_id)}
    for f in schema.fields:
        if f.id not in existing_ids:
            create_extraction_result(session, dossier_id=dossier_id, field_id=f.id)
    return list_extraction_results(session, dossier_id)


def _counters(dossier: Dossier) -> dict[str, int]:
    return {
        "total_files": dossier.total_files,
        "text_extracted": dossier.files_text_extracted,
        "non_analyzable": dossier.files_non_analyzable,
        "error": dossier.files_error,
        "classified": dossier.files_classified,
        "pieces_selected": dossier.pieces_selected,
        "pieces_checked": dossier.pieces_checked,
        "pieces_present": dossier.pieces_present,
        "pieces_absent": dossier.pieces_absent,
        "pieces_error": dossier.pieces_error,
        "fields_total": dossier.fields_total,
        "fields_extracted": dossier.fields_extracted,
        "fields_present": dossier.fields_present,
        "fields_absent": dossier.fields_absent,
        "fields_incoherent": dossier.fields_incoherent,
        "fields_error": dossier.fields_error,
    }


async def run_extraction_pipeline(dossier_id: str, *, document_ids: list[str] | None = None) -> None:
    """`document_ids` : sélection manuelle de documents (l'expert restreint tout le run à une
    liste choisie dans l'arborescence organisée, §engine.py point 5) — `None`/liste vide = run
    standard (couche 1 filtrée par catégorie de référence, comme d'habitude)."""
    manual_scope = bool(document_ids)
    await start_stage(
        dossier_id,
        status=DossierStatus.EXTRACTING,
        stage="extraction",
        message=(
            f"Extraction ciblée sur {len(document_ids)} document(s) sélectionné(s) manuellement…"
            if manual_scope
            else "Extraction des données (un appel par document de référence, recoupement)…"
        ),
    )

    def _prepare() -> list[DocumentSignal]:
        with session_scope() as s:
            ensure_results_initialized(s, dossier_id)
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)
            documents = list_documents(s, dossier_id)
            if manual_scope:
                allowed_ids = set(document_ids)
                documents = [d for d in documents if d.id in allowed_ids]
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

    signals = await asyncio.to_thread(_prepare)
    signals_by_id: dict[str, DocumentSignal] = {s.document_id: s for s in signals}
    schema = load_extraction_schema()
    extraction_cfg = get_models_config()["extraction"]
    cross_check_required_fields = set(extraction_cfg.get("cross_check_required_fields", []))
    max_cross_check_sources = int(extraction_cfg.get("cross_check_passes", 2))

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    def _persist(outcomes: dict[str, ExtractionOutcome]) -> None:
        with session_scope() as s:
            for field_id, outcome in outcomes.items():
                result = get_extraction_result_by_field(s, dossier_id, field_id)
                assert result is not None
                set_extraction_result(
                    s,
                    result,
                    match_layer=outcome.match_layer,
                    value=outcome.value,
                    confidence=outcome.confidence,
                    justification=outcome.justification,
                    citation=outcome.citation,
                    sources=outcome.sources,
                    cross_check_status=outcome.cross_check_status,
                    model_name=outcome.model_name,
                    model_version=outcome.model_version,
                    error=outcome.error,
                )
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)

    async def _run_calls(
        calls: list[tuple[DocumentSignal, list[ExtractionField]]]
    ) -> dict[str, DocumentExtractionResult]:
        # `resolve_field` (couche 1) ne tranche qu'une fois TOUS les documents de référence
        # analysés (recoupement multi-sources possible), donc `fields_extracted` en base ne
        # bouge pas pendant cette boucle — potentiellement la phase la plus longue du pipeline.
        # On diffuse une estimation optimiste (champs déjà couverts par au moins un appel
        # terminé) pour que la barre de progression avance document par document au lieu de
        # rester bloquée puis sauter d'un coup à la fin ; jamais écrite en base.
        results: dict[str, DocumentExtractionResult] = {}
        touched_field_ids: set[str] = set()
        for doc, fields_for_doc in calls:
            # OCR à la demande (§5 OPTIMISATION.md, phase 4) : no-op si le texte est déjà
            # définitif (option désactivée, ou document déjà OCRisé/natif) ; sinon ré-extrait ce
            # document précis maintenant, avant de l'analyser.
            doc = await asyncio.to_thread(ensure_document_ocr, dossier_id, doc)
            signals_by_id[doc.document_id] = doc
            result = await asyncio.to_thread(analyze_document, doc, fields_for_doc)
            results[doc.document_id] = result
            touched_field_ids.update(f.id for f in fields_for_doc)
            counters = await asyncio.to_thread(_read_counters)
            counters["fields_extracted"] = min(
                counters["fields_extracted"] + len(touched_field_ids), counters["fields_total"]
            )
            await progress_manager.broadcast(
                dossier_id,
                stage="extraction",
                status=DossierStatus.EXTRACTING.value,
                counters=counters,
                document={
                    "id": doc.document_id,
                    "filename": doc.filename,
                    "relative_path": doc.filename,
                    "fields_covered": len(fields_for_doc),
                    "error": result.error,
                },
            )
        return results

    # --- Couche 1 : un appel par document de référence (ou par document sélectionné) --------
    layer1_calls = (
        plan_manual_calls(schema.fields, signals)
        if manual_scope
        else plan_reference_document_calls(schema.fields, signals)
    )
    layer1_results = await _run_calls(layer1_calls)
    # Les documents OCRisés à la demande pendant la couche 1 doivent être vus à jour par le
    # recoupement ci-dessous.
    signals = list(signals_by_id.values())

    layer1_outcomes: dict[str, ExtractionOutcome] = {}
    for f in schema.fields:
        candidates = signals if manual_scope else reference_candidates(f, signals)
        outcome = resolve_field(
            f,
            candidates=candidates,
            results_by_document=layer1_results,
            match_layer=MatchLayer.CONTENT.value if manual_scope else MatchLayer.FILE.value,
            cross_check_required=f.id in cross_check_required_fields,
            max_cross_check_sources=max_cross_check_sources,
        )
        if outcome is not None:
            layer1_outcomes[f.id] = outcome
    await asyncio.to_thread(_persist, layer1_outcomes)

    # --- Couche 2 : absent, aucun appel LLM (pas de recherche élargie automatique — voir
    # `deepen_field` pour un approfondissement ponctuel à la demande) -----------------------
    missing_fields = [f for f in schema.fields if f.id not in layer1_outcomes]
    if missing_fields:
        absent_message = (
            "Aucune valeur trouvée dans les documents sélectionnés manuellement pour ce champ."
            if manual_scope
            else "Aucune valeur trouvée dans les documents de référence attendus pour ce champ."
        )
        absent_outcomes = {f.id: absent_outcome(absent_message) for f in missing_fields}
        await asyncio.to_thread(_persist, absent_outcomes)

    # --- Synthèse textuelle : un appel unique à partir des valeurs déjà résolues ------------
    def _read_field_values() -> list[tuple[str, str]]:
        with session_scope() as s:
            results_by_id = {r.field_id: r for r in list_extraction_results(s, dossier_id)}
        return [
            (f.libelle, results_by_id[f.id].final_value)
            for f in schema.fields
            if f.id in results_by_id and results_by_id[f.id].final_value
        ]

    field_values = await asyncio.to_thread(_read_field_values)
    synthesis = await asyncio.to_thread(generate_synthesis, field_values)

    def _persist_synthesis() -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            dossier.synthese_ia = synthesis.text if synthesis else None
            dossier.synthese_ia_model = synthesis.model_name if synthesis else None
            dossier.synthese_ia_generated_at = dt.datetime.now(dt.timezone.utc) if synthesis else None

    await asyncio.to_thread(_persist_synthesis)

    await finalize_stage(
        dossier_id,
        status=DossierStatus.EXTRACTION_REVIEW,
        stage="extraction",
        message="Extraction terminée — résultats prêts à valider",
        counters=_counters,
        recompute=lambda s, dossier: recompute_extraction_counters(s, dossier),
    )


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


async def deepen_field(dossier_id: str, field_id: str) -> None:
    """Approfondissement ponctuel d'un seul champ (§ docstring module, point 2) : recherche
    élargie par mots-clés sur tout le dossier (`layer2_candidates`/`MAX_LLM_CANDIDATES`),
    déclenchée explicitement par l'expert depuis l'écran de validation de l'étape 3 — jamais
    automatique, jamais pour les autres champs. Écrase la proposition existante de ce champ
    (même sémantique qu'un nouveau run standard sur ce champ)."""
    schema = load_extraction_schema()
    extraction_field = schema.by_id(field_id)
    if extraction_field is None:
        raise ValueError(f"Champ d'extraction inconnu : {field_id}")

    signals = await asyncio.to_thread(_document_signals, dossier_id)
    calls = plan_layer2_calls([extraction_field], signals)
    candidates = [doc for doc, _ in calls]

    results_by_document: dict[str, DocumentExtractionResult] = {}
    for doc, fields_for_doc in calls:
        doc = await asyncio.to_thread(ensure_document_ocr, dossier_id, doc)
        result = await asyncio.to_thread(analyze_document, doc, fields_for_doc)
        results_by_document[doc.document_id] = result

    outcome = resolve_field(
        extraction_field,
        candidates=candidates,
        results_by_document=results_by_document,
        match_layer=MatchLayer.CONTENT.value,
        cross_check_required=False,
    ) or absent_outcome(
        "Recherche élargie effectuée sur les documents les plus proches par mots-clés : "
        "aucune valeur trouvée."
    )

    def _persist() -> None:
        with session_scope() as s:
            result = get_extraction_result_by_field(s, dossier_id, field_id)
            assert result is not None
            set_extraction_result(
                s,
                result,
                match_layer=outcome.match_layer,
                value=outcome.value,
                confidence=outcome.confidence,
                justification=outcome.justification,
                citation=outcome.citation,
                sources=outcome.sources,
                cross_check_status=outcome.cross_check_status,
                model_name=outcome.model_name,
                model_version=outcome.model_version,
                error=outcome.error,
            )
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_extraction_counters(s, dossier)

    await asyncio.to_thread(_persist)
