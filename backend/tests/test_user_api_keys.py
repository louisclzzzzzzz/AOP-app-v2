"""Clé API Mistral personnelle par utilisateur (§app/api/me.py, app/pipeline_support.py,
app/mistral/client.py) : chaque personne apporte la sienne sur un déploiement public
(AOP_REQUIRE_AUTH) plutôt que de consommer le quota d'une clé globale partagée.

Couvre : chiffrement au repos (app/auth/crypto.py), CRUD (app/store/repository.py), bascule de
la clé effectivement utilisée par le client Mistral (app/mistral/client.py
`use_user_api_key`), le point de jonction qui résout la clé du propriétaire d'un dossier
(app/pipeline_support.py `owner_api_key`), et les routes REST (app/api/me.py). Pas de suivi de
consommation : Mistral n'expose aucune API de solde/quota (§app/store/models.py UserApiKey) —
le frontend renvoie vers admin.mistral.ai/subscription."""
from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.auth.crypto import decrypt_secret, encrypt_secret
from app.auth.security import COOKIE_NAME, create_session_cookie, hash_access_code
from app.settings import get_settings

_HTTPS = "https://testserver"


def _session_client(app, user_id: str) -> TestClient:
    token = create_session_cookie(secret_key=get_settings().secret_key, user_id=user_id)
    client = TestClient(app, base_url=_HTTPS)
    client.cookies.set(COOKIE_NAME, token)
    return client


# --- Chiffrement au repos ------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    token = encrypt_secret("ma-cle-secrete", secret_key="secret-a")
    assert token != "ma-cle-secrete"
    assert decrypt_secret(token, secret_key="secret-a") == "ma-cle-secrete"


def test_decrypt_with_wrong_secret_returns_none():
    token = encrypt_secret("ma-cle-secrete", secret_key="secret-a")
    assert decrypt_secret(token, secret_key="secret-b") is None


# --- Repository : CRUD ------------------------------------------------------------------------

def test_save_then_get_user_api_key(isolated_workspace):
    from app.store.db import session_scope
    from app.store.repository import get_user_api_key_row, save_user_api_key

    with session_scope() as s:
        save_user_api_key(s, "user-1", "encrypted-value")
    with session_scope() as s:
        row = get_user_api_key_row(s, "user-1")
        assert row is not None
        assert row.mistral_api_key_encrypted == "encrypted-value"


def test_save_user_api_key_overwrites_previous_value(isolated_workspace):
    from app.store.db import session_scope
    from app.store.repository import get_user_api_key_row, save_user_api_key

    with session_scope() as s:
        save_user_api_key(s, "user-1", "first")
        save_user_api_key(s, "user-1", "second")
    with session_scope() as s:
        assert get_user_api_key_row(s, "user-1").mistral_api_key_encrypted == "second"


def test_clear_user_api_key(isolated_workspace):
    from app.store.db import session_scope
    from app.store.repository import clear_user_api_key, get_user_api_key_row, save_user_api_key

    with session_scope() as s:
        save_user_api_key(s, "user-1", "encrypted-value")
    with session_scope() as s:
        clear_user_api_key(s, "user-1")
    with session_scope() as s:
        assert get_user_api_key_row(s, "user-1").mistral_api_key_encrypted is None


# --- app/mistral/client.py : bascule de clé via use_user_api_key ---------------------------

def test_use_user_api_key_overrides_configured_keys(isolated_workspace):
    import app.mistral.client as client_mod

    assert client_mod._configured_keys() == []
    with client_mod.use_user_api_key("user-1", "cle-personnelle"):
        assert client_mod._configured_keys() == ["cle-personnelle"]
        assert client_mod.api_slot_count() == 1
    assert client_mod._configured_keys() == []


def test_use_user_api_key_builds_a_single_client_for_that_key(isolated_workspace):
    import app.mistral.client as client_mod

    with client_mod.use_user_api_key("user-1", "cle-personnelle"):
        clients = client_mod.get_clients()
        assert len(clients) == 1
        assert clients[0].sdk_configuration.security.api_key == "cle-personnelle"


