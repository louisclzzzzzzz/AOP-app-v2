from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.settings import _win_long_path, get_models_config


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


# --- Chemins longs Windows (MAX_PATH) -----------------------------------------------------------
# Un DCE réel a des noms de dossiers/fichiers longs et une arborescence profonde (préservation
# fidèle de la source, jamais tronquée) : nichés sous workspace/<id>/.source.staging-xxxxxxxx/…,
# ils dépassent vite les 260 caractères que Windows autorise sans préfixe étendu `\\?\`
# (rencontré réellement en testant AOP-v2.exe : dépassement de quelques caractères seulement).


def test_win_long_path_is_noop_outside_windows():
    if sys.platform == "win32":
        pytest.skip("vérifie le comportement no-op hors Windows")
    p = Path("/tmp/some/deep/path")
    assert _win_long_path(p) is p


@pytest.mark.skipif(sys.platform != "win32", reason="préfixe \\\\?\\ : comportement Windows uniquement")
def test_win_long_path_adds_extended_prefix():
    p = Path(r"C:\Users\test\workspace")
    prefixed = _win_long_path(p)
    assert str(prefixed) == "\\\\?\\C:\\Users\\test\\workspace"


@pytest.mark.skipif(sys.platform != "win32", reason="préfixe \\\\?\\ : comportement Windows uniquement")
def test_win_long_path_is_idempotent():
    p = Path("\\\\?\\C:\\Users\\test\\workspace")
    assert _win_long_path(p) == p


@pytest.mark.skipif(sys.platform != "win32", reason="préfixe \\\\?\\ : comportement Windows uniquement")
def test_win_long_path_handles_unc_paths():
    p = Path(r"\\server\share\workspace")
    assert str(_win_long_path(p)) == "\\\\?\\UNC\\server\\share\\workspace"


@pytest.mark.skipif(sys.platform != "win32", reason="MAX_PATH est une limite Windows uniquement")
def test_settings_workspace_dir_is_long_path_safe(isolated_workspace):
    """`Settings.workspace_dir` doit porter le préfixe long-path : tout le code qui en dérive
    des chemins (ingestion, ocr, classify) en hérite automatiquement, sans changement requis
    ailleurs (voir app/ingestion/unzip.py, testé bout en bout dans test_unzip.py)."""
    from app.settings import get_settings

    settings = get_settings()
    assert str(settings.workspace_dir).startswith("\\\\?\\")
    # Le DSN SQLite ne doit PAS porter le préfixe : le "?" y casserait le parsing d'URL.
    assert "\\\\?\\" not in settings.database_url
