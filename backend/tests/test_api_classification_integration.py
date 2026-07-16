"""Test d'intégration bout-en-bout de l'étape 1 : upload -> ingestion -> classification
automatique (LLM monkeypatché, aucun appel réseau réel) -> checkpoint humain (consultation +
correction) -> application de la copie triée -> vérification sur disque que la source n'a
jamais été modifiée."""
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
    return buf.getvalue()


def _fake_classification_call(monkeypatch):
    """Simule le LLM batché (§2 OPTIMISATION.md) : un seul appel structuré peut couvrir
    plusieurs documents ambigus à la fois ; on classe chacun par mots-clés présents dans son
    bloc du prompt, sans appel réseau."""
    import re

    import app.classify.engine as engine

    def _decision_kwargs_for(block_text: str) -> dict:
        if "RC 2024.pdf" in block_text:
            return dict(
                category_path="ADMIN/RC", lot=None, document_type="RC-DCE",
                normalized_label="RC 2024", confidence=0.9,
                justification="Le contenu mentionne le règlement de consultation.",
            )
        if "CCAP.pdf" in block_text:
            return dict(
                category_path="ASS/CCAP", lot=None, document_type="CCAP",
                normalized_label="CCAP assurance", confidence=0.88,
                justification="Le contenu mentionne le CCAP assurance.",
            )
        return dict(
            category_path="AUTRES", lot=None, document_type="AUTRES",
            normalized_label="Document", confidence=0.3, justification="Aucun signal clair.",
        )

    def _fake(*, system_prompt, user_prompt, response_model, what, model=None):
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        blocks = re.split(r"--- Document index=(\d+) ---", user_prompt)[1:]
        items = [
            item_model(index=int(blocks[i]), **_decision_kwargs_for(blocks[i + 1]))
            for i in range(0, len(blocks), 2)
        ]
        return response_model(items=items), "mistral-small-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _wait_for_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut attendu {statuses} non atteint dans le délai imparti")


def test_full_classification_and_reorg_flow(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)

    from app.main import app
    from app.settings import get_settings

    client = TestClient(app)
    zip_bytes = _build_test_zip()
    response = client.post("/api/dossiers", files={"file": ("root.zip", zip_bytes, "application/zip")})
    assert response.status_code == 200, response.text
    dossier_id = response.json()["id"]

    final = _wait_for_status(client, dossier_id, {"classified", "error"})
    assert final["status"] == "classified", final.get("error_message")
    assert final["counters"]["total_files"] == 3
    assert final["counters"]["classified"] == 3

    classification = client.get(f"/api/dossiers/{dossier_id}/classification").json()
    by_path = {e["relative_path"]: e for e in classification}

    rc_entry = by_path["ADMIN/RC 2024.pdf"]
    assert rc_entry["final_category"] == "ADMIN/RC"
    assert rc_entry["confidence"] == 0.9
    assert rc_entry["classification_status"] == "proposed"
    assert rc_entry["is_manually_corrected"] is False

    demat_entry = by_path["ENVOI DEMAT/COPIE DEPOT/candidature.cle"]
    assert demat_entry["final_category"] == "ENVOI DEMAT/COPIE DEPOT"
    assert demat_entry["signals"]["rule"] == "auto_route"

    # --- Checkpoint humain : correction manuelle d'une proposition -----------------------
    ccap_entry = by_path["ASS/CCAP.pdf"]
    correction = client.patch(
        f"/api/dossiers/{dossier_id}/documents/{ccap_entry['document_id']}/classification",
        json={"category": "ASS/CCTP", "lot": "2", "doc_type": "CCTP", "filename": "ASS_LOT2_CCTP_CCAP.pdf"},
    )
    assert correction.status_code == 200, correction.text
    corrected = correction.json()
    assert corrected["final_category"] == "ASS/CCTP"
    assert corrected["is_manually_corrected"] is True
    # La proposition d'origine reste tracée (jamais écrasée par la correction)
    assert corrected["proposed_category"] == "ASS/CCAP"

    # Une catégorie hors taxonomie doit être rejetée
    bad = client.patch(
        f"/api/dossiers/{dossier_id}/documents/{ccap_entry['document_id']}/classification",
        json={"category": "N IMPORTE QUOI", "doc_type": "X", "filename": "x.pdf"},
    )
    assert bad.status_code == 400

    # --- Application de la copie triée ----------------------------------------------------
    apply_resp = client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    assert apply_resp.status_code == 200, apply_resp.text
    apply_body = apply_resp.json()
    assert apply_body["dossier"]["status"] == "reorganized"
    assert apply_body["report"]["total_files"] == 3

    settings = get_settings()
    dossier_dir = settings.workspace_dir / dossier_id
    organized_root = dossier_dir / "organized"

    assert (organized_root / "ADMIN" / "RC").exists()
    assert (organized_root / "ASS" / "CCTP" / "LOT 2" / "ASS_LOT2_CCTP_CCAP.pdf").exists()
    assert (organized_root / "ENVOI DEMAT" / "COPIE DEPOT").exists()

    # La source d'origine n'a jamais été touchée
    source_dir = dossier_dir / "source"
    assert (source_dir / "ADMIN/RC 2024.pdf").exists()
    assert (source_dir / "ASS/CCAP.pdf").exists()

    report_resp = client.get(f"/api/dossiers/{dossier_id}/reorganize/report")
    assert report_resp.status_code == 200
    assert report_resp.json()["total_files"] == 3

    final_dossier = client.get(f"/api/dossiers/{dossier_id}").json()
    assert final_dossier["status"] == "reorganized"
    assert final_dossier["reorg_applied_at"] is not None


def test_taxonomy_endpoint_returns_categories(isolated_workspace):
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert any(c["path"] == "ADMIN/RC" for c in body)
