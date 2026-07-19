"""Point d'entrée FastAPI : API REST + WebSocket de suivi de progression."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.classification import router as classification_router
from app.api.classification import taxonomy_router
from app.api.completeness import pieces_checklist_router
from app.api.completeness import router as completeness_router
from app.api.dossiers import router as dossiers_router
from app.api.extraction import extraction_schema_router
from app.api.extraction import router as extraction_router
from app.api.websocket import router as websocket_router
from app.settings import get_settings
from app.store.db import init_db

logging.basicConfig(level=logging.INFO)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AOP v2", lifespan=lifespan)

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

app.include_router(dossiers_router)
app.include_router(classification_router)
app.include_router(taxonomy_router)
app.include_router(completeness_router)
app.include_router(pieces_checklist_router)
app.include_router(extraction_router)
app.include_router(extraction_schema_router)
app.include_router(websocket_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# En production (build unique), le frontend compilé est servi directement par le backend
# pour respecter la contrainte "lancement en une commande" (§2 du PLAN).
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
