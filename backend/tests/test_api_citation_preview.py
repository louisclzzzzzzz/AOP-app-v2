"""Endpoint de localisation d'une citation (preuve visuelle de l'étape 3).

Le document est posé directement sur disque et en base : cet endpoint n'a rien à voir avec le
pipeline d'analyse, le faire passer par un run complet ne testerait que du décor.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

_CITATION = "La stratigraphie du sous-sol est decrite au chapitre trois."


def _write_pdf(path, pages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.drawString(60, 700, text)
        c.showPage()
    c.save()


@pytest.fixture
def dossier_with_pdf(isolated_workspace):
    """Dossier minimal contenant un PDF à couche de texte, sans passer par le pipeline."""
    from app.store.db import session_scope
    from app.store.repository import create_document, create_dossier

    with session_scope() as s:
        dossier_id = create_dossier(s, "test.zip").id
        document_id = create_document(
            s,
            dossier_id=dossier_id,
            relative_path="ASS/CCTP.pdf",
            filename="CCTP.pdf",
            extension=".pdf",
            size_bytes=1,
            sha256="x" * 64,
            category="pdf",
        ).id

    _write_pdf(
        isolated_workspace / dossier_id / "source" / "ASS" / "CCTP.pdf",
        ["Page de garde sans interet.", _CITATION],
    )
    return dossier_id, document_id


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_locate_returns_the_page_holding_the_citation(client, dossier_with_pdf):
    dossier_id, document_id = dossier_with_pdf

    response = client.get(
        f"/api/dossiers/{dossier_id}/documents/{document_id}/citation", params={"citation": _CITATION}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] is True
    assert body["page"] == 1
    assert body["highlighted"] is True
    assert body["filename"] == "CCTP.pdf"
    # Le frontend redessine le surlignage lui-même (PDF.js) : il a besoin des coordonnées, pas
    # d'une image déjà surlignée.
    assert len(body["rects"]) == 1
    rect = body["rects"][0]
    assert rect["x1"] > rect["x0"]
    assert rect["bottom"] > rect["top"]


def test_locate_reports_a_citation_absent_from_the_document(client, dossier_with_pdf):
    dossier_id, document_id = dossier_with_pdf

    body = client.get(
        f"/api/dossiers/{dossier_id}/documents/{document_id}/citation",
        params={"citation": "Un passage qui ne figure nulle part dans ce document."},
    ).json()

    assert body["found"] is False
    assert body["reason"] == "not_found"


def test_endpoint_refuses_a_document_of_another_dossier(client, dossier_with_pdf):
    """Même garde que le téléchargement du fichier original : un id de document ne doit pas
    donner accès au dossier d'un autre utilisateur."""
    _dossier_id, document_id = dossier_with_pdf

    response = client.get(
        f"/api/dossiers/un-autre-dossier/documents/{document_id}/citation", params={"citation": _CITATION}
    )

    assert response.status_code == 404


def test_locate_declines_a_non_pdf_document(client, isolated_workspace):
    from app.store.db import session_scope
    from app.store.repository import create_document, create_dossier

    with session_scope() as s:
        dossier_id = create_dossier(s, "test.zip").id
        document_id = create_document(
            s, dossier_id=dossier_id, relative_path="note.docx", filename="note.docx",
            extension=".docx", size_bytes=1, sha256="y" * 64, category="office",
        ).id
    path = isolated_workspace / dossier_id / "source" / "note.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pas un pdf")

    body = client.get(
        f"/api/dossiers/{dossier_id}/documents/{document_id}/citation", params={"citation": _CITATION}
    ).json()

    assert body["found"] is False
    assert body["reason"] == "not_a_pdf"
