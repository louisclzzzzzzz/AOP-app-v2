from __future__ import annotations

import pytest

from app.audit.schema import (
    LOT_FILTERED_CATEGORIES,
    VALID_GEORISQUES_ASPECTS,
    AuditSchema,
    AuditSection,
    load_audit_schema,
)
from app.classify.taxonomy import load_taxonomy


def test_load_audit_schema_sections_present_and_unique():
    schema = load_audit_schema()
    ids = [s.id for s in schema.sections]
    assert ids  # au moins une section
    assert len(ids) == len(set(ids))
    # les 6 grandes familles A→G du protocole
    assert "infrastructure_fondations" in ids
    assert "superstructure" in ids
    assert "couverture_etancheite" in ids
    assert "facades" in ids
    assert "equipements_enr" in ids
    assert "amenagements_interieurs" in ids


def test_load_audit_schema_every_section_has_instructions_and_pivots():
    schema = load_audit_schema()
    for section in schema.sections:
        assert section.pivot_categories, f"{section.id} sans pivot"
        assert section.instructions, f"{section.id} sans instructions"


def test_audit_schema_pivot_categories_exist_in_taxonomy():
    """Toute catégorie pivot déclarée doit exister dans taxonomy.yaml, sinon la sélection de
    documents ne matchera jamais rien silencieusement."""
    taxonomy = load_taxonomy()
    valid_paths = {c.path for c in taxonomy.categories}
    schema = load_audit_schema()
    for section in schema.sections:
        for cat in section.pivot_categories:
            assert cat in valid_paths, f"{section.id} référence une catégorie inconnue : {cat}"


def test_audit_schema_georisques_aspects_are_valid():
    schema = load_audit_schema()
    for section in schema.sections:
        for aspect in section.georisques_aspects:
            assert aspect in VALID_GEORISQUES_ASPECTS


def test_audit_schema_cctp_keywords_only_needed_when_lot_category_present():
    """Toute section incluant une catégorie « par lot » (CCTP travaux / DPGF) doit fournir des
    mots-clés de filtrage, sinon les dizaines de CCTP satureraient le contexte."""
    schema = load_audit_schema()
    for section in schema.sections:
        if LOT_FILTERED_CATEGORIES & set(section.pivot_categories):
            assert section.cctp_keywords, f"{section.id} inclut un lot filtrable sans cctp_keywords"


def test_load_audit_schema_rejects_unknown_georisques_aspect(tmp_path, monkeypatch):
    bad = tmp_path / "audit_risques_schema.yaml"
    bad.write_text(
        "sections:\n"
        "  - id: s1\n"
        "    titre: T\n"
        "    pivot_categories: ['TECH/RICT']\n"
        "    georisques_aspects: ['ouragan']\n"
        "    instructions: 'x'\n",
        encoding="utf-8",
    )
    import app.audit.schema as sch

    monkeypatch.setattr(sch, "get_config_dir", lambda: tmp_path)
    sch.load_audit_schema.cache_clear()
    with pytest.raises(AssertionError):
        sch.load_audit_schema()
    sch.load_audit_schema.cache_clear()
