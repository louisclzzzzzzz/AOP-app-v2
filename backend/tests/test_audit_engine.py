from __future__ import annotations

import app.audit.engine as engine
from app.audit.engine import (
    RiskItem,
    SectionOutcome,
    SectionRisks,
    assemble_report,
    extract_chantier_address,
    generate_section,
    select_section_documents,
)
from app.audit.georisques import GeorisquesReport
from app.audit.schema import AuditSchema, AuditSection
from app.ingestion.document_signal import DocumentSignal


def _section(**overrides) -> AuditSection:
    defaults = dict(
        id="sec",
        titre="Section de test",
        pivot_categories=["TECH/RICT", "TECH/CCTP TRAVAUX"],
        cctp_keywords=["etancheite", "couverture"],
        georisques_aspects=[],
        points_verification="→ Un point.",
        instructions="Audite la section.",
    )
    defaults.update(overrides)
    return AuditSection(**defaults)


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


def _risk(**overrides) -> RiskItem:
    defaults = dict(
        statut="🔴",
        element_ouvrage="FONDATIONS",
        risque="Défaut de stabilité",
        alea="Tassement",
        synoptique_description="Tassement différentiel possible.",
        synoptique_preconisation="Réclamer la G2 finale.",
        expose_situation="Le CCTP prévoit des semelles.",
        analyse_expert=["→ **Portance** : selon l'Eurocode 7…"],
        impact_assurabilite="Risque décennal élevé.",
        recommandations=["Exiger la note de calcul."],
        source="CCTP Lot 09",
    )
    defaults.update(overrides)
    return RiskItem(**defaults)


# --- select_section_documents : filtrage par mots-clés de lot + priorité + dédup ----------------

def test_select_section_documents_filters_cctp_by_keyword():
    rict = _doc(document_id="rict", filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="x")
    cctp_etanch = _doc(
        document_id="et", filename="LOT 11 - CCTP ETANCHEITE.pdf", final_category="TECH/CCTP TRAVAUX", content_excerpt="x"
    )
    cctp_peinture = _doc(
        document_id="pe", filename="LOT 21 - CCTP PEINTURE.pdf", final_category="TECH/CCTP TRAVAUX", content_excerpt="x"
    )

    selected = select_section_documents(_section(), [rict, cctp_etanch, cctp_peinture])
    ids = [d.document_id for d in selected]

    assert "rict" in ids  # RICT jamais filtré
    assert "et" in ids  # CCTP étanchéité matché
    assert "pe" not in ids  # CCTP peinture hors mots-clés


def test_select_section_documents_keyword_match_is_accent_and_case_insensitive():
    cctp = _doc(
        document_id="c", filename="LOT 11 - CCTP ÉTANCHÉITÉ.PDF", final_category="TECH/CCTP TRAVAUX", content_excerpt="x"
    )
    selected = select_section_documents(_section(cctp_keywords=["etancheite"]), [cctp])
    assert [d.document_id for d in selected] == ["c"]


def test_select_section_documents_orders_by_pivot_priority():
    cctp = _doc(document_id="c", filename="CCTP ETANCHEITE.pdf", final_category="TECH/CCTP TRAVAUX", content_excerpt="x")
    rict = _doc(document_id="r", filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="x")
    # pivot order = RICT puis CCTP → RICT en premier même s'il est passé en 2e argument
    selected = select_section_documents(_section(), [cctp, rict])
    assert [d.document_id for d in selected] == ["r", "c"]


def test_select_section_documents_skips_empty_content():
    rict = _doc(document_id="r", final_category="TECH/RICT", content_excerpt="")
    selected = select_section_documents(_section(), [rict])
    assert selected == []


def test_select_section_documents_non_lot_category_never_filtered():
    """Une catégorie hors LOT_FILTERED (étude de sol) est prise entière, sans passer par les
    mots-clés (son nom de fichier ne contient pas forcément 'etancheite')."""
    sol = _doc(document_id="s", filename="G2-PRO.pdf", final_category="TECH/ETUDE DE SOL", content_excerpt="x")
    section = _section(pivot_categories=["TECH/ETUDE DE SOL"], cctp_keywords=["etancheite"])
    selected = select_section_documents(section, [sol])
    assert [d.document_id for d in selected] == ["s"]


