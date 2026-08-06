"""Test d'intégration bout-en-bout de l'étape 3 : upload -> ingestion -> classification ->
copie triée -> complétude (sélection minimale + run + validate, tout monkeypatché) ->
extraction (fichiers de référence + recoupement, tout monkeypatché) -> correction manuelle
au checkpoint -> validation -> rapport.

Couvre explicitement le recoupement de champ critique (§6.3 : "croiser RC + CCAP + CCTP et
signaler les incohérences") — équivalent, pour l'étape 3, du cas "pièce noyée" de l'étape 2 :
le montant total HT diffère entre le RC et le CCAP, l'incohérence doit être signalée.
"""
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
            "ASS/RC.pdf",
            _dense_pdf_bytes("Reglement de consultation. Maitre d'ouvrage : Commune de Marly. Montant total HT : 1 000 000 EUR."),
        )
        zf.writestr(
            "ASS/CCAP.pdf",
            _dense_pdf_bytes(
                "CCAP assurance. Montant total HT : 950 000 EUR. "
                "Annexe stratigraphie du sous-sol : Marne argileuse à 2m."
            ),
        )
    return buf.getvalue()


def _fake_classification_call(monkeypatch):
    import re

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
    """Aucune pièce de la checklist étape 2 ne correspond aux catégories ASS/RC ou ASS/CCAP
    de ce dossier de test — tout doit rester résolu sans appel LLM, sauf recherche par
    mots-clés éventuelle, qu'on fait échouer systématiquement en absent. La complétude regroupe
    les appels LLM par document candidat (§4 AUDIT_BACKEND.md) : la réponse simulée couvre
    toutes les pièces demandées dans le prompt pour ce document."""
    import re

    import app.completeness.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what):
        piece_ids = re.findall(r'piece_id="([^"]+)"', user_prompt)
        items = [
            {"piece_id": piece_id, "presence": "absent", "confidence": 0.5, "justification": "Hors sujet.", "citation": ""}
            for piece_id in piece_ids
        ]
        decision = response_model(items=items)
        return decision, "mistral-large-test-fake"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def _fake_extraction_call(monkeypatch):
    """Simule le LLM d'extraction groupé par document (§3 OPTIMISATION.md) : un seul appel par
    document, une décision par field_id demandé dans le prompt. Simule aussi l'appel de synthèse
    textuelle en fin de pipeline (réponse à champ unique `synthese`, pas de regroupement par
    field_id — reconnu par l'absence du champ `items` sur le modèle de réponse demandé)."""
    import re

    import app.extraction.engine as engine

    def _decision_kwargs_for(field_id: str, filename: str) -> dict:
        if field_id == "montants_totaux_ht" and "RC.pdf" in filename:
            return dict(
                found=True, value="1 000 000 EUR", confidence=0.9,
                justification="Montant HT indiqué dans le RC.", citation="Montant total HT : 1 000 000 EUR",
            )
        if field_id == "montants_totaux_ht" and "CCAP.pdf" in filename:
            return dict(
                found=True, value="950 000 EUR", confidence=0.8,
                justification="Montant HT indiqué dans le CCAP.", citation="Montant total HT : 950 000 EUR",
            )
        if field_id == "nom_moa" and "RC.pdf" in filename:
            return dict(
                found=True, value="Commune de Marly", confidence=0.9,
                justification="Maître d'ouvrage identifié dans le RC.", citation="Maitre d'ouvrage : Commune de Marly",
            )
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


