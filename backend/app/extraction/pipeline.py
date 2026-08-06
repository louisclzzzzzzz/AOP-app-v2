"""Orchestration de l'étape 3 — extraction d'informations (§6 du PLAN).

Pas d'écran de sélection (contrairement à la complétude) : `donnees_de_ref.md` ne décrit pas
de cases à cocher, le schéma d'extraction est fixe — tous les champs sont toujours analysés.
Le lancement reste néanmoins déclenché explicitement par l'utilisateur (`POST .../extraction/run`)
depuis `completeness_validated`, jamais enchaîné automatiquement — même principe que les 2
étapes précédentes.

Un appel LLM par DOCUMENT de référence (pas par champ, §3 OPTIMISATION.md) : on appelle chaque
document de référence distinct une fois, couvrant tous les champs qu'il concerne (couche 1).
Un champ introuvable dans ses documents de référence (absents du dossier, ou présents mais sans
la valeur) déclenche automatiquement une recherche élargie sur l'ensemble du dossier, par
mots-clés ET par recherche sémantique (couche 2, `select_layer2_candidates`/`plan_layer2_calls`),
sans action de l'expert — le run
standard va donc chercher le maximum d'information disponible en une seule fois. Seuls les
champs toujours introuvables après cette recherche élargie sont déclarés absents, avec une
justification explicite. `run_extraction_pipeline(document_ids=...)` reste le seul mécanisme
séparé : une sélection manuelle de documents qui restreint tout le run (couche 1 sans filtrage
par catégorie, pas de couche 2 — le périmètre a déjà été choisi par l'expert).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy.orm import Session

from app.extraction.engine import (
    DocumentExtractionResult,
    ExtractionOutcome,
    absent_outcome,
    analyze_document,
    generate_synthesis,
    merge_document_results,
    plan_layer2_calls,
    plan_manual_calls,
    plan_reference_document_calls,
    reference_candidates,
    resolve_field,
)
from app.extraction.extraction_schema import ExtractionField, load_extraction_schema
from app.extraction.semantic_retrieval import mark_semantic_origin, select_layer2_candidates
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

# Concurrence bornée sur les appels LLM par document (couches 1 et 2). Mesuré empiriquement sur
# le vrai compte Mistral avant d'implémenter (script de test hors-projet, mistral-large-2512,
# gabarit d'appel identique à `analyze_document`) : 0 échec/429 jusqu'à 16 appels simultanés,
# débit passant de ~5 appels/min en séquentiel à ~30 appels/min à concurrence=8 (~37/min à
# concurrence=16, mais rendements décroissants au-delà de 8 et latence par appel qui grimpe —
# signe d'un début de throttling côté serveur avant tout 429 franc). 8 retenu comme valeur sûre
# avec marge sous le plus haut palier testé sans erreur, plutôt que 16 : les prompts réels
# (jusqu'à `MAX_FIELDS_PER_CALL` champs sur un extrait de `DOCUMENT_EXCERPT_MAX_CHARS`) sont plus
# variables que le gabarit de test. Même valeur reprise pour `_AUDIT_LLM_CONCURRENCY` et
# `_SYNTHESIS_LLM_CONCURRENCY` (mêmes modèle et rate limit) — auparavant 4 dans les trois cas,
# sans mesure empirique à l'appui. `_retry` (app/mistral/client.py) absorbe un 429 isolé avec
# backoff exponentiel ; le stimulateur par clé (`min_interval_seconds`) reste la seconde ligne de
# défense si la concurrence à elle seule dépassait un jour le débit réellement autorisé.
_EXTRACTION_LLM_CONCURRENCY = 8


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
        if not calls:
            return {}

        # Phase 1 — OCR à la demande (§5 OPTIMISATION.md, phase 4), en concurrence (déjà bornée
        # globalement par `ocr.max_concurrency`, §`ocr_slot()` dans app/mistral/client.py — donc
        # sans rapport avec la concurrence LLM ci-dessous). Dédupliquée par document : `calls`
        # peut contenir plusieurs entrées pour un même document quand ses champs ont été lotis
        # (`_batch_fields`), inutile de l'OCRiser deux fois (même dédup que app/audit/pipeline.py).
        unique_docs = {doc.document_id: doc for doc, _ in calls}
        ocr_started_at = time.monotonic()
        ocr_updated = await asyncio.gather(
            *(asyncio.to_thread(ensure_document_ocr, dossier_id, doc) for doc in unique_docs.values())
        )
        for doc in ocr_updated:
            signals_by_id[doc.document_id] = doc
        logger.info(
            "Extraction %s : OCR à la demande sur %d document(s) terminé en %.1fs",
            dossier_id, len(unique_docs), time.monotonic() - ocr_started_at,
        )

        # Phase 2 — un appel LLM par entrée de `calls`, en concurrence bornée
        # (`_EXTRACTION_LLM_CONCURRENCY`). `analyze_document` ne lève jamais (erreur capturée dans
        # `DocumentExtractionResult.error`), donc un échec isolé n'annule pas les autres appels en
        # vol dans le même `asyncio.gather`.
        results: dict[str, DocumentExtractionResult] = {}
        touched_field_ids: set[str] = set()
        semaphore = asyncio.Semaphore(_EXTRACTION_LLM_CONCURRENCY)

        async def _run_one(doc: DocumentSignal, fields_for_doc: list[ExtractionField]) -> None:
            current_doc = signals_by_id[doc.document_id]  # texte à jour après l'OCR ci-dessus
            async with semaphore:
                result = await asyncio.to_thread(analyze_document, current_doc, fields_for_doc)
            # Un même document donne lieu à plusieurs appels quand ses champs ont été lotis
            # (`_batch_fields`) : on fusionne au lieu d'écraser, sinon seul le dernier lot
            # survivrait et tous les champs des lots précédents seraient déclarés absents. Pas de
            # verrou nécessaire : asyncio est mono-thread, ce bloc ne contient aucun `await`.
            results[current_doc.document_id] = merge_document_results(
                results.get(current_doc.document_id), result
            )
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
                    "id": current_doc.document_id,
                    "filename": current_doc.filename,
                    "relative_path": current_doc.filename,
                    "fields_covered": len(fields_for_doc),
                    "error": result.error,
                },
            )

        llm_started_at = time.monotonic()
        await asyncio.gather(*(_run_one(doc, fields_for_doc) for doc, fields_for_doc in calls))
        logger.info(
            "Extraction %s : %d appel(s) LLM (concurrence=%d) terminé(s) en %.1fs",
            dossier_id, len(calls), _EXTRACTION_LLM_CONCURRENCY, time.monotonic() - llm_started_at,
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

    # --- Couche 2 : approfondissement automatique (recherche élargie par mots-clés sur tout le
    # dossier) pour tous les champs restés absents après la couche 1, en un seul passage — fusion
    # de l'ancien mécanisme à la demande (`deepen_missing_fields`) dans le run standard, pour que
    # l'expert n'ait jamais besoin de le déclencher manuellement. Non appliqué en sélection
    # manuelle (`manual_scope`) : le périmètre documentaire a déjà été choisi par l'expert, une
    # couche 2 élargirait le run au-delà de ce périmètre volontairement restreint. ----------------
    missing_fields = [f for f in schema.fields if f.id not in layer1_outcomes]
    if missing_fields and manual_scope:
        absent_outcomes = {
            f.id: absent_outcome("Aucune valeur trouvée dans les documents sélectionnés manuellement pour ce champ.")
            for f in missing_fields
        }
        await asyncio.to_thread(_persist, absent_outcomes)
    elif missing_fields:
        # Sélection des documents candidats calculée UNE SEULE FOIS pour tous les champs
        # manquants, puis réutilisée telle quelle par le plan d'appels et par la résolution : elle
        # vectorise le dossier (`select_layer2_candidates`), ce qu'il serait absurde de refaire à
        # chaque champ. Ne lève jamais — repli sur les mots-clés seuls si les embeddings sont
        # indisponibles.
        selections = await asyncio.to_thread(select_layer2_candidates, missing_fields, signals)
        layer2_calls = plan_layer2_calls(
            missing_fields,
            signals,
            candidates_by_field={field_id: sel.candidates for field_id, sel in selections.items()},
        )
        layer2_results = await _run_calls(layer2_calls)

        def _layer2_outcome(f: ExtractionField) -> ExtractionOutcome:
            selection = selections[f.id]
            outcome = resolve_field(
                f,
                candidates=selection.candidates,
                results_by_document=layer2_results,
                match_layer=MatchLayer.CONTENT.value,
                cross_check_required=False,
            )
            if outcome is None:
                return absent_outcome(
                    "Aucune valeur trouvée, y compris après recherche élargie (mots-clés et "
                    "recherche sémantique) sur l'ensemble du dossier."
                )
            # Une valeur tirée d'un document que seuls les embeddings ont proposé est signalée
            # comme telle : c'est là que le risque de valeur plausible mais fausse se concentre,
            # et l'expert doit pouvoir la repérer sans relire chaque citation.
            return mark_semantic_origin(outcome, selection)

        layer2_outcomes: dict[str, ExtractionOutcome] = {f.id: _layer2_outcome(f) for f in missing_fields}
        await asyncio.to_thread(_persist, layer2_outcomes)

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
