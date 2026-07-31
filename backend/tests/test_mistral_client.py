from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.mistral.client import MistralNotConfiguredError, _retry, get_clients, reset_slots_for_tests


def test_get_client_raises_when_api_key_missing(isolated_workspace, monkeypatch):
    # isolated_workspace neutralise déjà MISTRAL_API_KEY (jamais un delenv : le dépôt a un
    # vrai .env sur disque que pydantic-settings relirait sinon dès que la variable de
    # process est absente).
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    reset_slots_for_tests()
    with pytest.raises(MistralNotConfiguredError):
        get_clients()


def _fake_mistral_error(message: str):
    import httpx
    from mistralai.client.errors.mistralerror import MistralError

    fake_response = httpx.Response(status_code=500, request=httpx.Request("GET", "http://test"))
    return MistralError(message, fake_response)


def test_retry_succeeds_after_transient_failures(isolated_workspace, monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _fake_mistral_error("temporary failure")
        return "ok"

    # Neutralise l'attente de backoff. Via monkeypatch et JAMAIS par affectation directe :
    # `client_mod.time` est le module `time` global du processus, donc une affectation non
    # restaurée remplace `time.sleep` pour TOUTE la session de tests (c'était le cas ici, et ça
    # faisait passer à tort les tests de concurrence qui s'appuient sur un vrai sleep).
    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda _seconds: None)
    result = _retry(flaky, what="test")

    assert result == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_exhausting_attempts(isolated_workspace, monkeypatch):
    from mistralai.client.errors.mistralerror import MistralError

    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda _seconds: None)

    def always_fails():
        raise _fake_mistral_error("permanent failure")

    with pytest.raises(MistralError):
        _retry(always_fails, what="test")


def _fake_clock(monkeypatch, client_mod, start: float = 100.0):
    """Horloge monotone simulée + `sleep` qui la fait avancer, pour tester l'ordonnanceur sans
    attendre réellement."""
    now = {"t": start}
    sleeps: list[float] = []

    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr(client_mod.time, "sleep", fake_sleep)
    return now, sleeps


def _use_slots(monkeypatch, client_mod, count: int, *, min_interval: float) -> None:
    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"llm": {"min_interval_seconds": min_interval}})
    monkeypatch.setattr(client_mod, "api_slot_count", lambda: count)
    client_mod.reset_slots_for_tests()


