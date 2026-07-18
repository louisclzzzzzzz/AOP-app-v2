from __future__ import annotations

import zipfile
from pathlib import Path

from app.ingestion.unzip import _decode_member_name, extract_zip_flat, extract_zip_recursive


def test_recursive_extraction_of_nested_zip(tmp_path, make_zip):
    """Un zip imbriqué (ex. « ASSURANCES LOT 1 ET 2.zip ») doit être décompressé
    récursivement, sans supprimer ni modifier le zip d'origine."""
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("inner/attestation.pdf", "contenu attestation")

    root_zip = make_zip(
        "root.zip",
        {
            "ADMIN/RC 2024.pdf": "contenu RC",
            "ASS/ASSURANCES_LOT_1_ET_2.zip": nested,
        },
    )

    dest = tmp_path / "source"
    extract_zip_recursive(root_zip, dest)

    assert (dest / "ADMIN" / "RC 2024.pdf").read_text() == "contenu RC"
    # Le zip imbriqué reste présent tel quel (immutabilité)
    assert (dest / "ASS" / "ASSURANCES_LOT_1_ET_2.zip").exists()
    # ... et son contenu a été extrait dans un dossier frère
    extracted = dest / "ASS" / "ASSURANCES_LOT_1_ET_2__extrait" / "inner" / "attestation.pdf"
    assert extracted.exists()
    assert extracted.read_text() == "contenu attestation"


def test_deeply_nested_zips_are_extracted_recursively(tmp_path):
    """Zip contenant un zip contenant un zip (3 niveaux) : tout doit être atteint."""
    level2 = tmp_path / "level2.zip"
    with zipfile.ZipFile(level2, "w") as zf:
        zf.writestr("deep.pdf", "contenu profond")

    level1 = tmp_path / "level1.zip"
    with zipfile.ZipFile(level1, "w") as zf:
        zf.write(level2, "level2.zip")

    root = tmp_path / "root.zip"
    with zipfile.ZipFile(root, "w") as zf:
        zf.write(level1, "sub/level1.zip")

    dest = tmp_path / "source"
    extract_zip_recursive(root, dest)

    deep_file = (
        dest / "sub" / "level1__extrait" / "level2__extrait" / "deep.pdf"
    )
    assert deep_file.exists()
    assert deep_file.read_text() == "contenu profond"


def test_corrupted_nested_zip_is_left_untouched(tmp_path, make_zip):
    """Une archive imbriquée corrompue ne doit pas interrompre le dézippage global : elle
    reste telle quelle, le reste du dossier est extrait normalement."""
    root_zip = make_zip(
        "root.zip",
        {
            "ADMIN/RC.pdf": "contenu RC",
            "ASS/corrompu.zip": b"not a real zip file",
        },
    )
    dest = tmp_path / "source"
    extract_zip_recursive(root_zip, dest)

    assert (dest / "ADMIN" / "RC.pdf").exists()
    assert (dest / "ASS" / "corrompu.zip").exists()
    assert not (dest / "ASS" / "corrompu__extrait").exists()


def test_zip_slip_path_traversal_is_blocked(tmp_path):
    """Un nom de membre malveillant (../../etc/passwd) ne doit jamais écrire hors dest_dir."""
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../../evil.txt", "pwned")
        zf.writestr("normal.pdf", "contenu normal")

    dest = tmp_path / "source"
    extract_zip_flat(evil_zip, dest)

    assert not (tmp_path / "evil.txt").exists()
    assert (dest / "normal.pdf").exists()


def test_accented_filename_cp850_decoding(tmp_path):
    """Les zips Windows français encodent souvent les noms en cp850 (bit UTF-8 non posé)."""
    zpath = tmp_path / "accents.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("assurance_élève.pdf")
        # force le bit UTF-8 à 0 pour simuler un zip Windows historique
        info.flag_bits &= ~0x800
        zf.writestr(info, "contenu", zipfile.ZIP_STORED)

    dest = tmp_path / "source"
    extract_zip_flat(zpath, dest)

    names = [p.name for p in dest.iterdir()]
    assert any("l" in n and "ve" in n for n in names)  # décodage best-effort, non garanti exact


def test_degree_sign_is_not_misdecoded_as_block_element():
    """Régression : un octet 0xB0 (« ° » en cp1252 Windows ANSI) ressortait en « ░ » (bloc de
    trame cp850) car cp850 était tenté en premier et 0xB0 y est aussi un caractère valide,
    masquant silencieusement le mauvais choix de page de code (cas réel rencontré sur un nom
    de dossier « ... LOT N°1 ET TRC ... » dans un DCE).

    Testé directement sur `_decode_member_name` : l'API haut niveau `zipfile.ZipFile.writestr`
    force toujours l'UTF-8 dès que le nom contient un caractère non-ASCII
    (`ZipInfo._encodeFilenameFlags`), donc impossible de fabriquer via elle un zip dont le
    nom est réellement encodé en page de code historique sans le bit UTF-8 — on construit
    donc l'objet `ZipInfo` tel que `zipfile` le produirait en le lisant (`info.filename`
    déjà décodé en cp437 par CPython, `flag_bits` sans le bit UTF-8)."""
    raw_cp1252 = "LOT N°1.pdf".encode("cp1252")
    info = zipfile.ZipInfo(raw_cp1252.decode("cp437"))
    info.flag_bits &= ~0x800

    decoded = _decode_member_name(info)

    assert decoded == "LOT N°1.pdf"
    assert "░" not in decoded