def _wait_for_status(client: TestClient, dossier_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/dossiers/{dossier_id}").json()
        if detail["status"] in statuses:
            return detail
        time.sleep(0.1)
    raise AssertionError(f"statut attendu {statuses} non atteint dans le délai imparti")


def test_full_extraction_flow_with_cross_check_incoherence(isolated_workspace, monkeypatch):
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)

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

    # --- Complétude (étape 2), amenée jusqu'à validation pour débloquer l'étape 3 ------------
    run_completeness = client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    assert run_completeness.status_code == 200, run_completeness.text
    completeness_done = _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    assert completeness_done["status"] == "completeness_review", completeness_done.get("error_message")

    validate_completeness = client.post(f"/api/dossiers/{dossier_id}/completeness/validate")
    assert validate_completeness.status_code == 200, validate_completeness.text
    assert validate_completeness.json()["dossier"]["status"] == "completeness_validated"

    # --- Schéma d'extraction (§7.3) ----------------------------------------------------------
    schema_resp = client.get("/api/extraction-schema")
    assert schema_resp.status_code == 200
    all_field_ids = {f["id"] for f in schema_resp.json()}
    assert "montants_totaux_ht" in all_field_ids
    assert len(all_field_ids) == 50
    # Le libellé de section est servi par l'API (le client ne redéclare pas la table des sections).
    assert all(f["section_libelle"] for f in schema_resp.json())

    # Ne peut pas lancer l'extraction avant que la complétude ne soit validée serait bloqué,
    # mais on est déjà à completeness_validated : essai anticipé (avant tout GET) doit marcher.
    entries_before = client.get(f"/api/dossiers/{dossier_id}/extraction").json()
    assert len(entries_before) == len(all_field_ids)
    assert all(e["status"] == "pending" for e in entries_before)

    # --- Lancement de l'extraction -----------------------------------------------------------
    run_resp = client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    assert run_resp.status_code == 200, run_resp.text

    final = _wait_for_status(client, dossier_id, {"extraction_review", "error"})
    assert final["status"] == "extraction_review", final.get("error_message")

    results = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}

    # Cas golden : recoupement en désaccord entre RC et CCAP sur le montant total HT
    montant = results["montants_totaux_ht"]
    assert montant["cross_check_status"] == "incoherent"
    assert montant["final_value"] == "1 000 000 EUR"  # confiance la plus élevée
    assert {s["value"] for s in montant["sources"]} == {"1 000 000 EUR", "950 000 EUR"}

    # Champ trouvé sans recoupement (non critique)
    moa = results["nom_moa"]
    assert moa["final_value"] == "Commune de Marly"
    assert moa["match_layer"] == "file"

    # Champ absent (aucune valeur trouvée nulle part)
    absent_field = results["stratigraphie"]
    assert absent_field["final_value"] is None

    # --- Checkpoint humain : correction manuelle ---------------------------------------------
    correction = client.patch(
        f"/api/dossiers/{dossier_id}/extraction/montants_totaux_ht",
        json={"final_value": "950 000 EUR"},
    )
    assert correction.status_code == 200, correction.text
    corrected = correction.json()
    assert corrected["final_value"] == "950 000 EUR"
    assert corrected["is_manually_corrected"] is True
    # La proposition d'origine reste tracée
    assert corrected["proposed_value"] == "1 000 000 EUR"

    # --- Validation du checkpoint -------------------------------------------------------------
    validate_resp = client.post(f"/api/dossiers/{dossier_id}/extraction/validate")
    assert validate_resp.status_code == 200, validate_resp.text
    validate_body = validate_resp.json()
    assert validate_body["dossier"]["status"] == "extraction_validated"

    report_resp = client.get(f"/api/dossiers/{dossier_id}/extraction/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    report_by_id = {e["field_id"]: e for e in report["entries"]}
    assert report_by_id["montants_totaux_ht"]["value"] == "950 000 EUR"
    assert report_by_id["montants_totaux_ht"]["manually_corrected"] is True

    final_dossier = client.get(f"/api/dossiers/{dossier_id}").json()
    assert final_dossier["status"] == "extraction_validated"
    assert final_dossier["extraction_validated_at"] is not None
    assert final_dossier["synthese_ia"] == "Synthèse de test."

    # --- Réouverture des étapes déjà validées (FRICTIONS_EXPERT_METIER.md §3/§5) -------------

    # Étape 3 : rouvrable depuis extraction_validated, aucune donnée en aval à invalider.
    reopen_extraction = client.post(f"/api/dossiers/{dossier_id}/extraction/reopen")
    assert reopen_extraction.status_code == 200, reopen_extraction.text
    assert reopen_extraction.json()["status"] == "extraction_review"
    assert reopen_extraction.json()["extraction_validated_at"] is None
    # la correction manuelle déjà faite reste tracée, rien n'est effacé
    reopened_entries = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}
    assert reopened_entries["montants_totaux_ht"]["final_value"] == "950 000 EUR"

    revalidate = client.post(f"/api/dossiers/{dossier_id}/extraction/validate")
    assert revalidate.status_code == 200, revalidate.text
    assert revalidate.json()["dossier"]["status"] == "extraction_validated"

    # Étape 2 : rouvrable même après que l'étape 3 a été validée ; l'extraction n'est pas
    # invalidée puisqu'elle ne dépend pas des valeurs de complétude.
    reopen_completeness = client.post(f"/api/dossiers/{dossier_id}/completeness/reopen")
    assert reopen_completeness.status_code == 200, reopen_completeness.text
    assert reopen_completeness.json()["status"] == "completeness_review"
    assert reopen_completeness.json()["completeness_validated_at"] is None
    assert len(client.get(f"/api/dossiers/{dossier_id}/extraction").json()) == len(all_field_ids)

    # Étape 1 : rouvrable depuis n'importe quel statut atteint, mais invalide en cascade les
    # résultats des étapes 2/3 puisqu'ils référencent un classement sur le point de changer.
    reopen_reorg = client.post(f"/api/dossiers/{dossier_id}/reorganize/reopen")
    assert reopen_reorg.status_code == 200, reopen_reorg.text
    reopened_dossier = reopen_reorg.json()
    assert reopened_dossier["status"] == "classified"
    assert reopened_dossier["counters"]["fields_total"] == 0
    assert reopened_dossier["counters"]["pieces_selected"] == 0

    # les lignes sont re-créées à l'état "pending" au premier accès (ensure_results_initialized),
    # confirmant que l'ancien résultat (avec sa correction manuelle) a bien été supprimé
    post_reopen_entries = client.get(f"/api/dossiers/{dossier_id}/extraction").json()
    assert all(e["status"] == "pending" and e["final_value"] is None for e in post_reopen_entries)

    # Réouverture refusée si le dossier n'a pas encore atteint le statut requis.
    fresh_zip = client.post("/api/dossiers", files={"file": ("root2.zip", _build_test_zip(), "application/zip")})
    fresh_id = fresh_zip.json()["id"]
    _wait_for_status(client, fresh_id, {"classified", "error"})
    refused = client.post(f"/api/dossiers/{fresh_id}/extraction/reopen")
    assert refused.status_code == 409


