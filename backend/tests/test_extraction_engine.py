from __future__ import annotations

import re

import app.extraction.engine as engine
from app.extraction.engine import analyze_field
from app.extraction.extraction_schema import ExtractionField
from app.ingestion.document_signal import DocumentSignal


def _field(**overrides) -> ExtractionField:
    defaults = dict(
        id="test_field",
        libelle="Champ de test",
        section="principal",
        resultat_attendu=None,
        reference_categories=[],
        indices=[],
    )
    defaults.update(overrides)
    return ExtractionField(**defaults)


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


def test_reference_category_match_no_cross_check_calls_llm_once(monkeypatch):
    calls = []

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        calls.append(what)
        decision = response_model(
            found=True, value="42 rue de la Paix", confidence=0.9,
            justification="Adresse mentionnée en en-tête.", citation="42 rue de la Paix",
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(reference_categories=["ASS/RC"])
    doc = _doc(final_category="ASS/RC", content_excerpt="Maître d'ouvrage : 42 rue de la Paix")

    outcome = analyze_field(field=field, documents=[doc], cross_check_required=False)

    assert len(calls) == 1
    assert outcome.match_layer == "file"
    assert outcome.value == "42 rue de la Paix"
    assert outcome.cross_check_status == "not_applicable"
    assert outcome.error is None


def test_absent_when_no_reference_and_no_keyword_candidates_no_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans candidat")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    field = _field(reference_categories=["ASS/RC"], indices=[re.compile("mot cle", re.IGNORECASE)])
    doc = _doc(final_category="TECH/PLANNING", content_excerpt="Rien à voir avec la donnée recherchée.")

    outcome = analyze_field(field=field, documents=[doc], cross_check_required=False)

    assert outcome.match_layer == "none"
    assert outcome.value is None
    assert outcome.error is None


def test_intra_document_fallback_finds_value_when_no_reference_document(monkeypatch):
    """Une donnée peut être trouvée par mots-clés dans un document non classé dans une
    catégorie de référence (recherche élargie, §6.3)."""

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        decision = response_model(
            found=True, value="G2 PRO", confidence=0.8,
            justification="Mission G2 PRO mentionnée.", citation="mission G2 PRO réalisée",
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(
        id="etude_de_sol",
        reference_categories=["TECH/ETUDE DE SOL"],
        indices=[re.compile("G2 PRO", re.IGNORECASE)],
    )
    # Document non classé dans TECH/ETUDE DE SOL, mais contient le mot-clé recherché
    doc = _doc(
        document_id="d2", final_category="ASS/CCTP",
        content_excerpt="Le CCTP fait référence à une mission G2 PRO réalisée en amont.",
    )

    outcome = analyze_field(field=field, documents=[doc], cross_check_required=False)

    assert outcome.match_layer == "content"
    assert outcome.value == "G2 PRO"
    assert outcome.sources == [
        {"document_id": "d2", "filename": "doc.pdf", "value": "G2 PRO", "confidence": 0.8}
    ]


def test_cross_check_coherent_when_reference_documents_agree(monkeypatch):
    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        decision = response_model(
            found=True, value="1 000 000 EUR", confidence=0.9,
            justification="Montant HT indiqué.", citation="montant total HT : 1 000 000 EUR",
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(id="montants_totaux_ht", reference_categories=["ASS/RC", "ASS/CCAP"])
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf", final_category="ASS/RC", content_excerpt="montant total HT : 1 000 000 EUR")
    doc_ccap = _doc(document_id="ccap-1", filename="CCAP.pdf", final_category="ASS/CCAP", content_excerpt="montant total HT : 1 000 000 EUR")

    outcome = analyze_field(field=field, documents=[doc_rc, doc_ccap], cross_check_required=True)

    assert outcome.cross_check_status == "coherent"
    assert outcome.value == "1 000 000 EUR"
    assert len(outcome.sources) == 2


def test_cross_check_incoherent_when_reference_documents_disagree(monkeypatch):
    """Cas golden équivalent, pour l'étape 3, à la « pièce noyée » de l'étape 2 : le
    recoupement entre documents de référence doit signaler explicitement une divergence."""

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        if "RC.pdf" in what:
            decision = response_model(
                found=True, value="1 000 000 EUR", confidence=0.9,
                justification="Montant HT indiqué dans le RC.", citation="montant total HT : 1 000 000 EUR",
            )
        else:
            decision = response_model(
                found=True, value="950 000 EUR", confidence=0.85,
                justification="Montant HT indiqué dans le CCAP.", citation="montant total HT : 950 000 EUR",
            )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(id="montants_totaux_ht", reference_categories=["ASS/RC", "ASS/CCAP"])
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf", final_category="ASS/RC", content_excerpt="montant total HT : 1 000 000 EUR")
    doc_ccap = _doc(document_id="ccap-1", filename="CCAP.pdf", final_category="ASS/CCAP", content_excerpt="montant total HT : 950 000 EUR")

    outcome = analyze_field(field=field, documents=[doc_rc, doc_ccap], cross_check_required=True)

    assert outcome.cross_check_status == "incoherent"
    assert outcome.value == "1 000 000 EUR"  # confiance la plus élevée
    assert {s["value"] for s in outcome.sources} == {"1 000 000 EUR", "950 000 EUR"}
    assert "divergentes" in outcome.justification


def test_cross_check_single_source_when_only_one_reference_document(monkeypatch):
    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        decision = response_model(
            found=True, value="1 000 000 EUR", confidence=0.9,
            justification="Montant HT indiqué.", citation="montant total HT : 1 000 000 EUR",
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(id="montants_totaux_ht", reference_categories=["ASS/RC", "ASS/CCAP"])
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf", final_category="ASS/RC", content_excerpt="montant total HT : 1 000 000 EUR")

    outcome = analyze_field(field=field, documents=[doc_rc], cross_check_required=True)

    assert outcome.cross_check_status == "single_source"
    assert outcome.value == "1 000 000 EUR"


def test_llm_failure_surfaces_error(monkeypatch):
    def _fake_call(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    field = _field(reference_categories=["ASS/RC"])
    doc = _doc(final_category="ASS/RC", content_excerpt="contenu quelconque")

    outcome = analyze_field(field=field, documents=[doc], cross_check_required=False)

    assert outcome.error == "API indisponible"
    assert outcome.value is None
