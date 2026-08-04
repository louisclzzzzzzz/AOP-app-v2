"""Verrouillage anti-brute-force par IP sur /api/auth/login.

Un code à 4 chiffres n'a que 10 000 combinaisons : sans ce garde-fou, un script épuiserait
l'espace en quelques secondes. En mémoire (pas de table dédiée) : cohérent avec le reste de
l'app, mono-instance (§app/mistral/client.py fait déjà ce choix pour l'état des clés API), et
un redémarrage remet compteurs à zéro — sans conséquence pour un verrou anti-abus."""
from __future__ import annotations

import threading
import time

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

_lock = threading.Lock()
# IP -> (échecs consécutifs, instant monotone jusqu'auquel verrouillé — 0.0 si non verrouillé)
_state: dict[str, tuple[int, float]] = {}


def seconds_locked(ip: str) -> float:
    """0.0 si l'IP peut retenter maintenant, sinon le nombre de secondes restantes."""
    with _lock:
        _, locked_until = _state.get(ip, (0, 0.0))
    return max(0.0, locked_until - time.monotonic())


def record_failure(ip: str) -> None:
    with _lock:
        failures, _ = _state.get(ip, (0, 0.0))
        failures += 1
        locked_until = time.monotonic() + LOCKOUT_SECONDS if failures >= MAX_ATTEMPTS else 0.0
        _state[ip] = (failures, locked_until)


def record_success(ip: str) -> None:
    with _lock:
        _state.pop(ip, None)


def reset_for_tests() -> None:
    with _lock:
        _state.clear()
