from __future__ import annotations

import app.classify.engine as engine
from app.classify.engine import AmbiguousDocument, classify_document_by_rules, classify_documents_batch
from app.store.models import FileCategory


def test_dematerialise_files_are_auto_routed_without_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour un fichier dématérialisé")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document_by_rules(
        relative_path="ENVOI DEMAT/COPIE DEPOT/candidature.cle",
        filename="candidature.cle",
        file_category=FileCategory.DEMATERIALISE.value,
        non_analyzable_reason="Fichier de dépôt dématérialisé (non analysable)",
        content_excerpt="",
    )
    assert outcome is not None
    assert outcome.category == "ENVOI DEMAT/COPIE DEPOT"
    assert outcome.confidence == 1.0
    assert outcome.error is None


def test_extracted_archive_is_auto_routed_to_fallback(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une archive déjà extraite")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document_by_rules(
        relative_path="TECH/OS.zip",
        filename="OS.zip",
        file_category=FileCategory.ARCHIVE.value,
        non_analyzable_reason=None,
        content_excerpt="",
    )
    assert outcome is not None
    assert outcome.category == "AUTRES"
    assert outcome.error is None


def test_system_noise_is_auto_routed(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour du bruit système")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document_by_rules(
        relative_path="TECH/PLANS/Thumbs.db",
        filename="Thumbs.db",
        file_category=FileCategory.OTHER.value,
        non_analyzable_reason="Fichier système (non analysable)",
        content_excerpt="",
    )
    assert outcome is not None
    assert outcome.category == "AUTRES"
    assert outcome.confidence < 1.0


def test_unambiguous_signal_is_classified_by_rule_without_llm(monkeypatch):
    """Nom ET contenu pointent nettement vers la même catégorie (score combiné 2, aucun autre
    candidat) : classable par règles seules, zéro appel LLM (§2 OPTIMISATION.md)."""

    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour un signal net et unique")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = classify_document_by_rules(
        relative_path="ASS/CCAP.pdf",
        filename="CCAP.pdf",
        file_category=FileCategory.PDF.value,
        non_analyzable_reason=None,
        content_excerpt="Cahier des clauses administratives particulières applicable au lot 1.",
    )
    assert outcome is not None
    assert outcome.category == "ASS/CCAP"
    assert outcome.signals["rule"] == "unambiguous_signal"
    assert outcome.model_name is None


def test_ambiguous_document_returns_none_for_rules():
    """"RC 2024.pdf" est volontairement ambigu au niveau nom (existe côté ADMIN et ASS) et le
    contenu seul ne suffit pas (score 1, sous le seuil) : doit rester ambigu (LLM nécessaire)."""
    outcome = classify_document_by_rules(
        relative_path="ADMIN/RC 2024.pdf",
        filename="RC 2024.pdf",
        file_category=FileCategory.PDF.value,
        non_analyzable_reason=None,
        content_excerpt="Règlement de consultation applicable au marché public.",
    )
    assert outcome is None


def test_classify_documents_batch_single_item(monkeypatch):
    captured = {}

    def _fake_call(*, system_prompt, user_prompt, response_model, what, model=None):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        captured["model"] = model
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        decision = response_model(
            items=[
                item_model(
                    index=0,
                    category_path="ADMIN/RC",
                    lot=None,
                    document_type="RC-DCE",
                    normalized_label="RC 2024",
                    confidence=0.9,
                    justification="Le contenu mentionne le règlement de consultation.",
                )
            ]
        )
        return decision, "mistral-small-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    item = AmbiguousDocument(
        relative_path="ADMIN/RC 2024.pdf",
        filename="RC 2024.pdf",
        content_excerpt="Règlement de consultation applicable au marché public.",
        filename_matches=[],
        content_matches=engine.score_content("Règlement de consultation applicable au marché public."),
        lot_signal=None,
    )
    outcomes = classify_documents_batch([item])

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.category == "ADMIN/RC"
    assert outcome.confidence == 0.9
    assert outcome.error is None
    assert outcome.model_name == "mistral-small-test"
    assert outcome.normalized_filename.startswith("ADMIN")
    assert "RC 2024.pdf" in captured["user_prompt"]
    assert outcome.signals["llm_raw"]["category_path"] == "ADMIN/RC"
    # Utilise bien le modèle small dédié à la classification (config/models.yaml), pas le
    # modèle large par défaut de la complétude/extraction.
    assert captured["model"] == "mistral-small-2603"


def test_classify_documents_batch_reassociates_by_index_not_order(monkeypatch):
    """L'ordre des décisions renvoyées par le LLM n'est pas garanti : la réassociation doit se
    faire par `index`, pas par position dans la liste."""

    def _fake_call(*, system_prompt, user_prompt, response_model, what, model=None):
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        decision = response_model(
            items=[
                # Volontairement renvoyés dans le désordre (index 1 avant 0).
                item_model(
                    index=1, category_path="ASS/CCAP", lot=None, document_type="CCAP",
                    normalized_label="CCAP", confidence=0.8, justification="j2",
                ),
                item_model(
                    index=0, category_path="ADMIN/RC", lot=None, document_type="RC-DCE",
                    normalized_label="RC", confidence=0.9, justification="j1",
                ),
            ]
        )
        return decision, "mistral-small-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    items = [
        AmbiguousDocument("ADMIN/RC 2024.pdf", "RC 2024.pdf", "", [], [], None),
        AmbiguousDocument("ASS/CCAP.pdf", "CCAP.pdf", "", [], [], None),
    ]
    outcomes = classify_documents_batch(items)

    assert outcomes[0].category == "ADMIN/RC"
    assert outcomes[1].category == "ASS/CCAP"


def test_classify_documents_batch_falls_back_on_llm_failure(monkeypatch):
    def _fake_call(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    items = [
        AmbiguousDocument("ADMIN/RC 2024.pdf", "RC 2024.pdf", "Contenu illisible", [], [], None),
        AmbiguousDocument("TECH/plan_mystere.pdf", "plan_mystere.pdf", "Contenu illisible", [], [], None),
    ]
    outcomes = classify_documents_batch(items)

    assert len(outcomes) == 2
    for outcome in outcomes:
        assert outcome.category == "AUTRES"
        assert outcome.confidence == 0.0
        assert outcome.error == "API indisponible"


def test_batch_item_llm_can_only_return_a_taxonomy_category():
    from app.classify.taxonomy import load_taxonomy

    taxonomy = load_taxonomy()
    model = engine._batch_item_model_for_categories(taxonomy.paths())
    schema = model.model_json_schema()
    assert set(schema["properties"]["category_path"]["enum"]) == set(taxonomy.paths())
