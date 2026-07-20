from __future__ import annotations

import re

import app.extraction.engine as engine
from app.extraction.engine import (
    DocumentExtractionResult,
    absent_outcome,
    analyze_document,
    generate_synthesis,
    layer2_candidates,
    plan_layer2_calls,
    plan_manual_calls,
    plan_reference_document_calls,
    reference_candidates,
    resolve_field,
)
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


def _decision_item(response_model, **kwargs):
    item_model = response_model.model_fields["items"].annotation.__args__[0]
    return item_model(**kwargs)


class _FakeDecision:
    """Item de décision minimal (duck-typing) pour tester `resolve_field` sans repasser par un
    vrai appel LLM ni par le modèle Pydantic dynamique."""

    def __init__(self, *, found: bool, value: str, confidence: float, justification: str = "j", citation: str = "c"):
        self.found = found
        self.value = value
        self.confidence = confidence
        self.justification = justification
        self.citation = citation


# --- analyze_document : un seul appel LLM pour plusieurs champs --------------------------------

def test_analyze_document_covers_multiple_fields_in_one_call(monkeypatch):
    calls = []

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        calls.append(what)
        items = [
            _decision_item(
                response_model, field_id="nom_moa", found=True, value="Commune de Marly",
                confidence=0.9, justification="Maître d'ouvrage cité.", citation="Commune de Marly",
            ),
            _decision_item(
                response_model, field_id="montants_totaux_ht", found=True, value="1 000 000 EUR",
                confidence=0.85, justification="Montant HT indiqué.", citation="1 000 000 EUR",
            ),
        ]
        return response_model(items=items), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    fields = [
        _field(id="nom_moa", reference_categories=["ASS/RC"]),
        _field(id="montants_totaux_ht", reference_categories=["ASS/RC"]),
    ]
    doc = _doc(
        final_category="ASS/RC",
        content_excerpt="Maître d'ouvrage : Commune de Marly. Montant total HT : 1 000 000 EUR.",
    )

    result = analyze_document(doc, fields)

    assert len(calls) == 1  # un seul appel pour couvrir les 2 champs de ce document
    assert set(result.decisions.keys()) == {"nom_moa", "montants_totaux_ht"}
    assert result.decisions["nom_moa"].value == "Commune de Marly"
    assert result.decisions["montants_totaux_ht"].value == "1 000 000 EUR"
    assert result.error is None


def test_analyze_document_no_fields_skips_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans champ à demander")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    result = analyze_document(_doc(), [])
    assert result.decisions == {}
    assert result.error is None


def test_analyze_document_empty_content_excerpt_skips_llm_call(monkeypatch):
    """OCR différé (§5 OPTIMISATION.md phase 4) : si le texte est toujours vide après
    `ensure_document_ocr` (échec d'OCR, page réellement blanche), inutile d'appeler le LLM."""

    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé avec un contenu vide")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    result = analyze_document(
        _doc(final_category="ASS/RC", content_excerpt=""),
        [_field(reference_categories=["ASS/RC"])],
    )
    assert result.decisions == {}
    assert result.error is None


