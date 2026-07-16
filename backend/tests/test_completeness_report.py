from __future__ import annotations

import json

from app.completeness.report import REPORT_JSON_FILENAME, REPORT_MD_FILENAME, validate_completeness
from app.store.db import session_scope
from app.store.models import FileCategory
from app.store.repository import create_completeness_check, create_document, create_dossier, get_dossier


def _create_doc(session, dossier_id, *, doc_id_suffix, relative_path, final_category):
    filename = relative_path.rsplit("/", 1)[-1]
    return create_document(
        session,
        dossier_id=dossier_id,
        relative_path=relative_path,
        filename=filename,
        extension=".pdf",
        size_bytes=8,
        sha256=f"hash-{doc_id_suffix}",
        category=FileCategory.PDF.value,
        is_analyzable=True,
        final_category=final_category,
    )


def test_validate_completeness_writes_json_and_md_reports(tmp_path, isolated_workspace):
    dossier_dir = tmp_path / "dossier"
    dossier_dir.mkdir()

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        doc = _create_doc(
            s, dossier_id, doc_id_suffix="1", relative_path="TECH/etude_sol.pdf", final_category="TECH/ETUDE DE SOL"
        )
        create_completeness_check(
            s,
            dossier_id=dossier_id,
            piece_id="etude_sol_g2pro",
            is_selected=True,
            status="proposed",
            match_layer="file",
            proposed_presence="present",
            proposed_certainty="certain",
            proposed_justification="Document classé directement dans TECH/ETUDE DE SOL.",
            proposed_matched_document_ids_json=json.dumps([doc.id]),
            final_presence="present",
            final_certainty="certain",
        )
        create_completeness_check(
            s,
            dossier_id=dossier_id,
            piece_id="rict_initial",
            is_selected=True,
            status="proposed",
            match_layer="none",
            proposed_presence="absent",
            proposed_certainty="certain",
            proposed_justification="Aucun document classé dans TECH/RICT.",
            final_presence="absent",
            final_certainty="certain",
        )
        # Pièce non sélectionnée par l'utilisateur : ne doit pas apparaître dans le rapport Markdown
        create_completeness_check(s, dossier_id=dossier_id, piece_id="rfct_final", is_selected=False)

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = validate_completeness(s, dossier, dossier_dir=dossier_dir)

    assert report["total_pieces_selected"] == 2
    by_id = {e["piece_id"]: e for e in report["entries"]}
    assert by_id["etude_sol_g2pro"]["presence"] == "present"
    assert by_id["etude_sol_g2pro"]["matched_documents"][0]["relative_path"] == "TECH/etude_sol.pdf"
    assert by_id["rict_initial"]["presence"] == "absent"
    assert by_id["rfct_final"]["is_selected"] is False

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.completeness_report_json_path == REPORT_JSON_FILENAME
        assert dossier.completeness_report_md_path == REPORT_MD_FILENAME
        assert dossier.completeness_validated_at is not None

    json_report = json.loads((dossier_dir / REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    assert json_report["total_pieces_selected"] == 2

    md_report = (dossier_dir / REPORT_MD_FILENAME).read_text(encoding="utf-8")
    assert "Rapport d'étude de sol minimum G2 PRO" in md_report
    assert "Rapport Initial du Contrôleur Technique" in md_report
    # Pièce non sélectionnée exclue du rendu lisible
    assert "Rapport Final du Contrôleur Technique" not in md_report


def test_validate_completeness_is_idempotent_on_rerun(tmp_path, isolated_workspace):
    dossier_dir = tmp_path / "dossier"
    dossier_dir.mkdir()

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        create_completeness_check(
            s,
            dossier_id=dossier_id,
            piece_id="rict_initial",
            is_selected=True,
            status="proposed",
            proposed_presence="absent",
            final_presence="absent",
            final_certainty="certain",
        )

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        validate_completeness(s, dossier, dossier_dir=dossier_dir)
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = validate_completeness(s, dossier, dossier_dir=dossier_dir)

    assert report["total_pieces_selected"] == 1
    assert len(report["entries"]) == 1
