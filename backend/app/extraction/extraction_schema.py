"""Chargement du schéma d'extraction (config/extraction_schema.yaml, §7.3 / §6.2 du PLAN)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.classify.taxonomy import fix_word_boundary
from app.settings import get_config_dir


@dataclass(frozen=True)
class ExtractionField:
    id: str
    libelle: str
    section: str
    resultat_attendu: str | None = None
    reference_categories: list[str] = field(default_factory=list)
    indices: list[re.Pattern[str]] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractionSchema:
    fields: list[ExtractionField]

    def by_id(self, field_id: str) -> ExtractionField | None:
        for f in self.fields:
            if f.id == field_id:
                return f
        return None

    def by_section(self) -> dict[str, list[ExtractionField]]:
        grouped: dict[str, list[ExtractionField]] = {}
        for f in self.fields:
            grouped.setdefault(f.section, []).append(f)
        return grouped


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(fix_word_boundary(p), re.IGNORECASE) for p in patterns]


@lru_cache
def load_extraction_schema() -> ExtractionSchema:
    path = get_config_dir() / "extraction_schema.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    fields = [
        ExtractionField(
            id=f["id"],
            libelle=f["libelle"],
            section=f["section"],
            resultat_attendu=f.get("resultat_attendu"),
            reference_categories=f.get("reference_categories", []),
            indices=_compile(f.get("indices", [])),
        )
        for f in raw["fields"]
    ]
    ids = [f.id for f in fields]
    assert len(ids) == len(set(ids)), "des ids de champs sont dupliqués dans extraction_schema.yaml"
    return ExtractionSchema(fields=fields)
