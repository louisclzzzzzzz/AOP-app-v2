"""Moteur d'extraction (§6.3 du PLAN) :

1. **Candidats de référence** : documents déjà classifiés (étape 1) dans une des catégories
   de référence du champ (`field.reference_categories`, par ordre de priorité).
   - Champs critiques (montants, dates, garanties — `models.yaml.extraction.
     cross_check_required_fields`) : extraction **indépendante sur chaque candidat**
     (jusqu'à `cross_check_passes`), puis comparaison programmatique des valeurs obtenues —
     signale une incohérence si elles divergent (§6.3 : "croiser RC + CCAP + CCTP").
   - Autres champs : un seul appel, on essaie les candidats dans l'ordre jusqu'à confirmation.
2. **Recherche intra-document élargie** (si couche 1 vide) : mêmes mots-clés (`indices`) sur
   tous les documents analysables, scoring + plafond `MAX_LLM_CANDIDATES`.
3. **Absent** : aucune valeur trouvée nulle part → `value=None`, justification explicite,
   aucun appel LLM (rien à vérifier).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.extraction.extraction_schema import ExtractionField
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat
from app.settings import get_models_config
from app.store.models import CrossCheckStatus, MatchLayer

logger = logging.getLogger(__name__)

CONTENT_EXCERPT_MAX_CHARS = 4000
MAX_LLM_CANDIDATES = 3


@dataclass
class ExtractionOutcome:
    match_layer: str
    value: str | None
    confidence: float | None
    justification: str | None
    citation: str | None
    sources: list[dict[str, Any]]
    cross_check_status: str | None
    model_name: str | None
    model_version: str | None
    error: str | None


class _ExtractionDecision(BaseModel):
    found: bool
    value: str
    confidence: float
    justification: str
    citation: str


def _score_candidate(doc: DocumentSignal, field: ExtractionField) -> int:
    return sum(1 for p in field.indices if p.search(doc.content_excerpt))


def _reference_candidates(field: ExtractionField, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    candidates: list[DocumentSignal] = []
    for category in field.reference_categories:
        candidates.extend(d for d in documents if d.final_category == category and d.content_excerpt)
    return candidates


_SYSTEM_PROMPT = """Tu es un assistant expert en extraction de données depuis des dossiers de \
consultation des entreprises (DCE) pour l'underwriting assurance construction (SMABTP). Ta \
tâche : extraire la valeur d'une donnée précise depuis un document, avec une preuve.

Règles impératives :
- Ne renvoie found=true que si la valeur est explicitement présente dans le passage fourni.
- Cite le passage exact qui justifie la valeur (`citation`) — jamais une paraphrase.
- La valeur (`value`) doit être normalisée et concise (ex. une date JJ/MM/AAAA, un montant avec \
son unité) — jamais une phrase entière.
- Si la donnée n'est pas dans ce document, réponds found=false, value="", citation="".
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée).
"""


def _build_user_prompt(*, field: ExtractionField, doc: DocumentSignal) -> str:
    excerpt = doc.content_excerpt[:CONTENT_EXCERPT_MAX_CHARS]
    hint = f"\nFormat attendu : {field.resultat_attendu}" if field.resultat_attendu else ""
    return f"""Donnée recherchée : {field.libelle}{hint}

Document candidat : {doc.filename}
Extrait de contenu (texte natif ou OCR, tronqué) :
---
{excerpt}
---

