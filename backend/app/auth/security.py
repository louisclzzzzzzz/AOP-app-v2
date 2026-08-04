"""Hachage de mot de passe (bcrypt) + cookie de session signé (itsdangerous).

Session par cookie signé plutôt que table de sessions côté serveur : plus simple (pas de
nettoyage d'expiration à gérer), et le cookie est envoyé automatiquement par le navigateur
sur les requêtes HTTP *et* sur la poignée de main WebSocket (même origine), donc un seul
mécanisme couvre l'API REST et `/ws/dossiers/{id}` (§app/auth/dependencies.py)."""
from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "aop_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 jours

# bcrypt ignore silencieusement tout au-delà de 72 octets (limite de l'algorithme) — on
# tronque explicitement plutôt que de laisser un mot de passe long donner une fausse
# impression de sécurité au-delà de ce qui est réellement pris en compte.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(truncated, password_hash.encode("ascii"))
    except ValueError:
        return False  # hash stocké malformé/vide : jamais une exception qui casse le login


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="aop-session")


def create_session_cookie(user_id: str, *, secret_key: str) -> str:
    return _serializer(secret_key).dumps({"user_id": user_id})


def verify_session_cookie(token: str, *, secret_key: str) -> str | None:
    """Retourne le user_id si le cookie est valide et non expiré, sinon None (jamais
    d'exception : un cookie absent/altéré/expiré doit juste être traité comme non connecté)."""
    try:
        data = _serializer(secret_key).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    user_id = data.get("user_id")
    return user_id if isinstance(user_id, str) else None
