"""WebSocket de suivi de progression live pour un dossier (§3, §8)."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.progress import progress_manager

router = APIRouter()


@router.websocket("/ws/dossiers/{dossier_id}")
async def dossier_progress_ws(websocket: WebSocket, dossier_id: str) -> None:
    await progress_manager.connect(dossier_id, websocket)
    try:
        while True:
            # Le client n'a rien à envoyer ; on attend juste la déconnexion.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await progress_manager.disconnect(dossier_id, websocket)
