"""Métadonnées enrichies bon marché (§3.5) : titre détecté et mentions clés, calculés par
heuristique regex sur le texte déjà extrait — pas d'appel LLM ici (réservé aux étapes 1-3
où un jugement est réellement nécessaire)."""
from __future__ import annotations

import re

_KEY_MENTION_PATTERNS: dict[str, str] = {
    "lot": r"\blot\s*n?°?\s*(\d+)\b",
    "cctp": r"\bcctp\b",
    "ccap": r"\bccap\b",
    "rc": r"\brc\b",
    "aapc": r"\baapc\b",
    "g1": r"\bg1\b",
    "g2_avp": r"\bg2\s*avp\b",
    "g2_pro": r"\bg2\s*pro\b",
    "g4": r"\bg4\b",
    "g5": r"\bg5\b",
    "rict": r"\brict\b",
    "kbis": r"\bk[- ]?bis\b",
    "decennale": r"d[ée]cennale",
    "dc1": r"\bdc1\b",
    "dc2": r"\bdc2\b",
    "socabat": r"\bsocabat\b",
    "doc": r"d[ée]claration d['’]ouverture de chantier",
    "os": r"\bordre de service\b",
    "permis_construire": r"permis de construire",
}


def first_nonempty_line(text: str, max_len: int = 200) -> str | None:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#-* ").strip()
        if stripped and not stripped.startswith("<!--"):
            return stripped[:max_len]
    return None


def detect_key_mentions(text: str) -> dict[str, list[str]]:
    lower = text.lower()
    found: dict[str, list[str]] = {}
    for key, pattern in _KEY_MENTION_PATTERNS.items():
        matches = re.findall(pattern, lower)
        if matches:
            values = sorted({str(m) for m in matches if m}) or ["présent"]
            found[key] = values[:10]
    return found
