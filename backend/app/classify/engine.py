"""Moteur de classification à 3 signaux combinés (§4.3 du PLAN) :

1. Nom de fichier d'origine (regex/mots-clés de la taxonomie).
2. Contenu OCR/texte extrait (mêmes regex, décisif quand le nom ment).
3. LLM classifieur (`mistral-large`, sortie structurée contrainte à la taxonomie) qui reçoit
   nom + extrait de contenu + les deux signaux ci-dessus et tranche.

Certains fichiers sont routés par convention sans appel LLM (dépôt dématérialisé, archive déjà
décompressée, bruit système) : il n'y a alors aucun jugement à faire, un appel LLM n'ajouterait
rien à la précision et gaspillerait un appel API.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, create_model

from app.classify.naming import build_normalized_filename
from app.classify.taxonomy import Taxonomy, TaxonomyCategory, load_taxonomy
from app.mistral.client import call_structured_chat
from app.settings import get_models_config
from app.store.models import FileCategory

logger = logging.getLogger(__name__)

CONTENT_EXCERPT_MAX_CHARS = 4000

_LOT_SIGNAL_PATTERN = re.compile(
    r"lot[^a-z0-9]{0,3}(\d+(?:\s*(?:,|/|&|-|et)\s*\d+)*)", re.IGNORECASE
)

_SYSTEM_NOISE_REASONS = {"Fichier système (non analysable)", "Extension inconnue"}


@dataclass(frozen=True)
class SignalMatch:
    category_path: str
    score: int
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class ClassificationOutcome:
    category: str
    lot: str | None
    doc_type: str
    normalized_filename: str
    confidence: float
    justification: str
    signals: dict[str, Any]
    model_name: str | None
    model_version: str | None
    error: str | None


def extract_lot_signal(text: str) -> str | None:
    if not text:
        return None
    match = _LOT_SIGNAL_PATTERN.search(text)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _score_text(text: str, taxonomy: Taxonomy, *, use_content: bool) -> list[SignalMatch]:
    if not text:
        return []
    matches: list[SignalMatch] = []
    for cat in taxonomy.categories:
        patterns = cat.content_patterns if use_content else cat.filename_patterns
        matched = [p.pattern for p in patterns if p.search(text)]
        if matched:
            matches.append(SignalMatch(category_path=cat.path, score=len(matched), matched_keywords=matched))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def score_filename(filename: str, taxonomy: Taxonomy | None = None) -> list[SignalMatch]:
    return _score_text(filename, taxonomy or load_taxonomy(), use_content=False)


def score_content(text: str, taxonomy: Taxonomy | None = None) -> list[SignalMatch]:
    return _score_text(text, taxonomy or load_taxonomy(), use_content=True)


@lru_cache
def _response_model_for_categories(category_paths: tuple[str, ...]) -> type[BaseModel]:
    from typing import Literal

    return create_model(
        "ClassificationDecision",
        category_path=(Literal[category_paths], ...),
        lot=(str | None, None),
        document_type=(str, ...),
        normalized_label=(str, ...),
        confidence=(float, ...),
        justification=(str, ...),
    )


def _format_matches(matches: list[SignalMatch]) -> str:
    if not matches:
        return "(aucune correspondance)"
    return ", ".join(f"{m.category_path} (score {m.score})" for m in matches[:5])


def _category_catalog(taxonomy: Taxonomy) -> str:
    lines = []
    for c in taxonomy.categories:
        alt = f" (alias : {', '.join(c.alt_names)})" if c.alt_names else ""
        lines.append(f"- {c.path} — {c.label}{alt}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """Tu es un assistant expert en classement de dossiers de consultation des \
entreprises (DCE) pour l'underwriting assurance construction (SMABTP). Ta tâche : classer un \
document dans l'une des catégories de la taxonomie fournie, détecter un éventuel numéro de lot, \
et proposer un libellé court normalisé.

Règles impératives :
- Choisis TOUJOURS une catégorie parmi la liste fournie (jamais une catégorie inventée).
- Si aucune catégorie ne correspond avec certitude, choisis "AUTRES" et indique une confiance basse.
- N'invente jamais d'information absente du nom de fichier ou de l'extrait de contenu.
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée).
- Justifie brièvement ta décision en citant les signaux qui t'ont convaincu (nom, contenu, ou les deux).
"""


def _build_user_prompt(
    *,
    relative_path: str,
    filename: str,
    content_excerpt: str,
    filename_matches: list[SignalMatch],
    content_matches: list[SignalMatch],
    lot_signal: str | None,
    taxonomy: Taxonomy,
) -> str:
    excerpt = content_excerpt[:CONTENT_EXCERPT_MAX_CHARS] if content_excerpt else "(aucun contenu extrait)"
    return f"""Taxonomie disponible (chemin — libellé) :
{_category_catalog(taxonomy)}

