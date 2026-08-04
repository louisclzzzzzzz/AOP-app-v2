"""Dépendances FastAPI protégeant les routes HTTP et WebSocket derrière une session valide.

Deux variantes (HTTP vs WebSocket) car `Request` et `WebSocket` sont des types distincts
dans Starlette — mais les deux lisent le même cookie de session (même origine, envoyé
automatiquement par le navigateur sur la poignée de main WebSocket comme sur `fetch`)."""
from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status

from app.auth.security import COOKIE_NAME, verify_session_cookie
from app.settings import get_settings


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token is None or not verify_session_cookie(token, secret_key=get_settings().secret_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")


async def require_auth_ws(websocket: WebSocket) -> None:
    token = websocket.cookies.get(COOKIE_NAME)
    if token is None or not verify_session_cookie(token, secret_key=get_settings().secret_key):
        # Levée AVANT websocket.accept() : FastAPI referme la poignée de main proprement
        # (code 1008) plutôt que d'accepter puis fermer, ce qui perturberait des clients qui
        # considèrent la connexion établie dès l'accept.
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
