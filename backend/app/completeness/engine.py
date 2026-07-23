"""Moteur de complétude à 3 couches (§5.3 du PLAN) :

1. Correspondance par fichier direct : un document déjà classifié (étape 1, validée par
   l'utilisateur au checkpoint) correspond à la catégorie attendue de la pièce.
2. Correspondance intra-document : recherche par mots-clés (`indices` de la pièce) dans le
   texte (OCR ou natif) de tous les documents analysables.
3. Vérification LLM sur les meilleurs candidats de la couche 2, qui tranche présent / partiel
   / absent et fournit une citation.

Une pièce trouvée en couche 1 n'a jamais besoin d'un appel LLM : la classification qui la
révèle a déjà été validée par un humain au checkpoint étape 1, aucun jugement supplémentaire
à faire. Une pièce sans correspondance en couche 1 ni en couche 2 est déclarée absente sans
appel LLM non plus (rien à vérifier).

La couche 3 regroupe les appels LLM PAR DOCUMENT CANDIDAT plutôt que par (pièce, document) :
un même document est souvent candidat pour plusieurs pièces à la fois (ex. un marché signé qui
peut couvrir plusieurs attestations), donc un seul appel structuré lui demande de trancher pour
TOUTES les pièces qu'il pourrait couvrir en une fois — symétrique à `extraction.engine`
(§4 AUDIT_BACKEND.md, qui notait que la complétude était le seul des 3 pipelines LLM non
batché)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, create_model

from app.classify.taxonomy import strip_accents
from app.completeness.pieces_checklist import Piece
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat
from app.mistral.validation import confidence_validator
from app.settings import get_models_config
from app.store.models import Certainty, MatchLayer, Presence

logger = logging.getLogger(__name__)

CONTENT_EXCERPT_MAX_CHARS = 4000
MAX_LLM_CANDIDATES = 3


@dataclass
class CompletenessOutcome:
    match_layer: str
    presence: str
    certainty: str | None
    confidence: float | None
    justification: str
    matched_document_ids: list[str]
    matched_lots: dict[str, Any] | None
    model_name: str | None
    model_version: str | None
    error: str | None


class _CompletenessDocumentItem(BaseModel):
    piece_id: str
    presence: Literal["present", "partial", "absent"]
    confidence: float
    justification: str
    citation: str

    _clamp_confidence = confidence_validator()


@dataclass
class DocumentCompletenessResult:
    """Résultat d'UN appel LLM groupé sur un document candidat, couvrant plusieurs pièces."""

    document_id: str
    decisions: dict[str, _CompletenessDocumentItem] = field(default_factory=dict)
    model_name: str | None = None
    error: str | None = None


def _score_candidate(doc: DocumentSignal, piece: Piece) -> int:
    content = strip_accents(doc.content_excerpt)
    return sum(1 for p in piece.indices if p.search(content))


def _certainty_for_file_match(confidences: list[float | None], thresholds: dict[str, float]) -> str:
    known = [c for c in confidences if c is not None]
    if known and min(known) < thresholds["min_llm_confidence_for_certain"]:
        return Certainty.PROBABLE.value
    return Certainty.CERTAIN.value


def _absent_outcome(piece: Piece, *, reason: str) -> CompletenessOutcome:
    # Absence confiante seulement quand la pièce n'a PAS pu se cacher ailleurs : la
    # classification étape 1 (déjà validée par un humain) a couvert tout le dossier.
    certainty = (
        Certainty.CERTAIN.value
        if piece.categorie_attendue and not piece.peut_etre_inclus_dans_autre
        else Certainty.PROBABLE.value
    )
    return CompletenessOutcome(
        match_layer=MatchLayer.NONE.value,
        presence=Presence.ABSENT.value,
        certainty=certainty,
        confidence=None,
        justification=reason,
        matched_document_ids=[],
        matched_lots=None,
        model_name=None,
        model_version=None,
        error=None,
    )


