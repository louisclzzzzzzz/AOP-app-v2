"""Vérification du code d'accès + cookie de session signé (itsdangerous).

Pas de compte email/mot de passe : un code à 4 chiffres par personne (§app/settings.py
Settings.resolved_access_codes), donné à la main — ce qui permet de révoquer un accès
individuellement sans toucher aux codes des autres (§app/api/auth.py, app/auth/rate_limit.py
pour la protection anti-brute-force).

Session par cookie signé plutôt que table de sessions côté serveur : plus simple (pas de
nettoyage d'expiration à gérer), et le cookie est envoyé automatiquement par le navigateur
sur les requêtes HTTP *et* sur la poignée de main WebSocket (même origine), donc un seul
mécanisme couvre l'API REST et `/ws/dossiers/{id}` (§app/auth/dependencies.py).

Le cookie porte désormais un identifiant STABLE de la personne connectée (hash du code, jamais
le code en clair) — nécessaire depuis que chaque utilisateur a sa propre clé API Mistral
personnelle (§app/api/me.py, app/store/models.py UserApiKey) : il faut savoir QUI est connecté
pour retrouver SA clé, alors qu'avant la session n'était qu'un booléen anonyme."""
from __future__ import annotations

import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "aop_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 jours


def verify_access_code(candidate: str, valid_codes: list[str]) -> bool:
    """Comparaison à temps constant (par code) : une comparaison `==` naïve sur une chaîne
    aussi courte (4 chiffres) fuiterait un signal exploitable par mesure de timing. On
    n'interrompt jamais la boucle au premier essai — comparer TOUS les codes à chaque appel
    évite de fuiter, par le temps de réponse global, la position du code dans la liste."""
    matched = False
    for code in valid_codes:
        if secrets.compare_digest(candidate, code):
            matched = True
    return matched


def hash_access_code(code: str) -> str:
    """Identifiant stable et non-réversible d'une personne, dérivé de son code d'accès —
    utilisé comme clé primaire pour tout ce qui est propre à cette personne (clé API Mistral,
    compteur d'usage). Un hash plutôt que le code en clair dans le cookie/la base : défense en
    profondeur (le cookie est déjà httponly+secure, mais ne coûte rien à ne pas transporter le
    secret de connexion lui-même) — sans conséquence sur la sécurité du code, qui reste protégé
    à la connexion par le verrouillage anti-brute-force (app/auth/rate_limit.py), pas par ce
    hash (10 000 combinaisons se devinent trivialement hors-ligne)."""
    return hashlib.sha256(code.encode()).hexdigest()


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="aop-session")


def create_session_cookie(*, secret_key: str, user_id: str) -> str:
    return _serializer(secret_key).dumps({"authenticated": True, "uid": user_id})


def decode_session_cookie(token: str, *, secret_key: str) -> str | None:
    """Identifiant de la personne connectée si le cookie est valide, signé avec la bonne clé et
    non expiré ; None sinon — jamais d'exception : un cookie absent/altéré/expiré doit juste
    être traité comme non connecté."""
    try:
        data = _serializer(secret_key).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("authenticated") is not True:
        return None
    user_id = data.get("uid")
    return user_id if isinstance(user_id, str) else None
