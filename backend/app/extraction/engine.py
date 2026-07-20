"""Moteur d'extraction (§6.3 du PLAN), optimisé pour dépenser le budget LLM sur la profondeur
plutôt que sur le nombre d'appels (§3 OPTIMISATION.md) :

1. **Un appel riche par document de référence**, pas un appel par (champ × document) : pour
   chaque document déjà classifié (étape 1) dans une catégorie de référence d'au moins un
   champ, `analyze_document` extrait EN UNE FOIS toutes les valeurs pertinentes pour CE
   document. Le contexte transmis n'est plus une troncature aveugle en tête de document mais
   les passages les plus pertinents (scoring mots-clés sur les `indices`/libellés des champs
   demandés dans cet appel).
2. **Recoupement dérivé, pas appelé** : pour les champs critiques (montants, dates, garanties),
   les valeurs obtenues via plusieurs documents de référence lors de l'étape 1 sont comparées
   programmatiquement après coup (`resolve_field`) — aucun appel LLM dédié au recoupement.
3. **Recherche intra-document élargie** (si un champ reste sans valeur après l'étape 1) : même
   principe par document candidat (mots-clés, plafond `MAX_LLM_CANDIDATES`), mais un seul appel
   par document candidat pour tous les champs encore manquants qui le concernent.
4. **Absent** : aucune valeur trouvée nulle part → `value=None`, justification explicite,
   aucun appel LLM.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, create_model

from app.extraction.extraction_schema import ExtractionField
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat
from app.mistral.validation import confidence_validator
from app.settings import get_models_config
from app.store.models import CrossCheckStatus, MatchLayer

logger = logging.getLogger(__name__)

# Budget de contexte PAR APPEL (couvre potentiellement plusieurs champs sur le même document,
# donc plus généreux que l'ancien budget par champ) — sélectionné par pertinence, pas par
# troncature aveugle en tête de document (voir `_select_relevant_excerpt`).
DOCUMENT_EXCERPT_MAX_CHARS = 6000
MAX_LLM_CANDIDATES = 3
_MIN_RELEVANT_WORD_LEN = 4


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


@dataclass
class DocumentExtractionResult:
    """Résultat d'un appel `analyze_document` : les décisions par champ demandé (uniquement si
    l'appel a réussi), ou une erreur si l'appel entier a échoué."""

    document_id: str
    decisions: dict[str, Any] = field(default_factory=dict)  # field_id -> item de décision LLM
    model_name: str | None = None
    error: str | None = None


def _score_candidate(doc: DocumentSignal, extraction_field: ExtractionField) -> int:
    return sum(1 for p in extraction_field.indices if p.search(doc.content_excerpt))


def reference_candidates(extraction_field: ExtractionField, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Documents de référence pour ce champ, dans l'ordre de priorité de `reference_categories`."""
    candidates: list[DocumentSignal] = []
    for category in extraction_field.reference_categories:
        candidates.extend(d for d in documents if d.final_category == category and d.content_excerpt)
    return candidates


