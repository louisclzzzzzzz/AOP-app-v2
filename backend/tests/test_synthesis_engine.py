from __future__ import annotations

import app.synthesis.engine as engine
from app.classify.taxonomy import Taxonomy, TaxonomyCategory
from app.ingestion.document_signal import DocumentSignal
from app.synthesis.engine import (
    DocumentSummary,
    TopicOutcome,
    assemble_report,
    build_documents_cartography,
    generate_topic,
    select_topic_documents,
    summarize_document,
    topics_for_document,
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


def _summary(doc: DocumentSignal, **overrides) -> DocumentSummary:
    """Relevé de l'étape "map" pour ce document. `summaries_by_topic` ne contient que les thèmes
    informés ; `covered_topic_ids` tous ceux réellement traités par le LLM."""
    defaults = dict(
        document_id=doc.document_id,
        filename=doc.filename,
        final_category=doc.final_category,
        summaries_by_topic={},
        covered_topic_ids=frozenset(),
        model_name="mistral-large-test",
        error=None,
    )
    defaults.update(overrides)
    if "summaries_by_topic" in overrides and "covered_topic_ids" not in overrides:
        defaults["covered_topic_ids"] = frozenset(overrides["summaries_by_topic"])
    return DocumentSummary(**defaults)


def _index(*summaries: DocumentSummary) -> dict[str, DocumentSummary]:
    return {s.document_id: s for s in summaries}


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


# --- topics_for_document : l'inverse de select_topic_documents, base de la dédup du map ---------

def test_topics_for_document_returns_every_topic_the_document_is_pivot_for():
    schema = SynthesisSchema(
        topics=[
            _topic(id="rict_only", pivot_categories=["TECH/RICT"]),
            _topic(id="rict_et_sol", pivot_categories=["TECH/ETUDE DE SOL", "TECH/RICT"]),
            _topic(id="sol_only", pivot_categories=["TECH/ETUDE DE SOL"]),
        ]
    )
    doc = _doc(final_category="TECH/RICT", content_excerpt="Avis suspendu n°12.")

    assert [t.id for t in topics_for_document(doc, schema)] == ["rict_only", "rict_et_sol"]


def test_topics_for_document_ignores_extraction_fields_topics_and_empty_documents():
    schema = SynthesisSchema(
        topics=[
            _topic(id="identite", source="extraction_fields", pivot_categories=["TECH/RICT"]),
            _topic(id="rict", pivot_categories=["TECH/RICT"]),
        ]
    )
    assert [t.id for t in topics_for_document(_doc(final_category="TECH/RICT", content_excerpt="x"), schema)] == ["rict"]
    assert topics_for_document(_doc(final_category="TECH/RICT", content_excerpt=""), schema) == []
    assert topics_for_document(_doc(final_category=None, content_excerpt="x"), schema) == []


# --- summarize_document : étape "map", un seul appel LLM par document --------------------------

def test_summarize_document_covers_all_topics_in_a_single_untruncated_call(monkeypatch):
    """Un document pivot de plusieurs thèmes (ex. le RICT) n'est lu qu'UNE fois, et son texte part
    intégralement — c'est tout l'objet du map-reduce (l'ancien assemblage tronquait à 60 000
    caractères par document et par thème)."""
    calls = []

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        calls.append(user_prompt)
        item = response_model.model_fields["resumes"].annotation.__args__[0]
        return (
            response_model(
                resumes=[
                    item(
                        theme_id="synthese_rict",
                        apporte_des_informations=True,
                        constats=["Avis suspendu n°12.", "- Mission L confiée à SOCOTEC."],
                    ),
                    item(theme_id="recit_sol", apporte_des_informations=False, constats=[]),
                ]
            ),
            "mistral-large-test",
        )

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    long_text = "Avis suspendu n°12 sur les fondations. " * 5_000  # ~190 000 caractères
    doc = _doc(final_category="TECH/RICT", content_excerpt=long_text)
    topics = [_topic(id="synthese_rict"), _topic(id="recit_sol")]

    summary = summarize_document(doc, topics)

    assert len(calls) == 1  # un appel par document, pas un par (document, thème)
    assert long_text in calls[0]  # texte intégral, aucune troncature
    assert summary.error is None
    assert summary.model_name == "mistral-large-test"
    # Les constats sont recomposés en puces Markdown, et une puce déjà préfixée n'est pas doublée
    # Liste, pas bloc recollé : c'est ce qui permet d'étiqueter chaque constat individuellement
    # (`D1.1`, `D1.2`) au reduce. La puce résiduelle du modèle est retirée, jamais doublée.
    assert summary.summaries_by_topic == {
        "synthese_rict": ["Avis suspendu n°12.", "Mission L confiée à SOCOTEC."]
    }
    # `recit_sol` est traité mais sans information : à distinguer d'un thème non traité (repli brut)
    assert summary.covered_topic_ids == frozenset({"synthese_rict", "recit_sol"})


def test_summarize_document_ignores_unknown_theme_ids(monkeypatch):
    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        item = response_model.model_fields["resumes"].annotation.__args__[0]
        return (
            response_model(
                resumes=[
                    item(theme_id="theme_invente", apporte_des_informations=True, constats=["Hors périmètre."]),
                    item(theme_id=" synthese_rict ", apporte_des_informations=True, constats=["Avis suspendu."]),
                ]
            ),
            "mistral-large-test",
        )

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    summary = summarize_document(_doc(final_category="TECH/RICT", content_excerpt="x"), [_topic(id="synthese_rict")])

    assert summary.summaries_by_topic == {"synthese_rict": ["Avis suspendu."]}
    assert "theme_invente" not in summary.covered_topic_ids


def test_summarize_document_llm_failure_is_best_effort(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    summary = summarize_document(_doc(final_category="TECH/RICT", content_excerpt="x"), [_topic(id="synthese_rict")])

    assert summary.error == "API indisponible"
    assert summary.summaries_by_topic == {}
    assert summary.covered_topic_ids == frozenset()


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
        summaries={},
    )

    assert outcome.error is None
    assert outcome.model_name is None
    # Rendu en tableau à 2 colonnes (carte d'identité), pas en lignes « **libellé :** valeur ».
    assert "| Donnée | Valeur |" in outcome.content_md
    assert "| Nom du MOA | Commune de Marly |" in outcome.content_md
    # Un champ sans valeur reste affiché « non trouvé » : l'absence d'une donnée de souscription
    # est une information, une ligne omise se confondrait avec un champ jamais demandé.
    assert "| Adresse du MOA | _non trouvé_ |" in outcome.content_md


def test_extraction_fields_table_escapes_pipes_and_newlines():
    """Une valeur d'extraction peut contenir un « | » (champ `montants_garanties_demandes`, dont le
    résultat attendu est une correspondance garantie → montant) ou un retour à la ligne : les deux
    casseraient la ligne de tableau Markdown."""
    topic = _topic(source="extraction_fields", extraction_field_ids=["garanties_demandees"])
    outcome = generate_topic(
        topic,
        documents=[],
        field_values={"garanties_demandees": ("Garanties", "TRC | 12 M€\nDO | 8 M€")},
        summaries={},
    )

    lines = outcome.content_md.splitlines()
    assert len(lines) == 3  # en-tête + séparateur + une seule ligne de données
    row = lines[-1]
    assert row.count("\\|") == 2  # les 2 pipes de la valeur sont échappés
    # Seuls les 3 délimiteurs d'une ligne à 2 colonnes restent des pipes non échappés.
    assert row.count("|") - row.count("\\|") == 3
    assert row.startswith("| Garanties |") and row.endswith("|")


def test_generate_topic_extraction_fields_source_handles_no_data():
    topic = _topic(source="extraction_fields", extraction_field_ids=["nom_moa"])
    outcome = generate_topic(topic, documents=[], field_values={}, summaries={})
    assert "Aucune donnée disponible" in outcome.content_md


# --- _build_topic_context : étape "reduce", assemblage des relevés -----------------------------

def test_build_topic_context_keeps_every_pivot_document_attributed_to_its_file():
    """Aucun budget sur les relevés : même avec beaucoup de documents pivots, tous sont présents
    dans le prompt, chacun nommé par son fichier source — sans quoi le thème ne pourrait plus
    comparer les documents et signaler une divergence (ex. classement ERP CCTP vs arrêté PC)."""
    topic = _topic(id="destination_ambition", pivot_categories=["TECH/ARRETE PC", "TECH/CCTP TRAVAUX"])
    arrete = _doc(document_id="pc", filename="arrete_pc.pdf", final_category="TECH/ARRETE PC", content_excerpt="x")
    cctps = [
        _doc(document_id=f"c{i}", filename=f"cctp_{i}.pdf", final_category="TECH/CCTP TRAVAUX", content_excerpt="y")
        for i in range(30)
    ]
    summaries = _index(
        _summary(arrete, summaries_by_topic={"destination_ambition": ['ERP "type O, catégorie 2".']}),
        *(_summary(c, summaries_by_topic={"destination_ambition": ['ERP "5e catégorie".']}) for c in cctps),
    )

    context = engine._build_topic_context(topic, [arrete, *cctps], summaries)

    assert context.documents_used == ["arrete_pc.pdf"] + [c.filename for c in cctps]
    assert context.documents_degraded == []
    assert "### [D1] arrete_pc.pdf" in context.text
    assert "### [D31] cctp_29.pdf" in context.text
    # Chaque document reçoit une étiquette, et CHACUN de ses constats une étiquette pointée : ces
    # dernières sont ce qui permet au reduce de renvoyer à un fait précis plutôt qu'au fichier.
    assert [context.refs[f"D{i}"].filename for i in range(1, 32)] == ["arrete_pc.pdf"] + [c.filename for c in cctps]
    assert set(context.refs) == {f"D{i}" for i in range(1, 32)} | {f"D{i}.1" for i in range(1, 32)}
    # L'étiquette pointée porte le constat SEUL — c'est lui qu'on cherchera dans le PDF.
    assert context.refs["D1.1"].excerpt == 'ERP "type O, catégorie 2".'
    assert '[D1.1] ERP "type O, catégorie 2".' in context.text
    assert 'ERP "type O, catégorie 2".' in context.text


def test_build_topic_context_groups_documents_without_information():
    """Un document lu mais muet sur ce thème ne remplit pas le prompt d'un bloc vide, et ne compte
    pas comme source consultée — il reste signalé en une ligne pour que le LLM puisse conclure à
    une absence d'information en connaissance de cause."""
    topic = _topic(id="t1")
    doc_utile = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x")
    doc_muet = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="y")
    summaries = _index(
        _summary(doc_utile, summaries_by_topic={"t1": ["Avis suspendu n°12."]}),
        _summary(doc_muet, summaries_by_topic={}, covered_topic_ids=frozenset({"t1"})),
    )

    context = engine._build_topic_context(topic, [doc_utile, doc_muet], summaries)

    assert context.documents_used == ["a.pdf"]
    assert context.documents_without_info == ["b.pdf"]
    assert context.documents_degraded == []
    assert "sans information utile" in context.text
    assert context.text.count("### [D") == 1


