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
async def test_late_connect_replays_the_full_history_not_just_the_last_event():
    """Un client qui se connecte alors que le pipeline a déjà bien avancé doit recevoir toute
    la progression passée, pas seulement l'instantané final — sans quoi la barre de progression
    semble sauter directement de 0 à 100 %."""
    manager = ProgressManager()
    await manager.broadcast("dossier-a", stage="unzip", status="unzipping", message="1")
    await manager.broadcast("dossier-a", stage="inventory", status="inventorying", message="2")
    await manager.broadcast("dossier-a", stage="classify", status="classifying", message="3")

    ws_late = _FakeWebSocket()
    await manager.connect("dossier-a", ws_late)

    assert [e["message"] for e in ws_late.sent] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_history_is_bounded_per_dossier():
    manager = ProgressManager()
    for i in range(250):
        await manager.broadcast("dossier-a", stage="classify", status="classifying", message=str(i))

    ws_late = _FakeWebSocket()
    await manager.connect("dossier-a", ws_late)

    assert len(ws_late.sent) == 200
    assert ws_late.sent[0]["message"] == "50"
    assert ws_late.sent[-1]["message"] == "249"


@pytest.mark.asyncio
async def test_forget_clears_history_so_a_new_dossier_reusing_the_id_starts_clean():
    manager = ProgressManager()
    await manager.broadcast("dossier-a", stage="unzip", status="unzipping", message="go")

    await manager.forget("dossier-a")

    ws_late = _FakeWebSocket()
    await manager.connect("dossier-a", ws_late)
    assert ws_late.sent == []


@pytest.mark.asyncio
async def test_broadcast_drops_dead_connections_silently():
    manager = ProgressManager()
    ws = _FakeWebSocket()
    await manager.connect("dossier-a", ws)
    ws.closed = True

    await manager.broadcast("dossier-a", stage="unzip", status="unzipping")

    async with manager._lock:
        assert ws not in manager._connections["dossier-a"]
