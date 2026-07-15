"""Test d'intégration bout-en-bout : upload via l'API -> pipeline d'ingestion complet ->
inventaire + texte extrait, sans appel réel à l'API Mistral (documents natifs uniquement,
aucune page à faible densité -> aucun OCR déclenché)."""
from __future__ import annotations

import io
import time
import zipfile

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas


def _dense_pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for _ in range(40):
        c.drawString(50, y, text)
        y -= 18
        if y < 50:
            break
    c.showPage()
    c.save()
    return buf.getvalue()


def _build_test_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "ADMIN/RC 2024.pdf",
            _dense_pdf_bytes("Règlement de consultation applicable au marché public."),
        )
        zf.writestr(
            "ASS/CCAP.pdf",
            _dense_pdf_bytes("Cahier des clauses administratives particulières assurance."),
        )
        zf.writestr("ENVOI DEMAT/COPIE DEPOT/candidature.cle", "")
        zf.writestr("ENVOI DEMAT/COPIE DEPOT/descripteur.xml", "<xml/>")
    return buf.getvalue()


def test_upload_and_full_ingestion_via_api(isolated_workspace, monkeypatch):
    # Le pipeline importe déjà app.main indirectement ; s'assurer que la DB init_db()
    # utilise bien le workspace isolé du test (TestClient déclenche lifespan au premier appel).
    from app.main import app

    client = TestClient(app)

    zip_bytes = _build_test_zip()
    response = client.post(
        "/api/dossiers",
        files={"file": ("root.zip", zip_bytes, "application/zip")},
    )
    assert response.status_code == 200, response.text
    dossier = response.json()
    dossier_id = dossier["id"]
    assert dossier["original_filename"] == "root.zip"

    # Le pipeline tourne en tâche de fond (BackgroundTasks) ; on attend l'état final.
    deadline = time.time() + 20
    final = None
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in ("ready_step1", "error"):
            final = detail
            break
        time.sleep(0.1)

    assert final is not None, "le pipeline n'a pas terminé dans le délai imparti"
    assert final["status"] == "ready_step1", final.get("error_message")
    assert final["counters"]["total_files"] == 4
    assert final["counters"]["text_extracted"] == 2
    assert final["counters"]["non_analyzable"] == 2
    assert final["counters"]["error"] == 0

    docs = client.get(f"/api/dossiers/{dossier_id}/documents").json()
    by_path = {d["relative_path"]: d for d in docs}
    assert by_path["ADMIN/RC 2024.pdf"]["stage"] == "text_extracted"
    assert by_path["ADMIN/RC 2024.pdf"]["text_extraction_method"] == "native_pdf"
    assert by_path["ENVOI DEMAT/COPIE DEPOT/candidature.cle"]["stage"] == "non_analyzable"

    rc_doc_id = by_path["ADMIN/RC 2024.pdf"]["id"]
    text_resp = client.get(f"/api/dossiers/{dossier_id}/documents/{rc_doc_id}/text")
    assert text_resp.status_code == 200
    body = text_resp.json()
    assert "Règlement de consultation" in body["text"]
    assert body["method"] == "native_pdf"


def test_upload_rejects_non_zip(isolated_workspace):
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/dossiers",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_websocket_receives_progress_events(isolated_workspace):
    from app.main import app

    client = TestClient(app)
    zip_bytes = _build_test_zip()
    response = client.post(
        "/api/dossiers",
        files={"file": ("root.zip", zip_bytes, "application/zip")},
    )
    dossier_id = response.json()["id"]

    with client.websocket_connect(f"/ws/dossiers/{dossier_id}") as ws:
        stages_seen = set()
        deadline = time.time() + 20
        while time.time() < deadline and "done" not in stages_seen:
            event = ws.receive_json()
            stages_seen.add(event["stage"])
        assert "done" in stages_seen
