"""Test d'intégration bout-en-bout de la variante Perplexity Deep Research de la synthèse
projet (Phase 1) : upload -> classification -> copie triée -> complétude -> extraction ->
génération, tout monkeypatché — miroir de test_api_project_synthesis_integration.py (Mistral),
mais monkeypatche `app.synthesis_perplexity.client.run_deep_research` (la frontière du SDK
Perplexity) plutôt que `call_structured_chat` (frontière du SDK Mistral)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.test_api_project_synthesis_integration import (
    _build_test_zip,
    _fake_classification_call,
    _fake_completeness_call,
    _fake_extraction_call,
    _wait_for_status,
)


def _fake_deep_research_call(monkeypatch, *, content: str = "Contenu Deep Research généré."):
    import app.synthesis_perplexity.engine as engine

    def _fake(*, system_prompt, user_prompt, what):
        return content, ["https://source.example/norme"], "sonar-deep-research-test"

    monkeypatch.setattr(engine, "run_deep_research", _fake)


def _wait_for_perplexity_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["synthese_projet_perplexity_status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut de synthèse projet (Perplexity) attendu {statuses} non atteint dans le délai imparti")


def _reach_extraction_review(client: TestClient, dossier_id: str) -> None:
    client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    client.post(f"/api/dossiers/{dossier_id}/completeness/validate")
    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})


def test_generate_project_synthesis_perplexity_end_to_end(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)
    _fake_deep_research_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _reach_extraction_review(client, dossier_id)

    generate_resp = client.post(f"/api/dossiers/{dossier_id}/synthese-projet/generate-perplexity")
    assert generate_resp.status_code == 200, generate_resp.text
    assert generate_resp.json()["synthese_projet_perplexity_status"] == "generating"

    final = _wait_for_perplexity_status(client, dossier_id, {"done", "error"})
    assert final["synthese_projet_perplexity_status"] == "done", final.get("synthese_projet_perplexity_error")
    assert final["synthese_projet_perplexity_generated_at"] is not None
    assert final["synthese_projet_perplexity_model"] == "sonar-deep-research-test"

    report = final["synthese_projet_perplexity_md"]
    # Thème reformaté sans appel LLM (source=extraction_fields), à partir des valeurs de l'étape 3
    assert "Commune de Marly" in report
    # Contenu du seul appel Deep Research combiné
    assert "Contenu Deep Research généré." in report
    # Cartographie documentaire (Phase 0), déterministe
    assert "Cartographie des documents pivots" in report
    # Citations web renvoyées par Perplexity, distinctes des citations de documents internes
    assert "https://source.example/norme" in report

    # Le pipeline Mistral (colonnes distinctes) reste totalement indépendant : jamais déclenché
    assert final["synthese_projet_status"] == "not_generated"
    assert final["synthese_projet_md"] is None


def test_generate_project_synthesis_perplexity_refused_before_extraction(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})

    refused = client.post(f"/api/dossiers/{dossier_id}/synthese-projet/generate-perplexity")
    assert refused.status_code == 409


def test_generate_project_synthesis_perplexity_failure_sets_error_status_without_touching_dossier_status(
    isolated_workspace, monkeypatch
):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)

    import app.synthesis_perplexity.engine as engine

    def _boom(*, system_prompt, user_prompt, what):
        raise RuntimeError("Deep Research indisponible")

    monkeypatch.setattr(engine, "run_deep_research", _boom)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _reach_extraction_review(client, dossier_id)

    client.post(f"/api/dossiers/{dossier_id}/synthese-projet/generate-perplexity")
    final = _wait_for_perplexity_status(client, dossier_id, {"done", "error"})

    assert final["synthese_projet_perplexity_status"] == "error"
    assert "Deep Research indisponible" in final["synthese_projet_perplexity_error"]
    assert final["status"] == "extraction_review"  # jamais impacté par cet échec annexe
