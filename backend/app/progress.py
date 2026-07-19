"""Suivi de progression live (WebSocket) : global + par document.

Le pipeline d'ingestion tourne dans une tâche asyncio et diffuse un évènement JSON
après chaque étape significative (dézip, inventaire, extraction texte par document).
Chaque écran connecté au WS d'un dossier reçoit tous les évènements en temps réel.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ProgressManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        # Dernier évènement connu par dossier, pour rattraper un client qui se connecte tard.
        self._last_event: dict[str, dict[str, Any]] = {}

    async def connect(self, dossier_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[dossier_id].add(websocket)
        last = self._last_event.get(dossier_id)
        if last is not None:
            await websocket.send_json(last)

    async def disconnect(self, dossier_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            remaining = self._connections.get(dossier_id)
            if remaining is None:
                return
            remaining.discard(websocket)
            if not remaining:
                # Ne pas laisser une entrée vide s'accumuler indéfiniment dans le dict
                # (AUDIT_BACKEND.md §5) : un process longue durée avec beaucoup de dossiers
                # créés/consultés finirait par accumuler une clé par dossier pour toujours.
                del self._connections[dossier_id]

    async def forget(self, dossier_id: str) -> None:
        """Purge tout état résiduel d'un dossier (connexions + dernier évènement) — à appeler
        à la suppression du dossier, sans quoi `_last_event` grossit indéfiniment
        (AUDIT_BACKEND.md §5)."""
        async with self._lock:
            self._connections.pop(dossier_id, None)
            self._last_event.pop(dossier_id, None)

    async def broadcast(
        self,
        dossier_id: str,
        *,
        stage: str,
        status: str,
        counters: dict[str, int] | None = None,
        document: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        event = {
            "dossier_id": dossier_id,
            "stage": stage,
            "status": status,
            "counters": counters or {},
            "document": document,
            "message": message,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._last_event[dossier_id] = event
        async with self._lock:
            targets = list(self._connections.get(dossier_id, ()))
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                await self.disconnect(dossier_id, ws)


progress_manager = ProgressManager()
