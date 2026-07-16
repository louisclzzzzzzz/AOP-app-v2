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


def test_throttle_llm_call_spaces_out_consecutive_calls(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"llm": {"min_interval_seconds": 5.0}})
    client_mod._llm_last_call_at = 0.0

    fake_now = {"t": 100.0}
    sleeps: list[float] = []

    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_now["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now["t"] += seconds

    monkeypatch.setattr(client_mod.time, "sleep", fake_sleep)

    client_mod._throttle_llm_call()
    assert sleeps == []  # premier appel : rien à attendre

    fake_now["t"] += 1.0  # 1s plus tard, il en faudrait 5 -> attend 4
    client_mod._throttle_llm_call()
    assert sleeps == [4.0]


def test_throttle_llm_call_disabled_when_interval_zero(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"llm": {"min_interval_seconds": 0.0}})
    client_mod._llm_last_call_at = 0.0

    def fail_if_called(_seconds: float) -> None:
        raise AssertionError("time.sleep ne devrait jamais être appelé quand le throttle est désactivé")

    monkeypatch.setattr(client_mod.time, "sleep", fail_if_called)

    client_mod._throttle_llm_call()
    client_mod._throttle_llm_call()


def test_ocr_semaphore_bounds_concurrency(isolated_workspace, monkeypatch):
    import threading
    import time as time_mod

    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"ocr": {"max_concurrency": 2}})
    client_mod._ocr_semaphore = None
    client_mod._ocr_semaphore_size = None

    lock = threading.Lock()
    current = {"n": 0}
    peak = {"n": 0}

    def worker() -> None:
        with client_mod._get_ocr_semaphore():
            with lock:
                current["n"] += 1
                peak["n"] = max(peak["n"], current["n"])
            time_mod.sleep(0.05)
            with lock:
                current["n"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak["n"] <= 2
