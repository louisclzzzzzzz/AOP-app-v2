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


_HISTORY_LIMIT = 200


class ProgressManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        # Historique borné des derniers évènements par dossier, pour rattraper un client qui se
        # connecte tard (ex. pipeline auto-enchaîné déjà bien avancé au moment où l'écran de
        # progression s'ouvre) — sans quoi il ne recevait que le tout dernier évènement et voyait
        # la barre de progression sauter directement à sa valeur finale.
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def connect(self, dossier_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[dossier_id].add(websocket)
            history = list(self._history.get(dossier_id, ()))
        for event in history:
            await websocket.send_json(event)

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
        """Purge tout état résiduel d'un dossier (connexions + historique) — à appeler à la
        suppression du dossier, sans quoi `_history` grossit indéfiniment (AUDIT_BACKEND.md §5)."""
        async with self._lock:
            self._connections.pop(dossier_id, None)
            self._history.pop(dossier_id, None)

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
        async with self._lock:
            history = self._history[dossier_id]
            history.append(event)
            del history[:-_HISTORY_LIMIT]
            targets = list(self._connections.get(dossier_id, ()))
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                await self.disconnect(dossier_id, ws)


progress_manager = ProgressManager()
