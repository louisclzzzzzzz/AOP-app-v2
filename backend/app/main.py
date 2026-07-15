"""Point d'entrée FastAPI : API REST + WebSocket de suivi de progression."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.dossiers import router as dossiers_router
from app.api.websocket import router as websocket_router
from app.store.db import init_db

logging.basicConfig(level=logging.INFO)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AOP v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dossiers_router)
app.include_router(websocket_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# En production (build unique), le frontend compilé est servi directement par le backend
# pour respecter la contrainte "lancement en une commande" (§2 du PLAN).
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