# --- generate_section : appel LLM, grounding Géorisques, gestion d'erreur -----------------------

def test_generate_section_calls_llm_and_injects_georisques(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        return SectionRisks(risques=[_risk()]), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    section = _section(georisques_aspects=["seisme"])
    doc = _doc(filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="Avis suspendu n°190.")
    geo = GeorisquesReport(address_queried="x", resolved_label="Commune", lon=1.0, lat=2.0, seisme="3 - MODÉRÉE")

    outcome = generate_section(section, documents=[doc], georisques=geo)

    assert outcome.error is None
    assert outcome.model_name == "mistral-large-test"
    assert len(outcome.risks) == 1
    assert outcome.documents_used == ["RICT.pdf"]
    assert "Avis suspendu n°190" in captured["user_prompt"]
    assert "3 - MODÉRÉE" in captured["user_prompt"]  # grounding Géorisques injecté
    assert "sec" in captured["what"]


def test_generate_section_no_documents_no_georisques_skips_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit pas être appelé sans document ni Géorisques")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = generate_section(_section(), documents=[], georisques=None)
    assert outcome.error is None
    assert outcome.risks == []


def test_generate_section_runs_on_georisques_only_even_without_documents(monkeypatch):
    """Si le corpus n'a aucun document pour la section mais que Géorisques a des données, on
    interroge quand même le LLM (statuer sur les risques naturels du site)."""
    def _fake(*, system_prompt, user_prompt, response_model, what):
        return SectionRisks(risques=[_risk()]), "m"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)
    section = _section(georisques_aspects=["seisme"])
    geo = GeorisquesReport(address_queried="x", lon=1.0, lat=2.0, seisme="3 - MODÉRÉE")

    outcome = generate_section(section, documents=[], georisques=geo)
    assert len(outcome.risks) == 1


def test_generate_section_llm_failure_surfaces_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)
    doc = _doc(filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="x")

    outcome = generate_section(_section(), documents=[doc], georisques=None)
    assert outcome.risks == []
    assert outcome.error == "API indisponible"


def test_build_documents_context_excludes_beyond_budget():
    a = _doc(document_id="a", filename="a.pdf", content_excerpt="x" * 10)
    b = _doc(document_id="b", filename="b.pdf", content_excerpt="y" * 10)
    context, included = engine._build_documents_context([a, b], total_budget=10, per_document_budget=10)
    assert included == ["a.pdf"]
    assert "b.pdf" not in context


# --- assemble_report : tableau synoptique + analyse détaillée -----------------------------------

def _schema() -> AuditSchema:
    return AuditSchema(sections=[_section(id="s1", titre="Section 1"), _section(id="s2", titre="Section 2")])


def test_assemble_report_builds_synoptic_table_and_detail_in_schema_order():
    schema = _schema()
    outcomes = [
        SectionOutcome(section_id="s2", risks=[_risk(element_ouvrage="COUVERTURE")], model_name="m", error=None, documents_used=["d.pdf"], candidates_count=1),
        SectionOutcome(section_id="s1", risks=[_risk(element_ouvrage="FONDATIONS")], model_name="m", error=None, documents_used=["g.pdf"], candidates_count=1),
    ]

    report = assemble_report(outcomes, schema, georisques=None)

    # ordre des sections = ordre du schéma
    assert report.index("Section 1") < report.index("Section 2")
    # tableau synoptique présent avec les 2 risques
    assert "Tableau récapitulatif des risques" in report
    assert "FONDATIONS" in report
    assert "COUVERTURE" in report
    # format détaillé imposé par le protocole
    assert "[STATUT : 🔴]" in report
    assert "**Exposé de la situation :**" in report
    assert "**Impact Assurabilité :**" in report
    assert "**Recommandation de levée de doute :**" in report


