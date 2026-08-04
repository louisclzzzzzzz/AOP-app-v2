"""Gestion de la clé API Mistral personnelle (§AOP_REQUIRE_AUTH) : sur un déploiement public,
chaque utilisateur apporte la sienne plutôt que de consommer le quota d'une clé partagée par
tout le monde (§app/pipeline_support.py `owner_api_key`) — voir le guide (« Clé API » dans le
menu du frontend) pour l'obtenir.

Toujours protégé par `require_auth`, indépendamment du réglage global AOP_REQUIRE_AUTH
(§app/main.py) : ces routes n'ont de sens que pour un utilisateur identifié — en usage local/
exécutable Windows (jamais de session), elles restent montées mais renvoient 401 sans jamais
être appelées par le frontend (§frontend/src/App.tsx, gated sur `hasSession`)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from mistralai.client import Mistral
from pydantic import BaseModel

from app.auth.crypto import decrypt_secret, encrypt_secret
from app.auth.dependencies import get_current_user_id, require_auth
from app.settings import get_settings
from app.store.db import session_scope
from app.store.repository import clear_user_api_key, get_user_api_key_row, save_user_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"], dependencies=[Depends(require_auth)])


def _uid(request: Request) -> str:
    user_id = get_current_user_id(request)
    assert user_id is not None  # garanti par Depends(require_auth) au niveau du router
    return user_id


def _mask(raw_key: str) -> str:
    """Ne rend jamais la clé complète — juste de quoi la reconnaître (ex. confirmer qu'on a
    bien collé la bonne après une rotation), comme les derniers chiffres d'une carte bancaire."""
    if len(raw_key) <= 8:
        return "•" * len(raw_key)
    return f"{raw_key[:4]}{'•' * 6}{raw_key[-4:]}"


class ApiKeyIn(BaseModel):
    api_key: str


class ApiKeyStatusOut(BaseModel):
    configured: bool
    masked: str | None = None


class UsageOut(BaseModel):
    period: str
    requests_count: int


@router.get("/mistral-key", response_model=ApiKeyStatusOut)
async def get_mistral_key_status(request: Request) -> ApiKeyStatusOut:
    user_id = _uid(request)
    settings = get_settings()
    with session_scope() as s:
        row = get_user_api_key_row(s, user_id)
        encrypted = row.mistral_api_key_encrypted if row else None
    if not encrypted:
        return ApiKeyStatusOut(configured=False)
    raw = decrypt_secret(encrypted, secret_key=settings.secret_key)
    if raw is None:
        return ApiKeyStatusOut(configured=False)
    return ApiKeyStatusOut(configured=True, masked=_mask(raw))


@router.put("/mistral-key", response_model=ApiKeyStatusOut)
async def set_mistral_key(payload: ApiKeyIn, request: Request) -> ApiKeyStatusOut:
    user_id = _uid(request)
    raw_key = payload.api_key.strip()
    if len(raw_key) < 20:
        raise HTTPException(400, "Clé trop courte — vérifiez que vous avez copié la clé complète.")

    def _check() -> None:
        try:
            Mistral(api_key=raw_key).models.list()
        except Exception as exc:
            raise HTTPException(
                400, "Clé refusée par Mistral — vérifiez qu'elle est correcte et active."
            ) from exc

    await asyncio.to_thread(_check)

    settings = get_settings()
    encrypted = encrypt_secret(raw_key, secret_key=settings.secret_key)
    with session_scope() as s:
        save_user_api_key(s, user_id, encrypted)

    return ApiKeyStatusOut(configured=True, masked=_mask(raw_key))


@router.delete("/mistral-key", status_code=204)
async def delete_mistral_key(request: Request) -> None:
    user_id = _uid(request)
    with session_scope() as s:
        clear_user_api_key(s, user_id)


@router.get("/usage", response_model=UsageOut)
async def get_usage(request: Request) -> UsageOut:
    user_id = _uid(request)
    with session_scope() as s:
        row = get_user_api_key_row(s, user_id)
    current_period = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    if row is None or row.usage_period != current_period:
        return UsageOut(period=current_period, requests_count=0)
    return UsageOut(period=row.usage_period, requests_count=row.usage_count)
