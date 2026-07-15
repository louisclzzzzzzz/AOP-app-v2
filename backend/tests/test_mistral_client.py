from __future__ import annotations

import pytest

from app.mistral.client import MistralNotConfiguredError, _retry, get_client
from app.mistral.client import get_client as get_client_fn


def test_get_client_raises_when_api_key_missing(isolated_workspace, monkeypatch):
    # isolated_workspace neutralise déjà MISTRAL_API_KEY (jamais un delenv : le dépôt a un
    # vrai .env sur disque que pydantic-settings relirait sinon dès que la variable de
    # process est absente).
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    get_client_fn.cache_clear()
    with pytest.raises(MistralNotConfiguredError):
        get_client()


def _fake_mistral_error(message: str):
    import httpx
    from mistralai.client.errors.mistralerror import MistralError

    fake_response = httpx.Response(status_code=500, request=httpx.Request("GET", "http://test"))
    return MistralError(message, fake_response)


def test_retry_succeeds_after_transient_failures(isolated_workspace):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _fake_mistral_error("temporary failure")
        return "ok"

    # monkeypatch time.sleep to avoid slowing down the test with real backoff waits
    import app.mistral.client as client_mod

    original_sleep = client_mod.time.sleep
    client_mod.time.sleep = lambda _seconds: None
    try:
        result = _retry(flaky, what="test")
    finally:
        client_mod.time.sleep = original_sleep

    assert result == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_exhausting_attempts(isolated_workspace):
    from mistralai.client.errors.mistralerror import MistralError

    import app.mistral.client as client_mod

    client_mod.time.sleep = lambda _seconds: None

    def always_fails():
        raise _fake_mistral_error("permanent failure")

    with pytest.raises(MistralError):
        _retry(always_fails, what="test")
