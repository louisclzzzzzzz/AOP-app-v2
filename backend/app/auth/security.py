"""Vérification du code d'accès partagé + cookie de session signé (itsdangerous).

Pas de compte individuel : un seul code à 4 chiffres, donné à la main aux personnes
autorisées (§app/api/auth.py, app/auth/rate_limit.py pour la protection anti-brute-force).

Session par cookie signé plutôt que table de sessions côté serveur : plus simple (pas de
nettoyage d'expiration à gérer), et le cookie est envoyé automatiquement par le navigateur
sur les requêtes HTTP *et* sur la poignée de main WebSocket (même origine), donc un seul
mécanisme couvre l'API REST et `/ws/dossiers/{id}` (§app/auth/dependencies.py)."""
from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "aop_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 jours


def verify_access_code(candidate: str, expected: str) -> bool:
    """Comparaison à temps constant : une comparaison `==` naïve sur une chaîne aussi courte
    (4 chiffres) fuiterait un signal exploitable par mesure de timing."""
    return bool(expected) and secrets.compare_digest(candidate, expected)


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="aop-session")


def create_session_cookie(*, secret_key: str) -> str:
    return _serializer(secret_key).dumps({"authenticated": True})


def verify_session_cookie(token: str, *, secret_key: str) -> bool:
    """True si le cookie est valide, signé avec la bonne clé et non expiré — jamais
    d'exception : un cookie absent/altéré/expiré doit juste être traité comme non connecté."""
    try:
        data = _serializer(secret_key).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("authenticated") is True
