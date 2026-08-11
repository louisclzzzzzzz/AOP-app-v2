"""Chargement et application des critères de ciblage de la veille (`config/veille_criteres.yaml`).

Le filtrage tient en deux règles simples et auditables — `require_any` puis `exclude_any` —
délibérément à base de mots-clés et non d'un appel LLM : trier un flux quotidien d'avis est un
travail de tamis, pas de jugement, et il doit rester gratuit, instantané et explicable (on peut
toujours dire *quel* terme a retenu ou écarté un avis, ce qu'un LLM ne garantirait pas).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from app.settings import get_config_dir
from app.veille.notice import Notice


def normalize(text: str) -> str:
    """Minuscules, sans accents, ponctuation réduite à des espaces.

    Indispensable ici : les avis mélangent « dommages-ouvrage », « dommages ouvrage »,
    « DOMMAGES A L'OUVRAGE » et « Dommages Ouvrages ». Après normalisation, une seule
    expression de référence suffit à les attraper (les traits d'union et apostrophes
    devenant des espaces, comme dans les termes du YAML)."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents)).strip()


def _contains_term(haystack_normalized: str, term: str) -> bool:
    """Recherche d'un terme normalisé sur des frontières de mots.

    Le bornage n'est pas cosmétique : sans lui, l'abréviation « trc » (3 lettres, présente
    dans la liste des critères) matcherait « electrique », et « puc » matcherait « capucine ».
    """
    normalized_term = normalize(term)
    if not normalized_term:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", haystack_normalized) is not None


@dataclass(frozen=True)
class MatchResult:
    """Décision de ciblage pour un avis, avec sa justification — affichée dans l'UI pour que
    l'utilisateur puisse corriger les critères plutôt que subir un tri opaque."""

    retained: bool
    matched_terms: list[str] = field(default_factory=list)
    excluded_by: str | None = None

    def reason(self) -> str:
        if self.excluded_by:
            return f"écarté : « {self.excluded_by} »"
        if self.matched_terms:
            return "retenu : " + ", ".join(f"« {t} »" for t in self.matched_terms)
        return "écarté : aucun terme d'assurance construction"


@dataclass(frozen=True)
class VeilleCriteria:
    lookback_days: int
    require_any: list[str]
    exclude_any: list[str]
    boamp: dict[str, Any]
    ted: dict[str, Any]

    def match(self, notice: Notice) -> MatchResult:
        """Applique les critères. L'exclusion prime sur l'inclusion : un avis d'attribution
        qui parle de dommages-ouvrage reste un avis d'attribution."""
        haystack = normalize(notice.matching_text())
        for term in self.exclude_any:
            if _contains_term(haystack, term):
                return MatchResult(retained=False, excluded_by=term)
        matched = [term for term in self.require_any if _contains_term(haystack, term)]
        return MatchResult(retained=bool(matched), matched_terms=matched)


def _as_list(raw: Any) -> list[str]:
    return [str(item) for item in raw] if isinstance(raw, list) else []


@lru_cache
def get_veille_criteria() -> VeilleCriteria:
    with open(get_config_dir() / "veille_criteres.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    sources = raw.get("sources") or {}
    return VeilleCriteria(
        lookback_days=int(raw.get("lookback_days", 30)),
        require_any=_as_list(raw.get("require_any")),
        exclude_any=_as_list(raw.get("exclude_any")),
        boamp=sources.get("boamp") or {},
        ted=sources.get("ted") or {},
    )
