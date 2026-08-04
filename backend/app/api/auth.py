"""Connexion / déconnexion par cookie de session signé. Pas d'auto-inscription : les comptes
sont créés par un administrateur (voir backend/scripts/create_user.py) — l'app reste réservée
à l'équipe, jamais ouverte à qui obtient juste le lien."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.auth.dependencies import AuthenticatedUser, require_auth
from app.auth.repository import get_user_by_email
from app.auth.security import COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_cookie, verify_password
from app.settings import get_settings
from app.store.db import session_scope

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    email: str


@router.post("/login", response_model=UserOut)
async def login(payload: LoginRequest, response: Response) -> UserOut:
    with session_scope() as s:
        user = get_user_by_email(s, payload.email)
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email ou mot de passe incorrect")
        token = create_session_cookie(user.id, secret_key=get_settings().secret_key)
        email = user.email

    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return UserOut(email=email)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: AuthenticatedUser = Depends(require_auth)) -> UserOut:
    return UserOut(email=user.email)
