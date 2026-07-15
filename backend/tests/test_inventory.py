from __future__ import annotations

from app.ingestion.inventory import build_inventory, hash_file
from app.ingestion.unzip import extract_zip_recursive
from app.store.db import session_scope
from app.store.repository import create_dossier, get_dossier


def _build(tmp_path, make_zip, entries):
    root_zip = make_zip("root.zip", entries)
    dest = tmp_path / "source"
    extract_zip_recursive(root_zip, dest)
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        docs = build_inventory(s, dossier, dest)
        # détacher les valeurs utiles avant la fermeture de session
        return [
            {
                "relative_path": d.relative_path,
                "category": d.category,
                "is_analyzable": d.is_analyzable,
                "non_analyzable_reason": d.non_analyzable_reason,
                "parent_archive_id": d.parent_archive_id,
                "sha256": d.sha256,
                "id": d.id,
            }
            for d in docs
        ]


def test_dematerialise_files_marked_non_analyzable(tmp_path, isolated_workspace, make_zip):
    docs = _build(
        tmp_path,
        make_zip,
        {
            "ENVOI DEMAT/COPIE DEPOT/candidature.cle": "",
            "ENVOI DEMAT/COPIE DEPOT/candidature.cry": "",
            "ENVOI DEMAT/COPIE DEPOT/candidature.iv": "",
            "ENVOI DEMAT/COPIE DEPOT/CSL.pli": "",
            "ENVOI DEMAT/COPIE DEPOT/descripteur.xml": "<xml/>",
            "ADMIN/RC.pdf": "contenu RC",
        },
    )
    by_path = {d["relative_path"]: d for d in docs}

    for demat_file in [
        "ENVOI DEMAT/COPIE DEPOT/candidature.cle",
        "ENVOI DEMAT/COPIE DEPOT/candidature.cry",
        "ENVOI DEMAT/COPIE DEPOT/candidature.iv",
        "ENVOI DEMAT/COPIE DEPOT/CSL.pli",
        "ENVOI DEMAT/COPIE DEPOT/descripteur.xml",
    ]:
        assert by_path[demat_file]["category"] == "dematerialise"
        assert by_path[demat_file]["is_analyzable"] is False
        assert by_path[demat_file]["non_analyzable_reason"]

    assert by_path["ADMIN/RC.pdf"]["is_analyzable"] is True


def test_duplicate_content_shares_same_hash(tmp_path, isolated_workspace, make_zip):
    """Deux fichiers différents mais au contenu identique doivent porter le même hash
    (base du cache OCR partagé — jamais deux fois le même document OCRisé)."""
    docs = _build(
        tmp_path,
        make_zip,
        {
            "ADMIN/RC 2024.pdf": "contenu identique",
            "ASS/RC_copie.pdf": "contenu identique",
            "ASS/AUTRE.pdf": "contenu different",
        },
    )
    by_path = {d["relative_path"]: d for d in docs}
    assert by_path["ADMIN/RC 2024.pdf"]["sha256"] == by_path["ASS/RC_copie.pdf"]["sha256"]
    assert by_path["ADMIN/RC 2024.pdf"]["sha256"] != by_path["ASS/AUTRE.pdf"]["sha256"]


def test_extracted_archive_traceability(tmp_path, isolated_workspace, make_zip):
    """Un fichier issu d'un zip imbriqué doit référencer le document de l'archive parente,
    et l'archive elle-même doit être marquée non analysable mais conservée."""
    import zipfile

    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("inner/attestation.pdf", "contenu attestation")

    docs = _build(
        tmp_path,
        make_zip,
        {
            "ASS/ASSURANCES.zip": nested,
        },
    )
    by_path = {d["relative_path"]: d for d in docs}
    zip_doc = by_path["ASS/ASSURANCES.zip"]
    inner_doc = by_path["ASS/ASSURANCES__extrait/inner/attestation.pdf"]

    assert zip_doc["category"] == "archive"
    assert zip_doc["is_analyzable"] is False
    assert "extraite" in zip_doc["non_analyzable_reason"]
    assert inner_doc["parent_archive_id"] == zip_doc["id"]
    assert inner_doc["is_analyzable"] is True


def test_dwg_and_system_noise_marked_non_analyzable(tmp_path, isolated_workspace, make_zip):
    docs = _build(
        tmp_path,
        make_zip,
        {
            "PLANS/AR 010 - Plan masse.dwg": "binaire cao",
            "PLANS/Thumbs.db": "noise",
            "ADMIN/RC.pdf": "contenu texte, pas un plan",
        },
    )
    by_path = {d["relative_path"]: d for d in docs}
    assert by_path["PLANS/AR 010 - Plan masse.dwg"]["is_analyzable"] is False
    assert by_path["PLANS/Thumbs.db"]["is_analyzable"] is False
    assert by_path["ADMIN/RC.pdf"]["is_analyzable"] is True


def test_plan_pdf_and_image_marked_non_analyzable_by_filename(tmp_path, isolated_workspace, make_zip):
    """Un plan au format PDF/image (pas seulement DWG/DXF) doit être exclu de l'OCR par
    signal nom de fichier seul (taxonomie TECH/PLANS) : son contenu graphique n'apporte
    rien à l'analyse et son volume de pages peut être important."""
    docs = _build(
        tmp_path,
        make_zip,
        {
            "PLANS/AR 010 - Plan masse.pdf": "contenu plan",
            "PLANS/Facade nord.jpg": "contenu image plan",
            "ADMIN/RC.pdf": "contenu texte, pas un plan",
        },
    )
    by_path = {d["relative_path"]: d for d in docs}
    assert by_path["PLANS/AR 010 - Plan masse.pdf"]["is_analyzable"] is False
    assert "OCR non nécessaire" in by_path["PLANS/AR 010 - Plan masse.pdf"]["non_analyzable_reason"]
    assert by_path["PLANS/Facade nord.jpg"]["is_analyzable"] is False
    assert by_path["ADMIN/RC.pdf"]["is_analyzable"] is True


def test_hash_file_matches_sha256(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_bytes(b"hello world")
    import hashlib

    expected = hashlib.sha256(b"hello world").hexdigest()
    assert hash_file(f) == expected
