"""Recherche sémantique de la couche 2 (`app/extraction/semantic_retrieval.py`).

Les vecteurs sont fabriqués localement (`_fake_embeddings`) : aucun appel réseau, et surtout un
espace sémantique dont on maîtrise la géométrie — c'est le seul moyen de tester « le document dit
la même chose avec d'autres mots » de façon déterministe.
"""
from __future__ import annotations

import math
import re

import pytest

import app.extraction.semantic_retrieval as sr
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


def _doc(document_id: str, content: str, filename: str | None = None) -> DocumentSignal:
    return DocumentSignal(
        document_id=document_id,
        filename=filename or f"{document_id}.pdf",
        final_category=None,
        final_lot=None,
        classification_confidence=0.9,
        content_excerpt=content,
        ocr_confidence=None,
    )


# Axes du faux espace vectoriel : un texte est vectorisé sur la présence de ces thèmes, quels que
# soient les mots employés — deux formulations d'un même thème ont donc le même vecteur.
_THEMES = {
    "sol": ("stratigraphie", "nature des terrains", "sondages pressiométriques", "géotechnique"),
    "prix": ("montant", "euros", "prix global", "rémunération"),
    "delai": ("délai", "calendrier", "planning"),
}


def _fake_vector(text: str) -> list[float]:
    lowered = text.lower()
    vector = [float(sum(lowered.count(term) for term in terms)) for terms in _THEMES.values()]
    # Composante constante : un texte sans aucun thème reste un vecteur non nul (norme 1), donc
    # comparable, au lieu de produire une division par zéro.
    return vector + [0.1]


def _fake_embeddings(texts, *, what):
    return [_fake_vector(t) for t in texts]


@pytest.fixture
def semantic_enabled(tmp_path, monkeypatch):
    """Active les embeddings avec un faux vectoriseur et un workspace jetable (cache de vecteurs)."""
    monkeypatch.setenv("AOP_WORKSPACE_DIR", str(tmp_path))
    from app.settings import get_models_config, get_settings

    get_settings.cache_clear()
    get_models_config.cache_clear()
    monkeypatch.setattr(sr, "call_embeddings", _fake_embeddings)
    yield
    get_settings.cache_clear()
    get_models_config.cache_clear()


# --- Requête d'un champ -------------------------------------------------------------------------

def test_field_query_text_strips_regex_syntax_from_indices():
    """Les `indices` sont des regex : leur syntaxe n'a aucun sens sémantique et brouillerait le
    vecteur de la requête."""
    field = _field(
        libelle="Garanties demandées",
        resultat_attendu="TRC = tous risques chantier",
        indices=[
            re.compile(r"(?<![A-Za-z0-9])TRC(?![A-Za-z0-9])"),
            re.compile(r"dommage[s]? ouvrage"),
            re.compile(r"montant.{0,15}garantie"),
        ],
    )

    query = sr.field_query_text(field)

    assert "Garanties demandées" in query
    assert "tous risques chantier" in query
    assert "TRC" in query
    assert "dommage ouvrage" in query
    assert "montant garantie" in query
    for noise in ("(?<!", "[s]?", ".{0,15}", "\\b"):
        assert noise not in query


# --- Découpage --------------------------------------------------------------------------------

def test_chunk_text_respects_paragraphs_and_budget():
    text = "\n\n".join(["a" * 100, "b" * 100, "c" * 100])
    assert sr.chunk_text(text, 250) == ["a" * 100 + "\n\n" + "b" * 100, "c" * 100]


def test_chunk_text_splits_paragraph_longer_than_budget():
    """Un paragraphe hors gabarit doit être tranché : l'API refuse les entrées trop longues, et un
    morceau coupé au milieu d'une phrase reste vectorisable."""
    chunks = sr.chunk_text("x" * 250, 100)
    assert chunks == ["x" * 100, "x" * 100, "x" * 50]


def test_document_score_is_its_best_chunk(semantic_enabled):
    """Score = meilleur morceau, jamais la moyenne : une donnée rare tient dans un passage, et une
    moyenne pénaliserait les documents longs — précisément ceux qui contiennent ces données."""
    noise = "\n\n".join(["texte sans rapport aucun." * 20] * 6)
    doc = _doc("doc-long", noise + "\n\nLa stratigraphie des terrains est décrite ici.")
    index = sr.build_semantic_index([doc])
    query = sr._unit(_fake_vector("stratigraphie"))

    assert len(index.vectors_by_document["doc-long"]) > 1
    assert index.similarity("doc-long", query) > 0.9