def layer2_candidates(extraction_field: ExtractionField, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    return sorted(
        (d for d in documents if d.content_excerpt and _score_candidate(d, extraction_field) > 0),
        key=lambda d: _score_candidate(d, extraction_field),
        reverse=True,
    )[:MAX_LLM_CANDIDATES]


def plan_reference_document_calls(
    schema_fields: list[ExtractionField], documents: list[DocumentSignal]
) -> list[tuple[DocumentSignal, list[ExtractionField]]]:
    """Couche 1 : un appel par document de référence distinct, regroupant tous les champs dont
    ce document couvre la catégorie."""
    calls: list[tuple[DocumentSignal, list[ExtractionField]]] = []
    for doc in documents:
        if not doc.final_category:
            continue
        # Un document de référence par catégorie est proposé même si son texte n'a pas encore
        # été extrait (OCR différé, §5 OPTIMISATION.md) : `ensure_document_ocr` le comble juste
        # avant l'appel ; `analyze_document` gère proprement le cas où il reste vide malgré tout.
        fields_for_doc = [f for f in schema_fields if doc.final_category in f.reference_categories]
        if fields_for_doc:
            calls.append((doc, fields_for_doc))
    return calls


def plan_layer2_calls(
    missing_fields: list[ExtractionField], documents: list[DocumentSignal]
) -> list[tuple[DocumentSignal, list[ExtractionField]]]:
    """Couche 2 : un appel par document candidat (scoré par mots-clés), regroupant les champs
    encore manquants pertinents pour ce candidat."""
    doc_to_fields: dict[str, list[ExtractionField]] = {}
    doc_by_id = {d.document_id: d for d in documents}
    order: list[str] = []
    for extraction_field in missing_fields:
        for d in layer2_candidates(extraction_field, documents):
            if d.document_id not in doc_to_fields:
                doc_to_fields[d.document_id] = []
                order.append(d.document_id)
            doc_to_fields[d.document_id].append(extraction_field)
    return [(doc_by_id[doc_id], doc_to_fields[doc_id]) for doc_id in order]


# --- Sélection de contexte par pertinence (§3 OPTIMISATION.md, sans embeddings) -----------------

def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _relevant_words(fields: list[ExtractionField]) -> set[str]:
    words: set[str] = set()
    for f in fields:
        for w in re.findall(r"[a-zàâäéèêëïîôöùûüç]{%d,}" % _MIN_RELEVANT_WORD_LEN, f.libelle.lower()):
            words.add(w)
    return words


def _select_relevant_excerpt(
    doc: DocumentSignal, fields: list[ExtractionField], *, max_chars: int = DOCUMENT_EXCERPT_MAX_CHARS
) -> str:
    """Découpe le texte en paragraphes et ne garde que les plus pertinents pour les champs
    demandés (score par occurrence des `indices` ∪ mots du libellé), jusqu'au budget `max_chars`
    — remplace la troncature aveugle en tête de document."""
    text = doc.content_excerpt
    if len(text) <= max_chars:
        return text

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return text[:max_chars]

    patterns = [p for f in fields for p in f.indices]
    words = _relevant_words(fields)

    def _score(paragraph: str) -> int:
        lowered = paragraph.lower()
        return sum(1 for p in patterns if p.search(paragraph)) + sum(lowered.count(w) for w in words)

    scored = [(_score(p), i, p) for i, p in enumerate(paragraphs)]
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)

    selected: set[int] = set()
    budget = max_chars
    for _score_value, i, p in ranked:
        if not selected:
            # Garantit au moins un paragraphe (le plus pertinent) même s'il dépasse le budget.
            selected.add(i)
            budget -= len(p)
            continue
        if budget <= 0:
            break
        if len(p) > budget:
            continue  # ne rentre plus dans ce qu'il reste de budget, essayer le suivant
        selected.add(i)
        budget -= len(p)

    ordered = [paragraphs[i] for i in sorted(selected)]
    return "\n\n".join(ordered)[:max_chars]


# --- Appel LLM groupé par document --------------------------------------------------------------

def _document_item_model(field_ids: tuple[str, ...]) -> type[BaseModel]:
    from typing import Literal

    return create_model(
        "ExtractionDocumentItem",
        field_id=(Literal[field_ids], ...),
        found=(bool, ...),
        value=(str, ...),
        confidence=(float, ...),
        justification=(str, ...),
        citation=(str, ...),
        __validators__={"_clamp_confidence": confidence_validator()},
    )


def _document_response_model(field_ids: tuple[str, ...]) -> type[BaseModel]:
    item_model = _document_item_model(field_ids)
    return create_model("ExtractionDocumentDecision", items=(list[item_model], ...))