def test_single_key_scheduler_spaces_out_consecutive_calls(isolated_workspace, monkeypatch):
    """Comportement historique du token-bucket global : avec une seule clé, rien ne change."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 1, min_interval=5.0)
    now, sleeps = _fake_clock(monkeypatch, client_mod)

    assert client_mod._acquire_llm_slot() == 0
    assert sleeps == []  # premier appel : rien à attendre

    now["t"] += 1.0  # 1s plus tard, il en faudrait 5 -> attend 4
    assert client_mod._acquire_llm_slot() == 0
    assert sleeps == [4.0]


def test_scheduler_disabled_when_interval_zero(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 1, min_interval=0.0)

    def fail_if_called(_seconds: float) -> None:
        raise AssertionError("time.sleep ne devrait jamais être appelé quand le throttle est désactivé")

    monkeypatch.setattr(client_mod.time, "sleep", fail_if_called)

    client_mod._acquire_llm_slot()
    client_mod._acquire_llm_slot()


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

    monkeypatch.setattr(client_mod, "get_client", lambda slot=0: _Client())
    monkeypatch.setattr(client_mod, "_acquire_llm_slot", lambda: 0)
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


def test_call_structured_chat_honours_explicit_max_tokens(isolated_workspace, monkeypatch):
    """Les étapes « map » des deux phases produisent bien plus de sortie qu'un thème ou une section
    (le RICT du dossier de test rend ~117 constats sur 6 sections) : sans plafond propre, la réponse
    était coupée en plein JSON et le rattrapage rendait un relevé silencieusement appauvri."""
    import app.mistral.client as client_mod

    calls: list[int | None] = []

    class _Chat:
        def complete(self, **kwargs):
            calls.append(kwargs.get("max_tokens"))
            return _FakeResponse(_VALID_JSON, "stop")

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(client_mod, "get_client", lambda slot=0: _Client())
    monkeypatch.setattr(client_mod, "_acquire_llm_slot", lambda: 0)
    monkeypatch.setattr(
        client_mod, "get_models_config",
        lambda: {"llm": {"model": "m", "temperature": 0.0, "max_retries": 3, "parse_retries": 0,
                         "max_tokens": 8000, "max_tokens_document_summary": 16000}},
    )

    client_mod.call_structured_chat(system_prompt="s", user_prompt="u", response_model=_StubModel, what="t")
    client_mod.call_structured_chat(
        system_prompt="s", user_prompt="u", response_model=_StubModel, what="t",
        max_tokens=client_mod.document_summary_max_tokens(),
    )

    assert calls == [8000, 16000]


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


def _run_ocr_workers(client_mod, *, workers: int):
    """Lance `workers` threads dans `ocr_slot()` et relève la concurrence atteinte, globalement et
    par créneau. Les exceptions des threads sont remontées : sans ça, un `ocr_slot` cassé ferait
    passer le test avec un pic à 0."""
    import threading
    import time as time_mod

    lock = threading.Lock()
    current = {"n": 0}
    peak = {"n": 0}
    per_slot_peak: dict[int, int] = {}
    per_slot_current: dict[int, int] = {}
    used: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with client_mod.ocr_slot() as slot:
                with lock:
                    used.append(slot)
                    current["n"] += 1
                    peak["n"] = max(peak["n"], current["n"])
                    per_slot_current[slot] = per_slot_current.get(slot, 0) + 1
                    per_slot_peak[slot] = max(per_slot_peak.get(slot, 0), per_slot_current[slot])
                time_mod.sleep(0.05)
                with lock:
                    current["n"] -= 1
                    per_slot_current[slot] -= 1
        except BaseException as exc:  # noqa: BLE001 - remonté tel quel dans l'assertion
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"ocr_slot a levé : {errors[0]!r}"
    return peak["n"], per_slot_peak, used


def test_ocr_slot_bounds_concurrency_per_key(isolated_workspace, monkeypatch):
    """`max_concurrency` est un plafond PAR CLÉ : avec une seule clé, il borne tout l'OCR."""
    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"ocr": {"max_concurrency": 2}})
    monkeypatch.setattr(client_mod, "api_slot_count", lambda: 1)
    client_mod.reset_slots_for_tests()

    peak, per_slot_peak, used = _run_ocr_workers(client_mod, workers=6)

    assert peak <= 2
    assert set(used) == {0}
    assert per_slot_peak[0] <= 2


# --- Bascule de la charge quand une clé ne fonctionne pas ---------------------------------------

def _fake_mistral_error_with_status(message: str, status: int):
    import httpx
    from mistralai.client.errors.mistralerror import MistralError

    response = httpx.Response(status_code=status, request=httpx.Request("GET", "http://test"))
    return MistralError(message, response)


def test_all_keys_quarantined_still_attempts_instead_of_hanging(isolated_workspace, monkeypatch):
    """Mieux vaut un échec explicite qu'un dossier figé : si tout est écarté, on tente quand même
    la clé dont la quarantaine expire le plus tôt."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    now, sleeps = _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))
    now["t"] += 1  # la clé 1 sera écartée plus tard, donc plus longtemps que la clé 0
    client_mod.record_slot_failure(1, _fake_mistral_error_with_status("Too Many Requests", 429))

    slot = client_mod._acquire_llm_slot()

    assert slot == 0  # celle dont la quarantaine expire le plus tôt
    assert sleeps == []  # on n'attend pas la fin de la quarantaine


def test_health_snapshot_reports_slots_without_leaking_keys(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 3, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)
    client_mod.record_slot_failure(2, _fake_mistral_error_with_status("Unauthorized", 401))

    health = client_mod.api_slots_health()

    assert [h["slot"] for h in health] == [1, 2, 3]
    assert health[2]["quarantined"] is True
    assert health[0]["quarantined"] is False
    assert not any("key" in h or "cle" in h for h in health)


def test_call_structured_chat_moves_to_another_key_after_a_rejected_one(isolated_workspace, monkeypatch):
    """Bout-en-bout : la première clé refuse (401), `_retry` rejoue, et la tentative suivante part
    sur une autre clé — sans quoi les 3 tentatives se seraient acharnées sur la clé morte."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 3, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)
    monkeypatch.setattr(
        client_mod,
        "get_models_config",
        lambda: {"llm": {"min_interval_seconds": 0.0, "max_retries": 3, "model": "m", "parse_retries": 0}},
    )

    used: list[int] = []
    broken_slot = 0

    class _PerSlotClient:
        def __init__(self, slot: int) -> None:
            self.slot = slot
            self.chat = self

        def complete(self, **kwargs):
            used.append(self.slot)
            if self.slot == broken_slot:
                raise _fake_mistral_error_with_status("Unauthorized", 401)
            return _FakeResponse(_VALID_JSON, "stop")

    monkeypatch.setattr(client_mod, "get_client", lambda slot=0: _PerSlotClient(slot))

    parsed, _model = client_mod.call_structured_chat(
        system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
    )

    assert parsed.x == 1
    assert used[0] == broken_slot  # première tentative sur la clé qui casse
    assert used[-1] != broken_slot  # la réussite vient d'une autre clé
    assert client_mod.api_slots_health()[broken_slot]["quarantined"] is True