# --- Le cas qui motive la fonctionnalité --------------------------------------------------------

def test_document_without_any_keyword_match_is_selected_when_semantically_relevant(semantic_enabled):
    """L'angle mort corrigé : le document contient la réponse mais l'exprime avec d'autres mots que
    les `indices` du champ. Le scoring par mots-clés lui donne 0 et l'écarte définitivement ; la
    recherche sémantique doit le faire remonter."""
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    blind_spot = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")
    unrelated = [_doc(f"doc-{i}", "Le délai contractuel figure au planning.") for i in range(3)]

    from app.extraction.engine import layer2_candidates

    assert layer2_candidates(field, [blind_spot, *unrelated]) == []  # ancien comportement

    selected = sr.select_layer2_candidates([field], [blind_spot, *unrelated])

    assert [d.document_id for d in selected["stratigraphie"].candidates][0] == "doc-g2"


def test_keyword_hit_stays_ahead_of_semantic_only_candidate(semantic_enabled):
    """La précision des mots-clés n'est pas sacrifiée : un document qui contient littéralement
    l'indice reste en tête (donc `resolve_field` retient sa valeur en priorité), le candidat
    purement sémantique complète la sélection."""
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    literal = _doc("doc-cctp", "La stratigraphie du sol est détaillée au chapitre 3.")
    semantic_only = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")

    selected = sr.select_layer2_candidates([field], [semantic_only, literal])

    assert [d.document_id for d in selected["stratigraphie"].candidates] == ["doc-cctp", "doc-g2"]


def test_keyword_candidates_are_never_displaced(semantic_enabled):
    """LA propriété qui compte : la sélection d'avant reste un SOUS-ENSEMBLE de la nouvelle.

    Mesuré sur le dossier réel dce_grand_pic2, une fusion à plafond constant délogeait un document
    mots-clés porteur de la réponse et faisait régresser un champ déjà correct. Un run automatique
    ne doit jamais perdre ce qu'il trouvait avant."""
    from app.extraction.engine import layer2_candidates

    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    keyword_docs = [_doc(f"kw-{i}", f"La stratigraphie, variante {i}.") for i in range(5)]
    semantic_docs = [_doc(f"sem-{i}", f"Nature des terrains, sondages pressiométriques {i}.") for i in range(5)]
    documents = [*semantic_docs, *keyword_docs]

    before = [d.document_id for d in layer2_candidates(field, documents)]
    after = [d.document_id for d in sr.select_layer2_candidates([field], documents)["stratigraphie"].candidates]

    assert after[: len(before)] == before  # inchangés, et toujours en tête
    assert set(before) <= set(after)


def test_selection_adds_a_bounded_number_of_semantic_candidates(semantic_enabled):
    """Le surcoût est borné et explicite : `MAX_LLM_CANDIDATES` mots-clés + au plus
    `extra_semantic_candidates` documents supplémentaires."""
    from app.extraction.engine import MAX_LLM_CANDIDATES
    from app.settings import get_models_config

    extra = get_models_config()["embeddings"]["extra_semantic_candidates"]
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    docs = [_doc(f"doc-{i}", f"La stratigraphie et la nature des terrains, variante {i}.") for i in range(12)]

    selected = sr.select_layer2_candidates([field], docs)

    assert len(selected["stratigraphie"].candidates) == MAX_LLM_CANDIDATES + extra


def test_extra_semantic_candidates_zero_disables_the_semantic_contribution(semantic_enabled, monkeypatch):
    from app.settings import get_models_config

    monkeypatch.setitem(get_models_config()["embeddings"], "extra_semantic_candidates", 0)
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    blind_spot = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")

    assert sr.select_layer2_candidates([field], [blind_spot])["stratigraphie"].candidates == []


