from __future__ import annotations

import json

from app.classify.reorg import REPORT_JSON_FILENAME, REPORT_MD_FILENAME, apply_reorganization
from app.store.db import session_scope
from app.store.models import ClassificationStatus, FileCategory
from app.store.repository import create_dossier, create_document, get_dossier


def _make_source_file(source_dir, relative_path: str, content: str = "contenu") -> None:
    path = source_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_doc(session, dossier_id, *, relative_path, final_category, final_lot=None, final_filename=None):
    filename = relative_path.rsplit("/", 1)[-1]
    return create_document(
        session,
        dossier_id=dossier_id,
        relative_path=relative_path,
        filename=filename,
        extension=".pdf",
        size_bytes=8,
        sha256=f"hash-{relative_path}",
        category=FileCategory.PDF.value,
        is_analyzable=True,
        classification_status=ClassificationStatus.PROPOSED.value,
        final_category=final_category,
        final_lot=final_lot,
        final_doc_type="TEST",
        final_filename=final_filename or f"{final_category.replace('/', '_')}_{filename}",
        classification_confidence=0.8,
        classification_justification="justification de test",
    )


def test_apply_reorganization_copies_files_and_never_touches_source(tmp_path, isolated_workspace):
    source_dir = tmp_path / "source"
    organized_root = tmp_path / "organized"
    _make_source_file(source_dir, "ADMIN/RC 2024.pdf", "contenu RC")
    _make_source_file(source_dir, "ASS/CCAP.pdf", "contenu CCAP")

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _create_doc(s, dossier_id, relative_path="ADMIN/RC 2024.pdf", final_category="ADMIN/RC")
        _create_doc(s, dossier_id, relative_path="ASS/CCAP.pdf", final_category="ASS/CCAP", final_lot="1")

    original_rc_bytes = (source_dir / "ADMIN/RC 2024.pdf").read_bytes()

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)

    assert report["total_files"] == 2
    assert (organized_root / "ADMIN" / "RC").exists()
    rc_files = list((organized_root / "ADMIN" / "RC").iterdir())
    assert len(rc_files) == 1
    assert rc_files[0].read_text(encoding="utf-8") == "contenu RC"

    ccap_dir = organized_root / "ASS" / "CCAP" / "LOT 1"
    assert ccap_dir.exists()
    assert len(list(ccap_dir.iterdir())) == 1

    # La source n'est jamais modifiée
    assert (source_dir / "ADMIN/RC 2024.pdf").read_bytes() == original_rc_bytes

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.reorg_report_json_path == REPORT_JSON_FILENAME
        assert dossier.reorg_report_md_path == REPORT_MD_FILENAME
        assert dossier.reorg_applied_at is not None

    report_path = tmp_path / REPORT_JSON_FILENAME
    on_disk_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert on_disk_report["total_files"] == 2

    md_report = (tmp_path / REPORT_MD_FILENAME).read_text(encoding="utf-8")
    assert "ADMIN/RC" in md_report
    assert "ASS/CCAP" in md_report


def test_apply_reorganization_deduplicates_name_collisions(tmp_path, isolated_workspace):
    source_dir = tmp_path / "source"
    organized_root = tmp_path / "organized"
    _make_source_file(source_dir, "a/doc1.pdf", "un")
    _make_source_file(source_dir, "b/doc2.pdf", "deux")

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _create_doc(
            s, dossier_id, relative_path="a/doc1.pdf", final_category="TECH/AUTRES", final_filename="MEME_NOM.pdf"
        )
        _create_doc(
            s, dossier_id, relative_path="b/doc2.pdf", final_category="TECH/AUTRES", final_filename="MEME_NOM.pdf"
        )

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)

    target_dir = organized_root / "TECH" / "AUTRES"
    names = sorted(p.name for p in target_dir.iterdir())
    assert names == ["MEME_NOM-2.pdf", "MEME_NOM.pdf"]


def test_apply_reorganization_survives_a_missing_source_file(tmp_path, isolated_workspace):
    """AUDIT_BACKEND.md §9 : un fichier source manquant sur disque (incohérence DB/FS) ne doit
    pas faire planter toute l'opération à mi-parcours — les autres documents doivent quand
    même être copiés, et l'échec doit être consigné dans le rapport plutôt que masqué."""
    source_dir = tmp_path / "source"
    organized_root = tmp_path / "organized"
    _make_source_file(source_dir, "ADMIN/RC 2024.pdf", "contenu RC")
    # ASS/CCAP.pdf n'est volontairement PAS créé sur disque, malgré son entrée DB.

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _create_doc(s, dossier_id, relative_path="ADMIN/RC 2024.pdf", final_category="ADMIN/RC")
        _create_doc(s, dossier_id, relative_path="ASS/CCAP.pdf", final_category="ASS/CCAP")

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)

    # Le document dont le fichier source existe est bien copié...
    assert report["total_files"] == 1
    assert (organized_root / "ADMIN" / "RC").exists()

    # ...et l'échec de l'autre est consigné, pas masqué.
    assert report["files_failed"] == 1
    assert report["failures"][0]["source"] == "ASS/CCAP.pdf"

    # L'opération se termine normalement (pas d'exception, rapport généré, statut appliqué) :
    # le dossier n'est pas bloqué dans un état intermédiaire.
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.reorg_applied_at is not None

    md_report = (tmp_path / REPORT_MD_FILENAME).read_text(encoding="utf-8")
    assert "ASS/CCAP.pdf" in md_report


def test_apply_reorganization_is_idempotent_on_rerun(tmp_path, isolated_workspace):
    """Une seconde application (résumabilité) reconstruit organized/ proprement, sans
    accumuler d'anciennes copies."""
    source_dir = tmp_path / "source"
    organized_root = tmp_path / "organized"
    _make_source_file(source_dir, "ADMIN/RC 2024.pdf", "contenu RC")

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _create_doc(s, dossier_id, relative_path="ADMIN/RC 2024.pdf", final_category="ADMIN/RC")

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        apply_reorganization(s, dossier, source_dir=source_dir, organized_root=organized_root)

    rc_files = list((organized_root / "ADMIN" / "RC").iterdir())
    assert len(rc_files) == 1