# --- Clé de secours : bascule quand la clé principale ne fonctionne plus ------------------------

def _fake_mistral_error_with_status(message: str, status: int):
    import httpx
    from mistralai.client.errors.mistralerror import MistralError

    response = httpx.Response(status_code=status, request=httpx.Request("GET", "http://test"))
    return MistralError(message, response)


def test_primary_key_takes_every_call_while_it_works(isolated_workspace, monkeypatch):
    """Pas de répartition de charge : tant que la principale répond, le secours ne sert jamais."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 3, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    assert [client_mod._acquire_llm_slot() for _ in range(10)] == [0] * 10


def test_exhausted_primary_key_falls_back_to_the_backup(isolated_workspace, monkeypatch):
    """Cas visé : la clé principale arrive au bout de son quota (429 à répétition). Les appels
    doivent passer sur la clé de secours au lieu d'échouer."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))

    assert [client_mod._acquire_llm_slot() for _ in range(5)] == [1] * 5


def test_revoked_primary_key_falls_back_to_the_backup(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Unauthorized", 401))

    assert client_mod._acquire_llm_slot() == 1


def test_backup_chain_walks_down_in_declaration_order(isolated_workspace, monkeypatch):
    """Avec 3 clés, on descend dans l'ordre : principale, puis secours 1, puis secours 2."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 3, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("quota", 402))
    assert client_mod._acquire_llm_slot() == 1

    client_mod.record_slot_failure(1, _fake_mistral_error_with_status("quota", 402))
    assert client_mod._acquire_llm_slot() == 2


def test_primary_key_is_used_again_once_its_quarantine_expires(isolated_workspace, monkeypatch):
    """Le secours est temporaire : dès que la principale redevient utilisable (quota renouvelé,
    rate limit passé), elle reprend la main sans intervention."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    now, _sleeps = _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))
    assert client_mod._acquire_llm_slot() == 1

    now["t"] += client_mod._QUARANTINE_RATE_LIMITED_SECONDS + 1
    assert client_mod._acquire_llm_slot() == 0


def test_quarantine_lengthens_while_the_key_keeps_failing(isolated_workspace, monkeypatch):
    """Une clé réellement épuisée renvoie le même 429 à chaque essai : la mise à l'écart doit
    s'allonger, sinon on la re-teste toutes les 30 s pour rien."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    now, _sleeps = _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))
    first = client_mod.api_slots_health()[0]["quarantined_for_seconds"]

    now["t"] += first + 1
    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))
    second = client_mod.api_slots_health()[0]["quarantined_for_seconds"]

    assert second > first


def test_a_success_brings_the_primary_key_back_immediately(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Unauthorized", 401))
    assert client_mod._acquire_llm_slot() == 1

    client_mod.record_slot_success(0)

    assert client_mod._acquire_llm_slot() == 0


def test_transient_error_does_not_switch_key_immediately(isolated_workspace, monkeypatch):
    """Un 500 n'est pas imputable à la clé : basculer dès le premier échec écarterait la clé
    principale à la moindre indisponibilité de l'API."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("boom", 500))

    assert client_mod._acquire_llm_slot() == 0


def test_repeated_transient_errors_eventually_switch_key(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)

    for _ in range(client_mod._SLOT_FAILURE_THRESHOLD):
        client_mod.record_slot_failure(0, _fake_mistral_error_with_status("boom", 500))

    assert client_mod._acquire_llm_slot() == 1


def test_all_keys_quarantined_still_attempts_instead_of_hanging(isolated_workspace, monkeypatch):
    """Mieux vaut un échec explicite qu'un dossier figé : si tout est écarté, on tente quand même
    la clé dont la quarantaine expire le plus tôt, sans attendre sa fin."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    now, sleeps = _fake_clock(monkeypatch, client_mod)

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("Too Many Requests", 429))
    now["t"] += 1  # la clé 1 est écartée plus tard, donc pour plus longtemps
    client_mod.record_slot_failure(1, _fake_mistral_error_with_status("Too Many Requests", 429))

    assert client_mod._acquire_llm_slot() == 0
    assert sleeps == []


def test_backup_key_starts_with_its_own_pacing_budget(isolated_workspace, monkeypatch):
    """Le stimulateur est tenu par clé : après une bascule, le secours n'hérite pas de l'historique
    d'appels de la clé défaillante et démarre sans attente."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=5.0)
    _now, sleeps = _fake_clock(monkeypatch, client_mod)

    assert client_mod._acquire_llm_slot() == 0  # consomme le budget de la principale
    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("quota", 402))

    assert client_mod._acquire_llm_slot() == 1
    assert sleeps == []


