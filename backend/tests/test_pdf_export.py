"""Conversion Markdown -> PDF du rapport composite (§app/reports/pdf_export.py) : même
sous-ensemble Markdown que `app/reports/docx_export.py` et `frontend/src/components/Markdown.tsx`."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.reports.pdf_export import markdown_to_pdf, report_pdf_filename
from app.store.db import session_scope
from app.store.repository import create_dossier


def test_markdown_to_pdf_produces_a_valid_pdf_document():
    content = markdown_to_pdf(
        "# Titre\n\n## Section\n\n"
        "Un paragraphe avec **du gras**.\n\n"
        "---\n\n"
        "| Donnée | Valeur |\n|---|---|\n| Montant | 12 M€ |\n\n"
        "- Item 1\n  - Sous-item\n- Item 2\n\n"
        "1. Un\n2. Deux\n",
        title="Rapport d'analyse",
    )
    assert content.startswith(b"%PDF")
    assert len(content) > 500


def test_markdown_to_pdf_handles_empty_input():
    content = markdown_to_pdf("")
    assert content.startswith(b"%PDF")


def test_filename_is_derived_from_the_original_dossier_name():
    class _FakeDossier:
        id = "abc123"
        original_filename = "DCE Marly / conservatoire.zip"

    assert report_pdf_filename(_FakeDossier()) == "rapport_DCE Marly _ conservatoire.pdf"


def test_export_endpoint_serves_a_pdf(isolated_workspace):
    from app.main import app

    with session_scope() as s:
        dossier = create_dossier(s, original_filename="DCE Marly.zip")
        dossier_id = dossier.id

    client = TestClient(app)
    response = client.post(
        f"/api/dossiers/{dossier_id}/report/export.pdf",
        json={"markdown": "# Rapport\n\nContenu."},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_export_endpoint_404s_on_unknown_dossier(isolated_workspace):
    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers/inconnu/report/export.pdf", json={"markdown": "# X"})
    assert response.status_code == 404
