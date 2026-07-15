from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Isole chaque test dans son propre workspace + base SQLite temporaires."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.setenv("AOP_WORKSPACE_DIR", str(workspace_dir))
    monkeypatch.setenv("AOP_DATABASE_URL", f"sqlite:///{workspace_dir / 'test.db'}")

    from app.settings import get_settings, get_models_config
    from app.store.db import init_db, reset_engine_for_tests

    get_settings.cache_clear()
    get_models_config.cache_clear()
    reset_engine_for_tests()
    init_db()

    yield workspace_dir

    reset_engine_for_tests()
    get_settings.cache_clear()


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
