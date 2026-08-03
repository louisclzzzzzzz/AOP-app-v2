"""Point d'entrée de l'exécutable Windows empaqueté (PyInstaller) : lance le serveur en
local et ouvre le navigateur. Non utilisé en développement (voir start.sh / `uv run uvicorn`).

Appelle `uvicorn.run(app, ...)` avec l'objet `app` importé directement plutôt qu'une chaîne
"app.main:app" : cela évite le rechargement/spawn de sous-processus d'uvicorn (reload,
workers>1), un piège classique avec PyInstaller où l'exécutable frozen se relance en boucle.
"""
from __future__ import annotations

import multiprocessing
import sys
import threading
import time
import webbrowser

import uvicorn

from app.main import app
from app.settings import get_settings


def _open_browser(url: str) -> None:
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass  # poste sans navigateur par défaut configuré (ex. runner CI) : non bloquant


def main() -> None:
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.backend_port}"

    if not settings.mistral_api_key:
        print(
            "\n"
            "ATTENTION : aucune cle MISTRAL_API_KEY configuree.\n"
            "Creez un fichier .env a cote de cet executable (copiez .env.example) et\n"
            "renseignez MISTRAL_API_KEY avant d'utiliser l'OCR ou la classification IA.\n",
            file=sys.stderr,
        )

    print(f"-> AOP v2 demarre sur {url} (fermez cette fenetre pour arreter le serveur)")
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=settings.backend_port, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