def test_analyze_document_llm_failure_surfaces_error(monkeypatch):
    def _fake_call(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    result = analyze_document(
        _doc(final_category="ASS/RC", content_excerpt="contenu"),
        [_field(reference_categories=["ASS/RC"])],
    )
    assert result.error == "API indisponible"
    assert result.decisions == {}


# --- plan_reference_document_calls : regroupement par document ---------------------------------

def test_plan_reference_document_calls_groups_fields_by_document():
    doc_rc = _doc(document_id="rc-1", final_category="ASS/RC", content_excerpt="contenu")
    doc_other = _doc(document_id="other-1", final_category="TECH/PLANNING", content_excerpt="contenu")
    fields = [
        _field(id="f1", reference_categories=["ASS/RC"]),
        _field(id="f2", reference_categories=["ASS/RC"]),
        _field(id="f3", reference_categories=["ASS/CCAP"]),
    ]

    calls = plan_reference_document_calls(fields, [doc_rc, doc_other])

    assert len(calls) == 1  # un seul appel prévu, pas un par champ
    doc, fields_for_doc = calls[0]
    assert doc.document_id == "rc-1"
    assert {f.id for f in fields_for_doc} == {"f1", "f2"}


def test_plan_reference_document_calls_includes_document_with_pending_ocr():
    """OCR différé (§5 OPTIMISATION.md phase 4) : un document de référence sans texte pour
    l'instant (content_excerpt vide) doit quand même être proposé à l'appel — c'est
    `ensure_document_ocr`, exécuté juste avant, qui a la charge de combler ce vide."""
    doc_rc = _doc(document_id="rc-1", final_category="ASS/RC", content_excerpt="")
    fields = [_field(id="f1", reference_categories=["ASS/RC"])]

    calls = plan_reference_document_calls(fields, [doc_rc])

    assert len(calls) == 1
    doc, fields_for_doc = calls[0]
    assert doc.document_id == "rc-1"
    assert {f.id for f in fields_for_doc} == {"f1"}


# --- plan_manual_calls : sélection manuelle de documents ----------------------------------------

def test_plan_manual_calls_requests_all_fields_regardless_of_category():
    """Sélection manuelle : pas de filtrage par catégorie — chaque document sélectionné est
    interrogé pour TOUS les champs du schéma."""
    doc = _doc(document_id="d1", final_category="TECH/PLANNING", content_excerpt="contenu")
    fields = [_field(id="f1", reference_categories=["ASS/RC"]), _field(id="f2", reference_categories=["ASS/CCAP"])]

    calls = plan_manual_calls(fields, [doc])

    assert len(calls) == 1
    called_doc, fields_for_doc = calls[0]
    assert called_doc.document_id == "d1"
    assert {f.id for f in fields_for_doc} == {"f1", "f2"}


def test_plan_manual_calls_skips_documents_without_content():
    doc_empty = _doc(document_id="d1", content_excerpt="")
    doc_full = _doc(document_id="d2", content_excerpt="contenu")

    calls = plan_manual_calls([_field()], [doc_empty, doc_full])

    assert [doc.document_id for doc, _ in calls] == ["d2"]


# --- layer2_candidates / plan_layer2_calls : approfondissement ponctuel -------------------------

def test_layer2_candidates_scores_by_number_of_distinct_patterns_matched():
    """`_score_candidate` compte le nombre de motifs DISTINCTS qui matchent (pas le nombre
    d'occurrences) — un document qui matche 2 motifs sur 2 doit passer avant un qui n'en
    matche qu'un seul."""
    doc_strong = _doc(document_id="strong", content_excerpt="mission G2 PRO réalisée")
    doc_weak = _doc(document_id="weak", content_excerpt="G2 PRO uniquement")
    field = _field(indices=[re.compile("G2 PRO", re.IGNORECASE), re.compile("mission", re.IGNORECASE)])

    candidates = layer2_candidates(field, [doc_weak, doc_strong])

    assert [d.document_id for d in candidates] == ["strong", "weak"]


def test_layer2_candidates_capped_at_max_llm_candidates():
    field = _field(indices=[re.compile("G2 PRO", re.IGNORECASE)])
    docs = [_doc(document_id=f"d{i}", content_excerpt="G2 PRO") for i in range(5)]

    candidates = layer2_candidates(field, docs)

    assert len(candidates) == engine.MAX_LLM_CANDIDATES


def test_plan_layer2_calls_groups_missing_fields_by_scored_candidate():
    doc = _doc(document_id="d1", content_excerpt="mission G2 PRO réalisée")
    field = _field(id="etude_de_sol", indices=[re.compile("G2 PRO", re.IGNORECASE)])

    calls = plan_layer2_calls([field], [doc])

    assert len(calls) == 1
    called_doc, fields_for_doc = calls[0]
    assert called_doc.document_id == "d1"
    assert fields_for_doc == [field]


# --- resolve_field : dérivation à partir des appels déjà passés --------------------------------

def test_resolve_field_sequential_first_confirming_candidate_wins():
    doc1 = _doc(document_id="d1", filename="a.pdf")
    doc2 = _doc(document_id="d2", filename="b.pdf")
    field = _field(id="f1")

    results = {
        "d1": DocumentExtractionResult(document_id="d1", decisions={"f1": _FakeDecision(found=False, value="", confidence=0.0)}),
        "d2": DocumentExtractionResult(document_id="d2", decisions={"f1": _FakeDecision(found=True, value="42", confidence=0.8)}),
    }

    outcome = resolve_field(
        field, candidates=[doc1, doc2], results_by_document=results, match_layer="file", cross_check_required=False
    )

    assert outcome is not None
    assert outcome.value == "42"
    assert outcome.sources == [{"document_id": "d2", "filename": "b.pdf", "value": "42", "confidence": 0.8}]
    assert outcome.cross_check_status == "not_applicable"


def test_resolve_field_returns_none_when_nothing_found_and_no_error():
    field = _field(id="f1")
    doc = _doc(document_id="d1")
    results = {"d1": DocumentExtractionResult(document_id="d1", decisions={})}

    outcome = resolve_field(
        field, candidates=[doc], results_by_document=results, match_layer="file", cross_check_required=False
    )
    assert outcome is None


def test_resolve_field_surfaces_error_when_call_failed():
    field = _field(id="f1")
    doc = _doc(document_id="d1")
    results = {"d1": DocumentExtractionResult(document_id="d1", error="API indisponible")}

    outcome = resolve_field(
        field, candidates=[doc], results_by_document=results, match_layer="file", cross_check_required=False
    )
    assert outcome is not None
    assert outcome.value is None
    assert outcome.error is not None


def test_resolve_field_cross_check_coherent_when_reference_documents_agree():
    field = _field(id="montants_totaux_ht")
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf")
    doc_ccap = _doc(document_id="ccap-1", filename="CCAP.pdf")
    results = {
        "rc-1": DocumentExtractionResult(document_id="rc-1", decisions={"montants_totaux_ht": _FakeDecision(found=True, value="1 000 000 EUR", confidence=0.9)}),
        "ccap-1": DocumentExtractionResult(document_id="ccap-1", decisions={"montants_totaux_ht": _FakeDecision(found=True, value="1 000 000 EUR", confidence=0.85)}),
    }

    outcome = resolve_field(
        field, candidates=[doc_rc, doc_ccap], results_by_document=results, match_layer="file", cross_check_required=True
    )

    assert outcome is not None
    assert outcome.cross_check_status == "coherent"
    assert outcome.value == "1 000 000 EUR"
    assert len(outcome.sources) == 2


def test_resolve_field_cross_check_incoherent_when_reference_documents_disagree():
    """Cas golden équivalent, pour l'étape 3, à la « pièce noyée » de l'étape 2 : le
    recoupement entre documents de référence doit signaler explicitement une divergence."""
    field = _field(id="montants_totaux_ht")
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf")
    doc_ccap = _doc(document_id="ccap-1", filename="CCAP.pdf")
    results = {
        "rc-1": DocumentExtractionResult(document_id="rc-1", decisions={"montants_totaux_ht": _FakeDecision(found=True, value="1 000 000 EUR", confidence=0.9)}),
        "ccap-1": DocumentExtractionResult(document_id="ccap-1", decisions={"montants_totaux_ht": _FakeDecision(found=True, value="950 000 EUR", confidence=0.85)}),
    }

    outcome = resolve_field(
        field, candidates=[doc_rc, doc_ccap], results_by_document=results, match_layer="file", cross_check_required=True
    )

    assert outcome is not None
    assert outcome.cross_check_status == "incoherent"
    assert outcome.value == "1 000 000 EUR"  # confiance la plus élevée
    assert {s["value"] for s in outcome.sources} == {"1 000 000 EUR", "950 000 EUR"}
    assert "divergentes" in outcome.justification


def test_resolve_field_cross_check_single_source_when_only_one_reference_document():
    field = _field(id="montants_totaux_ht")
    doc_rc = _doc(document_id="rc-1", filename="RC.pdf")
    results = {
        "rc-1": DocumentExtractionResult(document_id="rc-1", decisions={"montants_totaux_ht": _FakeDecision(found=True, value="1 000 000 EUR", confidence=0.9)}),
    }

    outcome = resolve_field(
        field, candidates=[doc_rc], results_by_document=results, match_layer="file", cross_check_required=True
    )

    assert outcome is not None
    assert outcome.cross_check_status == "single_source"
    assert outcome.value == "1 000 000 EUR"


def test_reference_candidates_orders_by_category_priority():
    doc_a = _doc(document_id="a", final_category="ASS/CCAP", content_excerpt="x")
    doc_b = _doc(document_id="b", final_category="ASS/RC", content_excerpt="x")
    field = _field(reference_categories=["ASS/RC", "ASS/CCAP"])

    candidates = reference_candidates(field, [doc_a, doc_b])

    assert [d.document_id for d in candidates] == ["b", "a"]


def test_absent_outcome_has_no_llm_call_and_no_value():
    outcome = absent_outcome("rien trouvé")
    assert outcome.value is None
    assert outcome.match_layer == "none"
    assert outcome.error is None
    assert outcome.justification == "rien trouvé"


# --- Sélection de contexte par pertinence (§3 OPTIMISATION.md) ----------------------------------

def test_select_relevant_excerpt_keeps_relevant_paragraph_over_head_of_document():
    filler = "Texte sans rapport rempli de mots quelconques pour occuper de la place. " * 20
    relevant = "Montant total HT : 1 234 567 EUR pour le present marche."
    text = f"{filler}\n\n{relevant}\n\n{filler}"
    field = _field(
        id="montants_totaux_ht", libelle="Montant total HT",
        indices=[re.compile("montant total", re.IGNORECASE)],
    )
    doc = _doc(content_excerpt=text)

    excerpt = engine._select_relevant_excerpt(doc, [field], max_chars=200)

    assert "1 234 567 EUR" in excerpt
    assert excerpt != text[:200]


def test_select_relevant_excerpt_returns_full_text_when_under_budget():
    text = "Court texte."
    doc = _doc(content_excerpt=text)
    excerpt = engine._select_relevant_excerpt(doc, [_field()], max_chars=200)
    assert excerpt == text


# --- Synthèse textuelle (§9 AUDIT_BACKEND.md : aucun test avant) --------------------------------

def test_generate_synthesis_returns_none_without_any_field_value_and_no_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans aucune valeur à synthétiser")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    assert generate_synthesis([]) is None


def test_generate_synthesis_returns_text_and_model_name_on_success(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        return response_model(synthese="Synthèse générée."), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    outcome = generate_synthesis([("Montant total HT", "1 234 567 EUR")])

    assert outcome is not None
    assert outcome.text == "Synthèse générée."
    assert outcome.model_name == "mistral-large-test"
    assert "Montant total HT" in captured["user_prompt"]


def test_generate_synthesis_returns_none_on_llm_failure_without_raising(monkeypatch):
    """Best-effort : un échec de la synthèse ne doit jamais bloquer la validation du
    checkpoint étape 3 (cf. docstring de `generate_synthesis`)."""

    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    assert generate_synthesis([("Montant total HT", "1 234 567 EUR")]) is None
