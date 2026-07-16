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
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.completeness.pieces_checklist import Piece
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat
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


class _CompletenessDecision(BaseModel):
    presence: Literal["present", "partial", "absent"]
    confidence: float
    justification: str
    citation: str


def _score_candidate(doc: DocumentSignal, piece: Piece) -> int:
    return sum(1 for p in piece.indices if p.search(doc.content_excerpt))


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


_SYSTEM_PROMPT = """Tu es un assistant expert en analyse de complétude de dossiers de \
consultation des entreprises (DCE) pour l'underwriting assurance construction (SMABTP). Ta \
tâche : déterminer si un passage d'un document confirme la présence d'une pièce attendue, \
même noyée dans un document plus large (ex. une attestation d'assurance incluse dans un \
marché signé).

Règles impératives :
- Ne confirme la présence ("present") que si le passage fourni contient réellement une preuve claire.
- Si le passage évoque le sujet sans être une preuve suffisante, réponds "partial".
- Si le passage ne concerne pas la pièce recherchée, réponds "absent".
- Cite le passage exact qui justifie ta décision (`citation`) — jamais une paraphrase.
- La confiance doit refléter honnêtement ta certitude (1.0 = certain, 0.0 = aucune idée).
"""


def _build_user_prompt(*, piece: Piece, doc: DocumentSignal) -> str:
    excerpt = doc.content_excerpt[:CONTENT_EXCERPT_MAX_CHARS]
    aliases = ", ".join(piece.alias) if piece.alias else "(aucun)"
    return f"""Pièce recherchée : {piece.libelle}
Alias/synonymes : {aliases}

Document candidat : {doc.filename}
Extrait de contenu (texte natif ou OCR, tronqué) :
---
{excerpt}
---

Cette pièce est-elle réellement présente dans ce document ? Réponds present/partial/absent, \
une confiance entre 0 et 1, une justification concise, et la citation exacte du passage \
probant (chaîne vide si absent)."""


def _llm_certainty(llm_confidence: float, ocr_confidence: float | None, thresholds: dict[str, float]) -> str:
    if llm_confidence < 0.5:
        return Certainty.A_VERIFIER.value
    ocr_ok = ocr_confidence is None or ocr_confidence >= thresholds["min_ocr_confidence_for_certain"]
    if ocr_ok and llm_confidence >= thresholds["min_llm_confidence_for_certain"]:
        return Certainty.CERTAIN.value
    return Certainty.PROBABLE.value


def analyze_piece(
    *,
    piece: Piece,
    documents: list[DocumentSignal],
    all_lots: list[str] | None = None,
) -> CompletenessOutcome:
    thresholds = get_models_config()["completeness"]
    all_lots = all_lots or []

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

    candidates = sorted(
        (d for d in documents if d.content_excerpt and _score_candidate(d, piece) > 0),
        key=lambda d: _score_candidate(d, piece),
        reverse=True,
    )[:MAX_LLM_CANDIDATES]

    if not candidates:
        return _absent_outcome(
            piece, reason="Aucune mention trouvée dans le contenu des documents analysés."
        )

    last_error: str | None = None
    for doc in candidates:
        try:
            decision, api_model_name = call_structured_chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(piece=piece, doc=doc),
                response_model=_CompletenessDecision,
                what=f"complétude « {piece.libelle} » sur {doc.filename}",
            )
        except Exception as exc:
            logger.exception(
                "Échec de la vérification LLM de complétude pour %s / %s", piece.id, doc.document_id
            )
            last_error = str(exc)
            continue

        if decision.presence == "absent":
            continue

        certainty = _llm_certainty(decision.confidence, doc.ocr_confidence, thresholds)
        return CompletenessOutcome(
            match_layer=MatchLayer.LLM.value,
            presence=decision.presence,
            certainty=certainty,
            confidence=decision.confidence,
            justification=f"{decision.justification} — « {decision.citation} » ({doc.filename})",
            matched_document_ids=[doc.document_id],
            matched_lots=None,
            model_name=api_model_name,
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
            matched_document_ids=[d.document_id for d in candidates],
            matched_lots=None,
            model_name=None,
            model_version=None,
            error=last_error,
        )

    return _absent_outcome(
        piece,
        reason=(
            f"{len(candidates)} document(s) contenaient les mots-clés recherchés mais le LLM n'a "
            "confirmé la présence dans aucun d'eux."
        ),
    )
