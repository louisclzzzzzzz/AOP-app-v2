"""Point d'entrée FastAPI : API REST + WebSocket de suivi de progression."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.classification import router as classification_router
from app.api.classification import taxonomy_router
from app.api.completeness import pieces_checklist_router
from app.api.completeness import router as completeness_router
from app.api.dossiers import router as dossiers_router
from app.api.extraction import extraction_schema_router
from app.api.extraction import router as extraction_router
from app.api.me import router as me_router
from app.api.project_synthesis import router as project_synthesis_router
from app.api.websocket import router as websocket_router
from app.auth.dependencies import require_auth, require_auth_ws
from app.mistral.client import api_slots_health
from app.settings import get_bundle_dir, get_settings
from app.store.db import init_db

logging.basicConfig(level=logging.INFO)

_bundle_dir = get_bundle_dir()
FRONTEND_DIST = (
    (_bundle_dir / "frontend" / "dist")
    if _bundle_dir is not None
    else Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AOP v2", lifespan=lifespan)

# Derrière un proxy (Railway, Fly) : réécrit request.client à partir de X-Forwarded-For, sinon
# toutes les requêtes semblent venir du proxy — casserait le verrouillage anti-brute-force par
# IP sur /api/auth/login (app/auth/rate_limit.py). trusted_hosts="*" est sûr ici : le conteneur
# ne reçoit jamais de trafic direct depuis l'internet public, uniquement via ce proxy.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Dérivé de settings.frontend_port (AOP_FRONTEND_PORT) plutôt que codé en dur : sinon changer
# le port frontend via l'env casse silencieusement le CORS (AUDIT_BACKEND.md §7).
_frontend_port = get_settings().frontend_port
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_frontend_port}",
        f"http://127.0.0.1:{_frontend_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
# Toujours monté, protégé par require_auth EN INTERNE (§app/api/me.py) plutôt que par
# `dependencies=_auth` ci-dessous : ces routes n'ont de sens que pour un utilisateur identifié,
# indépendamment du réglage global AOP_REQUIRE_AUTH.
app.include_router(me_router)

# Off par défaut (AOP_REQUIRE_AUTH) : usage local (./start.sh, l'exécutable Windows) reste
# mono-utilisateur sans compte à créer, comme avant. Activé pour un déploiement public exposé
# à quiconque a le lien (ex. Railway) — seuls /api/auth/* et /api/health restent alors publics.
_auth = [Depends(require_auth)] if get_settings().require_auth else []
_auth_ws = [Depends(require_auth_ws)] if get_settings().require_auth else []
app.include_router(dossiers_router, dependencies=_auth)
app.include_router(classification_router, dependencies=_auth)
app.include_router(taxonomy_router, dependencies=_auth)
app.include_router(completeness_router, dependencies=_auth)
app.include_router(pieces_checklist_router, dependencies=_auth)
app.include_router(extraction_router, dependencies=_auth)
app.include_router(extraction_schema_router, dependencies=_auth)
app.include_router(project_synthesis_router, dependencies=_auth)
app.include_router(audit_router, dependencies=_auth)
app.include_router(websocket_router, dependencies=_auth_ws)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """État du service, et état des clés API.

    `api_keys` rend visible une bascule sur une clé de secours : sans ça, l'épuisement de la clé
    principale ne se lit que dans les logs du serveur. Ne contient jamais les clés elles-mêmes,
    seulement leur rang et leur disponibilité."""
    return {"status": "ok", "api_keys": api_slots_health()}


# En production (build unique), le frontend compilé est servi directement par le backend
# pour respecter la contrainte "lancement en une commande" (§2 du PLAN).
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