# --- app/pipeline_support.py : owner_api_key ------------------------------------------------

def test_owner_api_key_is_a_noop_when_require_auth_is_off(isolated_workspace):
    """Comportement historique inchangé (usage local/exécutable Windows) : jamais d'exigence de
    clé personnelle quand AOP_REQUIRE_AUTH est désactivé."""
    import app.mistral.client as client_mod
    from app.pipeline_support import owner_api_key

    async def _run():
        async with owner_api_key("dossier-inexistant"):
            assert client_mod._configured_keys() == []

    import asyncio

    asyncio.run(_run())


def test_owner_api_key_raises_when_owner_has_no_key_configured(isolated_workspace, monkeypatch):
    from app.mistral.client import MistralNotConfiguredError
    from app.pipeline_support import owner_api_key
    from app.store.db import session_scope
    from app.store.repository import create_dossier

    monkeypatch.setenv("AOP_REQUIRE_AUTH", "true")
    get_settings.cache_clear()

    with session_scope() as s:
        dossier = create_dossier(s, "test.zip", owner_user_id="user-1")
        dossier_id = dossier.id

    import asyncio
    import pytest

    async def _run():
        async with owner_api_key(dossier_id):
            pass

    with pytest.raises(MistralNotConfiguredError):
        asyncio.run(_run())


def test_owner_api_key_activates_the_owners_decrypted_key(isolated_workspace, monkeypatch):
    import asyncio

    import app.mistral.client as client_mod
    from app.pipeline_support import owner_api_key
    from app.store.db import session_scope
    from app.store.repository import create_dossier, save_user_api_key

    monkeypatch.setenv("AOP_REQUIRE_AUTH", "true")
    get_settings.cache_clear()
    settings = get_settings()

    with session_scope() as s:
        dossier = create_dossier(s, "test.zip", owner_user_id="user-1")
        dossier_id = dossier.id
        encrypted = encrypt_secret("cle-du-proprietaire", secret_key=settings.secret_key)
        save_user_api_key(s, "user-1", encrypted)

    async def _run():
        async with owner_api_key(dossier_id):
            assert client_mod._configured_keys() == ["cle-du-proprietaire"]

    asyncio.run(_run())
    assert client_mod._configured_keys() == []  # bien réinitialisé en sortie de bloc


# --- app/api/me.py ---------------------------------------------------------------------------

def _install_fake_mistral_models_list(monkeypatch, *, should_fail: bool) -> None:
    import app.api.me as me_mod

    class _FakeModels:
        def list(self):
            if should_fail:
                raise RuntimeError("clé invalide")

    class _FakeMistral:
        def __init__(self, api_key: str) -> None:
            self.models = _FakeModels()

    monkeypatch.setattr(me_mod, "Mistral", _FakeMistral)


def test_get_mistral_key_status_when_unconfigured(isolated_workspace):
    from app.main import app

    client = _session_client(app, "user-1")
    res = client.get("/api/me/mistral-key")
    assert res.status_code == 200
    assert res.json() == {"configured": False, "masked": None}


def test_set_mistral_key_rejects_a_key_refused_by_mistral(isolated_workspace, monkeypatch):
    from app.main import app

    _install_fake_mistral_models_list(monkeypatch, should_fail=True)
    client = _session_client(app, "user-1")
    res = client.put("/api/me/mistral-key", json={"api_key": "une-cle-suffisamment-longue-mais-invalide"})
    assert res.status_code == 400


def test_set_mistral_key_rejects_an_obviously_too_short_value(isolated_workspace):
    from app.main import app

    client = _session_client(app, "user-1")
    res = client.put("/api/me/mistral-key", json={"api_key": "trop-court"})
    assert res.status_code == 400


def test_set_then_get_mistral_key_status(isolated_workspace, monkeypatch):
    from app.main import app

    _install_fake_mistral_models_list(monkeypatch, should_fail=False)
    client = _session_client(app, "user-1")

    put_res = client.put("/api/me/mistral-key", json={"api_key": "une-cle-suffisamment-longue-et-valide"})
    assert put_res.status_code == 200
    body = put_res.json()
    assert body["configured"] is True
    assert body["masked"] is not None
    assert "une-cle-suffisamment-longue-et-valide" not in body["masked"]

    get_res = client.get("/api/me/mistral-key")
    assert get_res.json()["configured"] is True