def _file_match_outcome(
    piece: Piece,
    matched: list[DocumentSignal],
    *,
    all_lots: list[str],
    thresholds: dict[str, float],
) -> CompletenessOutcome:
    confidences = [d.classification_confidence for d in matched]
    certainty = _certainty_for_file_match(confidences, thresholds)
    matched_lots: dict[str, Any] | None = None

    if piece.par_lot and all_lots:
        covered = sorted({d.final_lot for d in matched if d.final_lot})
        missing = [lot for lot in all_lots if lot not in covered]
        matched_lots = {"covered": covered, "missing": missing}
        if missing:
            return CompletenessOutcome(
                match_layer=MatchLayer.FILE.value,
                presence=Presence.PARTIAL.value,
                certainty=Certainty.PROBABLE.value,
                confidence=None,
                justification=(
                    f"Présente pour le(s) lot(s) {', '.join(covered) or '—'} mais manquante pour "
                    f"le(s) lot(s) {', '.join(missing)}."
                ),
                matched_document_ids=[d.document_id for d in matched],
                matched_lots=matched_lots,
                model_name=None,
                model_version=None,
                error=None,
            )

    return CompletenessOutcome(
        match_layer=MatchLayer.FILE.value,
        presence=Presence.PRESENT.value,
        certainty=certainty,
        confidence=None,
        justification=f"Document classé directement dans {piece.categorie_attendue}.",
        matched_document_ids=[d.document_id for d in matched],
        matched_lots=matched_lots,
        model_name=None,
        model_version=None,
        error=None,
    )