def test_min_similarity_floor_filters_out_distant_documents(semantic_enabled, monkeypatch):
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    far_away = _doc("doc-planning", "Le délai contractuel figure au planning.")

    from app.settings import get_models_config

    config = get_models_config()
    monkeypatch.setitem(config["embeddings"], "min_similarity", 0.9)

    assert sr.select_layer2_candidates([field], [far_away])["stratigraphie"].candidates == []


# --- Dégradation : jamais d'échec de run à cause des embeddings ---------------------------------

def test_falls_back_to_keyword_ranking_when_embeddings_fail(semantic_enabled, monkeypatch):
    """Un run d'extraction ne doit jamais échouer parce que l'API embeddings est indisponible : la
    couche 2 redevient simplement ce qu'elle était."""

    def _boom(texts, *, what):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(sr, "call_embeddings", _boom)
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    literal = _doc("doc-cctp", "La stratigraphie du sol est détaillée au chapitre 3.")
    semantic_only = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")

    selected = sr.select_layer2_candidates([field], [semantic_only, literal])

    assert [d.document_id for d in selected["stratigraphie"].candidates] == ["doc-cctp"]


def test_disabled_in_config_keeps_keyword_only_selection(semantic_enabled, monkeypatch):
    def _boom(texts, *, what):
        raise AssertionError("aucune vectorisation ne doit avoir lieu quand l'option est désactivée")

    from app.settings import get_models_config

    monkeypatch.setitem(get_models_config()["embeddings"], "enabled", False)
    monkeypatch.setattr(sr, "call_embeddings", _boom)
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    literal = _doc("doc-cctp", "La stratigraphie du sol est détaillée au chapitre 3.")
    semantic_only = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")

    selected = sr.select_layer2_candidates([field], [semantic_only, literal])

    assert [d.document_id for d in selected["stratigraphie"].candidates] == ["doc-cctp"]


def test_documents_without_text_are_ignored(semantic_enabled):
    """Documents dont l'OCR est encore différé (texte vide) : rien à vectoriser, pas de plantage."""
    index = sr.build_semantic_index([_doc("doc-vide", "")])
    assert index.vectors_by_document == {}
    assert index.similarity("doc-vide", sr._unit(_fake_vector("stratigraphie"))) == -1.0


# --- Cache de vecteurs --------------------------------------------------------------------------

def test_vectors_are_cached_by_text_hash_across_runs(semantic_enabled, monkeypatch):
    """Le cache est clé par HASH DU TEXTE (comme le cache de texte) : un second run sur le même
    dossier, ou un autre dossier contenant le même document, ne re-vectorise rien."""
    calls: list[int] = []

    def _counting(texts, *, what):
        calls.append(len(texts))
        return _fake_embeddings(texts, what=what)

    monkeypatch.setattr(sr, "call_embeddings", _counting)
    docs = [_doc("doc-1", "La stratigraphie du sol est détaillée au chapitre 3.")]

    sr.build_semantic_index(docs)
    assert calls == [1]

    sr.build_semantic_index([_doc("doc-2", docs[0].content_excerpt)])
    assert calls == [1]  # aucun nouvel appel : même texte, donc même hash


def test_identical_texts_are_vectorised_once(semantic_enabled, monkeypatch):
    calls: list[list[str]] = []

    def _counting(texts, *, what):
        calls.append(list(texts))
        return _fake_embeddings(texts, what=what)

    monkeypatch.setattr(sr, "call_embeddings", _counting)
    content = "Nature des terrains reconnue par sondages pressiométriques."

    index = sr.build_semantic_index([_doc("doc-1", content), _doc("doc-2", content)])

    assert calls == [[content]]
    assert index.vectors_by_document["doc-1"] == index.vectors_by_document["doc-2"]


def test_cache_is_invalidated_when_the_model_changes(semantic_enabled, monkeypatch):
    from app.settings import get_models_config

    doc = _doc("doc-1", "La stratigraphie du sol est détaillée au chapitre 3.")
    sr.build_semantic_index([doc])

    calls: list[str] = []

    def _counting(texts, *, what):
        calls.append(what)
        return _fake_embeddings(texts, what=what)

    monkeypatch.setattr(sr, "call_embeddings", _counting)
    monkeypatch.setitem(get_models_config()["embeddings"], "model", "un-autre-modele")

    sr.build_semantic_index([doc])
    assert len(calls) == 1