Document à classer :
- Chemin d'origine dans le DCE : {relative_path}
- Nom de fichier : {filename}
- Signal nom de fichier (candidats par score) : {_format_matches(filename_matches)}
- Signal contenu (candidats par score) : {_format_matches(content_matches)}
- Numéro de lot détecté par regex (indicatif, à confirmer) : {lot_signal or "aucun"}

Extrait de contenu (texte natif ou OCR, tronqué) :
---
{excerpt}
---

Réponds avec la catégorie la plus probable, le lot si pertinent (ou null), un type de document \
court (ex: CCAP, RICT, ATT-ENT), un libellé court normalisé (2-5 mots, sans accents ni ponctuation \
superflue), une confiance entre 0 et 1, et une justification concise."""


def _auto_route(
    *, filename: str, category: str, doc_type: str, justification: str, confidence: float
) -> ClassificationOutcome:
    normalized = build_normalized_filename(
        category_path=category, lot=None, doc_type=doc_type, original_filename=filename
    )
    return ClassificationOutcome(
        category=category,
        lot=None,
        doc_type=doc_type,
        normalized_filename=normalized,
        confidence=confidence,
        justification=justification,
        signals={"rule": "auto_route"},
        model_name=None,
        model_version=None,
        error=None,
    )


def classify_document(
    *,
    relative_path: str,
    filename: str,
    file_category: str,
    non_analyzable_reason: str | None,
    content_excerpt: str,
) -> ClassificationOutcome:
    taxonomy = load_taxonomy()

    if file_category == FileCategory.DEMATERIALISE.value:
        return _auto_route(
            filename=filename,
            category="ENVOI DEMAT/COPIE DEPOT",
            doc_type="DEPOT",
            confidence=1.0,
            justification=(
                "Fichier de dépôt dématérialisé (.cle/.cry/.iv/.pli/.xml/.pde/.pdp) — routage "
                "automatique par convention, jamais analysé ni modifié."
            ),
        )
    if file_category == FileCategory.ARCHIVE.value:
        return _auto_route(
            filename=filename,
            category=taxonomy.fallback_category,
            doc_type="ARCHIVE",
            confidence=1.0,
            justification=(
                "Archive déjà décompressée pour analyse : son contenu est classé "
                "individuellement ; l'archive elle-même est conservée pour référence."
            ),
        )
    if non_analyzable_reason in _SYSTEM_NOISE_REASONS:
        return _auto_route(
            filename=filename,
            category=taxonomy.fallback_category,
            doc_type="AUTRES",
            confidence=0.3,
            justification=f"{non_analyzable_reason} — aucun contenu exploitable pour classification fiable.",
        )

    filename_matches = score_filename(filename, taxonomy)
    content_matches = score_content(content_excerpt, taxonomy) if content_excerpt else []
    lot_signal = extract_lot_signal(filename) or extract_lot_signal(content_excerpt or "")

    signals: dict[str, Any] = {
        "filename_matches": [m.category_path for m in filename_matches[:5]],
        "content_matches": [m.category_path for m in content_matches[:5]],
        "lot_signal": lot_signal,
    }

    user_prompt = _build_user_prompt(
        relative_path=relative_path,
        filename=filename,
        content_excerpt=content_excerpt,
        filename_matches=filename_matches,
        content_matches=content_matches,
        lot_signal=lot_signal,
        taxonomy=taxonomy,
    )
    response_model = _response_model_for_categories(taxonomy.paths())

    try:
        decision, api_model_name = call_structured_chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=response_model,
            what=f"classification de {relative_path}",
        )
    except Exception as exc:
        logger.exception("Échec de la classification LLM pour %s", relative_path)
        return ClassificationOutcome(
            category=taxonomy.fallback_category,
            lot=lot_signal,
            doc_type="AUTRES",
            normalized_filename=filename,
            confidence=0.0,
            justification="",
            signals=signals,
            model_name=None,
            model_version=None,
            error=str(exc),
        )

    signals["llm_raw"] = decision.model_dump(mode="json")

    normalized_filename = build_normalized_filename(
        category_path=decision.category_path,
        lot=decision.lot,
        doc_type=decision.document_type,
        original_filename=filename,
    )

    return ClassificationOutcome(
        category=decision.category_path,
        lot=decision.lot,
        doc_type=decision.document_type,
        normalized_filename=normalized_filename,
        confidence=float(decision.confidence),
        justification=decision.justification,
        signals=signals,
        model_name=api_model_name,
        model_version=get_models_config()["llm"]["model"],
        error=None,
    )
