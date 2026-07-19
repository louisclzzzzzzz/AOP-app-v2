"""Test d'intégration bout-en-bout de l'étape 2 : upload -> ingestion -> classification ->
copie triée -> écran de sélection des pièces -> analyse de complétude (fichier direct +
recherche intra-document + LLM, tout monkeypatché) -> correction manuelle au checkpoint ->
validation -> rapport.

Couvre explicitement le cas golden requis par PLAN.md §9 : une pièce noyée dans un autre
document (l'attestation décennale citée dans un marché signé, pas comme fichier dédié)."""
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
            "TECH/ETUDE_SOL.pdf",
            _dense_pdf_bytes("Mission G2 PRO étude géotechnique fondations superficielles."),
        )
        zf.writestr(
            "ASS/MARCHE_SIGNE.pdf",
            _dense_pdf_bytes(
                "Notification du marché. L'entreprise gros oeuvre justifie d'une assurance "
                "responsabilité civile décennale en cours de validité."
            ),
        )
    return buf.getvalue()


def _fake_classification_call(monkeypatch):
    import app.classify.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what):
        if "ETUDE_SOL.pdf" in user_prompt:
            decision = response_model(
                category_path="TECH/ETUDE DE SOL",
                lot=None,
                document_type="SOL",
                normalized_label="Etude sol G2 PRO",
                confidence=0.9,
                justification="Le contenu mentionne la mission G2 PRO.",
            )
        elif "MARCHE_SIGNE.pdf" in user_prompt:
            decision = response_model(
                category_path="ASS/MARCHE SIGNE",
                lot=None,
                document_type="MARCHE-SIGNE",
                normalized_label="Marche signe",
                confidence=0.88,
                justification="Le contenu mentionne la notification du marché.",
            )
        else:
            decision = response_model(
                category_path="AUTRES",
                lot=None,
                document_type="AUTRES",
                normalized_label="Document",
                confidence=0.3,
                justification="Aucun signal clair.",
            )
        return decision, "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_completeness_call(monkeypatch):
    """La seule pièce noyée-dans-un-autre-document de ce dossier de test est l'attestation
    décennale, citée dans le marché signé plutôt que comme fichier dédié — tout le reste doit
    être résolu sans appel LLM (couche 1 ou absence sans candidat). La complétude regroupe les
    appels LLM par document candidat (§4 AUDIT_BACKEND.md) : la réponse simulée couvre donc
    TOUTES les pièces demandées dans le prompt pour ce document, pas une seule."""
    import re

    import app.completeness.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what):
        present = "MARCHE_SIGNE.pdf" in user_prompt
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        items = [
            {
                "piece_id": piece_id,
                "presence": "present" if present else "absent",
                "confidence": 0.85 if present else 0.6,
                "justification": (
                    "Le marché signé mentionne explicitement la garantie décennale de l'entreprise."
                    if present
                    else "Hors sujet."
                ),
                "citation": (
                    "justifie d'une assurance responsabilité civile décennale en cours de validité"
                    if present
                    else ""
                ),
            }
            for piece_id in piece_ids
        ]
        decision = response_model(items=items)
        return decision, "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _wait_for_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut attendu {statuses} non atteint dans le délai imparti")


def test_full_completeness_flow_with_piece_hidden_in_another_document(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    zip_bytes = _build_test_zip()
    response = client.post("/api/dossiers", files={"file": ("root.zip", zip_bytes, "application/zip")})
    assert response.status_code == 200, response.text
    dossier_id = response.json()["id"]

    classified = _wait_for_status(client, dossier_id, {"classified", "error"})
    assert classified["status"] == "classified", classified.get("error_message")

    apply_resp = client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["dossier"]["status"] == "reorganized"

    # --- Écran de sélection des pièces (§5.2) ---------------------------------------------
    checklist_resp = client.get("/api/pieces-checklist")
    assert checklist_resp.status_code == 200
    all_piece_ids = {p["id"] for p in checklist_resp.json()}
    assert "etude_sol_g2pro" in all_piece_ids
    assert "attestation_decennale_par_lot" in all_piece_ids

    entries = client.get(f"/api/dossiers/{dossier_id}/completeness").json()
    assert len(entries) == len(all_piece_ids)
    by_id = {e["piece_id"]: e for e in entries}
    # Pièces obligatoires pré-cochées, non-obligatoires non cochées
    assert by_id["etude_sol_g2pro"]["is_selected"] is True
    assert by_id["refere_preventif"]["obligatoire"] is False
    assert by_id["refere_preventif"]["is_selected"] is False

    # Désélectionner une pièce sans rapport avec ce dossier
    sel_resp = client.patch(
        f"/api/dossiers/{dossier_id}/completeness/selection",
        json={"selection": [{"piece_id": "planning_travaux", "is_selected": False}]},
    )
    assert sel_resp.status_code == 200
    updated = {e["piece_id"]: e for e in sel_resp.json()}
    assert updated["planning_travaux"]["is_selected"] is False

    # --- Lancement de l'analyse --------------------------------------------------------------
    run_resp = client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    assert run_resp.status_code == 200, run_resp.text

    final = _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    assert final["status"] == "completeness_review", final.get("error_message")

    results = {e["piece_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/completeness").json()}

    # Couche 1 : fichier direct, sans appel LLM
    etude_sol = results["etude_sol_g2pro"]
    assert etude_sol["match_layer"] == "file"
    assert etude_sol["final_presence"] == "present"
    assert etude_sol["final_certainty"] == "certain"

    # Pièce noyée dans un autre document (marché signé), confirmée par LLM — cas golden §9
    attestation = results["attestation_decennale_par_lot"]
    assert attestation["match_layer"] == "llm"
    assert attestation["final_presence"] == "present"
    assert attestation["model_name"] == "mistral-large-test-fake"

    # Pièce désélectionnée : jamais analysée
    assert results["planning_travaux"]["status"] == "pending"

    # Pièce sélectionnée mais absente
    assert results["rict_initial"]["final_presence"] == "absent"

    # --- Checkpoint humain : correction manuelle --------------------------------------------
    correction = client.patch(
        f"/api/dossiers/{dossier_id}/completeness/rict_initial",
        json={"presence": "partial", "certainty": "a_verifier"},
    )
    assert correction.status_code == 200, correction.text
    corrected = correction.json()
    assert corrected["final_presence"] == "partial"
    assert corrected["is_manually_corrected"] is True
    # La proposition d'origine reste tracée
    assert corrected["proposed_presence"] == "absent"

    bad = client.patch(
        f"/api/dossiers/{dossier_id}/completeness/rict_initial",
        json={"presence": "n_importe_quoi"},
    )
    assert bad.status_code == 400

    # --- Validation du checkpoint --------------------------------------------------------------
    validate_resp = client.post(f"/api/dossiers/{dossier_id}/completeness/validate")
    assert validate_resp.status_code == 200, validate_resp.text
    validate_body = validate_resp.json()
    assert validate_body["dossier"]["status"] == "completeness_validated"

    report_resp = client.get(f"/api/dossiers/{dossier_id}/completeness/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    report_by_id = {e["piece_id"]: e for e in report["entries"]}
    assert report_by_id["rict_initial"]["presence"] == "partial"
    assert report_by_id["attestation_decennale_par_lot"]["matched_documents"][0]["relative_path"] == (
        "ASS/MARCHE_SIGNE.pdf"
    )

    final_dossier = client.get(f"/api/dossiers/{dossier_id}").json()
    assert final_dossier["status"] == "completeness_validated"
    assert final_dossier["completeness_validated_at"] is not None
