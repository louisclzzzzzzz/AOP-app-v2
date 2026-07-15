from __future__ import annotations

from app.classify.taxonomy import load_taxonomy


def test_taxonomy_loads_and_has_unique_paths():
    taxonomy = load_taxonomy()
    paths = [c.path for c in taxonomy.categories]
    assert len(paths) == len(set(paths)), "chemins de catégorie dupliqués dans taxonomy.yaml"
    assert len(paths) > 10


def test_fallback_category_exists_in_categories():
    taxonomy = load_taxonomy()
    assert taxonomy.by_path(taxonomy.fallback_category) is not None


def test_core_plan_categories_present():
    """Vérifie la présence des catégories-clés de l'arborescence-squelette (§4.2 du PLAN)."""
    taxonomy = load_taxonomy()
    paths = set(taxonomy.paths())
    expected = {
        "1.ETUDE BD",
        "ADMIN/AAPC",
        "ADMIN/RC",
        "ASS/CCAP",
        "ASS/CCTP",
        "ASS/ATT ASS/ENT",
        "ASS/ATT ASS/MOE",
        "ASS/DEROG COM",
        "ASS/MARCHE SIGNE",
        "ENVOI DEMAT/CANDIDATURE",
        "ENVOI DEMAT/OFFRE",
        "ENVOI DEMAT/COPIE DEPOT",
        "QR",
        "TECH/CCTP TRAVAUX",
        "TECH/ETUDE DE SOL",
        "TECH/PLANS",
        "TECH/RICT",
        "TECH/ARRETE PC",
        "TECH/SOCABAT",
        "TECH/AUTRES",
    }
    assert expected <= paths


def test_every_category_has_a_label():
    taxonomy = load_taxonomy()
    for c in taxonomy.categories:
        assert c.label.strip()
