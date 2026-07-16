"""Moteur de classification à 3 signaux combinés (§4.3 du PLAN), optimisé pour dépenser le
budget LLM avec parcimonie (§2 OPTIMISATION.md) :

1. Nom de fichier d'origine (regex/mots-clés de la taxonomie).
2. Contenu OCR/texte extrait (mêmes regex, décisif quand le nom ment).
3. LLM classifieur (`mistral-small`, sortie structurée contrainte à la taxonomie), appelé une
   seule fois pour tout un LOT de documents ambigus (jamais un par un) — seulement quand les
   signaux 1+2 ne désignent pas une catégorie nette et unique.

Certains fichiers sont routés par convention sans appel LLM (dépôt dématérialisé, archive déjà
décompressée, bruit système) : il n'y a alors aucun jugement à faire. D'autres sont classables
par les seuls signaux 1+2 quand un candidat ressort nettement au-dessus des autres : là non
plus, un appel LLM n'ajouterait rien à la précision et gaspillerait un appel API. Seuls les
fichiers réellement ambigus (aucun candidat, candidats à score proche, ou nom générique type
`scan001.pdf`) sont soumis au LLM, regroupés par lot (`classification.batch_size`).
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
# Extrait plus court en mode batché : la classification n'a besoin que d'un aperçu (nom + tête
# de document), pas du document entier — garde le prompt groupé raisonnable en tokens.
BATCH_CONTENT_EXCERPT_MAX_CHARS = 1500

_LOT_SIGNAL_PATTERN = re.compile(
    r"lot[^a-z0-9]{0,3}(\d+(?:\s*(?:,|/|&|-|et)\s*\d+)*)", re.IGNORECASE
)

_SYSTEM_NOISE_REASONS = {"Fichier système (non analysable)", "Extension inconnue"}

# Noms de fichiers génériques (scan, capture d'écran, export par défaut...) : jamais un signal
# fiable, toujours ambigu même si un score de règle ressortait par coïncidence.
_GENERIC_FILENAME_PATTERN = re.compile(
    r"^(scan\s*\d*|img[_-]?\d*|image\s*\d*|document\s*\(\d+\)|sans[ _]?titre|untitled|dsc\d*)$",
    re.IGNORECASE,
)

# Un candidat est retenu comme non-ambigu si son score dépasse ce seuil...
_UNAMBIGUOUS_SCORE_THRESHOLD = 2
# ...et qu'aucun autre candidat n'est à moins de cet écart (sinon : signal partagé, ambigu).
_UNAMBIGUOUS_SCORE_GAP = 1


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


@dataclass(frozen=True)
class AmbiguousDocument:
    """Document dont les signaux 1+2 ne suffisent pas — candidat à la classification LLM
    batchée. Porte les signaux déjà calculés pour ne jamais les recalculer côté prompt."""

    relative_path: str
    filename: str
    content_excerpt: str
    filename_matches: list[SignalMatch]
    content_matches: list[SignalMatch]
    lot_signal: str | None


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


def _is_generic_filename(filename: str) -> bool:
    stem = filename.rsplit(".", 1)[0].strip()
    return bool(_GENERIC_FILENAME_PATTERN.fullmatch(stem))


def _unambiguous_match(
    filename_matches: list[SignalMatch], content_matches: list[SignalMatch], filename: str
) -> SignalMatch | None:
    """Un fichier est classable par règles seules si un unique candidat ressort nettement des
    signaux nom+contenu combinés. Retourne None si ambigu (LLM nécessaire) : aucun candidat,
    plusieurs candidats à score proche, ou nom générique (scan, capture d'écran, export...)."""
    if _is_generic_filename(filename):
        return None

    combined_scores: dict[str, int] = {}
    keywords_by_path: dict[str, list[str]] = {}
    for m in (*filename_matches, *content_matches):
        combined_scores[m.category_path] = combined_scores.get(m.category_path, 0) + m.score
        keywords_by_path.setdefault(m.category_path, []).extend(m.matched_keywords)

    if not combined_scores:
        return None

    ranked = sorted(combined_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_path, top_score = ranked[0]
    if top_score < _UNAMBIGUOUS_SCORE_THRESHOLD:
        return None
    if len(ranked) > 1 and (top_score - ranked[1][1]) < _UNAMBIGUOUS_SCORE_GAP:
        return None

    return SignalMatch(category_path=top_path, score=top_score, matched_keywords=keywords_by_path[top_path])


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


def _rule_match_outcome(
    *, filename: str, match: SignalMatch, lot_signal: str | None, taxonomy: Taxonomy,
    filename_matches: list[SignalMatch], content_matches: list[SignalMatch],
) -> ClassificationOutcome:
    category = taxonomy.by_path(match.category_path)
    doc_type = category.doc_type_hint if category else "AUTRES"
    normalized_filename = build_normalized_filename(
        category_path=match.category_path, lot=lot_signal, doc_type=doc_type, original_filename=filename
    )
    return ClassificationOutcome(
        category=match.category_path,
        lot=lot_signal,
        doc_type=doc_type,
        normalized_filename=normalized_filename,
        confidence=0.9,
        justification=(
            f"Classement par règle : signal nom/contenu net et unique sur {match.category_path} "
            f"(score {match.score}, mots-clés : {', '.join(dict.fromkeys(match.matched_keywords)) or '—'})."
        ),
        signals={
            "rule": "unambiguous_signal",
            "filename_matches": [m.category_path for m in filename_matches[:5]],
            "content_matches": [m.category_path for m in content_matches[:5]],
            "lot_signal": lot_signal,
        },
        model_name=None,
        model_version=None,
        error=None,
    )


def classify_document_by_rules(
    *,
    relative_path: str,
    filename: str,
    file_category: str,
    non_analyzable_reason: str | None,
    content_excerpt: str,
) -> ClassificationOutcome | None:
    """Classe un document sans appel LLM quand c'est possible (auto-route par convention, ou
    signal nom/contenu net et unique). Retourne None si le document est ambigu : il doit alors
    être soumis à `classify_documents_batch` (regroupé avec d'autres documents ambigus)."""
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

    match = _unambiguous_match(filename_matches, content_matches, filename)
    if match is None:
        return None

    return _rule_match_outcome(
        filename=filename,
        match=match,
        lot_signal=lot_signal,
        taxonomy=taxonomy,
        filename_matches=filename_matches,
        content_matches=content_matches,
    )


@lru_cache
def _batch_item_model_for_categories(category_paths: tuple[str, ...]) -> type[BaseModel]:
    from typing import Literal

    return create_model(
        "ClassificationBatchItem",
        index=(int, ...),
        category_path=(Literal[category_paths], ...),
        lot=(str | None, None),
        document_type=(str, ...),
        normalized_label=(str, ...),
        confidence=(float, ...),
        justification=(str, ...),
    )


@lru_cache
def _batch_response_model_for_categories(category_paths: tuple[str, ...]) -> type[BaseModel]:
    item_model = _batch_item_model_for_categories(category_paths)
    return create_model("ClassificationBatchDecision", items=(list[item_model], ...))


_BATCH_SYSTEM_PROMPT = """Tu es un assistant expert en classement de dossiers de consultation des \
entreprises (DCE) pour l'underwriting assurance construction (SMABTP). Ta tâche : classer PLUSIEURS \
documents en une seule réponse, chacun dans l'une des catégories de la taxonomie fournie, détecter \
un éventuel numéro de lot par document, et proposer un libellé court normalisé par document.

Règles impératives :
- Réponds avec EXACTEMENT une décision par document reçu, en reprenant son `index` tel quel.
- Choisis TOUJOURS une catégorie parmi la liste fournie (jamais une catégorie inventée).
- Si aucune catégorie ne correspond avec certitude pour un document, choisis "AUTRES" et indique \
une confiance basse pour ce document.
- N'invente jamais d'information absente du nom de fichier ou de l'extrait de contenu.
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée), \
indépendamment pour chaque document.
- Justifie brièvement chaque décision en citant les signaux qui t'ont convaincu (nom, contenu, ou les deux).
"""


def _build_batch_user_prompt(*, items: list[AmbiguousDocument], taxonomy: Taxonomy) -> str:
    blocks = []
    for i, item in enumerate(items):
        excerpt = (
            item.content_excerpt[:BATCH_CONTENT_EXCERPT_MAX_CHARS]
            if item.content_excerpt
            else "(aucun contenu extrait)"
        )
        blocks.append(f"""--- Document index={i} ---
Chemin d'origine dans le DCE : {item.relative_path}
Nom de fichier : {item.filename}
Signal nom de fichier (candidats par score) : {_format_matches(item.filename_matches)}
Signal contenu (candidats par score) : {_format_matches(item.content_matches)}
Numéro de lot détecté par regex (indicatif, à confirmer) : {item.lot_signal or "aucun"}
Extrait de contenu (texte natif ou OCR, tronqué) :
---
{excerpt}
---""")

    return f"""Taxonomie disponible (chemin — libellé) :
{_category_catalog(taxonomy)}

Documents à classer ({len(items)}) :

{chr(10).join(blocks)}

Réponds avec une liste de {len(items)} décisions (une par index ci-dessus), chacune avec la \
catégorie la plus probable, le lot si pertinent (ou null), un type de document court (ex: CCAP, \
RICT, ATT-ENT), un libellé court normalisé (2-5 mots, sans accents ni ponctuation superflue), \
une confiance entre 0 et 1, et une justification concise."""


def _fallback_outcome_for(item: AmbiguousDocument, taxonomy: Taxonomy, *, error: str) -> ClassificationOutcome:
    return ClassificationOutcome(
        category=taxonomy.fallback_category,
        lot=item.lot_signal,
        doc_type="AUTRES",
        normalized_filename=item.filename,
        confidence=0.0,
        justification="",
        signals={
            "filename_matches": [m.category_path for m in item.filename_matches[:5]],
            "content_matches": [m.category_path for m in item.content_matches[:5]],
            "lot_signal": item.lot_signal,
        },
        model_name=None,
        model_version=None,
        error=error,
    )


def classify_documents_batch(items: list[AmbiguousDocument]) -> list[ClassificationOutcome]:
    """Un seul appel LLM structuré pour tout un lot de documents ambigus (§2 OPTIMISATION.md) —
    remplace un appel par document par un appel par lot de `classification.batch_size`."""
    if not items:
        return []

    taxonomy = load_taxonomy()
    response_model = _batch_response_model_for_categories(taxonomy.paths())
    classification_cfg = get_models_config().get("classification", {})
    model = classification_cfg.get("model")

    try:
        decision, api_model_name = call_structured_chat(
            system_prompt=_BATCH_SYSTEM_PROMPT,
            user_prompt=_build_batch_user_prompt(items=items, taxonomy=taxonomy),
            response_model=response_model,
            what=f"classification batchée de {len(items)} document(s)",
            model=model,
        )
    except Exception as exc:
        logger.exception("Échec de la classification LLM batchée (%d documents)", len(items))
        return [_fallback_outcome_for(item, taxonomy, error=str(exc)) for item in items]

    by_index = {d.index: d for d in decision.items}
    outcomes: list[ClassificationOutcome] = []
    for i, item in enumerate(items):
        d = by_index.get(i)
        if d is None:
            outcomes.append(
                _fallback_outcome_for(
                    item, taxonomy, error=f"Aucune décision reçue pour l'index {i} dans la réponse batchée."
                )
            )
            continue
        normalized_filename = build_normalized_filename(
            category_path=d.category_path, lot=d.lot, doc_type=d.document_type, original_filename=item.filename
        )
        outcomes.append(
            ClassificationOutcome(
                category=d.category_path,
                lot=d.lot,
                doc_type=d.document_type,
                normalized_filename=normalized_filename,
                confidence=float(d.confidence),
                justification=d.justification,
                signals={
                    "filename_matches": [m.category_path for m in item.filename_matches[:5]],
                    "content_matches": [m.category_path for m in item.content_matches[:5]],
                    "lot_signal": item.lot_signal,
                    "llm_raw": d.model_dump(mode="json"),
                },
                model_name=api_model_name,
                model_version=model or get_models_config()["llm"]["model"],
                error=None,
            )
        )
    return outcomes
