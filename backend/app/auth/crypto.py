"""Chiffrement au repos des secrets utilisateur (clé API Mistral personnelle, §app/api/me.py).

Dérive la clé Fernet de `AOP_SECRET_KEY` (déjà requis, stable en production, cf.
app/settings.py) plutôt que d'introduire un second secret à configurer séparément — un seul
`AOP_SECRET_KEY` perdu invalide à la fois les sessions ET les clés stockées, ce qui est le
comportement souhaité (une base compromise sans le secret ne révèle rien)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret_key: str) -> Fernet:
    # Fernet exige une clé de 32 octets encodée en base64 urlsafe — dérivée par hash plutôt que
    # d'exiger que AOP_SECRET_KEY fasse elle-même 32 octets (elle sert déjà à autre chose,
    # itsdangerous n'a aucune contrainte de longueur).
    digest = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, *, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(value.encode()).decode()


def decrypt_secret(token: str, *, secret_key: str) -> str | None:
    """None si le token est corrompu ou signé avec un autre secret — jamais d'exception : un
    secret indéchiffrable doit être traité comme absent, pas faire planter l'appelant."""
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken:
        return None
