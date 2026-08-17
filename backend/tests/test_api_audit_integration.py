"""Test d'intégration bout-en-bout de l'audit des risques (Phase 2 du protocole d'analyse) :
upload -> classification -> copie triée -> complétude -> extraction -> génération de l'audit
(sections A→G relisant les documents pivots + données Géorisques), tout monkeypatché (LLM et
Géorisques — aucun appel réseau réel)."""
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
            _dense_pdf_bytes("Reglement de consultation. Adresse du chantier : 8 bd du port, Amiens."),
        )
        zf.writestr(
            "TECH/RICT.pdf",
            _dense_pdf_bytes("Rapport initial de controle technique. Avis suspendu numero 190 sur les fondations."),
        )
        zf.writestr(
            "LOT 11 CCTP ETANCHEITE.pdf",
            _dense_pdf_bytes("CCTP etancheite. Toitures terrasses, releves d'etancheite, evacuations EP."),
        )
    return buf.getvalue()


def _fake_classification_call(monkeypatch):
    import app.classify.engine as engine

    def _decision_kwargs_for(block_text: str) -> dict:
        if "RC.pdf" in block_text:
            return dict(category_path="ASS/RC", lot=None, document_type="RC", normalized_label="RC",
                        confidence=0.9, justification="RC.")
        if "RICT.pdf" in block_text:
            return dict(category_path="TECH/RICT", lot=None, document_type="RICT", normalized_label="RICT",
                        confidence=0.92, justification="RICT.")
        if "ETANCHEITE" in block_text:
            return dict(category_path="TECH/CCTP TRAVAUX", lot="11", document_type="CCTP", normalized_label="CCTP étanchéité",
                        confidence=0.9, justification="CCTP étanchéité.")
        return dict(category_path="AUTRES", lot=None, document_type="AUTRES", normalized_label="Doc",
                    confidence=0.3, justification="Rien.")

    def _fake(*, system_prompt, user_prompt, response_model, what, model=None):
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        blocks = re.split(r"--- Document index=(\d+) ---", user_prompt)[1:]
        items = [item_model(index=int(blocks[i]), **_decision_kwargs_for(blocks[i + 1])) for i in range(0, len(blocks), 2)]
        return response_model(items=items), "mistral-small-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_completeness_call(monkeypatch):
    import app.completeness.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        items = [{"piece_id": p, "presence": "absent", "confidence": 0.5, "justification": "x", "citation": ""} for p in piece_ids]
        return response_model(items=items), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_extraction_call(monkeypatch):
    import app.extraction.engine as engine

    def _decision_kwargs_for(field_id: str, filename: str) -> dict:
        if field_id == "adresse_chantier" and "RC.pdf" in filename:
            return dict(found=True, value="8 bd du port, Amiens", confidence=0.9, justification="j", citation="c")
        return dict(found=False, value="", confidence=0.1, justification="Absent.", citation="")

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        if "synthese" in response_model.model_fields:
            return response_model(synthese="Synthèse de test."), "mistral-large-test-fake"
        filename_match = re.search(r"Document analysé : (.+)", user_prompt)
        filename = filename_match.group(1).strip() if filename_match else ""
        field_ids = re.findall(r'field_id="([^"]+)"', user_prompt)
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        items = [item_model(field_id=fid, **_decision_kwargs_for(fid, filename)) for fid in field_ids]
        return response_model(items=items), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_audit_call(monkeypatch):
    """Trois types d'appel depuis la Phase 2 : l'extraction de l'adresse chantier, l'étape « map »
    (un relevé par document pivot) puis l'étape « reduce » (les risques d'une section)."""
    import re

    import app.audit.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what, **kwargs):
        if "releves" in response_model.model_fields:
            item_model = response_model.model_fields["releves"].annotation.__args__[0]
            section_ids = re.findall(r"section_id : (\S+)", user_prompt)
            items = [
                item_model(
                    section_id=section_id,
                    concerne_cette_section=True,
                    constats=[f"Constat pour {section_id}."],
                )
                for section_id in section_ids
            ]
            return response_model(releves=items), "mistral-large-test-fake"
        if "adresse" in response_model.model_fields:
            return response_model(adresse="1 rue du Test, 38000 Grenoble"), "mistral-large-test-fake"
        risk = dict(
            statut="🔴", element_ouvrage="FONDATIONS", risque="Défaut de stabilité", alea="Tassement",
            synoptique_description="Tassement différentiel possible.", synoptique_preconisation="Réclamer la G2.",
            expose_situation="Le CCTP prévoit des semelles.",
            analyse_expert=["→ **Portance** : selon l'Eurocode 7…"],
            impact_assurabilite="Risque décennal élevé.",
            recommandations=["Exiger la note de calcul."],
        )
        return response_model(risques=[risk]), "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_georisques(monkeypatch):
    """Neutralise tout appel réseau Géorisques dans le pipeline : renvoie un rapport canonique."""
    import app.audit.pipeline as pipeline
    from app.audit.georisques import GeorisquesReport

    def _fake(address):
        return GeorisquesReport(
            address_queried=address or "", resolved_label="8 Boulevard du Port 80000 Amiens",
            lon=2.29, lat=49.89, code_insee="80021", seisme="1 - TRES FAIBLE",
            rga="Exposition moyenne", radon="catégorie 1 — potentiel faible",
        )

    monkeypatch.setattr(pipeline, "build_georisques_report", _fake)


def _wait_for_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut attendu {statuses} non atteint")


def _wait_for_audit_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 25) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["audit_risques_status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut d'audit attendu {statuses} non atteint")


def _reach_extraction_review(client: TestClient, dossier_id: str) -> None:
    client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    client.post(f"/api/dossiers/{dossier_id}/completeness/validate")
    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})


def test_generate_audit_risques_end_to_end(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)
    _fake_audit_call(monkeypatch)
    _fake_georisques(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _reach_extraction_review(client, dossier_id)

    generate_resp = client.post(f"/api/dossiers/{dossier_id}/audit-risques/generate")
    assert generate_resp.status_code == 200, generate_resp.text
    assert generate_resp.json()["audit_risques_status"] == "generating"

    final = _wait_for_audit_status(client, dossier_id, {"done", "error"})
    assert final["audit_risques_status"] == "done", final.get("audit_risques_error")
    assert final["audit_risques_generated_at"] is not None
    assert final["audit_risques_model"] == "mistral-large-test-fake"

    report = final["audit_risques_md"]
    assert "Tableau récapitulatif des risques" in report
    assert "Analyse détaillée par section" in report
    assert "[STATUT : 🔴]" in report
    assert "FONDATIONS" in report
    # section Géorisques présente avec la donnée géocodée injectée par le fake
    assert "Contexte réglementaire — Risques naturels (Géorisques)" in report
    assert "1 - TRES FAIBLE" in report


def test_generate_audit_risques_refused_before_extraction(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})

    refused = client.post(f"/api/dossiers/{dossier_id}/audit-risques/generate")
    assert refused.status_code == 409


def test_generate_audit_risques_404_for_unknown_dossier(isolated_workspace):
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/dossiers/does-not-exist/audit-risques/generate")
    assert resp.status_code == 404
