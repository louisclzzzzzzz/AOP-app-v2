from __future__ import annotations

import re

import app.completeness.engine as engine
from app.classify.taxonomy import strip_accents
from app.completeness.engine import DocumentSignal, analyze_pieces
from app.completeness.pieces_checklist import Piece


def _piece(**overrides) -> Piece:
    defaults = dict(
        id="test_piece",
        libelle="Pièce de test",
        phase="A",
        alias=[],
        categorie_attendue=None,
        obligatoire=True,
        peut_etre_inclus_dans_autre=False,
        indices=[],
        par_lot=False,
    )
    defaults.update(overrides)
    return Piece(**defaults)


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


def _analyze_one(piece: Piece, documents: list[DocumentSignal], all_lots: list[str] | None = None):
    """La complétude ne s'analyse plus pièce par pièce (les appels LLM sont désormais groupés
    par document candidat, §4 AUDIT_BACKEND.md) — ce petit adaptateur garde les tests existants
    lisibles pour le cas à une seule pièce."""
    return analyze_pieces(pieces=[piece], documents=documents, all_lots=all_lots)[piece.id]


def test_file_direct_match_does_not_call_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une correspondance fichier directe")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(categorie_attendue="TECH/ETUDE DE SOL", peut_etre_inclus_dans_autre=False)
    doc = _doc(document_id="d1", final_category="TECH/ETUDE DE SOL", classification_confidence=0.95)

    outcome = _analyze_one(piece, [doc])

    assert outcome.match_layer == "file"
    assert outcome.presence == "present"
    assert outcome.certainty == "certain"
    assert outcome.matched_document_ids == ["d1"]
    assert outcome.error is None


def test_absent_without_llm_when_not_included_elsewhere(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé quand peut_etre_inclus_dans_autre=false")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(categorie_attendue="TECH/RICT", peut_etre_inclus_dans_autre=False)
    doc = _doc(final_category="ADMIN/RC")

    outcome = _analyze_one(piece, [doc])

    assert outcome.match_layer == "none"
    assert outcome.presence == "absent"
    # Absence confiante : classification étape 1 déjà validée par un humain, pièce non cherchable ailleurs
    assert outcome.certainty == "certain"


def test_absent_when_no_keyword_candidates_no_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans candidat par mots-clés")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(
        categorie_attendue=None,
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("coût définitif", re.IGNORECASE)],
    )
    doc = _doc(content_excerpt="Ce document ne parle pas du tout du sujet recherché.")

    outcome = _analyze_one(piece, [doc])

    assert outcome.match_layer == "none"
    assert outcome.presence == "absent"
    # Moins confiant : la pièce aurait pu être noyée ailleurs, seule une recherche par
    # mots-clés a été faite
    assert outcome.certainty == "probable"


def test_piece_noyee_dans_un_autre_document_calls_llm_and_confirms(monkeypatch):
    """Cas golden explicitement requis par PLAN.md §9 : une pièce noyée dans un autre
    document (ex. attestation décennale citée dans un marché signé) doit être trouvée via
    recherche par mots-clés + vérification LLM, sans exister comme fichier dédié."""
    captured = {}

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        decision = response_model(
            items=[
                {
                    "piece_id": "attestation_decennale_par_lot",
                    "presence": "present",
                    "confidence": 0.85,
                    "justification": "Le marché signé mentionne explicitement la garantie décennale.",
                    "citation": "l'entreprise justifie d'une assurance responsabilité civile décennale en cours",
                }
            ]
        )
        return decision, "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    piece = _piece(
        id="attestation_decennale_par_lot",
        categorie_attendue="ASS/ATT ASS/ENT",
        peut_etre_inclus_dans_autre=True,
        # Motif accent-plié comme le fait réellement `pieces_checklist._compile` (§strip_accents :
        # un texte natif/OCR perd parfois ses accents, cf. app/classify/taxonomy.py).
        indices=[re.compile(strip_accents("responsabilité civile décennale"), re.IGNORECASE)],
    )
    marche_signe_doc = _doc(
        document_id="marche-1",
        filename="Marche signe entreprise GROS OEUVRE.pdf",
        final_category="ASS/MARCHE SIGNE",
        content_excerpt="Article 12 — l'entreprise justifie d'une assurance responsabilité civile décennale en cours.",
        ocr_confidence=0.92,
    )

    outcome = _analyze_one(piece, [marche_signe_doc])

    assert outcome.match_layer == "llm"
    assert outcome.presence == "present"
    assert outcome.certainty == "certain"
    assert outcome.matched_document_ids == ["marche-1"]
    assert outcome.model_name == "mistral-large-test"
    assert "Marche signe entreprise GROS OEUVRE.pdf" in captured["user_prompt"]


