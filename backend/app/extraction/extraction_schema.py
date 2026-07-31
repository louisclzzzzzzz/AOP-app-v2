"""Chargement du schéma d'extraction (config/extraction_schema.yaml, §7.3 / §6.2 du PLAN)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.classify.taxonomy import fix_word_boundary
from app.settings import get_config_dir


@dataclass(frozen=True)
class ExtractionSection:
    """Regroupement thématique des champs (bloc `sections` du YAML). L'ordre de déclaration fait
    foi partout où les champs sont présentés : écran de validation, rapport Markdown, export
    Excel — la Feuil2 de `donnes_ref_v2.md` est une liste à plat d'une cinquantaine de données,
    illisible sans ce découpage."""

    id: str
    libelle: str


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
    sections: list[ExtractionSection] = field(default_factory=list)

    def by_id(self, field_id: str) -> ExtractionField | None:
        for f in self.fields:
            if f.id == field_id:
                return f
        return None

    def section_label(self, section_id: str) -> str:
        """Libellé lisible d'une section — son id brut en dernier recours, pour qu'un rapport
        ancien référençant une section supprimée reste rendable."""
        for s in self.sections:
            if s.id == section_id:
                return s.libelle
        return section_id

    def by_section(self) -> dict[str, list[ExtractionField]]:
        """Champs groupés par section, dans l'ordre de déclaration des sections (et non l'ordre
        d'apparition des champs) — une section déclarée mais vide n'apparaît pas."""
        grouped: dict[str, list[ExtractionField]] = {}
        for section in self.sections:
            fields_in_section = [f for f in self.fields if f.section == section.id]
            if fields_in_section:
                grouped[section.id] = fields_in_section
        return grouped


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(fix_word_boundary(p), re.IGNORECASE) for p in patterns]


@lru_cache
def load_extraction_schema() -> ExtractionSchema:
    path = get_config_dir() / "extraction_schema.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    sections = [ExtractionSection(id=s["id"], libelle=s["libelle"]) for s in raw.get("sections", [])]
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

    section_ids = [s.id for s in sections]
    assert len(section_ids) == len(set(section_ids)), "des ids de section sont dupliqués dans extraction_schema.yaml"
    known = set(section_ids)
    unknown = sorted({f.section for f in fields} - known)
    assert not unknown, f"sections inconnues référencées par des champs de extraction_schema.yaml : {unknown}"

    return ExtractionSchema(fields=fields, sections=sections)