def _fake_extraction_call_with_stratigraphie_in_ccap(monkeypatch):
    """Variante du fake d'extraction : `stratigraphie` (absente des documents de référence
    directs de ce dossier de test, donc introuvable en couche 1) a une valeur cachée dans le
    CCAP — récupérable seulement par une recherche élargie (approfondissement ponctuel) ou par
    une sélection manuelle qui inclut ce document."""
    import re

    import app.extraction.engine as engine

    def _decision_kwargs_for(field_id: str, filename: str) -> dict:
        if field_id == "montants_totaux_ht" and "RC.pdf" in filename:
            return dict(found=True, value="1 000 000 EUR", confidence=0.9, justification="j", citation="c")
        if field_id == "nom_moa" and "RC.pdf" in filename:
            return dict(found=True, value="Commune de Marly", confidence=0.9, justification="j", citation="c")
        if field_id == "stratigraphie" and "CCAP.pdf" in filename:
            return dict(found=True, value="Marne argileuse", confidence=0.7, justification="Mentionnée en annexe du CCAP.", citation="Marne argileuse à 2m")
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


def _run_completeness_and_reach_extraction_review(client: TestClient, dossier_id: str) -> None:
    client.post(f"/api/dossiers/{dossier_id}/reorganize/apply")
    client.post(f"/api/dossiers/{dossier_id}/completeness/run")
    _wait_for_status(client, dossier_id, {"completeness_review", "error"})
    client.post(f"/api/dossiers/{dossier_id}/completeness/validate")