_DOCUMENT_SYSTEM_PROMPT = """Tu es un assistant expert en extraction de données depuis des \
dossiers de consultation des entreprises (DCE) pour l'underwriting assurance construction \
(SMABTP). Ta tâche : extraire, en une seule réponse, la valeur de PLUSIEURS données précises \
depuis un même document, chacune avec une preuve.

Règles impératives :
- Réponds avec EXACTEMENT une décision par donnée demandée, en reprenant son `field_id` tel quel.
- Ne renvoie found=true pour une donnée que si sa valeur est explicitement présente dans le \
passage fourni.
- Cite le passage exact qui justifie chaque valeur (`citation`) — jamais une paraphrase.
- Chaque valeur (`value`) doit être normalisée et concise (ex. une date JJ/MM/AAAA, un montant \
avec son unité) — jamais une phrase entière.
- Si une donnée n'est pas dans ce document, réponds pour elle found=false, value="", citation="" \
— indépendamment des autres données demandées.
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée), \
indépendamment pour chaque donnée.
"""


def _build_document_user_prompt(*, doc: DocumentSignal, fields: list[ExtractionField], excerpt: str) -> str:
    fields_desc = "\n".join(
        f'- field_id="{f.id}" : {f.libelle}' + (f" (format attendu : {f.resultat_attendu})" if f.resultat_attendu else "")
        for f in fields
    )
    return f"""Document analysé : {doc.filename}

Données recherchées dans CE document ({len(fields)}) :
{fields_desc}

Extrait de contenu (texte natif ou OCR, passages les plus pertinents sélectionnés) :
---
{excerpt}
---

Pour CHAQUE donnée ci-dessus, réponds avec son field_id exact, found=true/false, la valeur \
normalisée, une confiance entre 0 et 1, une justification concise, et la citation exacte du \
passage probant (chaînes vides si absente)."""


def analyze_document(doc: DocumentSignal, fields: list[ExtractionField]) -> DocumentExtractionResult:
    """Un seul appel LLM structuré demandant TOUTES les valeurs de `fields` pour ce document."""
    if not fields:
        return DocumentExtractionResult(document_id=doc.document_id)
    if not doc.content_excerpt:
        # OCR différé (§5 OPTIMISATION.md) : ni texte natif ni OCR à la demande n'ont rien donné
        # (page réellement vide, échec d'OCR) — rien à soumettre au LLM.
        return DocumentExtractionResult(document_id=doc.document_id)

    field_ids = tuple(f.id for f in fields)
    response_model = _document_response_model(field_ids)
    excerpt = _select_relevant_excerpt(doc, fields)

    try:
        decision, api_model_name = call_structured_chat(
            system_prompt=_DOCUMENT_SYSTEM_PROMPT,
            user_prompt=_build_document_user_prompt(doc=doc, fields=fields, excerpt=excerpt),
            response_model=response_model,
            what=f"extraction de {len(fields)} champ(s) sur {doc.filename}",
        )
    except Exception as exc:
        logger.exception("Échec de l'extraction groupée pour %s (%d champs)", doc.document_id, len(fields))
        return DocumentExtractionResult(document_id=doc.document_id, error=str(exc))

    decisions = {item.field_id: item for item in decision.items}
    return DocumentExtractionResult(document_id=doc.document_id, decisions=decisions, model_name=api_model_name)


# --- Résolution par champ à partir des appels déjà passés ---------------------------------------

def _sequential_resolved_outcome(
    doc: DocumentSignal, decision: Any, model_name: str | None, *, match_layer: str
) -> ExtractionOutcome:
    return ExtractionOutcome(
        match_layer=match_layer,
        value=decision.value,
        confidence=decision.confidence,
        justification=decision.justification,
        citation=decision.citation,
        sources=[
            {"document_id": doc.document_id, "filename": doc.filename, "value": decision.value, "confidence": decision.confidence}
        ],
        cross_check_status=CrossCheckStatus.NOT_APPLICABLE.value,
        model_name=model_name,
        model_version=get_models_config()["llm"]["model"],
        error=None,
    )


