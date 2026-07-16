"""Chargement de la taxonomie de classement (config/taxonomy.yaml, §7.1 / §4.2 du PLAN)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.settings import get_config_dir


@dataclass(frozen=True)
class TaxonomyCategory:
    path: str
    label: str
    alt_names: list[str] = field(default_factory=list)
    filename_patterns: list[re.Pattern[str]] = field(default_factory=list)
    content_patterns: list[re.Pattern[str]] = field(default_factory=list)
    lot_aware: bool = False
    doc_type_hint: str = "AUTRES"


@dataclass(frozen=True)
class Taxonomy:
    categories: list[TaxonomyCategory]
    fallback_category: str

    def paths(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.categories)

    def by_path(self, path: str) -> TaxonomyCategory | None:
        for c in self.categories:
            if c.path == path:
                return c
        return None


def fix_word_boundary(pattern: str) -> str:
    """`\\b` traite `_` comme un caractère de mot : `\\brit\\b` ne matche pas `_RIT_`, très
    fréquent dans les noms de fichiers du DCE (séparateurs `_`). On remplace les `\\b` en
    tête/fin de motif par des frontières qui excluent aussi `_` des deux côtés.

    Public : réutilisé tel quel par `app/completeness/pieces_checklist.py` (mêmes motifs
    filename/contenu, même piège avec les séparateurs `_`)."""
    if pattern.startswith(r"\b"):
        pattern = r"(?<![A-Za-z0-9])" + pattern[2:]
    if pattern.endswith(r"\b"):
        pattern = pattern[:-2] + r"(?![A-Za-z0-9])"
    return pattern


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(fix_word_boundary(p), re.IGNORECASE) for p in patterns]


@lru_cache
def load_taxonomy() -> Taxonomy:
    path = get_config_dir() / "taxonomy.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    categories = [
        TaxonomyCategory(
            path=c["path"],
            label=c["label"],
            alt_names=c.get("alt_names", []),
            filename_patterns=_compile(c.get("filename_keywords", [])),
            content_patterns=_compile(c.get("content_indices", [])),
            lot_aware=bool(c.get("lot_aware", False)),
            doc_type_hint=c.get("doc_type_hint", "AUTRES"),
        )
        for c in raw["categories"]
    ]
    fallback = raw["fallback_category"]
    assert any(c.path == fallback for c in categories), "fallback_category doit exister dans categories"
    return Taxonomy(categories=categories, fallback_category=fallback)