Cette donnée est-elle présente dans ce document ? Réponds found=true/false, la valeur \
normalisée, une confiance entre 0 et 1, une justification concise, et la citation exacte du \
passage probant (chaînes vides si absente)."""


def _call_llm(field: ExtractionField, doc: DocumentSignal) -> tuple[_ExtractionDecision, str | None]:
    return call_structured_chat(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(field=field, doc=doc),
        response_model=_ExtractionDecision,
        what=f"extraction « {field.libelle} » sur {doc.filename}",
    )


def _absent_outcome(reason: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        match_layer=MatchLayer.NONE.value,
        value=None,
        confidence=None,
        justification=reason,
        citation=None,
        sources=[],
        cross_check_status=None,
        model_name=None,
        model_version=None,
        error=None,
    )


def _sequential_outcome(
    field: ExtractionField, candidates: list[DocumentSignal], *, match_layer: str
) -> ExtractionOutcome | None:
    """Essaie les candidats un par un jusqu'à confirmation — pour les champs non critiques.
    Retourne None (pas d'erreur, rien confirmé) si on peut essayer la couche suivante."""
    last_error: str | None = None
    for doc in candidates:
        try:
            decision, api_model_name = _call_llm(field, doc)
        except Exception as exc:
            logger.exception("Échec de l'extraction pour %s / %s", field.id, doc.document_id)
            last_error = str(exc)
            continue

        if not decision.found:
            continue

        return ExtractionOutcome(
            match_layer=match_layer,
            value=decision.value,
            confidence=decision.confidence,
            justification=decision.justification,
            citation=decision.citation,
            sources=[
                {
                    "document_id": doc.document_id,
                    "filename": doc.filename,
                    "value": decision.value,
                    "confidence": decision.confidence,
                }
            ],
            cross_check_status=CrossCheckStatus.NOT_APPLICABLE.value,
            model_name=api_model_name,
            model_version=get_models_config()["llm"]["model"],
            error=None,
        )

    if last_error:
        return ExtractionOutcome(
            match_layer=match_layer,
            value=None,
            confidence=None,
            justification="Candidat(s) trouvé(s) mais échec de l'appel LLM.",
            citation=None,
            sources=[],
            cross_check_status=None,
            model_name=None,
            model_version=None,
            error=last_error,
        )
    return None


def _cross_check_outcome(
    field: ExtractionField, candidates: list[DocumentSignal], *, max_passes: int
) -> ExtractionOutcome | None:
    """Extraction indépendante sur chaque candidat de référence (jusqu'à `max_passes`), puis
    comparaison programmatique des valeurs obtenues — signale une incohérence si elles
    divergent. Retourne None si aucun candidat ne confirme et qu'aucune erreur n'est survenue
    (on peut alors essayer la recherche intra-document élargie)."""
    found: list[tuple[DocumentSignal, _ExtractionDecision, str | None]] = []
    last_error: str | None = None

    for doc in candidates[:max_passes]:
        try:
            decision, api_model_name = _call_llm(field, doc)
        except Exception as exc:
            logger.exception("Échec de l'extraction (recoupement) pour %s / %s", field.id, doc.document_id)
            last_error = str(exc)
            continue
        if decision.found:
            found.append((doc, decision, api_model_name))

    if not found:
        if last_error:
            return ExtractionOutcome(
                match_layer=MatchLayer.FILE.value,
                value=None,
                confidence=None,
                justification="Candidat(s) de référence trouvé(s) mais échec de l'appel LLM.",
                citation=None,
                sources=[],
                cross_check_status=None,
                model_name=None,
                model_version=None,
                error=last_error,
            )
        return None

    sources = [
        {
            "document_id": doc.document_id,
            "filename": doc.filename,
            "value": decision.value,
            "confidence": decision.confidence,
        }
        for doc, decision, _ in found
    ]
    normalized_values = {d.value.strip().casefold() for _, d, _ in found}

    best_doc, best_decision, best_model = max(found, key=lambda item: item[1].confidence)

    if len(found) == 1:
        cross_check_status = CrossCheckStatus.SINGLE_SOURCE.value
    elif len(normalized_values) == 1:
        cross_check_status = CrossCheckStatus.COHERENT.value
    else:
        cross_check_status = CrossCheckStatus.INCOHERENT.value

    justification = best_decision.justification
    if cross_check_status == CrossCheckStatus.INCOHERENT.value:
        divergent = ", ".join(f"{d.value!r} ({doc.filename})" for doc, d, _ in found)
        justification = f"Valeurs divergentes entre documents de référence : {divergent}."

    return ExtractionOutcome(
        match_layer=MatchLayer.FILE.value,
        value=best_decision.value,
        confidence=best_decision.confidence,
        justification=justification,
        citation=best_decision.citation,
        sources=sources,
        cross_check_status=cross_check_status,
        model_name=best_model,
        model_version=get_models_config()["llm"]["model"],
        error=None,
    )


def analyze_field(
    *,
    field: ExtractionField,
    documents: list[DocumentSignal],
    cross_check_required: bool,
) -> ExtractionOutcome:
    cfg = get_models_config()["extraction"]
    max_passes = int(cfg.get("cross_check_passes", 2))

    reference_candidates = _reference_candidates(field, documents)
    if reference_candidates:
        outcome = (
            _cross_check_outcome(field, reference_candidates, max_passes=max_passes)
            if cross_check_required
            else _sequential_outcome(field, reference_candidates, match_layer=MatchLayer.FILE.value)
        )
        if outcome is not None:
            return outcome

    content_candidates = sorted(
        (d for d in documents if d.content_excerpt and _score_candidate(d, field) > 0),
        key=lambda d: _score_candidate(d, field),
        reverse=True,
    )[:MAX_LLM_CANDIDATES]

    if content_candidates:
        outcome = _sequential_outcome(field, content_candidates, match_layer=MatchLayer.CONTENT.value)
        if outcome is not None:
            return outcome

    return _absent_outcome(
        "Aucune valeur trouvée : ni dans les documents de référence, ni par recherche de mots-clés."
    )