def test_ocr_also_falls_back_to_the_backup_key(isolated_workspace, monkeypatch):
    """L'OCR suit la même règle : sinon un dossier scanné continuerait de taper la clé épuisée."""
    import app.mistral.client as client_mod

    monkeypatch.setattr(client_mod, "get_models_config", lambda: {"ocr": {"max_concurrency": 2}})
    monkeypatch.setattr(client_mod, "api_slot_count", lambda: 2)
    client_mod.reset_slots_for_tests()

    with client_mod.ocr_slot() as slot:
        assert slot == 0

    client_mod.record_slot_failure(0, _fake_mistral_error_with_status("quota", 402))

    with client_mod.ocr_slot() as slot:
        assert slot == 1


def test_health_snapshot_reports_slots_without_leaking_keys(isolated_workspace, monkeypatch):
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 3, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)
    client_mod.record_slot_failure(2, _fake_mistral_error_with_status("Unauthorized", 401))

    health = client_mod.api_slots_health()

    assert [h["slot"] for h in health] == [1, 2, 3]
    assert health[2]["quarantined"] is True
    assert health[0]["quarantined"] is False
    assert not any("key" in h or "cle" in h for h in health)


def test_call_structured_chat_moves_to_the_backup_after_the_primary_is_rejected(isolated_workspace, monkeypatch):
    """Bout-en-bout : la principale refuse (401), `_retry` rejoue, et la tentative suivante part
    sur le secours — sans quoi les 3 tentatives se seraient acharnées sur la clé morte."""
    import app.mistral.client as client_mod

    _use_slots(monkeypatch, client_mod, 2, min_interval=0.0)
    _fake_clock(monkeypatch, client_mod)
    monkeypatch.setattr(
        client_mod,
        "get_models_config",
        lambda: {"llm": {"min_interval_seconds": 0.0, "max_retries": 3, "model": "m", "parse_retries": 0}},
    )

    used: list[int] = []

    class _PerSlotClient:
        def __init__(self, slot: int) -> None:
            self.slot = slot
            self.chat = self

        def complete(self, **kwargs):
            used.append(self.slot)
            if self.slot == 0:
                raise _fake_mistral_error_with_status("Unauthorized", 401)
            return _FakeResponse(_VALID_JSON, "stop")

    monkeypatch.setattr(client_mod, "get_client", lambda slot=0: _PerSlotClient(slot))

    parsed, _model = client_mod.call_structured_chat(
        system_prompt="s", user_prompt="u", response_model=_StubModel, what="test"
    )

    assert parsed.x == 1
    assert used == [0, 1]  # 1re tentative sur la principale, 2e sur le secours
    assert client_mod.api_slots_health()[0]["quarantined"] is True
