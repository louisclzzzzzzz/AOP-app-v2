"""Orchestration de l'étape 0 (ingestion) : dézip -> inventaire -> extraction de texte,
avec diffusion de progression live (WebSocket) après chaque étape et par document.

Tourne comme tâche asyncio ; les opérations bloquantes (I/O disque, hash, appels Mistral)
sont déportées via `asyncio.to_thread` pour ne jamais geler la boucle d'évènements — et donc
ne jamais interrompre la diffusion de progression aux clients connectés.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.ingestion.inventory import build_inventory
from app.ingestion.metadata import detect_key_mentions, first_nonempty_line
from app.ingestion.text_extraction import extract_text_for_file
from app.ingestion.unzip import extract_zip_recursive
from app.ocr.cache import read_text_cache, write_text_cache_files
from app.progress import progress_manager
from app.settings import get_models_config, get_settings
from app.store.db import session_scope
from app.store.models import CacheStatus, Dossier, DossierStatus
from app.store.repository import (
    get_dossier,
    get_document,
    get_or_create_pending_text_cache,
    list_documents,
    recompute_dossier_counters,
    set_document_text_result,
    set_dossier_status,
    update_text_cache_result,
)

logger = logging.getLogger(__name__)


def _counters(dossier: Dossier) -> dict[str, int]:
    return {
        "total_files": dossier.total_files,
        "text_extracted": dossier.files_text_extracted,
        "non_analyzable": dossier.files_non_analyzable,
        "error": dossier.files_error,
    }


async def run_ingestion_pipeline(dossier_id: str, uploaded_zip_path: Path) -> None:
    settings = get_settings()
    source_dir = settings.workspace_dir / dossier_id / "source"

    def _set_status(status: DossierStatus, error_message: str | None = None) -> None:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            set_dossier_status(s, dossier, status, error_message=error_message)

    # --- 1) Dézippage récursif -----------------------------------------------------
    await asyncio.to_thread(_set_status, DossierStatus.UNZIPPING)
    await progress_manager.broadcast(
        dossier_id, stage="unzip", status=DossierStatus.UNZIPPING.value, message="Décompression en cours…"
    )
    try:
        await asyncio.to_thread(extract_zip_recursive, uploaded_zip_path, source_dir)
    except Exception as exc:
        logger.exception("Échec du dézippage pour %s", dossier_id)
        await asyncio.to_thread(_set_status, DossierStatus.ERROR, f"Échec du dézippage : {exc}")
        await progress_manager.broadcast(
            dossier_id, stage="unzip", status=DossierStatus.ERROR.value, message=str(exc)
        )
        return

    # --- 2) Inventaire ---------------------------------------------------------------
    await asyncio.to_thread(_set_status, DossierStatus.INVENTORYING)
    await progress_manager.broadcast(
        dossier_id, stage="inventory", status=DossierStatus.INVENTORYING.value, message="Inventaire des fichiers…"
    )

    def _do_inventory() -> list[tuple[str, str]]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            docs = build_inventory(s, dossier, source_dir)
            recompute_dossier_counters(s, dossier)
            return [(d.id, d.sha256) for d in docs]

    inventory_items = await asyncio.to_thread(_do_inventory)
    document_ids = [doc_id for doc_id, _sha256 in inventory_items]
    hash_by_document_id = dict(inventory_items)

    def _read_counters() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            return _counters(dossier)

    counters = await asyncio.to_thread(_read_counters)
    await progress_manager.broadcast(
        dossier_id,
        stage="inventory",
        status=DossierStatus.INVENTORYING.value,
        counters=counters,
        message=f"{len(document_ids)} fichiers inventoriés",
    )

    # --- 3) Extraction de texte / OCR, document par document -------------------------
    await asyncio.to_thread(_set_status, DossierStatus.EXTRACTING_TEXT)
    await progress_manager.broadcast(
        dossier_id,
        stage="text_extraction",
        status=DossierStatus.EXTRACTING_TEXT.value,
        counters=counters,
        message="Extraction de texte / OCR…",
    )

    # Concurrence bornée (§1/§4 OPTIMISATION.md) : chaque document est indépendant (hash,
    # extraction native ou OCR), le sémaphore côté client Mistral protège déjà l'API OCR d'un
    # dépassement — la taille de lot ici sert surtout à diffuser les évènements par groupes.
    batch_size = max(1, int(get_models_config()["ocr"].get("max_concurrency", 3)))
    for i in range(0, len(document_ids), batch_size):
        batch = document_ids[i : i + batch_size]

        # Dédupliquer par hash de contenu AVANT de dispatcher en parallèle : deux documents
        # identiques dans le même lot (fréquent dans les DCE réels, cf. OPTIMISATION.md §1)
        # déclencheraient sinon une course sur la contrainte unique TextCache.content_hash —
        # IntegrityError non catchée qui fait passer tout le dossier en erreur
        # (AUDIT_BACKEND.md §1). On ne traite en concurrence qu'un représentant par hash ; les
        # doublons du lot sont traités juste après, séquentiellement — à ce moment le cache est
        # déjà rempli, donc simple lecture, sans coût OCR/LLM supplémentaire.
        leaders, followers = _dedupe_batch_by_hash(batch, hash_by_document_id)

        doc_events = list(
            await asyncio.gather(
                *(asyncio.to_thread(_process_document_text, dossier_id, doc_id, source_dir) for doc_id in leaders)
            )
        )
        for doc_id in followers:
            doc_events.append(await asyncio.to_thread(_process_document_text, dossier_id, doc_id, source_dir))

        for doc_event in doc_events:
            if doc_event is None:
                continue  # non analysable, rien à diffuser
            counters = await asyncio.to_thread(_read_counters)
            await progress_manager.broadcast(
                dossier_id,
                stage="text_extraction",
                status=DossierStatus.EXTRACTING_TEXT.value,
                counters=counters,
                document=doc_event,
            )

    # --- 4) Terminé : prêt pour l'étape 1 ---------------------------------------------
    def _finalize() -> dict[str, int]:
        with session_scope() as s:
            dossier = get_dossier(s, dossier_id)
            assert dossier is not None
            recompute_dossier_counters(s, dossier)
            set_dossier_status(s, dossier, DossierStatus.READY_STEP1)
            return _counters(dossier)

    final_counters = await asyncio.to_thread(_finalize)
    await progress_manager.broadcast(
        dossier_id,
        stage="done",
        status=DossierStatus.READY_STEP1.value,
        counters=final_counters,
        message="Ingestion terminée — prêt pour l'étape 1",
    )


def _dedupe_batch_by_hash(
    batch: list[str], hash_by_document_id: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Sépare un lot en (leaders, followers) : un seul document par hash de contenu part en
    traitement concurrent (leader), les autres documents partageant ce même hash (followers)
    sont traités après coup, une fois le cache rempli par leur leader."""
    seen_hashes: set[str] = set()
    leaders: list[str] = []
    followers: list[str] = []
    for doc_id in batch:
        content_hash = hash_by_document_id[doc_id]
        if content_hash in seen_hashes:
            followers.append(doc_id)
        else:
            seen_hashes.add(content_hash)
            leaders.append(doc_id)
    return leaders, followers


