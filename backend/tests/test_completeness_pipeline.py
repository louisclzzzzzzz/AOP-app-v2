"""Test unitaire direct de run_completeness_pipeline — vérifie que le regroupement des appels
LLM par document candidat (§4 AUDIT_BACKEND.md) fonctionne de bout en bout à travers le
pipeline complet (statut, compteurs, diffusion, persistance), pas seulement au niveau du
moteur `completeness.engine.analyze_pieces` (déjà couvert par test_completeness_engine.py)."""
from __future__ import annotations

import hashlib
import re

import app.completeness.engine as engine
from app.completeness.pipeline import ensure_checks_initialized, run_completeness_pipeline
from app.ocr.cache import write_text_cache_files
from app.store.db import session_scope
from app.store.models import ClassificationStatus, DossierStatus, FileCategory
from app.store.repository import (
    create_document,
    create_dossier,
    get_dossier,
    get_or_create_pending_text_cache,
    list_completeness_checks,
    set_completeness_selection,
    update_text_cache_result,
)

# Deux pièces réelles de la config (categorie_attendue: null, peut_etre_inclus_dans_autre: true)
# dont les indices peuvent toutes deux matcher le contenu d'UN SEUL document candidat commun.
_PIECE_A = "demande_assurance"  # indices: "extrait k-bis", "k-bis", ...
_PIECE_B = "materiaux_reemploi"  # indices: "matériaux de réemploi", "réemploi"


def _make_document_with_text(session, dossier_id, *, relative_path, text) -> None:
    filename = relative_path.rsplit("/", 1)[-1]
    content_hash = hashlib.sha256(relative_path.encode()).hexdigest()
    cache_entry, _created = get_or_create_pending_text_cache(session, content_hash, ".pdf")
    text_path, _json_path = write_text_cache_files(content_hash, text, None)
    update_text_cache_result(
        session,
        cache_entry.id,
        method="native_pdf",
        text_path=text_path,
        char_count=len(text),
        page_count=1,
        avg_confidence=None,
        model_name=None,
        model_version=None,
        pages_meta=None,
        error=None,
    )
    create_document(
        session,
        dossier_id=dossier_id,
        relative_path=relative_path,
        filename=filename,
        extension=".pdf",
        size_bytes=len(text),
        sha256=content_hash,
        category=FileCategory.PDF.value,
        is_analyzable=True,
        classification_status=ClassificationStatus.PROPOSED.value,
        final_category="ADMIN/AUTRES",
        classification_confidence=0.7,
        text_cache_id=cache_entry.id,
    )


async def test_two_pieces_sharing_one_candidate_document_trigger_a_single_llm_call(isolated_workspace, monkeypatch):
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _make_document_with_text(
            s,
            dossier_id,
            relative_path="ADMIN/dossier_societe.pdf",
            text="Extrait K-BIS de la société jointe. Liste des matériaux de réemploi utilisés.",
        )
        ensure_checks_initialized(s, dossier_id)
        # Ne sélectionner QUE les 2 pièces du test, pour un scénario minimal et lisible.
        for check in list_completeness_checks(s, dossier_id):
            set_completeness_selection(s, check, is_selected=check.piece_id in (_PIECE_A, _PIECE_B))

    calls: list[list[str]] = []

    def _fake(*, system_prompt, user_prompt, response_model, what):
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        calls.append(piece_ids)
        items = [
            {
                "piece_id": pid,
                "presence": "present",
                "confidence": 0.9,
                "justification": "trouvé",
                "citation": "preuve",
            }
            for pid in piece_ids
        ]
        return response_model(items=items), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)
    await run_completeness_pipeline(dossier_id)

    # Le point central : 2 pièces candidates sur le même document -> 1 seul appel LLM, pas 2.
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted([_PIECE_A, _PIECE_B])

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.COMPLETENESS_REVIEW.value
        assert dossier.pieces_checked == 2
        assert dossier.pieces_present == 2

        checks = {c.piece_id: c for c in list_completeness_checks(s, dossier_id)}
        assert checks[_PIECE_A].final_presence == "present"
        assert checks[_PIECE_B].final_presence == "present"
        assert checks[_PIECE_A].match_layer == "llm"
