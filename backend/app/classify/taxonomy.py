"""Chargement de la taxonomie de classement (config/taxonomy.yaml, §7.1 / §4.2 du PLAN)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.settings import get_config_dir


def strip_accents(text: str) -> str:
    """Neutralise les accents (`contrôle` -> `controle`) pour que les motifs de la taxonomie
    matchent aussi bien un texte proprement accentué qu'un texte natif/OCR qui les a perdus —
    fréquent sur les titres en capitales de documents administratifs français (ex. un RICT réel
    dont le titre extrait était "CONTROLE" sans accent, faisant échouer silencieusement le motif
    `content_indices: rapport initial de contrôle technique` malgré une correspondance quasi
    parfaite par ailleurs). Appliqué à la fois aux motifs compilés (`_compile`) et au texte scoré
    (`app/classify/engine.py::_score_text`) pour rester symétrique dans les deux sens."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


@dataclass(frozen=True)
class TaxonomyCategory:
    path: str
    label: str
    alt_names: list[str] = field(default_factory=list)
    filename_patterns: list[re.Pattern[str]] = field(default_factory=list)
    content_patterns: list[re.Pattern[str]] = field(default_factory=list)
    lot_aware: bool = False
    doc_type_hint: str = "AUTRES"
    is_pivot: bool = False


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

    def pivot_paths(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.categories if c.is_pivot)


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
    return [re.compile(fix_word_boundary(strip_accents(p)), re.IGNORECASE) for p in patterns]


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
            is_pivot=bool(c.get("is_pivot", False)),
        )
        for c in raw["categories"]
    ]
    fallback = raw["fallback_category"]
    assert any(c.path == fallback for c in categories), "fallback_category doit exister dans categories"
    return Taxonomy(categories=categories, fallback_category=fallback)