def _reconcile_cross_check(found: list[tuple[DocumentSignal, Any, str | None]]) -> ExtractionOutcome:
    """Comparaison programmatique des valeurs obtenues indépendamment sur chaque document de
    référence (§6.3 : "croiser RC + CCAP + CCTP et signaler les incohérences")."""
    sources = [
        {"document_id": doc.document_id, "filename": doc.filename, "value": d.value, "confidence": d.confidence}
        for doc, d, _ in found
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


def resolve_field(
    extraction_field: ExtractionField,
    *,
    candidates: list[DocumentSignal],
    results_by_document: dict[str, DocumentExtractionResult],
    match_layer: str,
    cross_check_required: bool,
    max_cross_check_sources: int = 2,
) -> ExtractionOutcome | None:
    """Dérive la décision finale d'un champ à partir des appels par document déjà effectués
    (`results_by_document`), en respectant l'ordre de priorité de `candidates`. Retourne None si
    rien n'est confirmé et qu'aucune erreur n'est survenue (couche suivante à essayer)."""
    found: list[tuple[DocumentSignal, Any, str | None]] = []
    any_error = False

    for doc in candidates:
        result = results_by_document.get(doc.document_id)
        if result is None:
            continue
        if result.error:
            any_error = True
            continue
        decision = result.decisions.get(extraction_field.id)
        if decision is not None and decision.found:
            found.append((doc, decision, result.model_name))
            if not cross_check_required:
                break  # premier document confirmant suffit hors recoupement

    if found:
        if cross_check_required:
            return _reconcile_cross_check(found[:max_cross_check_sources])
        doc, decision, model_name = found[0]
        return _sequential_resolved_outcome(doc, decision, model_name, match_layer=match_layer)

    if any_error:
        return ExtractionOutcome(
            match_layer=match_layer,
            value=None,
            confidence=None,
            justification="Candidat(s) trouvé(s) mais échec d'au moins un appel LLM.",
            citation=None,
            sources=[],
            cross_check_status=None,
            model_name=None,
            model_version=None,
            error="Échec de l'appel LLM sur au moins un document candidat.",
        )
    return None


def absent_outcome(reason: str) -> ExtractionOutcome:
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


# --- Synthèse textuelle (vision globale du dossier) ---------------------------------------------
#
# Un seul appel LLM, en fin de pipeline, à partir des valeurs DÉJÀ résolues (jamais une relecture
# des documents bruts) : pas de notion de recoupement ici (les valeurs sources sont déjà
# tranchées), donc aucune des complications du modèle par-document/cross-check ci-dessus.

@dataclass
class SynthesisOutcome:
    text: str
    model_name: str | None


class _SynthesisResponse(BaseModel):
    synthese: str


_SYNTHESIS_SYSTEM_PROMPT = """Tu es un assistant expert en assurance construction (SMABTP). \
À partir de données déjà extraites et validées d'un dossier de consultation des entreprises \
(DCE), rédige une courte synthèse textuelle donnant une vision globale du projet.

Règles impératives :
- 2 à 4 phrases, en français, style neutre et factuel.
- N'utilise QUE les données fournies ci-dessous — n'invente et ne suppose jamais une donnée \
absente de la liste.
- S'il n'y a que peu de données, rédige une synthèse plus courte plutôt que de combler les \
manques.
"""


def _build_synthesis_prompt(field_values: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"- {libelle} : {value}" for libelle, value in field_values)
    return f"""Données extraites de ce dossier :
{lines}

Rédige la synthèse."""


def generate_synthesis(field_values: list[tuple[str, str]]) -> SynthesisOutcome | None:
    """`field_values` : paires (libellé, valeur finale) des champs trouvés — voir
    `ensure_results_initialized`/`list_extraction_results` côté pipeline. Retourne None si
    aucune donnée n'est disponible, ou si l'appel LLM échoue (best-effort, ne bloque jamais la
    validation du checkpoint étape 3)."""
    if not field_values:
        return None
    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=_build_synthesis_prompt(field_values),
            response_model=_SynthesisResponse,
            what="synthèse textuelle du dossier",
        )
    except Exception:
        logger.exception("Échec de la génération de la synthèse textuelle du dossier")
        return None
    return SynthesisOutcome(text=parsed.synthese, model_name=api_model_name)