# --- Composition -----------------------------------------------------------------------------

def test_append_semantic_candidates_keeps_keyword_order_then_adds():
    keyword = [_doc(f"k{i}", "x") for i in range(3)]
    semantic = [_doc(f"s{i}", "x") for i in range(3)]
    composed = sr.append_semantic_candidates(keyword, semantic, keyword_limit=3, extra=2)
    assert [d.document_id for d in composed] == ["k0", "k1", "k2", "s0", "s1"]


def test_append_semantic_candidates_does_not_duplicate_a_shared_document():
    shared = _doc("commun", "x")
    keyword = [shared, _doc("k1", "x")]
    semantic = [shared, _doc("s1", "x")]
    composed = sr.append_semantic_candidates(keyword, semantic, keyword_limit=3, extra=2)
    assert [d.document_id for d in composed] == ["commun", "k1", "s1"]


def test_append_semantic_candidates_fills_everything_when_keywords_find_nothing():
    """Cas de l'angle mort total : aucun document ne contient d'indice du champ."""
    semantic = [_doc(f"s{i}", "x") for i in range(4)]
    composed = sr.append_semantic_candidates([], semantic, keyword_limit=3, extra=2)
    assert [d.document_id for d in composed] == ["s0", "s1"]


def test_cosine_of_identical_unit_vectors_is_one():
    vector = sr._unit([3.0, 4.0])
    assert math.isclose(sr._cosine(vector, vector), 1.0, rel_tol=1e-6)


# --- Traçabilité de l'origine sémantique --------------------------------------------------------

def _outcome(value, sources):
    from app.extraction.engine import ExtractionOutcome

    return ExtractionOutcome(
        match_layer="content", value=value, confidence=0.7, justification="Trouvé.", citation="cite",
        sources=sources, cross_check_status=None, model_name="m", model_version="v", error=None,
    )


def test_selection_flags_documents_that_only_the_semantic_search_proposed(semantic_enabled):
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    literal = _doc("doc-cctp", "La stratigraphie du sol est détaillée au chapitre 3.")
    semantic_only = _doc("doc-g2", "Nature des terrains reconnue par sondages pressiométriques.")

    selection = sr.select_layer2_candidates([field], [semantic_only, literal])["stratigraphie"]

    assert selection.semantic_only == frozenset({"doc-g2"})


def test_a_keyword_document_pushed_out_of_the_top_is_not_flagged_semantic(semantic_enabled):
    """Un document contenant un indice du champ, même mal classé, n'est pas un rapprochement par
    le sens — le marquer comme tel serait un faux signal pour l'expert."""
    field = _field(id="stratigraphie", libelle="Stratigraphie", indices=[re.compile(r"stratigraphie")])
    docs = [_doc(f"kw-{i}", f"La stratigraphie, variante {i}.") for i in range(6)]

    selection = sr.select_layer2_candidates([field], docs)["stratigraphie"]

    assert selection.semantic_only == frozenset()


def test_mark_semantic_origin_flags_the_source_and_the_justification():
    selection = sr.Layer2Selection(candidates=[], semantic_only=frozenset({"doc-g2"}))
    marked = sr.mark_semantic_origin(
        _outcome("Marne argileuse", [{"document_id": "doc-g2", "filename": "g2.pdf", "value": "x", "confidence": 0.7}]),
        selection,
    )

    assert marked.sources[0]["selection"] == "semantic"
    assert marked.justification.startswith("[Document rapproché par recherche sémantique")
    assert "Trouvé." in marked.justification


def test_mark_semantic_origin_leaves_a_keyword_sourced_value_untouched():
    selection = sr.Layer2Selection(candidates=[], semantic_only=frozenset({"doc-g2"}))
    outcome = _outcome("Marne", [{"document_id": "doc-cctp", "filename": "cctp.pdf", "value": "x", "confidence": 0.9}])

    marked = sr.mark_semantic_origin(outcome, selection)

    assert marked is outcome  # aucune copie, aucun marquage


def test_mark_semantic_origin_ignores_an_absent_field():
    selection = sr.Layer2Selection(candidates=[], semantic_only=frozenset({"doc-g2"}))
    outcome = _outcome(None, [])
    assert sr.mark_semantic_origin(outcome, selection) is outcome
