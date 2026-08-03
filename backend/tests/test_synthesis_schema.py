from __future__ import annotations

from app.classify.taxonomy import load_taxonomy
from app.extraction.extraction_schema import load_extraction_schema
from app.synthesis.schema import load_synthesis_schema


def test_synthesis_schema_loads_and_has_unique_ids():
    schema = load_synthesis_schema()
    ids = [t.id for t in schema.topics]
    assert len(ids) == len(set(ids)), "ids de thème dupliqués dans synthese_projet_schema.yaml"
    assert len(ids) == 15


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
    assert not identite.pivot_categories
    # Carte d'identité : rendue en tableau, sans aucun appel LLM.
    assert identite.format == "tableau"
    assert identite.extraction_field_ids[:4] == ["nom_moa", "adresse_moa", "nom_chantier", "adresse_chantier"]
    # Données de souscription ajoutées par la Feuil2 v2, qui n'existaient dans aucun thème avant.
    for field_id in ("localisation", "type_zone", "montants_garanties_demandes", "duree_chantier_mois"):
        assert field_id in identite.extraction_field_ids


def test_max_lignes_caps_every_llm_generated_topic():
    """Le plafond de volume est le levier chiffré de la réduction de la Phase 1 : un thème appelant
    le LLM sans plafond est un thème qui peut repartir en essai de 10 000 caractères."""
    schema = load_synthesis_schema()
    for topic in schema.topics:
        if topic.source == "documents":
            assert topic.max_lignes, f"{topic.id} : thème LLM sans max_lignes"
            assert topic.max_lignes <= 40, f"{topic.id} : max_lignes trop permissif"


def test_tables_are_the_default_form():
    """Doctrine de forme : la prose reste l'exception. On borne le nombre de thèmes en prose pure
    pour qu'un ajout futur de thème narratif soit un choix conscient, pas une dérive."""
    schema = load_synthesis_schema()
    prose_only = [t.id for t in schema.topics if t.format == "prose"]
    assert set(prose_only) == {"description_operation", "diagnostic_existant"}


def test_document_sourced_topics_have_pivot_categories_and_instructions():
    schema = load_synthesis_schema()
    for topic in schema.topics:
        if topic.source == "documents":
            assert topic.pivot_categories, f"{topic.id} sans pivot_categories"
            assert topic.instructions, f"{topic.id} sans instructions"


def test_by_id_returns_none_for_unknown_topic():
    schema = load_synthesis_schema()
    assert schema.by_id("inexistant") is None


def test_nature_fonction_ouvrage_sees_cctp_travaux_and_flags_contradictions():
    """Cas réel trouvé sur dce_grand_pic2 (§ANALYSE_ORIGINE_ERREURS.md) : le classement ERP
    était donné différemment par un CCTP (TECH/CCTP TRAVAUX) et par un rapport SDIS embarqué dans
    l'arrêté PC — mais le thème ne regardait pas TECH/CCTP TRAVAUX, donc ne pouvait jamais voir la
    contradiction ni la signaler.

    Le thème s'appelait `destination_ambition` ; la Feuil2 v2 l'a scindé en trois
    (`nature_fonction_ouvrage`, `description_operation`, `objectifs_specifiques`) et c'est le
    premier qui porte désormais la qualification réglementaire — donc cette garantie."""
    schema = load_synthesis_schema()
    topic = schema.by_id("nature_fonction_ouvrage")
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
    # La consigne doit continuer d'exiger que la divergence soit rendue visible plutôt qu'arbitrée
    # en silence — c'est le fond de la régression protégée ici, indépendamment de sa formulation.
    assert "divergence" in topic.instructions
    assert "ne tranche pas en silence" in topic.instructions

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
