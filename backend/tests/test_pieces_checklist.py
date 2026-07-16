from __future__ import annotations

from app.classify.taxonomy import load_taxonomy
from app.completeness.pieces_checklist import load_pieces_checklist


def test_pieces_checklist_loads_and_has_unique_ids():
    checklist = load_pieces_checklist()
    ids = [p.id for p in checklist.pieces]
    assert len(ids) == len(set(ids)), "ids de pièces dupliqués dans pieces_checklist.yaml"
    assert len(ids) >= 15


def test_all_three_phases_represented():
    checklist = load_pieces_checklist()
    phases = {p.phase for p in checklist.pieces}
    assert phases == {"A", "B", "C"}


def test_obligatoire_pieces_have_indices():
    checklist = load_pieces_checklist()
    for p in checklist.pieces:
        if p.obligatoire:
            assert p.indices, f"{p.id} est obligatoire mais n'a aucun indice de détection"


def test_categorie_attendue_is_a_valid_taxonomy_path_when_set():
    checklist = load_pieces_checklist()
    taxonomy = load_taxonomy()
    for p in checklist.pieces:
        if p.categorie_attendue is not None:
            assert (
                taxonomy.by_path(p.categorie_attendue) is not None
            ), f"{p.id} référence une catégorie taxonomie inconnue : {p.categorie_attendue}"


def test_by_id_and_by_phase():
    checklist = load_pieces_checklist()
    piece = checklist.by_id("etude_sol_g2pro")
    assert piece is not None
    assert piece.phase == "A"
    assert checklist.by_id("inexistant") is None

    by_phase = checklist.by_phase()
    assert set(by_phase.keys()) == {"A", "B", "C"}
    assert all(p.phase == "A" for p in by_phase["A"])


def test_attestation_decennale_is_par_lot():
    checklist = load_pieces_checklist()
    piece = checklist.by_id("attestation_decennale_par_lot")
    assert piece is not None
    assert piece.par_lot is True
    assert piece.peut_etre_inclus_dans_autre is True
