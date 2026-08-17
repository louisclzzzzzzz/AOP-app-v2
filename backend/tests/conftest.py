from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Isole chaque test dans son propre workspace + base SQLite temporaires.

    Neutralise aussi TOUTES les clés Mistral : le dépôt a un vrai `.env` (utilisé pour les
    vérifications manuelles via un serveur réel) et pydantic-settings le charge par défaut.
    Sans ce blindage, tout test qui déclenche le pipeline (ingestion -> classification
    étape 1) sans monkeypatcher explicitement l'appel LLM ferait un VRAI appel réseau vers
    l'API Mistral et pourrait bloquer plusieurs minutes (timeout x retries) si le sandbox
    n'a pas d'accès réseau — un test doit rester rapide et déterministe par défaut ; un test
    qui veut vérifier le comportement avec une clé réelle doit la reposer explicitement.

    Les clés de SECOURS comptent autant que la principale (§app/settings.py
    `MistralApiKeySettings.resolved_keys`) : n'en neutraliser qu'une laissait les autres fuiter
    dès qu'un `.env` local en déclarait une — donc de vrais appels facturés depuis les tests."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.setenv("AOP_WORKSPACE_DIR", str(workspace_dir))
    monkeypatch.setenv("AOP_DATABASE_URL", f"sqlite:///{workspace_dir / 'test.db'}")
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    monkeypatch.setenv("MISTRAL_API_KEYS", "")
    for numero in range(2, 6):
        monkeypatch.setenv(f"MISTRAL_API_KEY_{numero}", "")

    from app.mistral.client import reset_slots_for_tests
    from app.settings import get_settings, get_models_config
    from app.store.db import init_db, reset_engine_for_tests

    get_settings.cache_clear()
    get_models_config.cache_clear()
    reset_slots_for_tests()
    reset_engine_for_tests()
    init_db()

    yield workspace_dir

    reset_engine_for_tests()
    get_settings.cache_clear()
    reset_slots_for_tests()


@pytest.fixture
def make_zip(tmp_path):
    """Fabrique un zip à partir d'un mapping {chemin_dans_le_zip: contenu_bytes_ou_str}."""

    def _make(name: str, entries: dict[str, bytes | str | Path]) -> Path:
        zpath = tmp_path / name
        with zipfile.ZipFile(zpath, "w") as zf:
            for arcname, content in entries.items():
                if isinstance(content, Path):
                    zf.write(content, arcname)
                elif isinstance(content, bytes):
                    zf.writestr(arcname, content)
                else:
                    zf.writestr(arcname, content)
        return zpath

    return _make
