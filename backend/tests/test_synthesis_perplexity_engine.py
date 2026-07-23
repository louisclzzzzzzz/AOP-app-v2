"""Tests du moteur `app/synthesis_perplexity/engine.py` : sélection/assemblage du contexte
documentaire et du prompt combiné, sans jamais appeler le vrai Deep Research (monkeypatché)."""
from __future__ import annotations

import app.synthesis_perplexity.engine as engine
from app.classify.taxonomy import Taxonomy, TaxonomyCategory
from app.ingestion.document_signal import DocumentSignal
from app.synthesis.schema import SynthesisSchema, SynthesisTopic


def _topic(**overrides) -> SynthesisTopic:
    defaults = dict(
        id="test_topic",
        titre="Thème de test",
        format="prose",
        source="documents",
        extraction_field_ids=[],
        pivot_categories=["TECH/RICT"],
        grounding_field_ids=[],
        cross_document=False,
        instructions="Fais une synthèse.",
    )
    defaults.update(overrides)
    return SynthesisTopic(**defaults)


def _doc(**overrides) -> DocumentSignal:
    defaults = dict(
        document_id="doc-1",
        filename="doc.pdf",
        final_category=None,
        final_lot=None,
        classification_confidence=0.9,
        content_excerpt="",
        ocr_confidence=None,
    )
    defaults.update(overrides)
    return DocumentSignal(**defaults)


def _taxonomy() -> Taxonomy:
    return Taxonomy(
        categories=[
            TaxonomyCategory(path="TECH/RICT", label="RICT", is_pivot=True),
            TaxonomyCategory(path="TECH/PLANS", label="Plans", is_pivot=False),
        ],
        fallback_category="TECH/PLANS",
    )


# --- _select_pivot_documents ---------------------------------------------------------------

def test_select_pivot_documents_dedupes_across_topics_sharing_a_category():
    schema = SynthesisSchema(
        topics=[
            _topic(id="t1", pivot_categories=["TECH/RICT"]),
            _topic(id="t2", pivot_categories=["TECH/RICT"]),
        ]
    )
    doc = _doc(document_id="rict", final_category="TECH/RICT", content_excerpt="x")

    selected = engine._select_pivot_documents(schema, [doc])

    assert [d.document_id for d in selected] == ["rict"]


def test_select_pivot_documents_excludes_non_pivot_and_empty_content():
    schema = SynthesisSchema(topics=[_topic(pivot_categories=["TECH/RICT"])])
    doc_other_category = _doc(document_id="plan", final_category="TECH/PLANS", content_excerpt="x")
    doc_empty = _doc(document_id="empty", final_category="TECH/RICT", content_excerpt="")
    doc_ok = _doc(document_id="ok", final_category="TECH/RICT", content_excerpt="x")

    selected = engine._select_pivot_documents(schema, [doc_other_category, doc_empty, doc_ok])

    assert [d.document_id for d in selected] == ["ok"]


def test_select_pivot_documents_ignores_extraction_fields_topics():
    schema = SynthesisSchema(
        topics=[_topic(source="extraction_fields", extraction_field_ids=["nom_moa"], pivot_categories=[])]
    )
    doc = _doc(final_category="TECH/RICT", content_excerpt="x")

    selected = engine._select_pivot_documents(schema, [doc])

    assert selected == []


# --- _build_documents_context ---------------------------------------------------------------

def test_build_documents_context_excludes_documents_beyond_total_budget():
    doc_a = _doc(document_id="a", filename="a.pdf", content_excerpt="x" * 10)
    doc_b = _doc(document_id="b", filename="b.pdf", content_excerpt="y" * 10)

    context, included = engine._build_documents_context([doc_a, doc_b], total_budget=10, per_document_budget=10)

    assert included == ["a.pdf"]
    assert "b.pdf" not in context


# --- generate_project_synthesis ---------------------------------------------------------------

def test_generate_project_synthesis_no_candidates_skips_deep_research_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("Deep Research ne doit jamais être appelé sans document pivot candidat")

    monkeypatch.setattr(engine, "run_deep_research", _boom)

    schema = SynthesisSchema(topics=[_topic(pivot_categories=["TECH/RICT"])])
    result = engine.generate_project_synthesis(schema, _taxonomy(), documents=[], field_values={})

    assert "Aucun document pivot" in result.report_md
    assert result.model_name is None


def test_generate_project_synthesis_calls_deep_research_with_combined_prompt(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, what):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        return "## Thème de test\n\nContenu rédigé.", ["https://source.example"], "sonar-deep-research-test"

    monkeypatch.setattr(engine, "run_deep_research", _fake)

    schema = SynthesisSchema(
        topics=[
            _topic(id="identite", titre="Identité", source="extraction_fields", extraction_field_ids=["nom_moa"]),
            _topic(id="test_topic", titre="Thème de test", pivot_categories=["TECH/RICT"], grounding_field_ids=["existence_rict"]),
        ]
    )
    doc = _doc(final_category="TECH/RICT", content_excerpt="Avis suspendu n°1 sur les fondations.")
    field_values = {
        "nom_moa": ("Nom du MOA", "Commune de Marly"),
        "existence_rict": ("Existence RICT", "Oui"),
    }

    result = engine.generate_project_synthesis(schema, _taxonomy(), documents=[doc], field_values=field_values)

    assert "Avis suspendu n°1" in captured["user_prompt"]
    assert "Existence RICT : Oui" in captured["user_prompt"]
    assert "Thème de test" in captured["user_prompt"]
    assert "test_topic" not in captured["what"]  # `what` est un libellé générique, pas par thème

    assert "Commune de Marly" in result.report_md  # thème déterministe, sans appel Deep Research
    assert "Contenu rédigé." in result.report_md
    assert result.model_name == "sonar-deep-research-test"
    assert result.documents_used == ["doc.pdf"]
    assert "https://source.example" in result.report_md
    assert "Cartographie des documents pivots" in result.report_md


def test_generate_project_synthesis_notes_documents_dropped_by_budget(monkeypatch):
    def _fake(*, system_prompt, user_prompt, what):
        return "Contenu.", [], "sonar-deep-research-test"

    monkeypatch.setattr(engine, "run_deep_research", _fake)
    monkeypatch.setattr(engine, "DOCUMENTS_TOTAL_CONTEXT_MAX_CHARS", 10)
    monkeypatch.setattr(engine, "_build_documents_context", lambda candidates, **_: ("ctx", [candidates[0].filename]))

    schema = SynthesisSchema(topics=[_topic(pivot_categories=["TECH/RICT"])])
    doc_a = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x")
    doc_b = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="y")

    result = engine.generate_project_synthesis(schema, _taxonomy(), documents=[doc_a, doc_b], field_values={})

    assert result.documents_used == ["a.pdf"]
    assert "budget de contexte atteint" in result.report_md
