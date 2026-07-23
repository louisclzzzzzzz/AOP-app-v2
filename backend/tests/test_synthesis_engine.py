from __future__ import annotations

import app.synthesis.engine as engine
from app.classify.taxonomy import Taxonomy, TaxonomyCategory
from app.ingestion.document_signal import DocumentSignal
from app.synthesis.engine import (
    TopicOutcome,
    assemble_report,
    build_documents_cartography,
    generate_topic,
    select_topic_documents,
)
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


# --- select_topic_documents : ordre de priorité + exclusion des documents sans texte -----------

def test_select_topic_documents_orders_by_pivot_category_priority():
    doc_sol = _doc(document_id="sol", final_category="TECH/ETUDE DE SOL", content_excerpt="x")
    doc_rict = _doc(document_id="rict", final_category="TECH/RICT", content_excerpt="x")
    topic = _topic(pivot_categories=["TECH/RICT", "TECH/ETUDE DE SOL"])

    selected = select_topic_documents(topic, [doc_sol, doc_rict])

    assert [d.document_id for d in selected] == ["rict", "sol"]


def test_select_topic_documents_skips_documents_without_content():
    doc_empty = _doc(document_id="empty", final_category="TECH/RICT", content_excerpt="")
    doc_full = _doc(document_id="full", final_category="TECH/RICT", content_excerpt="x")
    topic = _topic(pivot_categories=["TECH/RICT"])

    selected = select_topic_documents(topic, [doc_empty, doc_full])

    assert [d.document_id for d in selected] == ["full"]


# --- generate_topic : source=extraction_fields, aucun appel LLM --------------------------------

def test_generate_topic_extraction_fields_source_never_calls_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour un thème source=extraction_fields")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    topic = _topic(source="extraction_fields", extraction_field_ids=["nom_moa", "adresse_moa"])
    outcome = generate_topic(
        topic,
        documents=[],
        field_values={"nom_moa": ("Nom du MOA", "Commune de Marly"), "adresse_moa": ("Adresse du MOA", "")},
    )

    assert outcome.error is None
    assert outcome.model_name is None
    assert "Commune de Marly" in outcome.content_md
    assert "Adresse du MOA" not in outcome.content_md  # valeur vide, exclue du rendu


def test_generate_topic_extraction_fields_source_handles_no_data():
    topic = _topic(source="extraction_fields", extraction_field_ids=["nom_moa"])
    outcome = generate_topic(topic, documents=[], field_values={})
    assert "Aucune donnée disponible" in outcome.content_md


# --- generate_topic : source=documents, avec appel LLM ------------------------------------------

def test_generate_topic_documents_source_calls_llm_with_context(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        return response_model(contenu="Contenu généré."), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    topic = _topic(pivot_categories=["TECH/RICT"], grounding_field_ids=["existence_rict"])
    doc = _doc(final_category="TECH/RICT", content_excerpt="Avis suspendu n°1 sur les fondations.")

    outcome = generate_topic(
        topic, documents=[doc], field_values={"existence_rict": ("Existence RICT", "Oui")}
    )

    assert outcome.content_md == "Contenu généré."
    assert outcome.model_name == "mistral-large-test"
    assert outcome.error is None
    assert outcome.documents_used == ["doc.pdf"]
    assert "Avis suspendu n°1" in captured["user_prompt"]
    assert "Existence RICT : Oui" in captured["user_prompt"]
    assert "test_topic" in captured["what"]


def test_build_documents_context_excludes_documents_beyond_total_budget():
    doc_a = _doc(document_id="a", filename="a.pdf", content_excerpt="x" * 10)
    doc_b = _doc(document_id="b", filename="b.pdf", content_excerpt="y" * 10)

    context, included = engine._build_documents_context(
        [doc_a, doc_b], total_budget=10, per_document_budget=10
    )

    assert included == ["a.pdf"]
    assert "b.pdf" not in context


def test_generate_topic_documents_used_excludes_candidates_dropped_by_budget(monkeypatch):
    """documents_used ne doit lister que les documents dont le contenu a réellement été inclus
    dans le prompt — pas tous les candidats matchés par catégorie (§bug réel trouvé en testant un
    dossier à 69 candidats CCTP/CCAP dont seuls les 2 premiers tenaient dans le budget)."""

    def _fake_chat(*, system_prompt, user_prompt, response_model, what):
        return response_model(contenu="Contenu généré."), "mistral-large-test"

    def _fake_context(candidates):
        return "contexte tronqué", [candidates[0].filename]

    monkeypatch.setattr(engine, "call_structured_chat", _fake_chat)
    monkeypatch.setattr(engine, "_build_documents_context", _fake_context)

    topic = _topic(pivot_categories=["TECH/RICT"])
    doc_included = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x")
    doc_dropped = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="y")

    outcome = generate_topic(topic, documents=[doc_included, doc_dropped], field_values={})

    assert outcome.documents_used == ["a.pdf"]
    assert outcome.candidates_count == 2