def test_llm_tries_next_candidate_when_first_says_absent(monkeypatch):
    calls = []

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        calls.append(what)
        piece_id = re.search(r'piece_id="([^"]+)"', user_prompt).group(1)
        if "doc-a" in user_prompt:
            item = {"piece_id": piece_id, "presence": "absent", "confidence": 0.8, "justification": "Hors sujet.", "citation": ""}
        else:
            item = {
                "piece_id": piece_id,
                "presence": "present",
                "confidence": 0.9,
                "justification": "Confirmé.",
                "citation": "preuve trouvée",
            }
        return response_model(items=[item]), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    piece = _piece(
        categorie_attendue=None,
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("mot cle", re.IGNORECASE), re.compile("autre mot", re.IGNORECASE)],
    )
    # doc-a matche les 2 indices (score plus haut, essayé en premier) mais le LLM dit absent
    doc_a = _doc(document_id="doc-a", filename="doc-a.pdf", content_excerpt="mot cle et autre mot")
    doc_b = _doc(document_id="doc-b", filename="doc-b.pdf", content_excerpt="mot cle uniquement")

    outcome = _analyze_one(piece, [doc_a, doc_b])

    assert len(calls) == 2
    assert outcome.presence == "present"
    assert outcome.matched_document_ids == ["doc-b"]


def test_shared_candidate_document_is_analyzed_in_a_single_llm_call(monkeypatch):
    """Le point central de la bascule vers le batching (§4 AUDIT_BACKEND.md) : deux pièces
    candidates sur le MÊME document ne doivent déclencher qu'UN SEUL appel LLM, pas deux."""
    calls = []

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        calls.append(what)
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        items = [
            {"piece_id": pid, "presence": "present", "confidence": 0.9, "justification": "Confirmé.", "citation": "preuve"}
            for pid in piece_ids
        ]
        return response_model(items=items), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    piece_a = _piece(
        id="piece_a",
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("mot cle", re.IGNORECASE)],
    )
    piece_b = _piece(
        id="piece_b",
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("mot cle", re.IGNORECASE)],
    )
    doc = _doc(document_id="doc-shared", filename="doc-shared.pdf", content_excerpt="mot cle présent ici")

    outcomes = analyze_pieces(pieces=[piece_a, piece_b], documents=[doc])

    assert len(calls) == 1
    assert outcomes["piece_a"].presence == "present"
    assert outcomes["piece_b"].presence == "present"
    assert outcomes["piece_a"].matched_document_ids == ["doc-shared"]
    assert outcomes["piece_b"].matched_document_ids == ["doc-shared"]


def test_par_lot_coverage_reports_missing_lots(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une correspondance fichier directe")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(
        categorie_attendue="ASS/ATT ASS/ENT",
        peut_etre_inclus_dans_autre=True,
        par_lot=True,
    )
    doc_lot1 = _doc(document_id="d1", final_category="ASS/ATT ASS/ENT", final_lot="1")

    outcome = _analyze_one(piece, [doc_lot1], all_lots=["1", "2"])

    assert outcome.match_layer == "file"
    assert outcome.presence == "partial"
    assert outcome.matched_lots == {"covered": ["1"], "missing": ["2"]}


def test_par_lot_full_coverage_is_present(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une correspondance fichier directe")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(categorie_attendue="ASS/ATT ASS/ENT", peut_etre_inclus_dans_autre=True, par_lot=True)
    doc_lot1 = _doc(document_id="d1", final_category="ASS/ATT ASS/ENT", final_lot="1")
    doc_lot2 = _doc(document_id="d2", final_category="ASS/ATT ASS/ENT", final_lot="2")

    outcome = _analyze_one(piece, [doc_lot1, doc_lot2], all_lots=["1", "2"])

    assert outcome.presence == "present"
    assert outcome.matched_lots == {"covered": ["1", "2"], "missing": []}


def test_low_classification_confidence_downgrades_certainty_to_probable(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé pour une correspondance fichier directe")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    piece = _piece(categorie_attendue="TECH/PLANNING", peut_etre_inclus_dans_autre=False)
    doc = _doc(final_category="TECH/PLANNING", classification_confidence=0.4)

    outcome = _analyze_one(piece, [doc])

    assert outcome.presence == "present"
    assert outcome.certainty == "probable"


def test_llm_failure_on_only_candidate_surfaces_error(monkeypatch):
    def _fake_call(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    piece = _piece(
        categorie_attendue=None,
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("mot cle", re.IGNORECASE)],
    )
    doc = _doc(content_excerpt="mot cle présent ici")

    outcome = _analyze_one(piece, [doc])

    assert outcome.error == "API indisponible"
    assert outcome.presence == "absent"
    assert outcome.certainty == "a_verifier"


def test_confidence_out_of_range_is_clamped(monkeypatch):
    """AUDIT_BACKEND.md §9 : une confiance hors [0, 1] renvoyée par le LLM doit être bornée,
    pas rejetée (ce qui ferait échouer tout l'appel groupé)."""

    def _fake_call(*, system_prompt, user_prompt, response_model, what):
        item = {"piece_id": "test_piece", "presence": "present", "confidence": 95, "justification": "x", "citation": "x"}
        return response_model(items=[item]), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_call)

    piece = _piece(
        categorie_attendue=None,
        peut_etre_inclus_dans_autre=True,
        indices=[re.compile("mot cle", re.IGNORECASE)],
    )
    doc = _doc(content_excerpt="mot cle présent ici")

    outcome = _analyze_one(piece, [doc])

    assert outcome.confidence == 1.0
