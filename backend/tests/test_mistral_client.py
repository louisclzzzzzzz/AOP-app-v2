from __future__ import annotations

import pytest
from pydantic import BaseModel

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


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = None
        self.model = "mistral-large-test"


class _StubModel(BaseModel):
    x: int


_VALID_JSON = '{"x": 1}'

# Reproduction fidèle du JSON cassé observé en production (§BUG_map_reduce_json_parse_failures.md,
# run e2e du 2026-07-29) : un guillemet droit non échappé au milieu d'une valeur texte, qui referme
# la chaîne avant sa fin. C'est ce que `json.loads` signale par "Expecting ',' delimiter".
_MALFORMED_JSON = (
    '{\n'
    '  "resumes": [\n'
    '    {\n'
    '      "theme_id": "synthese_rict",\n'
    '      "apporte_des_informations": true,\n'
    '      "resume": "Le CCTP precise "beton arme classe XC2" pour les fondations."\n'
    '    }\n'
    '  ]\n'
    '}'
)


def _install_llm_stub(monkeypatch, raw_contents):
    """Faux client dont `chat.complete` renvoie successivement les textes bruts `raw_contents` —
    c'est-à-dire exactement ce que le modèle produit AVANT tout parsing (JSON valide ou non).
    Chaque élément est soit une chaîne, soit un couple (chaîne, finish_reason)."""
    import app.mistral.client as client_mod

    calls = {"temps": [], "prompts": []}
    seq = list(raw_contents)

    class _Chat:
        def complete(self, **kwargs):
            calls["temps"].append(kwargs.get("temperature"))
            calls["prompts"].append(kwargs["messages"][1]["content"])
            item = seq.pop(0)
            content, finish_reason = item if isinstance(item, tuple) else (item, "stop")
            return _FakeResponse(content, finish_reason)

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(client_mod, "get_client", lambda: _Client())
    monkeypatch.setattr(client_mod, "_throttle_llm_call", lambda: None)
    monkeypatch.setattr(
        client_mod, "get_models_config",
        lambda: {"llm": {"model": "mistral-large-test", "temperature": 0.0, "max_retries": 3, "parse_retries": 2}},
    )
    return client_mod, calls


def test_call_structured_chat_retries_on_invalid_json(isolated_workspace, monkeypatch):
    client_mod, calls = _install_llm_stub(monkeypatch, [_MALFORMED_JSON, _VALID_JSON])

    parsed, api_model = client_mod.call_structured_chat(
        system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
    )

    assert parsed.x == 1
    assert api_model == "mistral-large-test"
    # 2 appels : le 1er à T=0.0 (échec JSON), le 2e à température relevée
    assert len(calls["temps"]) == 2
    assert calls["temps"][0] == 0.0
    assert calls["temps"][1] > 0.0


def test_call_structured_chat_repair_instruction_only_on_retry(isolated_workspace, monkeypatch):
    """Le rattrapage doit changer la CONSIGNE, pas seulement la température : à consigne inchangée,
    un prompt qui demande structurellement des guillemets droits recasse le JSON à chaque
    tentative (0% de rattrapage sur les 9 échecs du run e2e du 2026-07-29)."""
    client_mod, calls = _install_llm_stub(monkeypatch, [_MALFORMED_JSON, _VALID_JSON])

    client_mod.call_structured_chat(
        system_prompt="s", user_prompt="Consigne initiale.", response_model=_StubModel, what="test"
    )

    assert calls["prompts"][0] == "Consigne initiale."
    assert calls["prompts"][1].startswith("Consigne initiale.")
    assert "guillemet droit" in calls["prompts"][1]


def test_call_structured_chat_logs_raw_text_around_the_break(isolated_workspace, monkeypatch, caplog):
    """Le SDK (`chat.parse`) n'expose jamais le texte brut qui a fait échouer son `json.loads`, ce
    qui rendait la cause indevinable autrement que par la position de l'erreur."""
    client_mod, _ = _install_llm_stub(monkeypatch, [_MALFORMED_JSON, _VALID_JSON])

    with caplog.at_level("WARNING"):
        client_mod.call_structured_chat(
            system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
        )

    logged = caplog.text
    assert "⟦ICI⟧" in logged  # marqueur du point de rupture
    assert "beton arme classe XC2" in logged  # le guillemet fautif est lisible dans le log
    assert "finish_reason=stop" in logged


def test_call_structured_chat_diagnoses_degenerate_whitespace_loop(isolated_workspace, monkeypatch, caplog):
    """Cause réelle des 9 échecs du run e2e du 2026-07-29 : le modèle ferme correctement la chaîne,
    puis boucle sur du whitespace (toujours licite entre deux tokens JSON, donc jamais interrompu
    par la grammaire) jusqu'à ce que le serveur avorte la génération. Le JSON est un préfixe valide
    qui n'atteint jamais la virgule suivante. Le log doit nommer ce symptôme, sinon on repart sur la
    fausse piste du guillemet non échappé."""
    truncated = '{\n  "resumes": [\n    {\n      "resume": "Fin du constat.\\n"\n' + " \t" * 400
    client_mod, _ = _install_llm_stub(monkeypatch, [(truncated, "error")] * 3)

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="non parsable"):
        client_mod.call_structured_chat(
            system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
        )

    assert "finish_reason=error" in caplog.text
    assert "whitespace final" in caplog.text


def test_call_structured_chat_flags_max_tokens_truncation_distinctly(isolated_workspace, monkeypatch):
    """Même symptôme quand la boucle sature max_tokens au lieu d'être avortée par le serveur."""
    client_mod, _ = _install_llm_stub(monkeypatch, [('{"x": 1', "length")] * 3)

    with pytest.raises(RuntimeError, match="non parsable"):
        client_mod.call_structured_chat(
            system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
        )


def test_call_structured_chat_raises_after_parse_retries_exhausted(isolated_workspace, monkeypatch):
    client_mod, calls = _install_llm_stub(monkeypatch, [_MALFORMED_JSON] * 3)

    with pytest.raises(RuntimeError, match="non parsable"):
        client_mod.call_structured_chat(system_prompt="s", user_prompt="u", response_model=_StubModel, what="test")
    assert len(calls["temps"]) == 3


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
