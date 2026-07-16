from __future__ import annotations

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.settings import get_models_config


def test_extraction_schema_loads_and_has_unique_ids():
    schema = load_extraction_schema()
    ids = [f.id for f in schema.fields]
    assert len(ids) == len(set(ids)), "ids de champs dupliqués dans extraction_schema.yaml"
    assert len(ids) == 30


def test_both_sections_represented():
    schema = load_extraction_schema()
    sections = {f.section for f in schema.fields}
    assert sections == {"principal", "complementaire"}
    by_section = schema.by_section()
    assert len(by_section["principal"]) == 24
    assert len(by_section["complementaire"]) == 6


def test_reference_categories_are_valid_taxonomy_paths():
    schema = load_extraction_schema()
    taxonomy = load_taxonomy()
    for f in schema.fields:
        for category in f.reference_categories:
            assert taxonomy.by_path(category) is not None, (
                f"{f.id} référence une catégorie taxonomie inconnue : {category}"
            )


def test_cross_check_required_fields_exist_in_schema():
    schema = load_extraction_schema()
    ids = {f.id for f in schema.fields}
    required = get_models_config()["extraction"]["cross_check_required_fields"]
    for field_id in required:
        assert field_id in ids, f"{field_id} (cross_check_required_fields) absent du schéma"


def test_by_id():
    schema = load_extraction_schema()
    field = schema.by_id("etude_de_sol")
    assert field is not None
    assert field.section == "principal"
    assert field.reference_categories == ["TECH/ETUDE DE SOL"]
    assert schema.by_id("inexistant") is None
