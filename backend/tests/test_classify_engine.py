from __future__ import annotations

import app.classify.engine as engine
from app.classify.engine import classify_document
from app.store.models import FileCategory


def test_dematerialise_files_are_auto_routed_without_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour un fichier dématérialisé")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document(
        relative_path="ENVOI DEMAT/COPIE DEPOT/candidature.cle",
        filename="candidature.cle",
        file_category=FileCategory.DEMATERIALISE.value,
        non_analyzable_reason="Fichier de dépôt dématérialisé (non analysable)",
        content_excerpt="",
    )
    assert outcome.category == "ENVOI DEMAT/COPIE DEPOT"
    assert outcome.confidence == 1.0
    assert outcome.error is None


def test_extracted_archive_is_auto_routed_to_fallback(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une archive déjà extraite")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document(
        relative_path="TECH/OS.zip",
        filename="OS.zip",
        file_category=FileCategory.ARCHIVE.value,
        non_analyzable_reason=None,
        content_excerpt="",
    )
    assert outcome.category == "AUTRES"
    assert outcome.error is None


def test_system_noise_is_auto_routed(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour du bruit système")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document(
        relative_path="TECH/PLANS/Thumbs.db",
        filename="Thumbs.db",
        file_category=FileCategory.OTHER.value,
        non_analyzable_reason="Fichier système (non analysable)",
        content_excerpt="",
    )
    assert outcome.category == "AUTRES"
    assert outcome.confidence < 1.0


def test_analyzable_document_calls_llm_and_builds_normalized_filename(monkeypatch):
    captured = {}

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        decision = response_model(
            category_path="ASS/CCAP",
            lot="1",
            document_type="CCAP",
            normalized_label="CCAP Lot 1",
            confidence=0.92,
            justification="Le contenu mentionne le cahier des clauses administratives particulières.",
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    outcome = classify_document(
        relative_path="ASS/CCAP.pdf",
        filename="CCAP.pdf",
        file_category=FileCategory.PDF.value,
        non_analyzable_reason=None,
        content_excerpt="Cahier des clauses administratives particulières applicable au lot 1.",
    )

    assert outcome.category == "ASS/CCAP"
    assert outcome.lot == "1"
    assert outcome.confidence == 0.92
    assert outcome.error is None
    assert outcome.model_name == "mistral-large-test"
    assert outcome.normalized_filename.startswith("ASS_LOT1_CCAP_")
    assert "CCAP.pdf" in captured["user_prompt"]
    assert outcome.signals["llm_raw"]["category_path"] == "ASS/CCAP"


def test_llm_failure_falls_back_to_fallback_category_without_crashing(monkeypatch):
    def _fake_call(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    outcome = classify_document(
        relative_path="TECH/plan_mystere.pdf",
        filename="plan_mystere.pdf",
        file_category=FileCategory.PDF.value,
        non_analyzable_reason=None,
        content_excerpt="Contenu illisible",
    )

    assert outcome.category == "AUTRES"
    assert outcome.confidence == 0.0
    assert outcome.error == "API indisponible"


def test_llm_can_only_return_a_taxonomy_category():
    from app.classify.taxonomy import load_taxonomy

    taxonomy = load_taxonomy()
    model = engine._response_model_for_categories(taxonomy.paths())
    schema = model.model_json_schema()
    assert set(schema["properties"]["category_path"]["enum"]) == set(taxonomy.paths())