def test_build_topic_context_falls_back_to_raw_excerpt_when_summary_is_missing():
    """Repli best-effort : un document dont le relevé a échoué (ou dont le thème est absent de la
    réponse) est réinjecté en extrait brut tronqué, jamais perdu."""
    topic = _topic(id="t1")
    doc_ok = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x")
    doc_ko = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="TEXTE BRUT " * 100)
    doc_absent = _doc(document_id="c", filename="c.pdf", final_category="TECH/RICT", content_excerpt="AUTRE BRUT")
    summaries = _index(
        _summary(doc_ok, summaries_by_topic={"t1": ["Relevé."]}),
        _summary(doc_ko, error="API indisponible"),
        # `doc_absent` n'a aucune entrée : le LLM n'a pas traité ce thème pour ce document.
    )

    context = engine._build_topic_context(topic, [doc_ok, doc_ko, doc_absent], summaries)

    assert context.documents_used == ["a.pdf", "b.pdf", "c.pdf"]
    assert context.documents_degraded == ["b.pdf", "c.pdf"]
    assert "relevé indisponible" in context.text
    assert "TEXTE BRUT" in context.text
    assert "AUTRE BRUT" in context.text


def test_build_topic_context_bounds_the_raw_excerpt_fallback_only():
    """Le budget résiduel ne borne QUE le chemin de repli : il ne peut jamais évincer un relevé."""
    topic = _topic(id="t1")
    doc_resume = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x" * 100)
    doc_brut_1 = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="y" * 100)
    doc_brut_2 = _doc(document_id="c", filename="c.pdf", final_category="TECH/RICT", content_excerpt="z" * 100)
    summaries = _index(_summary(doc_resume, summaries_by_topic={"t1": ["Relevé conservé."]}))

    context = engine._build_topic_context(
        topic,
        [doc_brut_1, doc_brut_2, doc_resume],
        summaries,
        fallback_total_budget=10,
        fallback_per_document_budget=10,
    )

    assert "Relevé conservé." in context.text
    assert context.documents_used == ["b.pdf", "a.pdf"]  # c.pdf évincé, budget de repli épuisé
    assert context.documents_degraded == ["b.pdf"]


