"""Connexion / déconnexion par code d'accès (4 chiffres) + cookie de session signé.

Un code par personne (configuré via AOP_ACCESS_CODES, jamais en dur) plutôt qu'un compte
email/mot de passe — révocable individuellement sans toucher aux codes des autres.
Verrouillage anti-brute-force par IP (app/auth/rate_limit.py) — indispensable : 4 chiffres
n'offrent que 10 000 combinaisons."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.auth import rate_limit
from app.auth.dependencies import require_auth
from app.auth.security import COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_cookie, verify_access_code
from app.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    code: str


def _client_ip(request: Request) -> str:
    # Derrière le proxy Railway, ProxyHeadersMiddleware (app/main.py) réécrit request.client
    # à partir de X-Forwarded-For : c'est déjà la vraie IP visiteur, pas celle du proxy.
    return request.client.host if request.client else "unknown"


@router.post("/login", status_code=204)
async def login(payload: LoginRequest, request: Request, response: Response) -> None:
    ip = _client_ip(request)
    locked_for = rate_limit.seconds_locked(ip)
    if locked_for > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Trop de tentatives — réessayez dans {int(locked_for // 60) + 1} min",
        )

    settings = get_settings()
    if not verify_access_code(payload.code, settings.resolved_access_codes()):
        rate_limit.record_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code incorrect")

    rate_limit.record_success(ip)
    token = create_session_cookie(secret_key=settings.secret_key)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", status_code=204, dependencies=[Depends(require_auth)])
async def me() -> None:
    """Renvoie 204 si la session est valide, 401 sinon — utilisé par le frontend pour savoir
    s'il doit afficher le bouton de déconnexion (pas de statut à autre chose à retourner : pas
    de compte individuel, donc pas d'identité à refléter)."""