def test_extraction_run_automatically_widens_search_for_missing_fields(isolated_workspace, monkeypatch):
    """`stratigraphie` n'a aucune catégorie de référence dans ce dossier de test (absente du
    schéma normal pour ces docs) : le run standard doit désormais la retrouver automatiquement
    dans le CCAP via la recherche élargie (couche 2), sans action manuelle de l'expert — plus de
    bouton "Approfondir" séparé, la couche 2 est intégrée à `POST .../extraction/run`."""
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call_with_stratigraphie_in_ccap(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _run_completeness_and_reach_extraction_review(client, dossier_id)

    # Le mot-clé "argileuse" doit être dans les `indices` du champ stratigraphie du schéma réel
    # (config/extraction_schema.yaml) pour que la couche 2 le trouve dans le CCAP de test.
    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})

    entries = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}
    # Champs de référence trouvés dès la couche 1, ne doivent pas bouger.
    assert entries["nom_moa"]["final_value"] == "Commune de Marly"
    assert entries["montants_totaux_ht"]["final_value"] == "1 000 000 EUR"
    # Absent de toute catégorie de référence pour ce dossier de test -> retrouvé automatiquement
    # par la recherche élargie (couche 2) sur le CCAP.
    assert entries["stratigraphie"]["match_layer"] == "content"
    assert entries["stratigraphie"]["final_value"] == "Marne argileuse"
    # Un champ resté introuvable même après la recherche élargie porte une justification
    # explicite plutôt que d'être laissé dans un état ambigu.
    still_absent = [fid for fid, e in entries.items() if not e["final_value"]]
    assert still_absent  # au moins un champ reste absent dans ce dossier de test
    for fid in still_absent:
        assert "élargie" in (entries[fid]["justification"] or "")


def _build_zip_with_reworded_soil_annex() -> bytes:
    """Même dossier de test, mais l'annexe « sol » du CCAP est FORMULÉE AUTREMENT : aucun des
    `indices` du champ `stratigraphie` (stratigraphie, lithologie, couches du sous-sol, coupe
    géologique) n'y figure littéralement, alors que l'information s'y trouve bel et bien.

    C'est l'angle mort de la sélection par mots-clés de la couche 2 : score nul, donc document
    écarté avant même d'être soumis au LLM."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "ASS/RC.pdf",
            _dense_pdf_bytes("Reglement de consultation. Maitre d'ouvrage : Commune de Marly. Montant total HT : 1 000 000 EUR."),
        )
        zf.writestr(
            "ASS/CCAP.pdf",
            _dense_pdf_bytes(
                "CCAP assurance. Montant total HT : 950 000 EUR. "
                "Annexe geotechnique : marne argileuse rencontree a 2m, puis calcaire a 5m."
            ),
        )
    return buf.getvalue()


def _fake_soil_aware_embeddings(monkeypatch):
    """Faux vectoriseur : projette un texte sur trois axes thématiques repérés par des mots
    TÉMOINS (jamais les `indices` du champ, sinon on ne testerait que du mot-clé déguisé). Deux
    formulations d'un même thème obtiennent ainsi des vecteurs voisins, ce qui reproduit
    localement — et de façon déterministe — ce que fait un vrai modèle d'embeddings."""
    import app.extraction.semantic_retrieval as semantic_retrieval

    axes = (
        ("sol", ("stratigraphie", "lithologie", "sous-sol", "geotechnique", "marne", "calcaire", "terrain")),
        ("prix", ("montant", "euros", "eur", "prix")),
        ("moa", ("ouvrage", "commune", "consultation")),
    )

    def _fake(texts, *, what):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([float(sum(lowered.count(m) for m in markers)) for _axis, markers in axes] + [0.1])
        return vectors

    monkeypatch.setattr(semantic_retrieval, "call_embeddings", _fake)


