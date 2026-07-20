"""Validation partagée des sorties structurées LLM (§9 AUDIT_BACKEND.md).

Un LLM peut renvoyer une confiance hors de [0, 1] (ex. 95 au lieu de 0.95) : rien dans les 3
moteurs (classification, complétude, extraction) ne la bornait jusqu'ici. On la borne plutôt
que de rejeter la réponse, pour ne pas faire échouer tout un appel — potentiellement batché sur
plusieurs documents — à cause d'un seul champ mal formé.
"""
from __future__ import annotations

from pydantic import field_validator


def clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def confidence_validator(field_name: str = "confidence") -> classmethod:
    """Validateur Pydantic (mode 'after') bornant `field_name` à [0, 1] — à passer à
    `create_model(__validators__={...})` ou à utiliser directement comme décorateur sur un
    `BaseModel` statique."""
    return field_validator(field_name, mode="after")(classmethod(lambda cls, v: clamp_confidence(v)))