def _process_document_text(dossier_id: str, document_id: str, source_dir: Path) -> dict | None:
    """Traite un document (synchrone, exécuté via asyncio.to_thread). Retourne un résumé
    d'évènement à diffuser via WebSocket, ou None si le document n'était pas analysable."""
    with session_scope() as s:
        document = get_document(s, document_id)
        assert document is not None
        if not document.is_analyzable:
            return None
        rel_path = document.relative_path
        category = document.category
        sha256 = document.sha256
        extension = document.extension
        filename = document.filename

        cache_entry, _created = get_or_create_pending_text_cache(s, sha256, extension)
        cache_id = cache_entry.id
        already_done = cache_entry.status == CacheStatus.DONE.value
        cached_method = cache_entry.method
        cached_text_path = cache_entry.text_path

    if already_done:
        # Cache déjà rempli par un document identique (même hash) : on ne ré-extrait pas.
        text = read_text_cache(cached_text_path) if cached_text_path else ""
        with session_scope() as s:
            document = get_document(s, document_id)
            assert document is not None
            set_document_text_result(
                s,
                document,
                text_cache_id=cache_id,
                method=cached_method,
                detected_title=first_nonempty_line(text) if text else None,
                preview_text=text[:1000] if text else None,
                key_mentions=detect_key_mentions(text) if text else None,
                error=None,
            )
        return {
            "id": document_id,
            "filename": filename,
            "relative_path": rel_path,
            "stage": "text_extracted",
            "method": cached_method,
            "from_cache": True,
        }

    path = source_dir / rel_path
    defer_ocr = bool(get_models_config()["text_extraction"].get("defer_ocr_to_extraction", False))
    try:
        outcome = extract_text_for_file(path, category, allow_ocr=not defer_ocr)
    except Exception as exc:
        logger.exception("Échec extraction texte pour %s (%s)", rel_path, document_id)
        with session_scope() as s:
            update_text_cache_result(
                s,
                cache_id,
                method="none",
                text_path=None,
                char_count=0,
                page_count=None,
                avg_confidence=None,
                model_name=None,
                model_version=None,
                pages_meta=None,
                error=str(exc),
            )
            document = get_document(s, document_id)
            assert document is not None
            set_document_text_result(
                s,
                document,
                text_cache_id=cache_id,
                method=None,
                detected_title=None,
                preview_text=None,
                key_mentions=None,
                error=str(exc),
            )
        return {
            "id": document_id,
            "filename": filename,
            "relative_path": rel_path,
            "stage": "error",
            "error": str(exc),
        }

    text_path_rel, _json_rel = write_text_cache_files(sha256, outcome.combined_text, outcome.raw_json)

    with session_scope() as s:
        update_text_cache_result(
            s,
            cache_id,
            method=outcome.method,
            text_path=text_path_rel,
            char_count=outcome.char_count,
            page_count=outcome.page_count,
            avg_confidence=outcome.avg_confidence,
            model_name=outcome.model_name,
            model_version=outcome.model_version,
            pages_meta=outcome.pages_meta,
            error=outcome.error,
        )
        document = get_document(s, document_id)
        assert document is not None
        set_document_text_result(
            s,
            document,
            text_cache_id=cache_id,
            method=outcome.method,
            detected_title=first_nonempty_line(outcome.combined_text) if outcome.combined_text else None,
            preview_text=outcome.combined_text[:1000] if outcome.combined_text else None,
            key_mentions=detect_key_mentions(outcome.combined_text) if outcome.combined_text else None,
            error=outcome.error,
        )

    return {
        "id": document_id,
        "filename": filename,
        "relative_path": rel_path,
        "stage": "error" if outcome.error else "text_extracted",
        "method": outcome.method,
        "avg_confidence": outcome.avg_confidence,
        "error": outcome.error,
        "from_cache": False,
    }
