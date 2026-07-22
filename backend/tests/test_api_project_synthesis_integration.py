"""Test d'intégration bout-en-bout de la synthèse projet (Phase 1 du protocole d'analyse) :
upload -> classification -> copie triée -> complétude -> extraction -> génération de la
synthèse projet (documents pivots relus directement, en plus des valeurs déjà résolues à
l'étape 3), tout monkeypatché."""
from __future__ import annotations

import io
import re
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
            "ASS/RC.pdf",
            _dense_pdf_bytes("Reglement de consultation. Maitre d'ouvrage : Commune de Marly. Montant total HT : 1 000 000 EUR."),
        )
        zf.writestr(
            "ASS/CCAP.pdf",
            _dense_pdf_bytes("CCAP assurance. Montant total HT : 950 000 EUR."),
        )
        zf.writestr(
            "TECH/RICT.pdf",
            _dense_pdf_bytes("Rapport initial de controle technique. Avis suspendu numero 12 sur les fondations."),
        )
    return buf.getvalue()


def _fake_classification_call(monkeypatch):
    import app.classify.engine as engine

    def _decision_kwargs_for(block_text: str) -> dict:
        if "RC.pdf" in block_text:
            return dict(
                category_path="ASS/RC", lot=None, document_type="RC", normalized_label="RC assurance",
                confidence=0.9, justification="Règlement de consultation assurance.",
            )
        if "CCAP.pdf" in block_text:
            return dict(
                category_path="ASS/CCAP", lot=None, document_type="CCAP", normalized_label="CCAP assurance",
                confidence=0.88, justification="CCAP assurance identifié.",
            )
        if "RICT.pdf" in block_text:
            return dict(
                category_path="TECH/RICT", lot=None, document_type="RICT", normalized_label="RICT",
                confidence=0.92, justification="Rapport initial de contrôle technique identifié.",
            )
        return dict(
            category_path="AUTRES", lot=None, document_type="AUTRES", normalized_label="Document",
            confidence=0.3, justification="Aucun signal clair.",
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


def _fake_completeness_call(monkeypatch):
    import app.completeness.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what):
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        items = [
            {"piece_id": piece_id, "presence": "absent", "confidence": 0.5, "justification": "Hors sujet.", "citation": ""}
            for piece_id in piece_ids
        ]
        return response_model(items=items), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_extraction_call(monkeypatch):
    import app.extraction.engine as engine

    def _decision_kwargs_for(field_id: str, filename: str) -> dict:
        if field_id == "montants_totaux_ht" and "RC.pdf" in filename:
            return dict(found=True, value="1 000 000 EUR", confidence=0.9, justification="j", citation="c")
        if field_id == "nom_moa" and "RC.pdf" in filename:
            return dict(found=True, value="Commune de Marly", confidence=0.9, justification="j", citation="c")
        return dict(found=False, value="", confidence=0.1, justification="Absent.", citation="")

    def _fake(*, system_prompt, user_prompt, response_model, what):
        if "synthese" in response_model.model_fields:
            return response_model(synthese="Synthèse de test."), "mistral-large-test-fake"
        filename_match = re.search(r"Document analysé : (.+)", user_prompt)
        filename = filename_match.group(1).strip() if filename_match else ""
        field_ids = re.findall(r'field_id="([^"]+)"', user_prompt)
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        items = [item_model(field_id=fid, **_decision_kwargs_for(fid, filename)) for fid in field_ids]
        return response_model(items=items), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_synthesis_call(monkeypatch):
    import app.synthesis.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what):
        return response_model(contenu=f"Contenu généré pour : {what}"), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _wait_for_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut attendu {statuses} non atteint dans le délai imparti")


def _wait_for_synthesis_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["synthese_projet_status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut de synthèse projet attendu {statuses} non atteint dans le délai imparti")


def _reach_extraction_review(client: TestClient, dossier_id: str) -> None:
    client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    client.post(f"/api/dossiers/{dossier_id}/completeness/validate")
    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})


def test_generate_project_synthesis_end_to_end(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)
    _fake_synthesis_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _reach_extraction_review(client, dossier_id)

    generate_resp = client.post(f"/api/dossiers/{dossier_id}/synthese-projet/generate")
    assert generate_resp.status_code == 200, generate_resp.text
    assert generate_resp.json()["synthese_projet_status"] == "generating"

    final = _wait_for_synthesis_status(client, dossier_id, {"done", "error"})
    assert final["synthese_projet_status"] == "done", final.get("synthese_projet_error")
    assert final["synthese_projet_generated_at"] is not None
    assert final["synthese_projet_model"] == "mistral-large-test-fake"

    report = final["synthese_projet_md"]
    # Thème reformaté sans appel LLM (source=extraction_fields), à partir des valeurs de l'étape 3
    assert "Commune de Marly" in report
    # Thème relisant directement le document pivot TECH/RICT, via l'appel LLM simulé
    assert "synthese_rict" in report
    # Cartographie documentaire (Phase 0), déterministe
    assert "Cartographie des documents pivots" in report
    assert "Document pivot" in report


def test_generate_project_synthesis_refused_before_extraction(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})

    refused = client.post(f"/api/dossiers/{dossier_id}/synthese-projet/generate")
    assert refused.status_code == 409