def test_assemble_report_renders_lists_as_bullets_and_keeps_source_on_its_own_line():
    """`analyse_expert` et `recommandations` sont des listes (et non des textes multi-lignes, qui
    déclenchaient une boucle dégénérée du décodage JSON). Le rendu doit les remettre en forme —
    et `**Source :**` ne doit plus se retrouver collé à la dernière puce de la recommandation,
    comme c'était le cas quand `recommandation` était un texte à puces embarquées."""
    schema = _schema()
    risk = _risk(
        analyse_expert=["→ **Portance** : premier point.", "→ **Drainage** : second point."],
        recommandations=["- Exiger la note de calcul.", "2. Réclamer le rapport G2 final."],
        source="CCTP Lot 09, art. 3.2",
    )
    outcomes = [
        SectionOutcome(section_id="s1", risks=[risk], model_name="m", error=None, documents_used=["g.pdf"], candidates_count=1)
    ]

    report = assemble_report(outcomes, schema, georisques=None)

    assert "- Exiger la note de calcul." in report
    assert "- Réclamer le rapport G2 final." in report  # puce/numérotation résiduelle non doublée
    assert "→ **Portance** : premier point.\n\n→ **Drainage** : second point." in report
    assert "\n**Source :** CCTP Lot 09, art. 3.2" in report


def test_assemble_report_handles_empty_lists_without_crashing():
    schema = _schema()
    outcomes = [
        SectionOutcome(
            section_id="s1",
            risks=[_risk(analyse_expert=[], recommandations=["   "])],
            model_name="m", error=None, documents_used=["g.pdf"], candidates_count=1,
        )
    ]

    report = assemble_report(outcomes, schema, georisques=None)

    assert "_Non renseigné._" in report
    assert "_Non renseignée._" in report


def test_assemble_report_shows_error_note_for_failed_section():
    schema = AuditSchema(sections=[_section(id="s1", titre="Section 1")])
    outcomes = [SectionOutcome(section_id="s1", risks=[], model_name=None, error="API indisponible")]
    report = assemble_report(outcomes, schema)
    assert "Section non générée" in report
    assert "API indisponible" in report


def test_assemble_report_escapes_pipes_in_synoptic_cells():
    schema = AuditSchema(sections=[_section(id="s1", titre="Section 1")])
    risk = _risk(synoptique_description="A | B contradiction")
    outcomes = [SectionOutcome(section_id="s1", risks=[risk], model_name="m", error=None, documents_used=["d.pdf"], candidates_count=1)]
    report = assemble_report(outcomes, schema)
    assert "A \\| B contradiction" in report


# --- extract_chantier_address : fallback LLM de géolocalisation ---------------------------------

def test_extract_chantier_address_uses_priority_source_and_returns_address(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what):
        captured["user_prompt"] = user_prompt
        return response_model(adresse="Station de la Toussuire, 73300 Fontcouverte"), "m"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    notice = _doc(document_id="n", filename="notice.pdf", final_category="TECH/NOTICE", content_excerpt="notice")
    arrete = _doc(document_id="a", filename="PC.pdf", final_category="TECH/ARRETE PC", content_excerpt="arrete PC")

    address = extract_chantier_address([notice, arrete])
    assert address == "Station de la Toussuire, 73300 Fontcouverte"
    assert "arrete PC" in captured["user_prompt"]  # arrêté PC prioritaire sur la notice


def test_extract_chantier_address_empty_result_returns_none(monkeypatch):
    monkeypatch.setattr(engine, "call_structured_chat", lambda **k: (engine._ChantierAddress(adresse="  "), "m"))
    arrete = _doc(final_category="TECH/ARRETE PC", content_excerpt="x")
    assert extract_chantier_address([arrete]) is None


def test_extract_chantier_address_no_source_document_skips_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("aucun appel LLM sans document source")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)
    doc = _doc(final_category="TECH/PLANS", content_excerpt="x")
    assert extract_chantier_address([doc]) is None


def test_extract_chantier_address_llm_failure_returns_none(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)
    arrete = _doc(final_category="TECH/ARRETE PC", content_excerpt="x")
    assert extract_chantier_address([arrete]) is None


def test_assemble_report_includes_georisques_context_section():
    schema = AuditSchema(sections=[_section(id="s1", titre="Section 1")])
    outcomes = [SectionOutcome(section_id="s1", risks=[], model_name=None, error=None)]
    geo = GeorisquesReport(address_queried="8 rue X", resolved_label="8 rue X 80000 Ville", lon=2.0, lat=49.0, seisme="1 - TRES FAIBLE")
    report = assemble_report(outcomes, schema, georisques=geo)
    assert "Contexte réglementaire — Risques naturels (Géorisques)" in report
    assert "1 - TRES FAIBLE" in report
