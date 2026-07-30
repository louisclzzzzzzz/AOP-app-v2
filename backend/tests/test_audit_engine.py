from __future__ import annotations

import app.audit.engine as engine
from app.audit.engine import (
    DocumentAuditSummary,
    RiskItem,
    SectionOutcome,
    SectionRisks,
    assemble_report,
    extract_chantier_address,
    generate_section,
    sections_for_document,
    select_section_documents,
    summarize_document_for_audit,
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


# --- sections_for_document : l'inverse de select_section_documents, base de la dédup du map ------

def test_sections_for_document_returns_every_section_the_document_is_pivot_for():
    schema = AuditSchema(
        sections=[
            _section(id="fondations", pivot_categories=["TECH/RICT"]),
            _section(id="facades", pivot_categories=["TECH/ETUDE DE SOL"]),
            _section(id="couverture", pivot_categories=["TECH/RICT", "TECH/NOTICE"]),
        ]
    )
    doc = _doc(final_category="TECH/RICT", content_excerpt="x")

    assert [s.id for s in sections_for_document(doc, schema)] == ["fondations", "couverture"]


def test_sections_for_document_applies_the_lot_keyword_filter():
    """Le filtre `cctp_keywords` doit s'appliquer à l'identique dans les deux sens, sinon le map
    lirait des CCTP de lots hors sujet (ou en oublierait)."""
    schema = AuditSchema(
        sections=[
            _section(id="etancheite", pivot_categories=["TECH/CCTP TRAVAUX"], cctp_keywords=["etancheite"]),
            _section(id="fondations", pivot_categories=["TECH/CCTP TRAVAUX"], cctp_keywords=["gros-oeuvre"]),
        ]
    )
    cctp_etancheite = _doc(filename="LOT 11 - CCTP ÉTANCHÉITÉ.pdf", final_category="TECH/CCTP TRAVAUX", content_excerpt="x")

    assert [s.id for s in sections_for_document(cctp_etancheite, schema)] == ["etancheite"]
    # cohérence avec le sens direct
    assert select_section_documents(schema.sections[0], [cctp_etancheite]) == [cctp_etancheite]
    assert select_section_documents(schema.sections[1], [cctp_etancheite]) == []


# --- summarize_document_for_audit : étape « map » -----------------------------------------------

def test_summarize_document_for_audit_covers_all_sections_in_one_untruncated_call(monkeypatch):
    """Un document pivot de plusieurs sections (le RICT en alimentait 5) n'est lu qu'UNE fois, et
    son texte part intégralement — l'ancien assemblage le renvoyait tronqué à 60 000 caractères
    une fois par section."""
    calls = []

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        calls.append(user_prompt)
        item = response_model.model_fields["releves"].annotation.__args__[0]
        return (
            response_model(
                releves=[
                    item(section_id="fondations", concerne_cette_section=True,
                         constats=["Pieux forés ancrés dans les moraines.", "- Aucune note de calcul fournie."]),
                    item(section_id="facades", concerne_cette_section=False, constats=[]),
                ]
            ),
            "mistral-large-test",
        )

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    long_text = "Avis suspendu n°190 sur les fondations. " * 3_000  # ~120 000 caractères
    doc = _doc(final_category="TECH/RICT", content_excerpt=long_text)
    sections = [_section(id="fondations"), _section(id="facades")]

    summary = summarize_document_for_audit(doc, sections)

    assert len(calls) == 1  # un appel par document, pas un par (document, section)
    assert long_text in calls[0]  # texte intégral, aucune troncature
    assert summary.error is None
    assert summary.constats_by_section == {
        "fondations": "- Pieux forés ancrés dans les moraines.\n- Aucune note de calcul fournie."
    }
    # `facades` est traitée mais sans élément : à distinguer d'une section non traitée (repli brut)
    assert summary.covered_section_ids == frozenset({"fondations", "facades"})


def test_summarize_document_for_audit_llm_failure_is_best_effort(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    summary = summarize_document_for_audit(_doc(content_excerpt="x"), [_section(id="fondations")])

    assert summary.error == "API indisponible"
    assert summary.constats_by_section == {}
    assert summary.covered_section_ids == frozenset()


# --- _build_section_context : étape « reduce », assemblage des relevés ---------------------------

def _summary(doc, **overrides):
    defaults = dict(
        document_id=doc.document_id, filename=doc.filename,
        final_category=doc.final_category, final_lot=doc.final_lot,
        constats_by_section={}, covered_section_ids=frozenset(),
        model_name="m", error=None,
    )
    defaults.update(overrides)
    if "constats_by_section" in overrides and "covered_section_ids" not in overrides:
        defaults["covered_section_ids"] = frozenset(overrides["constats_by_section"])
    return DocumentAuditSummary(**defaults)


def test_build_section_context_keeps_every_pivot_document_attributed_to_its_file():
    """Aucun budget sur les relevés : tous les documents pivots sont présents, chacun nommé par son
    fichier — sans quoi la section ne pourrait plus confronter les lots entre eux."""
    section = _section(id="fondations", pivot_categories=["TECH/RICT"])
    docs = [
        _doc(document_id=str(i), filename=f"{i}.pdf", final_category="TECH/RICT", content_excerpt="x" * 80_000)
        for i in range(12)
    ]
    summaries = {d.document_id: _summary(d, constats_by_section={"fondations": "- Constat."}) for d in docs}

    context = engine._build_section_context(section, docs, summaries)

    assert context.documents_used == [d.filename for d in docs]
    assert context.documents_degraded == []
    assert context.text.count("### Document : ") == 12


def test_build_section_context_falls_back_to_raw_excerpt_when_summary_is_missing():
    section = _section(id="fondations", pivot_categories=["TECH/RICT"])
    ok = _doc(document_id="a", filename="a.pdf", final_category="TECH/RICT", content_excerpt="x")
    ko = _doc(document_id="b", filename="b.pdf", final_category="TECH/RICT", content_excerpt="TEXTE BRUT")
    muet = _doc(document_id="c", filename="c.pdf", final_category="TECH/RICT", content_excerpt="y")
    summaries = {
        "a": _summary(ok, constats_by_section={"fondations": "- Constat."}),
        "b": _summary(ko, error="API indisponible"),
        "c": _summary(muet, constats_by_section={}, covered_section_ids=frozenset({"fondations"})),
    }

    context = engine._build_section_context(section, [ok, ko, muet], summaries)

    assert context.documents_used == ["a.pdf", "b.pdf"]
    assert context.documents_degraded == ["b.pdf"]
    assert context.documents_without_info == ["c.pdf"]
    assert "TEXTE BRUT" in context.text
    assert "relevé indisponible" in context.text
    assert "sans élément pertinent" in context.text


# --- generate_section : appel LLM, grounding Géorisques, gestion d'erreur -----------------------

def test_generate_section_calls_llm_and_injects_georisques(monkeypatch):
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        captured["user_prompt"] = user_prompt
        captured["what"] = what
        return SectionRisks(risques=[_risk()]), "mistral-large-test"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)

    section = _section(id="fondations", georisques_aspects=["seisme"])
    doc = _doc(filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="TEXTE BRUT DU RICT")
    geo = GeorisquesReport(address_queried="x", resolved_label="Commune", lon=1.0, lat=2.0, seisme="3 - MODÉRÉE")
    summaries = {doc.document_id: _summary(doc, constats_by_section={"fondations": "- Avis suspendu n°190."})}

    outcome = generate_section(section, documents=[doc], georisques=geo, summaries=summaries)

    assert outcome.error is None
    assert outcome.model_name == "mistral-large-test"
    assert len(outcome.risks) == 1
    assert outcome.documents_used == ["RICT.pdf"]
    assert outcome.documents_degraded == []
    assert "Avis suspendu n°190" in captured["user_prompt"]
    assert "3 - MODÉRÉE" in captured["user_prompt"]  # grounding Géorisques injecté
    assert "sec" in captured["what"]
    # le reduce lit les relevés, plus le texte brut
    assert "TEXTE BRUT DU RICT" not in captured["user_prompt"]


def test_generate_section_no_documents_no_georisques_skips_llm(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("le LLM ne doit pas être appelé sans document ni Géorisques")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    outcome = generate_section(_section(), documents=[], georisques=None, summaries={})
    assert outcome.error is None
    assert outcome.risks == []


def test_generate_section_runs_on_georisques_only_even_without_documents(monkeypatch):
    """Si le corpus n'a aucun document pour la section mais que Géorisques a des données, on
    interroge quand même le LLM (statuer sur les risques naturels du site)."""
    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        return SectionRisks(risques=[_risk()]), "m"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)
    section = _section(georisques_aspects=["seisme"])
    geo = GeorisquesReport(address_queried="x", lon=1.0, lat=2.0, seisme="3 - MODÉRÉE")

    outcome = generate_section(section, documents=[], georisques=geo, summaries={})
    assert len(outcome.risks) == 1


def test_generate_section_llm_failure_surfaces_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)
    doc = _doc(filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="x")

    outcome = generate_section(_section(), documents=[doc], georisques=None, summaries={})
    assert outcome.risks == []
    assert outcome.error == "API indisponible"


def test_generate_section_still_runs_when_no_summary_could_be_produced(monkeypatch):
    """Best-effort de bout en bout : un map entièrement en échec ne fait pas échouer la section,
    il la fait retomber sur les extraits bruts d'avant."""
    captured = {}

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        captured["user_prompt"] = user_prompt
        return SectionRisks(risques=[_risk()]), "m"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)
    doc = _doc(filename="RICT.pdf", final_category="TECH/RICT", content_excerpt="TEXTE BRUT DU RICT")

    outcome = generate_section(_section(), documents=[doc], georisques=None, summaries={})

    assert len(outcome.risks) == 1
    assert outcome.documents_used == ["RICT.pdf"]
    assert outcome.documents_degraded == ["RICT.pdf"]
    assert "TEXTE BRUT DU RICT" in captured["user_prompt"]


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

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
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
