"""Dépendances FastAPI protégeant les routes HTTP et WebSocket derrière une session valide.

Deux variantes (HTTP vs WebSocket) car `Request` et `WebSocket` sont des types distincts
dans Starlette — mais les deux lisent le même cookie de session (même origine, envoyé
automatiquement par le navigateur sur la poignée de main WebSocket comme sur `fetch`)."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status

from app.auth.repository import get_user_by_id
from app.auth.security import COOKIE_NAME, verify_session_cookie
from app.settings import get_settings
from app.store.db import session_scope


@dataclass(frozen=True)
class AuthenticatedUser:
    """Copie légère (pas l'ORM `User`) : évite tout accès à un objet détaché une fois la
    session SQLAlchemy de la dépendance refermée."""

    id: str
    email: str


def _resolve_user(token: str | None) -> AuthenticatedUser | None:
    if not token:
        return None
    user_id = verify_session_cookie(token, secret_key=get_settings().secret_key)
    if user_id is None:
        return None
    with session_scope() as s:
        user = get_user_by_id(s, user_id)
        if user is None:
            return None  # compte supprimé depuis : la session existante ne doit plus valoir
        return AuthenticatedUser(id=user.id, email=user.email)


def require_auth(request: Request) -> AuthenticatedUser:
    user = _resolve_user(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")
    return user


async def require_auth_ws(websocket: WebSocket) -> AuthenticatedUser:
    user = _resolve_user(websocket.cookies.get(COOKIE_NAME))
    if user is None:
        # Levée AVANT websocket.accept() : FastAPI referme la poignée de main proprement
        # (code 1008) plutôt que d'accepter puis fermer, ce qui perturberait des clients qui
        # considèrent la connexion établie dès l'accept.
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return user
