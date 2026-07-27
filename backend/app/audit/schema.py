"""Chargement du schéma de l'audit des risques (config/audit_risques_schema.yaml, Phase 2 du
protocole d'analyse)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.settings import get_config_dir

# Catégories "par lot" (un document par corps d'état) : sur un gros dossier elles comptent des
# dizaines de fichiers, donc `cctp_keywords` les restreint aux seuls lots pertinents pour la
# section. Les autres catégories pivots (RICT, étude de sol, notice) restent toujours entières.
LOT_FILTERED_CATEGORIES = {"TECH/CCTP TRAVAUX", "TECH/DPGF"}

VALID_GEORISQUES_ASPECTS = {"seisme", "rga", "inondation", "radon", "cavites", "mvt"}


@dataclass(frozen=True)
class AuditSection:
    id: str
    titre: str
    pivot_categories: list[str]
    cctp_keywords: list[str] = field(default_factory=list)
    georisques_aspects: list[str] = field(default_factory=list)
    points_verification: str | None = None
    instructions: str | None = None


@dataclass(frozen=True)
class AuditSchema:
    sections: list[AuditSection]

    def by_id(self, section_id: str) -> AuditSection | None:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None


@lru_cache
def load_audit_schema() -> AuditSchema:
    path = get_config_dir() / "audit_risques_schema.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    sections = [
        AuditSection(
            id=s["id"],
            titre=s["titre"],
            pivot_categories=s["pivot_categories"],
            cctp_keywords=s.get("cctp_keywords", []),
            georisques_aspects=s.get("georisques_aspects", []),
            points_verification=s.get("points_verification"),
            instructions=s.get("instructions"),
        )
        for s in raw["sections"]
    ]
    ids = [s.id for s in sections]
    assert len(ids) == len(set(ids)), "des ids de section sont dupliqués dans audit_risques_schema.yaml"
    for s in sections:
        assert s.pivot_categories, f"{s.id} : section sans pivot_categories"
        assert s.instructions, f"{s.id} : section sans instructions"
        for aspect in s.georisques_aspects:
            assert aspect in VALID_GEORISQUES_ASPECTS, f"{s.id} : aspect Géorisques inconnu {aspect!r}"
    return AuditSchema(sections=sections)