# --- generate_topic : source=documents, avec appel LLM ------------------------------------------

def test_generate_topic_documents_source_calls_llm_with_summaries(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        return response_model(contenu="Contenu généré."), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    topic = _topic(id="synthese_rict", pivot_categories=["TECH/RICT"], grounding_field_ids=["existence_rict"])
    doc = _doc(final_category="TECH/RICT", content_excerpt="Texte brut intégral du RICT.")
    summaries = _index(_summary(doc, summaries_by_topic={"synthese_rict": ["Avis suspendu n°1 sur les fondations."]}))

    outcome = generate_topic(
        topic,
        documents=[doc],
        field_values={"existence_rict": ("Existence RICT", "Oui")},
        summaries=summaries,
    )

    assert outcome.content_md == "Contenu généré."
    assert outcome.model_name == "mistral-large-test"
    assert outcome.error is None
    assert outcome.documents_used == ["doc.pdf"]
    assert outcome.documents_degraded == []
    assert "Avis suspendu n°1" in captured["user_prompt"]
    assert "Existence RICT : Oui" in captured["user_prompt"]
    assert "synthese_rict" in captured["what"]
    # Le reduce lit les relevés, plus le texte brut du document
    assert "Texte brut intégral du RICT." not in captured["user_prompt"]


def test_generate_topic_documents_used_counts_every_candidate_it_could_exploit(monkeypatch):
    """Plus aucun candidat n'est écarté faute de budget (§bug réel : un dossier à 69 candidats
    CCTP/CCAP dont seuls les 2 premiers tenaient dans l'ancien budget de contexte)."""

    def _fake_chat(*, system_prompt, user_prompt, response_model, what):
        return response_model(contenu="Contenu généré."), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake_chat)

    topic = _topic(id="t1", pivot_categories=["TECH/RICT"])
    docs = [
        _doc(document_id=str(i), filename=f"{i}.pdf", final_category="TECH/RICT", content_excerpt="x" * 60_000)
        for i in range(69)
    ]
    summaries = _index(*(_summary(d, summaries_by_topic={"t1": ["Relevé."]}) for d in docs))

    outcome = generate_topic(topic, documents=docs, field_values={}, summaries=summaries)

    assert len(outcome.documents_used) == 69
    assert outcome.candidates_count == 69


