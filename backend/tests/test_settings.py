from __future__ import annotations

from app.settings import get_models_config


def test_models_config_has_all_required_sections():
    cfg = get_models_config()
    for section in (
        "ocr",
        "llm",
        "extraction",
        "completeness",
        "classification",
        "text_extraction",
        "feature_flags",
    ):
        assert section in cfg, f"section manquante : {section}"

    assert cfg["ocr"]["model"]
    assert cfg["llm"]["temperature"] == 0.0
    assert cfg["feature_flags"]["precompute_rcmo_trc"] is False
    assert cfg["text_extraction"]["scanned_pdf_density_threshold"] < cfg["text_extraction"]["native_text_density_threshold"]


# --- Clé principale + clés de secours ----------------------------------------------------------

def _reload_settings(monkeypatch, **env):
    """Recharge les settings avec un environnement donné. Chaque variable est explicitement posée
    (jamais delenv) : le dépôt a un vrai .env sur disque que pydantic-settings relirait sinon."""
    from app.settings import get_settings

    for name in ("MISTRAL_API_KEY", "MISTRAL_API_KEY_2", "MISTRAL_API_KEY_3",
                 "MISTRAL_API_KEY_4", "MISTRAL_API_KEY_5", "MISTRAL_API_KEYS"):
        monkeypatch.setenv(name, env.get(name, ""))
    get_settings.cache_clear()
    return get_settings()


def test_single_key_stays_the_only_key(isolated_workspace, monkeypatch):
    settings = _reload_settings(monkeypatch, MISTRAL_API_KEY="principale")

    assert settings.mistral_api_keys == ["principale"]
    assert settings.mistral_api_key == "principale"


def test_numbered_backup_keys_are_ordered_by_priority(isolated_workspace, monkeypatch):
    settings = _reload_settings(
        monkeypatch, MISTRAL_API_KEY="principale", MISTRAL_API_KEY_2="secours1", MISTRAL_API_KEY_3="secours2"
    )

    assert settings.mistral_api_keys == ["principale", "secours1", "secours2"]
    # `mistral_api_key` reste la clé principale : tout le code existant continue de fonctionner.
    assert settings.mistral_api_key == "principale"


def test_comma_separated_form_wins_over_the_numbered_one(isolated_workspace, monkeypatch):
    settings = _reload_settings(
        monkeypatch, MISTRAL_API_KEY="ignoree", MISTRAL_API_KEYS=" a , b ,c "
    )

    assert settings.mistral_api_keys == ["a", "b", "c"]


def test_duplicate_and_empty_keys_are_dropped(isolated_workspace, monkeypatch):
    """Une clé de secours identique à la principale partage le même quota : la compter deux fois
    ferait croire à un secours qui n'existe pas."""
    settings = _reload_settings(
        monkeypatch, MISTRAL_API_KEY="principale", MISTRAL_API_KEY_2="principale", MISTRAL_API_KEY_3="secours"
    )

    assert settings.mistral_api_keys == ["principale", "secours"]


def test_gap_in_the_numbering_does_not_drop_later_keys(isolated_workspace, monkeypatch):
    settings = _reload_settings(monkeypatch, MISTRAL_API_KEY="principale", MISTRAL_API_KEY_3="secours")

    assert settings.mistral_api_keys == ["principale", "secours"]


def test_no_key_configured_yields_an_empty_list(isolated_workspace, monkeypatch):
    settings = _reload_settings(monkeypatch)

    assert settings.mistral_api_keys == []
    assert settings.mistral_api_key == ""
