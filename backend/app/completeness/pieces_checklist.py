"""Chargement de la checklist de pièces (config/pieces_checklist.yaml, §7.2 / §5.2 du PLAN)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.classify.taxonomy import fix_word_boundary
from app.settings import get_config_dir


@dataclass(frozen=True)
class Piece:
    id: str
    libelle: str
    phase: str
    alias: list[str] = field(default_factory=list)
    categorie_attendue: str | None = None
    obligatoire: bool = False
    peut_etre_inclus_dans_autre: bool = False
    indices: list[re.Pattern[str]] = field(default_factory=list)
    par_lot: bool = False
    controle_date: str | None = None
    fallback: str | None = None


@dataclass(frozen=True)
class PiecesChecklist:
    pieces: list[Piece]

    def by_id(self, piece_id: str) -> Piece | None:
        for p in self.pieces:
            if p.id == piece_id:
                return p
        return None

    def by_phase(self) -> dict[str, list[Piece]]:
        grouped: dict[str, list[Piece]] = {}
        for p in self.pieces:
            grouped.setdefault(p.phase, []).append(p)
        return grouped


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(fix_word_boundary(p), re.IGNORECASE) for p in patterns]


@lru_cache
def load_pieces_checklist() -> PiecesChecklist:
    path = get_config_dir() / "pieces_checklist.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    pieces = [
        Piece(
            id=p["id"],
            libelle=p["libelle"],
            phase=p["phase"],
            alias=p.get("alias", []),
            categorie_attendue=p.get("categorie_attendue"),
            obligatoire=bool(p.get("obligatoire", False)),
            peut_etre_inclus_dans_autre=bool(p.get("peut_etre_inclus_dans_autre", False)),
            indices=_compile(p.get("indices", [])),
            par_lot=bool(p.get("par_lot", False)),
            controle_date=p.get("controle_date"),
            fallback=p.get("fallback"),
        )
        for p in raw["pieces"]
    ]
    ids = [p.id for p in pieces]
    assert len(ids) == len(set(ids)), "des ids de pièces sont dupliqués dans pieces_checklist.yaml"
    return PiecesChecklist(pieces=pieces)
