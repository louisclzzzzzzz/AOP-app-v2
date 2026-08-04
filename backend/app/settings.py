"""Configuration de l'application : variables d'environnement (.env) + fichiers YAML de config/."""
from __future__ import annotations

import secrets
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Exécutable empaqueté (PyInstaller) : le bootloader pose sys.frozen et sys._MEIPASS pointe
# vers le dossier d'extraction des ressources en lecture seule (temporaire en mode --onefile,
# effacé à la fermeture). Les données persistantes (workspace/, .env) doivent au contraire
# vivre à côté de l'exécutable lui-même, jamais dans ce dossier temporaire.
_FROZEN_BUNDLE_DIR = Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False) else None

PROJECT_ROOT = Path(sys.executable).resolve().parent if _FROZEN_BUNDLE_DIR is not None else BACKEND_DIR.parent
CONFIG_DIR = (_FROZEN_BUNDLE_DIR / "config") if _FROZEN_BUNDLE_DIR is not None else BACKEND_DIR / "config"


def get_bundle_dir() -> Path | None:
    """Dossier des ressources en lecture seule embarquées (config, frontend/dist) si l'app
    tourne comme exécutable empaqueté, None en exécution normale (uv run)."""
    return _FROZEN_BUNDLE_DIR


def _win_long_path(path: Path) -> Path:
    """Windows refuse mkdir/open au-delà de MAX_PATH (260 caractères) sauf préfixe étendu
    `\\\\?\\`, qui fonctionne sans droits admin ni réglage système (contrairement à l'opt-in
    `LongPathsEnabled` sinon nécessaire) — indispensable ici : un DCE réel a une arborescence
    et des noms de fichiers longs (traçabilité = copie fidèle de la source, jamais tronquée),
    qui dépassent vite la limite une fois nichés sous workspace/<id>/.source.staging-xxxxxxxx/…
    (cas réel rencontré au premier test Windows : dépassement de quelques caractères
    seulement). No-op hors Windows, où cette limite n'existe pas."""
    if sys.platform != "win32":
        return path
    s = str(path)
    if s.startswith("\\\\?\\"):
        return path
    if s.startswith("\\\\"):  # chemin UNC \\serveur\partage\...
        return Path("\\\\?\\UNC\\" + s[2:])
    return Path("\\\\?\\" + s)


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

    # Signe les cookies de session (app/auth/security.py). Doit être fixé explicitement (et
    # stable) en production — sinon (dev local) une valeur aléatoire est générée à chaque
    # démarrage : sans conséquence, juste des sessions invalidées à chaque redémarrage.
    secret_key: str = ""
    # Off par défaut : usage local (./start.sh, l'exécutable Windows empaqueté) reste
    # mono-utilisateur sans friction, comme avant — pas de code à saisir pour un testeur qui
    # lance juste AOP-v2.exe sur son poste. À activer explicitement (AOP_REQUIRE_AUTH=true)
    # pour un déploiement public exposé à quiconque a le lien (ex. Railway).
    require_auth: bool = False
    # Un code à 4 chiffres par personne (pas de compte email/mot de passe) — chacun a le
    # sien, ce qui permet de révoquer un accès individuellement (retirer son code de la
    # liste) sans devoir faire tourner le code de tout le monde. N'a de sens que si
    # require_auth=True. Brut (chaîne "1111,2222,3333") plutôt que list[str] : pydantic-settings
    # attend du JSON pour parser une liste depuis l'env, pas une liste séparée par virgules —
    # même choix que mistral_api_keys ci-dessus (MistralApiKeySettings.resolved_keys()).
    access_codes: str = ""

    def resolved_access_codes(self) -> list[str]:
        return [c.strip() for c in self.access_codes.split(",") if c.strip()]

    def model_post_init(self, __context: Any) -> None:
        resolved = Path(self.workspace_dir).resolve()
        if not self.database_url:
            # DSN construit sur le chemin résolu SANS préfixe long-path : le "?" de `\\?\`
            # casserait le parsing d'URL SQLAlchemy (tout ce qui suit deviendrait une
            # querystring). Sans risque pour MAX_PATH : aop.db reste à la racine de
            # workspace/, jamais nichée sous la profondeur arbitraire d'un DCE.
            self.database_url = f"sqlite:///{resolved / 'aop.db'}"
        self.workspace_dir = _win_long_path(resolved)
        if not self.secret_key:
            self.secret_key = secrets.token_urlsafe(32)


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