def layer2_candidates(piece: Piece, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Couche 2 : documents candidats scorés par mots-clés, les plus prometteurs d'abord."""
    return sorted(
        (d for d in documents if d.content_excerpt and _score_candidate(d, piece) > 0),
        key=lambda d: _score_candidate(d, piece),
        reverse=True,
    )[:MAX_LLM_CANDIDATES]


def resolve_without_llm(
    piece: Piece,
    documents: list[DocumentSignal],
    *,
    all_lots: list[str],
    thresholds: dict[str, float],
) -> CompletenessOutcome | None:
    """Résout une pièce sans appel LLM quand c'est possible (couche 1, ou absence certaine
    faute de pouvoir se cacher ailleurs). Retourne `None` si une recherche/vérification par
    contenu (couches 2/3) est nécessaire."""
    if piece.categorie_attendue:
        file_matches = [d for d in documents if d.final_category == piece.categorie_attendue]
        if file_matches:
            return _file_match_outcome(piece, file_matches, all_lots=all_lots, thresholds=thresholds)

    if not piece.peut_etre_inclus_dans_autre:
        return _absent_outcome(
            piece,
            reason=(
                f"Aucun document classé dans {piece.categorie_attendue} et cette pièce n'est "
                "pas recherchée ailleurs (peut_etre_inclus_dans_autre=false)."
            ),
        )
    return None


@lru_cache
def _document_item_model(piece_ids: tuple[str, ...]) -> type[BaseModel]:
    from typing import Literal as _Literal

    return create_model(
        "CompletenessDocumentItem",
        piece_id=(_Literal[piece_ids], ...),
        presence=(_Literal["present", "partial", "absent"], ...),
        confidence=(float, ...),
        justification=(str, ...),
        citation=(str, ...),
        __validators__={"_clamp_confidence": confidence_validator()},
    )


def _document_response_model(piece_ids: tuple[str, ...]) -> type[BaseModel]:
    item_model = _document_item_model(piece_ids)
    return create_model("CompletenessDocumentDecision", items=(list[item_model], ...))


_DOCUMENT_SYSTEM_PROMPT = """Tu es un assistant expert en analyse de complétude de dossiers de \
consultation des entreprises (DCE) pour l'underwriting assurance construction (SMABTP). Ta \
tâche : déterminer, en une seule réponse, si CE document confirme la présence de PLUSIEURS \
pièces attendues, chacune éventuellement noyée dans un document plus large (ex. une attestation \
d'assurance incluse dans un marché signé).

Règles impératives :
- Réponds avec EXACTEMENT une décision par pièce demandée, en reprenant son `piece_id` tel quel.
- Ne confirme la présence ("present") que si le passage fourni contient réellement une preuve \
claire de CETTE pièce précise — indépendamment des autres pièces demandées.
- Si le passage évoque le sujet sans être une preuve suffisante, réponds "partial".
- Si le passage ne concerne pas la pièce recherchée, réponds "absent".
- Cite le passage exact qui justifie chaque décision (`citation`) — jamais une paraphrase \
(chaîne vide si absent).
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée), \
indépendamment pour chaque pièce.
"""


def _build_document_user_prompt(*, doc: DocumentSignal, pieces: list[Piece]) -> str:
    pieces_desc = "\n".join(
        f'- piece_id="{p.id}" : {p.libelle}'
        + (f" (alias : {', '.join(p.alias)})" if p.alias else "")
        for p in pieces
    )
    excerpt = doc.content_excerpt[:CONTENT_EXCERPT_MAX_CHARS]
    return f"""Document candidat : {doc.filename}

Pièces recherchées dans CE document ({len(pieces)}) :
{pieces_desc}

Extrait de contenu (texte natif ou OCR, tronqué) :
---
{excerpt}
---

Pour CHAQUE pièce ci-dessus, réponds avec son piece_id exact, present/partial/absent, une \
confiance entre 0 et 1, une justification concise, et la citation exacte du passage probant \
(chaîne vide si absent)."""


def analyze_document_for_pieces(doc: DocumentSignal, pieces: list[Piece]) -> DocumentCompletenessResult:
    """Un seul appel LLM structuré demandant, pour CE document candidat, la présence de
    PLUSIEURS pièces à la fois — remplace un appel par (pièce, document) par un appel par
    document, symétrique à `extraction.engine.analyze_document` (§4 AUDIT_BACKEND.md)."""
    if not pieces:
        return DocumentCompletenessResult(document_id=doc.document_id)

    piece_ids = tuple(p.id for p in pieces)
    response_model = _document_response_model(piece_ids)

    try:
        decision, api_model_name = call_structured_chat(
            system_prompt=_DOCUMENT_SYSTEM_PROMPT,
            user_prompt=_build_document_user_prompt(doc=doc, pieces=pieces),
            response_model=response_model,
            what=f"complétude de {len(pieces)} pièce(s) sur {doc.filename}",
        )
    except Exception as exc:
        logger.exception(
            "Échec de la vérification LLM de complétude groupée pour %s (%d pièces)",
            doc.document_id,
            len(pieces),
        )
        return DocumentCompletenessResult(document_id=doc.document_id, error=str(exc))

    decisions = {item.piece_id: item for item in decision.items}
    return DocumentCompletenessResult(document_id=doc.document_id, decisions=decisions, model_name=api_model_name)


def _llm_certainty(llm_confidence: float, ocr_confidence: float | None, thresholds: dict[str, float]) -> str:
    if llm_confidence < 0.5:
        return Certainty.A_VERIFIER.value
    ocr_ok = ocr_confidence is None or ocr_confidence >= thresholds["min_ocr_confidence_for_certain"]
    if ocr_ok and llm_confidence >= thresholds["min_llm_confidence_for_certain"]:
        return Certainty.CERTAIN.value
    return Certainty.PROBABLE.value


def _resolve_llm_outcome(
    piece: Piece,
    candidate_doc_ids: list[str],
    results_by_doc_id: dict[str, DocumentCompletenessResult],
    doc_by_id: dict[str, DocumentSignal],
    thresholds: dict[str, float],
) -> CompletenessOutcome:
    """Reconstitue le résultat d'une pièce à partir des appels groupés déjà effectués sur ses
    documents candidats (dans le même ordre de score décroissant qu'avant le batching) : le
    premier candidat confirmé (present/partial) l'emporte."""
    last_error: str | None = None
    for doc_id in candidate_doc_ids:
        result = results_by_doc_id[doc_id]
        if result.error:
            last_error = result.error
            continue
        item = result.decisions.get(piece.id)
        if item is None or item.presence == "absent":
            continue

        doc = doc_by_id[doc_id]
        certainty = _llm_certainty(item.confidence, doc.ocr_confidence, thresholds)
        return CompletenessOutcome(
            match_layer=MatchLayer.LLM.value,
            presence=item.presence,
            certainty=certainty,
            confidence=item.confidence,
            justification=f"{item.justification} — « {item.citation} » ({doc.filename})",
            matched_document_ids=[doc.document_id],
            matched_lots=None,
            model_name=result.model_name,
            model_version=get_models_config()["llm"]["model"],
            error=None,
        )

    if last_error:
        return CompletenessOutcome(
            match_layer=MatchLayer.CONTENT.value,
            presence=Presence.ABSENT.value,
            certainty=Certainty.A_VERIFIER.value,
            confidence=None,
            justification="Candidats trouvés par mots-clés mais échec de la vérification LLM.",
            matched_document_ids=candidate_doc_ids,
            matched_lots=None,
            model_name=None,
            model_version=None,
            error=last_error,
        )

    return _absent_outcome(
        piece,
        reason=(
            f"{len(candidate_doc_ids)} document(s) contenaient les mots-clés recherchés mais le "
            "LLM n'a confirmé la présence dans aucun d'eux."
        ),
    )


@dataclass
class CompletenessPlan:
    """Résultat de la planification (couches 1/2, aucun appel LLM) : ce qui est déjà résolu, et
    les appels LLM par document candidat restant à effectuer pour le reste (couche 3)."""

    resolved: dict[str, CompletenessOutcome]
    piece_candidate_ids: dict[str, list[str]]
    doc_calls: list[tuple[DocumentSignal, list[Piece]]]
    pieces_by_id: dict[str, Piece]
    doc_by_id: dict[str, DocumentSignal]
    thresholds: dict[str, float]


def plan_completeness(
    pieces: list[Piece], documents: list[DocumentSignal], all_lots: list[str] | None = None
) -> CompletenessPlan:
    """Couches 1/2 (sans LLM) : résout ce qui peut l'être, et regroupe par document candidat les
    vérifications LLM restantes (couche 3) — un appel par document couvrant plusieurs pièces à
    la fois plutôt qu'un appel par (pièce, document) (§4 AUDIT_BACKEND.md). Ne fait aucun appel
    LLM elle-même : à l'appelant d'exécuter `doc_calls` (via `analyze_document_for_pieces`), puis
    de reconstituer le résultat avec `finalize_completeness`."""
    thresholds = get_models_config()["completeness"]
    all_lots = all_lots or []

    resolved: dict[str, CompletenessOutcome] = {}
    needs_llm: dict[str, Piece] = {}
    for piece in pieces:
        outcome = resolve_without_llm(piece, documents, all_lots=all_lots, thresholds=thresholds)
        if outcome is not None:
            resolved[piece.id] = outcome
        else:
            needs_llm[piece.id] = piece

    doc_by_id = {d.document_id: d for d in documents}
    piece_candidate_ids: dict[str, list[str]] = {}
    doc_to_pieces: dict[str, list[Piece]] = {}
    for piece in needs_llm.values():
        candidates = layer2_candidates(piece, documents)
        if not candidates:
            resolved[piece.id] = _absent_outcome(
                piece, reason="Aucune mention trouvée dans le contenu des documents analysés."
            )
            continue
        piece_candidate_ids[piece.id] = [d.document_id for d in candidates]
        for d in candidates:
            doc_to_pieces.setdefault(d.document_id, []).append(piece)

    doc_calls = [(doc_by_id[doc_id], pieces_for_doc) for doc_id, pieces_for_doc in doc_to_pieces.items()]
    return CompletenessPlan(
        resolved=resolved,
        piece_candidate_ids=piece_candidate_ids,
        doc_calls=doc_calls,
        pieces_by_id=needs_llm,
        doc_by_id=doc_by_id,
        thresholds=thresholds,
    )


def finalize_completeness(
    plan: CompletenessPlan, results_by_doc_id: dict[str, DocumentCompletenessResult]
) -> dict[str, CompletenessOutcome]:
    """Reconstitue le résultat de chaque pièce restée en attente de LLM à partir des appels
    groupés déjà exécutés (`results_by_doc_id`, indexé par `document_id`, un par appel de
    `plan.doc_calls`)."""
    outcomes = dict(plan.resolved)
    for piece_id, candidate_doc_ids in plan.piece_candidate_ids.items():
        outcomes[piece_id] = _resolve_llm_outcome(
            plan.pieces_by_id[piece_id], candidate_doc_ids, results_by_doc_id, plan.doc_by_id, plan.thresholds
        )
    return outcomes


def analyze_pieces(
    *, pieces: list[Piece], documents: list[DocumentSignal], all_lots: list[str] | None = None
) -> dict[str, CompletenessOutcome]:
    """Version tout-en-un, synchrone, de `plan_completeness` + `finalize_completeness` :
    pratique pour les tests et tout appelant qui n'a pas besoin de notifier une progression
    pendant la phase LLM (potentiellement la plus longue) — sinon préférer piloter
    `plan_completeness`/`analyze_document_for_pieces`/`finalize_completeness` soi-même, comme le
    fait `completeness.pipeline.run_completeness_pipeline`."""
    plan = plan_completeness(pieces, documents, all_lots)
    results_by_doc_id = {
        doc.document_id: analyze_document_for_pieces(doc, pieces_for_doc) for doc, pieces_for_doc in plan.doc_calls
    }
    return finalize_completeness(plan, results_by_doc_id)