def test_generate_topic_documents_source_no_candidates_skips_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans document pivot candidat")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    topic = _topic(pivot_categories=["TECH/RICT"])
    outcome = generate_topic(topic, documents=[], field_values={})

    assert outcome.error is None
    assert "Aucun document pivot" in outcome.content_md


def test_generate_topic_documents_source_llm_failure_surfaces_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    topic = _topic(pivot_categories=["TECH/RICT"])
    doc = _doc(final_category="TECH/RICT", content_excerpt="contenu")

    outcome = generate_topic(topic, documents=[doc], field_values={})

    assert outcome.content_md is None
    assert outcome.error == "API indisponible"
    assert outcome.documents_used == ["doc.pdf"]


# --- build_documents_cartography ----------------------------------------------------------------

def _taxonomy_with(*, rict_pivot=True, plans_pivot=False) -> Taxonomy:
    return Taxonomy(
        categories=[
            TaxonomyCategory(path="TECH/RICT", label="RICT", is_pivot=rict_pivot),
            TaxonomyCategory(path="TECH/PLANS", label="Plans", is_pivot=plans_pivot),
        ],
        fallback_category="TECH/PLANS",
    )


def test_build_documents_cartography_groups_by_category_and_flags_pivots():
    docs = [
        _doc(document_id="d1", final_category="TECH/RICT"),
        _doc(document_id="d2", final_category="TECH/RICT"),
        _doc(document_id="d3", final_category="TECH/PLANS"),
    ]
    md = build_documents_cartography(docs, _taxonomy_with())

    assert "RICT" in md
    assert "| RICT | 2 | Oui |" in md
    assert "| Plans | 1 | Non |" in md


def test_build_documents_cartography_handles_no_classified_documents():
    md = build_documents_cartography([], _taxonomy_with())
    assert "Aucun document classifié" in md


# --- assemble_report -----------------------------------------------------------------------------

def test_assemble_report_includes_cartography_and_topics_in_schema_order():
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier"), _topic(id="t2", titre="Second")])
    outcomes = [
        TopicOutcome(topic_id="t2", content_md="Contenu 2", model_name=None, error=None),
        TopicOutcome(topic_id="t1", content_md="Contenu 1", model_name=None, error=None),
    ]

    report = assemble_report(outcomes, schema, cartography_md="| a | b |")

    assert report.index("Premier") < report.index("Second")
    assert "Contenu 1" in report
    assert "Contenu 2" in report
    assert "| a | b |" in report


def test_assemble_report_shows_error_note_for_failed_topic():
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier")])
    outcomes = [TopicOutcome(topic_id="t1", content_md=None, model_name=None, error="API indisponible")]

    report = assemble_report(outcomes, schema)

    assert "Section non générée" in report
    assert "API indisponible" in report
