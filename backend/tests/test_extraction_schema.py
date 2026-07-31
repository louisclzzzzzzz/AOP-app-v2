from __future__ import annotations

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.settings import get_models_config


def test_extraction_schema_loads_and_has_unique_ids():
    schema = load_extraction_schema()
    ids = [f.id for f in schema.fields]
    assert len(ids) == len(set(ids)), "ids de champs dupliqués dans extraction_schema.yaml"
    assert len(ids) == 50


def test_every_field_belongs_to_a_declared_section():
    schema = load_extraction_schema()
    declared = {s.id for s in schema.sections}
    assert declared, "aucune section déclarée dans extraction_schema.yaml"
    for f in schema.fields:
        assert f.section in declared, f"{f.id} référence une section inconnue : {f.section}"


def test_by_section_follows_declaration_order_and_covers_all_fields():
    schema = load_extraction_schema()
    by_section = schema.by_section()
    # Ordre de déclaration des sections, pas ordre d'apparition des champs.
    assert list(by_section) == [s.id for s in schema.sections if s.id in by_section]
    assert sum(len(v) for v in by_section.values()) == len(schema.fields)


def test_section_label_falls_back_to_id_for_unknown_section():
    schema = load_extraction_schema()
    assert schema.section_label("identification") == "Identification de l'opération"
    assert schema.section_label("section_supprimee") == "section_supprimee"


def test_v2_specific_fields_are_present():
    """Champs ajoutés par la Feuil2 de `donnes_ref_v2.md` et absents de la v1 — ils portent des
    conditions directes du tableau des conventions (Feuil1)."""
    schema = load_extraction_schema()
    ids = {f.id for f in schema.fields}
    for field_id in (
        "localisation",
        "montants_garanties_demandes",
        "hauteur_totale_batiment",
        "surfaces_au_sol",
        "duree_chantier_mois",
        "type_zone",
        "niveaux_sous_sol",
        "profondeur_sous_sol",
        "emprise_sous_sol",
        "pollution_sols",
        "structure_ossature",
        "portee_max_appuis",
        "presence_reemploi",
        "presence_photovoltaique",
        "ouvrage_bois",
        "equipements_machines",
        "duree_essais",
        "duree_montage",
        "duree_garantie_maintenance",
        "cas_particuliers",
    ):
        assert field_id in ids, f"{field_id} (Feuil2 v2) absent du schéma"


def test_narrative_and_georisques_rows_are_not_extraction_fields():
    """Les 3 rédactions longues de la Feuil2 relèvent de la Phase 1 (synthèse projet) et « Présence
    d'un risque » de la Phase 2 (API Géorisques) : aucune ne doit devenir un champ d'extraction."""
    schema = load_extraction_schema()
    ids = {f.id for f in schema.fields}
    for field_id in (
        "nature_fonction_ouvrage",
        "objectifs_specifiques",
        "description_operation",
        "presence_risque",
    ):
        assert field_id not in ids


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
    assert field.section == "sol_soussol"
    assert field.reference_categories == ["TECH/ETUDE DE SOL"]
    assert schema.by_id("inexistant") is None
