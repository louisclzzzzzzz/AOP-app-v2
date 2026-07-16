from __future__ import annotations

import json

from app.extraction.report import REPORT_JSON_FILENAME, REPORT_MD_FILENAME, validate_extraction
from app.store.db import session_scope
from app.store.models import FileCategory
from app.store.repository import create_document, create_dossier, create_extraction_result, get_dossier


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


def test_validate_extraction_writes_json_and_md_reports(tmp_path, isolated_workspace):
    dossier_dir = tmp_path / "dossier"
    dossier_dir.mkdir()

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        doc = _create_doc(s, dossier_id, doc_id_suffix="1", relative_path="ASS/RC.pdf", final_category="ASS/RC")
        create_extraction_result(
            s,
            dossier_id=dossier_id,
            field_id="nom_moa",
            status="proposed",
            match_layer="file",
            proposed_value="Commune de Marly",
            proposed_confidence=0.9,
            proposed_justification="Maître d'ouvrage identifié dans le RC.",
            proposed_citation="Maître d'ouvrage : Commune de Marly",
            proposed_sources_json=json.dumps(
                [{"document_id": doc.id, "filename": "RC.pdf", "value": "Commune de Marly", "confidence": 0.9}]
            ),
            cross_check_status="not_applicable",
            final_value="Commune de Marly",
        )
        create_extraction_result(
            s,
            dossier_id=dossier_id,
            field_id="montants_totaux_ht",
            status="proposed",
            match_layer="none",
            proposed_justification="Aucune valeur trouvée.",
            cross_check_status=None,
            final_value=None,
        )

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = validate_extraction(s, dossier, dossier_dir=dossier_dir)

    assert report["total_fields"] == 2
    by_id = {e["field_id"]: e for e in report["entries"]}
    assert by_id["nom_moa"]["value"] == "Commune de Marly"
    assert by_id["nom_moa"]["sources"][0]["relative_path"] == "ASS/RC.pdf"
    assert by_id["montants_totaux_ht"]["value"] is None

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.extraction_report_json_path == REPORT_JSON_FILENAME
        assert dossier.extraction_report_md_path == REPORT_MD_FILENAME
        assert dossier.extraction_validated_at is not None

    json_report = json.loads((dossier_dir / REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    assert json_report["total_fields"] == 2

    md_report = (dossier_dir / REPORT_MD_FILENAME).read_text(encoding="utf-8")
    assert "Nom du MOA" in md_report
    assert "Commune de Marly" in md_report
    assert "non trouvée" in md_report


def test_validate_extraction_is_idempotent_on_rerun(tmp_path, isolated_workspace):
    dossier_dir = tmp_path / "dossier"
    dossier_dir.mkdir()

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        create_extraction_result(
            s, dossier_id=dossier_id, field_id="montants_totaux_ht", status="proposed", final_value=None
        )

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        validate_extraction(s, dossier, dossier_dir=dossier_dir)
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        report = validate_extraction(s, dossier, dossier_dir=dossier_dir)

    assert report["total_fields"] == 1
    assert len(report["entries"]) == 1