def test_layer2_keyword_scoring_alone_misses_a_reworded_document(isolated_workspace, monkeypatch):
    """Constat de départ (comportement historique, embeddings désactivés) : l'information est dans
    le CCAP mais formulée autrement que les `indices` du champ — la couche 2 par mots-clés seuls ne
    propose jamais ce document au LLM, et le champ est déclaré absent à tort."""
    from app.settings import get_models_config

    monkeypatch.setitem(get_models_config()["embeddings"], "enabled", False)
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call_with_stratigraphie_in_ccap(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/dossiers", files={"file": ("root.zip", _build_zip_with_reworded_soil_annex(), "application/zip")}
    )
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _run_completeness_and_reach_extraction_review(client, dossier_id)

    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})

    entries = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}
    assert entries["stratigraphie"]["final_value"] is None


def test_layer2_semantic_retrieval_finds_a_reworded_document(isolated_workspace, monkeypatch):
    """Même dossier, embeddings actifs : le CCAP remonte par proximité de sens malgré un score
    mots-clés nul, et la valeur est retrouvée. Les champs déjà résolus en couche 1 ne bougent
    pas — la recherche sémantique ne touche qu'à la sélection des candidats de la couche 2."""
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call_with_stratigraphie_in_ccap(monkeypatch)
    _fake_soil_aware_embeddings(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/dossiers", files={"file": ("root.zip", _build_zip_with_reworded_soil_annex(), "application/zip")}
    )
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _run_completeness_and_reach_extraction_review(client, dossier_id)

    client.post(f"/api/dossiers/{dossier_id}/extraction/run")
    _wait_for_status(client, dossier_id, {"extraction_review", "error"})

    entries = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}
    assert entries["stratigraphie"]["match_layer"] == "content"
    assert entries["stratigraphie"]["final_value"] == "Marne argileuse"
    assert entries["nom_moa"]["final_value"] == "Commune de Marly"
    assert entries["montants_totaux_ht"]["final_value"] == "1 000 000 EUR"


def test_run_extraction_with_manual_document_selection_ignores_reference_categories(isolated_workspace, monkeypatch):
    """Sélection manuelle : en limitant le run au seul CCAP (qui n'est catégorie de référence
    d'aucun champ dans ce dossier de test), `montants_totaux_ht` doit être trouvé UNIQUEMENT
    via le CCAP (950 000 EUR), pas via le RC — la restriction manuelle prime sur le filtrage
    standard par catégorie."""
    _fake_classification_call(monkeypatch)
    _fake_completeness_call(monkeypatch)
    _fake_extraction_call(monkeypatch)

    from app.main import app

    client = TestClient(app)
    response = client.post("/api/dossiers", files={"file": ("root.zip", _build_test_zip(), "application/zip")})
    dossier_id = response.json()["id"]
    _wait_for_status(client, dossier_id, {"classified", "error"})
    _run_completeness_and_reach_extraction_review(client, dossier_id)

    documents = client.get(f"/api/dossiers/{dossier_id}/documents").json()
    ccap_doc = next(d for d in documents if "CCAP" in d["filename"])

    run_resp = client.post(
        f"/api/dossiers/{dossier_id}/extraction/run", json={"document_ids": [ccap_doc["id"]]}
    )
    assert run_resp.status_code == 200, run_resp.text

    _wait_for_status(client, dossier_id, {"extraction_review", "error"})
    results = {e["field_id"]: e for e in client.get(f"/api/dossiers/{dossier_id}/extraction").json()}

    montant = results["montants_totaux_ht"]
    assert montant["final_value"] == "950 000 EUR"
    # `montants_totaux_ht` est soumis au recoupement (cross_check_required_fields) : la
    # réconciliation programmatique force match_layer="file" quel que soit le mode (pré-existant,
    # cf. `_reconcile_cross_check`) — seule la restriction aux sources compte ici.
    assert {s["document_id"] for s in montant["sources"]} == {ccap_doc["id"]}

    # nom_moa n'est trouvé que dans le RC, exclu de la sélection manuelle -> absent ici
    assert results["nom_moa"]["final_value"] is None
