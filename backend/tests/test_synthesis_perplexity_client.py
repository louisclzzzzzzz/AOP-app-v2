"""Tests du wrapper `app/synthesis_perplexity/client.py` : soumission + scrutation d'un job
Deep Research, sans jamais toucher le vrai SDK/réseau (client Perplexity entièrement simulé)."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.synthesis_perplexity import client as ppx_client


def _job(status, *, id="job-1", response=None, error_message=None):
    return SimpleNamespace(id=id, status=status, response=response, error_message=error_message)


def _response(text, *, citations=None, model="sonar-deep-research-test", usage=None):
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], citations=citations or [], model=model, usage=usage)


class _FakeCompletions:
    def __init__(self, get_sequence, *, create_result=None):
        self._get_sequence = list(get_sequence)
        self._create_result = create_result or _job("CREATED")
        self.get_calls = 0
        self.create_calls = 0

    def create(self, *, request):
        self.create_calls += 1
        return self._create_result

    def get(self, job_id):
        self.get_calls += 1
        return self._get_sequence.pop(0)


class _FakeClient:
    def __init__(self, completions):
        self.async_ = SimpleNamespace(chat=SimpleNamespace(completions=completions))


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Les tests ne doivent jamais attendre pour de vrai : `poll_interval_seconds` réel ou pas,
    on neutralise `time.sleep` globalement pour la durée du test."""
    monkeypatch.setattr(ppx_client.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _small_config(monkeypatch):
    monkeypatch.setattr(
        ppx_client,
        "get_models_config",
        lambda: {
            "perplexity": {
                "model": "sonar-deep-research",
                "poll_interval_seconds": 0,
                "max_wait_seconds": 5,
                "max_retries": 2,
                "temperature": 0.2,
            }
        },
    )


def test_run_deep_research_polls_until_completed(monkeypatch):
    completions = _FakeCompletions(
        [
            _job("IN_PROGRESS"),
            _job("COMPLETED", response=_response("Contenu final.", citations=["https://a"], model="sonar-deep-research")),
        ]
    )
    monkeypatch.setattr(ppx_client, "get_client", lambda: _FakeClient(completions))

    content, citations, model = ppx_client.run_deep_research(system_prompt="s", user_prompt="u", what="test")

    assert content == "Contenu final."
    assert citations == ["https://a"]
    assert model == "sonar-deep-research"
    assert completions.get_calls == 2


def test_run_deep_research_raises_on_failed_status(monkeypatch):
    completions = _FakeCompletions([_job("FAILED", error_message="boum")])
    monkeypatch.setattr(ppx_client, "get_client", lambda: _FakeClient(completions))

    with pytest.raises(ppx_client.DeepResearchFailedError, match="boum"):
        ppx_client.run_deep_research(system_prompt="s", user_prompt="u", what="test")


def test_run_deep_research_raises_on_timeout(monkeypatch):
    completions = _FakeCompletions([])
    monkeypatch.setattr(ppx_client, "get_client", lambda: _FakeClient(completions))
    monkeypatch.setattr(
        ppx_client,
        "get_models_config",
        lambda: {
            "perplexity": {
                "model": "sonar-deep-research",
                "poll_interval_seconds": 0,
                "max_wait_seconds": 0,
                "max_retries": 1,
                "temperature": 0.2,
            }
        },
    )

    with pytest.raises(ppx_client.DeepResearchTimeoutError):
        ppx_client.run_deep_research(system_prompt="s", user_prompt="u", what="test")


def test_extract_text_handles_plain_string_and_structured_chunks():
    assert ppx_client._extract_text("bonjour") == "bonjour"
    chunks = [SimpleNamespace(text="a"), SimpleNamespace(text=None), SimpleNamespace(text="b")]
    assert ppx_client._extract_text(chunks) == "a\nb"
    assert ppx_client._extract_text(None) == ""


def test_retry_recovers_after_transient_error_then_succeeds():
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ppx_client.PerplexityError("transient")
        return "ok"

    result = ppx_client._retry(_flaky, what="test", max_retries=3)
    assert result == "ok"
    assert calls["n"] == 2


def test_retry_gives_up_after_max_retries():
    def _boom():
        raise ppx_client.PerplexityError("dead")

    with pytest.raises(ppx_client.PerplexityError, match="dead"):
        ppx_client._retry(_boom, what="test", max_retries=2)


def test_retry_retries_specifically_on_rate_limit():
    calls = {"n": 0}
    request = httpx.Request("POST", "https://api.perplexity.ai/async/chat/completions")
    response = httpx.Response(status_code=429, request=request)

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ppx_client.RateLimitError("rate limited", response=response, body=None)
        return "ok"

    result = ppx_client._retry(_flaky, what="test", max_retries=3)
    assert result == "ok"
    assert calls["n"] == 2
