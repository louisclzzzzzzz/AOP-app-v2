from __future__ import annotations

from app.classify.naming import build_normalized_filename, dedupe_target_filename


def test_build_normalized_filename_basic():
    name = build_normalized_filename(
        category_path="ASS/CCAP", lot=None, doc_type="CCAP", original_filename="CCAP 2024.pdf"
    )
    assert name == "ASS_CCAP_CCAP-2024.pdf"


def test_build_normalized_filename_with_lot():
    name = build_normalized_filename(
        category_path="ASS/CCTP", lot="1 ET 2", doc_type="CCTP", original_filename="Lot 1 _ DO 2024.pdf"
    )
    assert name.startswith("ASS_LOT1-ET-2_CCTP_")
    assert name.endswith(".pdf")


def test_build_normalized_filename_strips_accents_and_specials():
    name = build_normalized_filename(
        category_path="TECH/ETUDE DE SOL",
        lot=None,
        doc_type="SOL",
        original_filename="Rapport géotechnique été n°2 (V1).pdf",
    )
    assert name.isascii()
    assert " " not in name
    assert name.endswith(".pdf")


def test_build_normalized_filename_preserves_extension_case_insensitively():
    name = build_normalized_filename(
        category_path="TECH/PLANS", lot=None, doc_type="PLAN", original_filename="plan.PDF"
    )
    assert name.endswith(".pdf")


def test_build_normalized_filename_never_cuts_a_word_in_the_middle():
    """Régression : un libellé long tronqué à une largeur fixe pouvait finir en plein mot
    (ex. « ...AE-LO » au lieu de « ...AE-LOT »), donnant l'impression d'un nom cassé."""
    name = build_normalized_filename(
        category_path="ADMIN/AE",
        lot="1",
        doc_type="AE",
        original_filename=(
            "AE LOT 1 DO CONSERVATOIRE DCE DO ET AE LOT N°1 ET TRC ET AE LOT N°2.docx"
        ),
    )
    libelle = name.split(".")[0].split("_")[-1]
    assert not libelle.endswith("-LO"), name
    # jamais de "-" en toute fin (signe qu'on a coupé pile après un séparateur de mot)
    assert not libelle.endswith("-"), name


def test_build_normalized_filename_distinguishes_sibling_lots():
    """Régression concrète (FRICTIONS_EXPERT_METIER.md §3) : deux fichiers de lots différents
    dont le nom d'origine ne diffère que sur la fin (numéro de lot) ne doivent plus produire
    un libellé tronqué identique, sans quoi l'arborescence proposée les rend indiscernables."""
    common_prefix = "CONSERVATOIRE DCE DO ET AE LOT N°1 ET TRC ET AE LOT N°"
    name_lot1 = build_normalized_filename(
        category_path="ADMIN/AE", lot="1", doc_type="AE", original_filename=f"{common_prefix}1.docx"
    )
    name_lot2 = build_normalized_filename(
        category_path="ADMIN/AE", lot="2", doc_type="AE", original_filename=f"{common_prefix}2.docx"
    )
    libelle_lot1 = name_lot1.split(".")[0].split("_")[-1]
    libelle_lot2 = name_lot2.split(".")[0].split("_")[-1]
    assert libelle_lot1 != libelle_lot2, (name_lot1, name_lot2)


def test_dedupe_target_filename_no_collision():
    assert dedupe_target_filename("a.pdf", set()) == "a.pdf"


def test_dedupe_target_filename_collision_adds_suffix():
    taken = {"a.pdf"}
    result = dedupe_target_filename("a.pdf", taken)
    assert result == "a-2.pdf"


def test_dedupe_target_filename_multiple_collisions():
    taken = {"a.pdf", "a-2.pdf", "a-3.pdf"}
    result = dedupe_target_filename("a.pdf", taken)
    assert result == "a-4.pdf"
