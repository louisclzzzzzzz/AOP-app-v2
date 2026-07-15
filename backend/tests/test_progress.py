from __future__ import annotations

import pytest

from app.progress import ProgressManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        if self.closed:
            raise RuntimeError("socket fermé")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_broadcast_reaches_connected_clients_for_the_right_dossier():
    manager = ProgressManager()
    ws_a = _FakeWebSocket()
    ws_b = _FakeWebSocket()
    await manager.connect("dossier-a", ws_a)
    await manager.connect("dossier-b", ws_b)

    await manager.broadcast("dossier-a", stage="unzip", status="unzipping", message="go")

    assert len(ws_a.sent) == 1
    assert ws_a.sent[0]["stage"] == "unzip"
    assert ws_b.sent == []  # pas concerné par ce dossier


@pytest.mark.asyncio
async def test_late_connect_receives_last_known_event():
    manager = ProgressManager()
    await manager.broadcast("dossier-a", stage="unzip", status="unzipping", message="go")

    ws_late = _FakeWebSocket()
    await manager.connect("dossier-a", ws_late)

    assert len(ws_late.sent) == 1
    assert ws_late.sent[0]["stage"] == "unzip"


@pytest.mark.asyncio
async def test_broadcast_drops_dead_connections_silently():
    manager = ProgressManager()
    ws = _FakeWebSocket()
    await manager.connect("dossier-a", ws)
    ws.closed = True

    await manager.broadcast("dossier-a", stage="unzip", status="unzipping")

    async with manager._lock:
        assert ws not in manager._connections["dossier-a"]