def test_generate_topic_documents_source_no_candidates_skips_llm_call(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sans document pivot candidat")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    topic = _topic(pivot_categories=["TECH/RICT"])
    outcome = generate_topic(topic, documents=[], field_values={}, summaries={})

    assert outcome.error is None
    assert "Aucun document pivot" in outcome.content_md


def test_generate_topic_documents_source_llm_failure_surfaces_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    topic = _topic(id="t1", pivot_categories=["TECH/RICT"])
    doc = _doc(final_category="TECH/RICT", content_excerpt="contenu")
    summaries = _index(_summary(doc, summaries_by_topic={"t1": ["Relevé."]}))

    outcome = generate_topic(topic, documents=[doc], field_values={}, summaries=summaries)

    assert outcome.content_md is None
    assert outcome.error == "API indisponible"
    assert outcome.documents_used == ["doc.pdf"]


def test_generate_topic_still_runs_when_no_summary_could_be_produced(monkeypatch):
    """Best-effort de bout en bout : un map entièrement en échec ne fait pas échouer le thème, il
    le fait retomber sur les extraits bruts d'avant."""
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        captured["user_prompt"] = user_prompt
        return response_model(contenu="Contenu dégradé."), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    topic = _topic(id="t1", pivot_categories=["TECH/RICT"])
    doc = _doc(final_category="TECH/RICT", content_excerpt="TEXTE BRUT DU RICT")

    outcome = generate_topic(topic, documents=[doc], field_values={}, summaries={})

    assert outcome.content_md == "Contenu dégradé."
    assert outcome.documents_used == ["doc.pdf"]
    assert outcome.documents_degraded == ["doc.pdf"]
    assert "TEXTE BRUT DU RICT" in captured["user_prompt"]


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

    report = assemble_report(outcomes, schema, cartography_md="| a | b |").markdown

    assert report.index("Premier") < report.index("Second")
    assert "Contenu 1" in report
    assert "Contenu 2" in report
    assert "| a | b |" in report


def test_assemble_report_traces_sources_and_documents_without_information():
    """La liste des fichiers exploités par thème est la trace de sourcing retenue en production —
    les relevés du map n'ont plus à porter une citation par phrase."""
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier")])
    outcomes = [
        TopicOutcome(
            topic_id="t1",
            content_md="Contenu",
            model_name="m",
            error=None,
            documents_used=["a.pdf"],
            candidates_count=3,
            documents_degraded=["b.pdf"],
        )
    ]

    report = assemble_report(outcomes, schema).markdown

    assert "_Sources (1) : a.pdf_" in report
    assert "+2 pivot(s) sans information utile" in report
    assert "extrait brut tronqué utilisé pour : b.pdf" in report


def test_sources_note_truncates_long_file_lists_but_keeps_the_count():
    """Sur un dossier à 24 CCTP par lot, cette note atteignait ~1 500 caractères PAR SECTION —
    à elle seule une part majeure du rapport, pour une information de second plan (la source
    précise de chaque donnée est portée par la colonne "Source" des tableaux)."""
    filenames = [f"lot{i:02d}.pdf" for i in range(20)]
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier")])
    outcomes = [
        TopicOutcome(
            topic_id="t1",
            content_md="Contenu",
            model_name="m",
            error=None,
            documents_used=filenames,
            candidates_count=len(filenames),
        )
    ]

    report = assemble_report(outcomes, schema).markdown

    assert "_Sources (20) : " in report  # le compte total reste affiché, rien n'est masqué
    assert "lot00.pdf" in report
    assert "+14 autre(s)" in report
    assert "lot19.pdf" not in report


def test_sources_note_never_truncates_degraded_documents():
    """La liste dégradée est un signal de qualité : l'expert doit voir TOUS les fichiers concernés
    pour juger s'il peut se fier à la section, même s'ils sont nombreux."""
    degraded = [f"deg{i:02d}.pdf" for i in range(20)]
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier")])
    outcomes = [
        TopicOutcome(
            topic_id="t1",
            content_md="Contenu",
            model_name="m",
            error=None,
            documents_used=degraded,
            candidates_count=len(degraded),
            documents_degraded=degraded,
        )
    ]

    report = assemble_report(outcomes, schema).markdown

    assert all(name in report for name in degraded)


def test_assemble_report_shows_error_note_for_failed_topic():
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Premier")])
    outcomes = [TopicOutcome(topic_id="t1", content_md=None, model_name=None, error="API indisponible")]

    report = assemble_report(outcomes, schema).markdown

    assert "Section non générée" in report
    assert "API indisponible" in report


# --- Citations : renvois [Dn] du LLM → marqueurs globaux + registre -----------------------------

def test_assemble_report_resolves_llm_refs_into_citation_markers_and_registry():
    """La synthèse rédige en prose libre : les renvois posés en fin de phrase deviennent des
    marqueurs résolubles, et le registre permet d'ouvrir le document depuis l'écran."""
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Thème 1")])
    outcome = TopicOutcome(
        topic_id="t1",
        content_md="Le classement ERP est de type O, catégorie 2. [D1] Le CCTP retient la 5e. [D2]",
        model_name="m",
        error=None,
        citation_refs={
            "D1": engine.CitationRef(document_id="pc", filename="arrete_pc.pdf", excerpt="ERP type O."),
            "D2": engine.CitationRef(document_id="c0", filename="cctp_0.pdf", excerpt="ERP 5e catégorie."),
        },
    )

    report = assemble_report([outcome], schema)

    assert "[D1]" not in report.markdown and "[D2]" not in report.markdown
    assert "type O, catégorie 2. ⟦cite:c1⟧" in report.markdown
    assert report.citations == {
        "c1": {"document_id": "pc", "filename": "arrete_pc.pdf", "excerpt": "ERP type O."},
        "c2": {"document_id": "c0", "filename": "cctp_0.pdf", "excerpt": "ERP 5e catégorie."},
    }


def test_assemble_report_leaves_deterministic_topics_untouched():
    """Un thème `extraction_fields` est un reformatage sans appel LLM : il n'a aucune étiquette, et
    doit traverser la résolution sans être modifié."""
    schema = SynthesisSchema(topics=[_topic(id="t1", titre="Thème 1")])
    contenu = "| Donnée | Valeur | Source |\n|---|---|---|\n| Maître d'ouvrage | Ville de X | pc.pdf |"
    outcome = TopicOutcome(topic_id="t1", content_md=contenu, model_name=None, error=None)

    report = assemble_report([outcome], schema)

    assert contenu in report.markdown
    assert report.citations == {}


def test_build_topic_context_shows_the_normalized_filename_in_citations_when_available():
    """Même règle que l'audit : le nom normalisé à l'étape 1 est celui que l'expert reconnaît,
    donc celui affiché par la pastille de citation."""
    topic = _topic(id="t1", pivot_categories=["TECH/RICT"])
    doc = _doc(document_id="a", filename="2024_0129_export.pdf", final_filename="RICT.pdf", final_category="TECH/RICT")
    summaries = _index(_summary(doc, summaries_by_topic={"t1": ["Constat."]}))

    context = engine._build_topic_context(topic, [doc], summaries)

    assert context.refs["D1"].filename == "RICT.pdf"


def test_build_topic_context_falls_back_to_the_original_filename_when_not_normalized():
    topic = _topic(id="t1", pivot_categories=["TECH/RICT"])
    doc = _doc(document_id="a", filename="2024_0129_export.pdf", final_filename=None, final_category="TECH/RICT")
    summaries = _index(_summary(doc, summaries_by_topic={"t1": ["Constat."]}))

    context = engine._build_topic_context(topic, [doc], summaries)

    assert context.refs["D1"].filename == "2024_0129_export.pdf"
