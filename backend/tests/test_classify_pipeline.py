"""Test unitaire direct de run_classification_pipeline — jusqu'ici seulement exercée à travers
les tests d'intégration API (TestClient + polling), jamais isolément, et jamais avec plus de
`classification.batch_size` documents ambigus en même temps (donc jamais vérifié qu'un 2e lot
LLM est bien déclenché)."""
from __future__ import annotations

import re

import app.classify.engine as engine
from app.classify.pipeline import run_classification_pipeline
from app.settings import get_models_config
from app.store.db import session_scope
from app.store.models import ClassificationStatus, DossierStatus, FileCategory
from app.store.repository import create_dossier, create_document, get_dossier, list_documents


def _create_doc(session, dossier_id, *, relative_path, filename=None):
    filename = filename or relative_path.rsplit("/", 1)[-1]
    return create_document(
        session,
        dossier_id=dossier_id,
        relative_path=relative_path,
        filename=filename,
        extension=".pdf",
        size_bytes=8,
        sha256=f"hash-{relative_path}",
        category=FileCategory.PDF.value,
        is_analyzable=True,
        classification_status=ClassificationStatus.PENDING.value,
    )


async def _run(monkeypatch, dossier_id, relative_paths):
    with session_scope() as s:
        for rel in relative_paths:
            _create_doc(s, dossier_id, relative_path=rel)

    calls: list[list[int]] = []

    def _fake(*, system_prompt, user_prompt, response_model, what, model=None):
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        indices = [int(m) for m in re.findall(r"--- Document index=(\d+) ---", user_prompt)]
        calls.append(indices)
        items = [
            item_model(
                index=i,
                category_path="AUTRES",
                lot=None,
                document_type="AUTRES",
                normalized_label="Document",
                confidence=0.5,
                justification="stub de test",
            )
            for i in indices
        ]
        return response_model(items=items), "mistral-small-test-stub"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)
    await run_classification_pipeline(dossier_id)
    return calls


async def test_unambiguous_filenames_are_classified_by_rules_without_any_llm_call(isolated_workspace, monkeypatch):
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id

    calls = await _run(monkeypatch, dossier_id, ["ADMIN/C.DC1_candidature.pdf"])

    assert calls == []  # aucun appel LLM : le nom de fichier est net et unique (règle)
    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.CLASSIFIED.value
        docs = list_documents(s, dossier_id)
        assert docs[0].proposed_category == "ENVOI DEMAT/CANDIDATURE"


async def test_ambiguous_documents_beyond_batch_size_trigger_a_second_llm_call(isolated_workspace, monkeypatch):
    """Régression ciblée : `classification.batch_size` documents ambigus doivent tenir dans
    UN appel LLM ; au-delà, un 2e lot doit être déclenché (jamais vérifié auparavant)."""
    batch_size = int(get_models_config()["classification"]["batch_size"])
    # Noms génériques (cf. _GENERIC_FILENAME_PATTERN) : toujours ambigus, jamais résolus par
    # les règles, quel que soit leur nombre.
    relative_paths = [f"DIVERS/scan{i}.pdf" for i in range(batch_size + 3)]

    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id

    calls = await _run(monkeypatch, dossier_id, relative_paths)

    assert len(calls) == 2  # 2 lots : batch_size documents, puis les 3 restants
    assert len(calls[0]) == batch_size
    assert len(calls[1]) == 3

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        assert dossier.status == DossierStatus.CLASSIFIED.value
        docs = list_documents(s, dossier_id)
        assert len(docs) == batch_size + 3
        assert all(d.proposed_category == "AUTRES" for d in docs)


async def test_llm_failure_on_a_batch_marks_the_batch_documents_as_error_but_pipeline_completes(
    isolated_workspace, monkeypatch
):
    """Une panne LLM sur le lot d'ambigus ne doit pas faire planter tout le pipeline : les
    documents du lot sont marqués en erreur, le dossier atteint quand même `classified`."""
    with session_scope() as s:
        dossier = create_dossier(s, "root.zip")
        dossier_id = dossier.id
        _create_doc(s, dossier_id, relative_path="DIVERS/scan1.pdf")

    def _boom(**kwargs):
        raise RuntimeError("API Mistral indisponible")

    monkeypatch.setattr(engine, "call_structured_chat", _boom)

    await run_classification_pipeline(dossier_id)

    with session_scope() as s:
        dossier = get_dossier(s, dossier_id)
        # Le pipeline lui-même ne plante pas : c'est run_pipeline_safely (couche API) qui
        # gère l'exception s'il y en avait une non rattrapée plus haut. Ici, l'échec LLM est
        # rattrapé au niveau du moteur de classification lui-même (par document).
        assert dossier.status == DossierStatus.CLASSIFIED.value
        docs = list_documents(s, dossier_id)
        assert docs[0].classification_status == ClassificationStatus.ERROR.value
        assert docs[0].classification_error is not None
