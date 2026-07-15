"""Configuration de l'application : variables d'environnement (.env) + fichiers YAML de config/."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
CONFIG_DIR = BACKEND_DIR / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="AOP_",
        extra="ignore",
    )

    workspace_dir: Path = PROJECT_ROOT / "workspace"
    database_url: str = ""
    backend_port: int = 8000
    frontend_port: int = 5173

    # MISTRAL_API_KEY n'a pas le préfixe AOP_ -> champ dédié
    mistral_api_key: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.database_url:
            self.database_url = f"sqlite:///{self.workspace_dir / 'aop.db'}"
        self.workspace_dir = Path(self.workspace_dir).resolve()


class MistralApiKeySettings(BaseSettings):
    """Chargé séparément car la variable n'a pas le préfixe AOP_."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        extra="ignore",
    )
    mistral_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    key_settings = MistralApiKeySettings()
    if key_settings.mistral_api_key:
        settings.mistral_api_key = key_settings.mistral_api_key
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache
def get_models_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "models.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config_dir() -> Path:
    return CONFIG_DIR
