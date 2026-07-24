from __future__ import annotations

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.synthesis.schema import load_synthesis_schema


def test_synthesis_schema_loads_and_has_unique_ids():
    schema = load_synthesis_schema()
    ids = [t.id for t in schema.topics]
    assert len(ids) == len(set(ids)), "ids de thème dupliqués dans synthese_projet_schema.yaml"
    assert len(ids) == 13


def test_extraction_field_ids_are_valid():
    schema = load_synthesis_schema()
    extraction_schema = load_extraction_schema()
    ids = {f.id for f in extraction_schema.fields}
    for topic in schema.topics:
        for field_id in topic.extraction_field_ids + topic.grounding_field_ids:
            assert field_id in ids, f"{topic.id} référence un champ d'extraction inconnu : {field_id}"


def test_pivot_categories_are_valid_taxonomy_paths():
    schema = load_synthesis_schema()
    taxonomy = load_taxonomy()
    for topic in schema.topics:
        for category in topic.pivot_categories:
            assert taxonomy.by_path(category) is not None, (
                f"{topic.id} référence une catégorie taxonomie inconnue : {category}"
            )


def test_extraction_fields_topic_has_no_pivot_categories_requirement():
    schema = load_synthesis_schema()
    identite = schema.by_id("identite_operation")
    assert identite is not None
    assert identite.source == "extraction_fields"
    assert identite.extraction_field_ids == ["nom_moa", "adresse_moa", "nom_chantier", "adresse_chantier"]


def test_document_sourced_topics_have_pivot_categories_and_instructions():
    schema = load_synthesis_schema()
    for topic in schema.topics:
        if topic.source == "documents":
            assert topic.pivot_categories, f"{topic.id} sans pivot_categories"
            assert topic.instructions, f"{topic.id} sans instructions"


def test_by_id_returns_none_for_unknown_topic():
    schema = load_synthesis_schema()
    assert schema.by_id("inexistant") is None


def test_destination_ambition_sees_cctp_travaux_and_flags_contradictions():
    """Cas réel trouvé sur dce_grand_pic2 (§ANALYSE_ORIGINE_ERREURS.md) : le classement ERP
    était donné différemment par un CCTP (TECH/CCTP TRAVAUX) et par un rapport SDIS embarqué dans
    l'arrêté PC — mais destination_ambition ne regardait pas TECH/CCTP TRAVAUX, donc ne pouvait
    jamais voir la contradiction ni la signaler."""
    schema = load_synthesis_schema()
    topic = schema.by_id("destination_ambition")
    assert topic is not None
    categories = topic.pivot_categories
    assert "TECH/CCTP TRAVAUX" in categories

    # Régression constatée en testant ce changement : TECH/CCTP TRAVAUX compte souvent 15-25
    # documents (un par lot), assez pour épuiser le budget de contexte à lui seul. Le mettre
    # avant TECH/ARRETE PC (1 seul document, celui qui porte la version concurrente) fait qu'on
    # ne voit plus QUE la version CCTP — l'inverse de l'effet recherché. TECH/ARRETE PC doit donc
    # rester prioritaire (plus tôt dans la liste) sur TECH/CCTP TRAVAUX.
    assert categories.index("TECH/ARRETE PC") < categories.index("TECH/CCTP TRAVAUX")
    assert topic.cross_document is True
    assert "contredis" in topic.instructions

    # Comparaison avec le rapport de référence validé (Le Grand Pic) : le RICT contient la
    # formulation qui réconcilie arrêté PC et CCTP ("bâtiments d'habitation (3ème famille B)...
    # classés ERP 5") — sans lui, seule la version arrêté PC ("ERP type O, catégorie 2") était
    # visible. TECH/RICT est un document unique, doit rester garanti comme TECH/ARRETE PC.
    assert "TECH/RICT" in categories
    assert categories.index("TECH/RICT") < categories.index("TECH/CCTP TRAVAUX")


def test_qualification_operation_sees_arrete_pc_and_rict_for_niveaux_count():
    """Cas réel trouvé sur dce_grand_pic2 : sans TECH/ARRETE PC ni TECH/RICT, ce thème ne voyait
    le nombre de niveaux que via une plage nommée dans le CCTP/la notice (ex. "du niveau -8 au
    niveau +3"), que le LLM a confondu avec un total ("8 niveaux" — faux, la plage compte 12
    niveaux, et contredit l'arrêté PC qui donne le compte exact par bâtiment : 12/13/11)."""
    schema = load_synthesis_schema()
    topic = schema.by_id("qualification_operation")
    assert topic is not None
    categories = topic.pivot_categories
    assert "TECH/ARRETE PC" in categories
    assert "TECH/RICT" in categories


def test_economie_projet_sees_dpgf():
    """Cas réel trouvé sur dce_grand_pic2 : ASS/CCAP, ASS/CCTP, ASS/RC (catégories assurance)
    sont absentes d'un DCE brut, alors que le DPGF (décomposition du prix, par lot) y est bien
    présent — sans TECH/DPGF dans le périmètre, ce thème ne trouvait jamais aucun document."""
    schema = load_synthesis_schema()
    topic = schema.by_id("economie_projet")
    assert topic is not None
    assert "TECH/DPGF" in topic.pivot_categories


def test_equipe_projet_sees_notice_cctp_and_rict():
    """Cas réel trouvé sur dce_grand_pic2 : les noms d'acteurs (architecte, BET, bureau de
    contrôle) figurent en page de garde de la notice/du CCTP/du RICT, pas seulement dans des
    pièces d'assurance absentes d'un DCE brut."""
    schema = load_synthesis_schema()
    topic = schema.by_id("equipe_projet")
    assert topic is not None
    categories = topic.pivot_categories
    assert "TECH/NOTICE" in categories
    assert "TECH/RICT" in categories
    assert "TECH/CCTP TRAVAUX" in categories