def test_delete_mistral_key(isolated_workspace, monkeypatch):
    from app.main import app

    _install_fake_mistral_models_list(monkeypatch, should_fail=False)
    client = _session_client(app, "user-1")
    client.put("/api/me/mistral-key", json={"api_key": "une-cle-suffisamment-longue-et-valide"})

    del_res = client.delete("/api/me/mistral-key")
    assert del_res.status_code == 204
    assert client.get("/api/me/mistral-key").json()["configured"] is False


def test_mistral_key_is_scoped_per_user(isolated_workspace, monkeypatch):
    """La clé configurée par une personne n'apparaît jamais pour une autre — chacun n'accède
    qu'à la sienne (§hash_access_code, identifiant par session)."""
    from app.main import app

    _install_fake_mistral_models_list(monkeypatch, should_fail=False)
    alice = _session_client(app, hash_access_code("1111"))
    bob = _session_client(app, hash_access_code("2222"))

    alice.put("/api/me/mistral-key", json={"api_key": "cle-personnelle-alice-xxx"})

    assert alice.get("/api/me/mistral-key").json()["configured"] is True
    assert bob.get("/api/me/mistral-key").json()["configured"] is False


# --- app/api/dossiers.py : l'upload exige une clé personnelle quand AOP_REQUIRE_AUTH est actif --

def _tiny_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("piece.txt", "contenu")
    return buf.getvalue()


def _stub_classification_llm(monkeypatch) -> None:
    """Le pipeline d'ingestion enchaîne automatiquement sur la classification (étape 1,
    §app/api/dossiers.py `_run_pipeline_safely`), qui appelle le LLM pour de vrai sans ce stub
    — avec la fausse clé personnelle utilisée ici, chaque tentative échoue (401) et épuise les
    3 essais de `_retry` avec un vrai backoff réseau (~25s observés). Ce test ne veut vérifier
    que le comportement du garde-fou d'upload, pas le pipeline en tâche de fond."""
    import re

    import app.classify.engine as engine

    def _fake(*, system_prompt, user_prompt, response_model, what, model=None):
        item_model = response_model.model_fields["items"].annotation.__args__[0]
        indices = [int(m) for m in re.findall(r"--- Document index=(\d+) ---", user_prompt)]
        items = [
            item_model(
                index=i, category_path="AUTRES", lot=None, document_type="AUTRES",
                normalized_label="Document", confidence=0.5, justification="stub de test",
            )
            for i in indices
        ]
        return response_model(items=items), "mistral-small-test-stub"

    monkeypatch.setattr(engine, "call_structured_chat", _fake)


def test_upload_is_rejected_without_a_personal_key_when_require_auth(isolated_workspace, monkeypatch):
    from app.main import app

    monkeypatch.setenv("AOP_REQUIRE_AUTH", "true")
    get_settings.cache_clear()

    client = _session_client(app, "user-1")
    res = client.post("/api/dossiers", files={"file": ("test.zip", _tiny_zip_bytes(), "application/zip")})
    assert res.status_code == 400


def test_upload_succeeds_once_a_personal_key_is_configured(isolated_workspace, monkeypatch):
    from app.main import app
    from app.store.db import session_scope
    from app.store.repository import save_user_api_key

    _stub_classification_llm(monkeypatch)
    monkeypatch.setenv("AOP_REQUIRE_AUTH", "true")
    get_settings.cache_clear()
    settings = get_settings()

    user_id = "user-1"
    with session_scope() as s:
        encrypted = encrypt_secret("cle-personnelle", secret_key=settings.secret_key)
        save_user_api_key(s, user_id, encrypted)

    client = _session_client(app, user_id)
    res = client.post("/api/dossiers", files={"file": ("test.zip", _tiny_zip_bytes(), "application/zip")})
    assert res.status_code == 200
    assert res.json()["original_filename"] == "test.zip"
