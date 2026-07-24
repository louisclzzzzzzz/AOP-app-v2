"""Chargement du schéma de synthèse projet (config/synthese_projet_schema.yaml, Phase 1 du
protocole d'analyse)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.settings import get_config_dir

VALID_SOURCES = {"extraction_fields", "documents"}


@dataclass(frozen=True)
class SynthesisTopic:
    id: str
    titre: str
    format: str
    source: str
    extraction_field_ids: list[str] = field(default_factory=list)
    pivot_categories: list[str] = field(default_factory=list)
    grounding_field_ids: list[str] = field(default_factory=list)
    cross_document: bool = False
    instructions: str | None = None


@dataclass(frozen=True)
class SynthesisSchema:
    topics: list[SynthesisTopic]

    def by_id(self, topic_id: str) -> SynthesisTopic | None:
        for t in self.topics:
            if t.id == topic_id:
                return t
        return None


@lru_cache
def load_synthesis_schema() -> SynthesisSchema:
    path = get_config_dir() / "synthese_projet_schema.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    topics = [
        SynthesisTopic(
            id=t["id"],
            titre=t["titre"],
            format=t["format"],
            source=t["source"],
            extraction_field_ids=t.get("extraction_field_ids", []),
            pivot_categories=t.get("pivot_categories", []),
            grounding_field_ids=t.get("grounding_field_ids", []),
            cross_document=bool(t.get("cross_document", False)),
            instructions=t.get("instructions"),
        )
        for t in raw["topics"]
    ]
    ids = [t.id for t in topics]
    assert len(ids) == len(set(ids)), "des ids de thème sont dupliqués dans synthese_projet_schema.yaml"
    for t in topics:
        assert t.source in VALID_SOURCES, f"{t.id} : source inconnue {t.source!r}"
        if t.source == "extraction_fields":
            assert t.extraction_field_ids, f"{t.id} : source=extraction_fields sans extraction_field_ids"
        if t.source == "documents":
            assert t.pivot_categories, f"{t.id} : source=documents sans pivot_categories"
            assert t.instructions, f"{t.id} : source=documents sans instructions"
    return SynthesisSchema(topics=topics)
