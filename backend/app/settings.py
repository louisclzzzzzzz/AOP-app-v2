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
    # Toutes les clés utilisables, dans l'ordre de déclaration — `mistral_api_key` en est la
    # première. Une seule clé = comportement historique inchangé (voir MistralApiKeySettings).
    mistral_api_keys: list[str] = []

    def model_post_init(self, __context: Any) -> None:
        if not self.database_url:
            self.database_url = f"sqlite:///{self.workspace_dir / 'aop.db'}"
        self.workspace_dir = Path(self.workspace_dir).resolve()


class MistralApiKeySettings(BaseSettings):
    """Chargé séparément car les variables n'ont pas le préfixe AOP_.

    Plusieurs clés peuvent être déclarées : la PREMIÈRE est la clé principale et prend tous les
    appels, les suivantes sont des clés de SECOURS. Une clé de secours n'est sollicitée que si
    celles qui la précèdent ne fonctionnent plus (quota épuisé, clé révoquée, rate limit) —
    il n'y a pas de répartition de charge entre les clés (§app/mistral/client.py).

    Deux écritures possibles dans `.env`, la première qui donne un résultat l'emporte :
      MISTRAL_API_KEYS=cle_principale,cle_secours  (forme générale, nombre libre)
      MISTRAL_API_KEY=cle_principale               (forme historique)
      MISTRAL_API_KEY_2=cle_secours                + clés de secours numérotées
      MISTRAL_API_KEY_3=cle_secours_2
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        extra="ignore",
    )
    mistral_api_key: str = ""
    mistral_api_key_2: str = ""
    mistral_api_key_3: str = ""
    mistral_api_key_4: str = ""
    mistral_api_key_5: str = ""
    mistral_api_keys: str = ""

    def resolved_keys(self) -> list[str]:
        """Clés retenues, dans l'ordre de priorité, sans doublon ni valeur vide.

        La déduplication n'est pas cosmétique : une clé de secours identique à la principale
        partage le même quota, donc elle ne servirait de secours à rien — autant ne pas la
        compter et laisser l'échec être visible."""
        if self.mistral_api_keys.strip():
            candidates = self.mistral_api_keys.split(",")
        else:
            candidates = [
                self.mistral_api_key,
                self.mistral_api_key_2,
                self.mistral_api_key_3,
                self.mistral_api_key_4,
                self.mistral_api_key_5,
            ]
        seen: set[str] = set()
        keys: list[str] = []
        for candidate in candidates:
            key = candidate.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    keys = MistralApiKeySettings().resolved_keys()
    if keys:
        settings.mistral_api_keys = keys
        settings.mistral_api_key = keys[0]
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    return settings


@lru_cache
def get_models_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "models.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config_dir() -> Path:
    return CONFIG_DIR
