"""Moteur de génération de la synthèse projet (Phase 1 du protocole d'analyse).

Contrairement à `app/extraction/engine.py` (un champ = une valeur atomique courte), ici un
THÈME = une section narrative (prose, tableau ou liste), obtenue en un seul appel LLM par thème
qui relit directement le texte complet des documents pivots concernés (`config/taxonomy.yaml`
`is_pivot` + `pivot_categories` du thème) — jamais une simple reformulation des valeurs déjà
extraites, sauf pour les thèmes `source: extraction_fields` (identité de l'opération), qui sont
de simples reformatages déterministes, sans appel LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.classify.taxonomy import Taxonomy
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat
from app.synthesis.schema import SynthesisSchema, SynthesisTopic

logger = logging.getLogger(__name__)

# Budget de contexte PAR THÈME (potentiellement plusieurs documents pivots) — plus généreux que
# le budget par appel de l'extraction (§extraction/engine.py DOCUMENT_EXCERPT_MAX_CHARS) car un
# thème comme "récit du sol" ou "synthèse RICT" a besoin du texte complet du document, pas d'un
# extrait scoré par mots-clés.
SYNTHESIS_TOTAL_CONTEXT_MAX_CHARS = 40_000
SYNTHESIS_PER_DOCUMENT_MAX_CHARS = 16_000

# (libellé, valeur finale) d'un champ déjà résolu à l'étape 3, indexé par field_id.
FieldValues = dict[str, tuple[str, str]]


@dataclass(frozen=True)
class TopicOutcome:
    topic_id: str
    content_md: str | None
    model_name: str | None
    error: str | None
    documents_used: list[str] = field(default_factory=list)


class _TopicResponse(BaseModel):
    contenu: str


_TOPIC_SYSTEM_PROMPT = """Tu es un expert en audit technique et souscription assurance \
construction (SMABTP), en train de rédiger la Phase 1 (synthèse projet) d'un rapport d'analyse \
de dossier de consultation des entreprises (DCE).

Règles impératives :
- N'utilise QUE les informations présentes dans les documents fournis ci-dessous et les données \
déjà validées — n'invente jamais une donnée absente.
- Si une information demandée est absente des documents fournis, dis-le explicitement \
("non précisé dans les documents fournis") plutôt que de l'omettre silencieusement ou de \
l'inventer.
- Utilise systématiquement des citations : après chaque donnée factuelle, indique entre \
parenthèses le document source (ex. "(Source : RICT SOCOTEC)").
- Respecte strictement le format demandé (prose, tableau Markdown, ou liste à puces).
- Réponds uniquement avec le contenu Markdown de cette section, sans reprendre le titre de la \
section (déjà affiché séparément dans le rapport)."""


def select_topic_documents(topic: SynthesisTopic, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Documents pivots pour ce thème, dans l'ordre de priorité de `pivot_categories` — même
    logique que `reference_candidates` côté extraction, gardée indépendante ici (chaque moteur
    reste autonome, cf. app/pipeline_support.py)."""
    selected: list[DocumentSignal] = []
    for category in topic.pivot_categories:
        selected.extend(d for d in documents if d.final_category == category and d.content_excerpt)
    return selected


def _build_documents_context(
    documents: list[DocumentSignal],
    *,
    total_budget: int = SYNTHESIS_TOTAL_CONTEXT_MAX_CHARS,
    per_document_budget: int = SYNTHESIS_PER_DOCUMENT_MAX_CHARS,
) -> str:
    blocks: list[str] = []
    remaining = total_budget
    for doc in documents:
        if remaining <= 0:
            break
        cap = min(per_document_budget, remaining)
        excerpt = doc.content_excerpt[:cap]
        blocks.append(f"### Document : {doc.filename} (catégorie : {doc.final_category or 'inconnue'})\n{excerpt}")
        remaining -= len(excerpt)
    return "\n\n".join(blocks)


def _format_extraction_fields_topic(topic: SynthesisTopic, field_values: FieldValues) -> str:
    lines = []
    for field_id in topic.extraction_field_ids:
        pair = field_values.get(field_id)
        if pair is None or not pair[1]:
            continue
        libelle, value = pair
        lines.append(f"**{libelle} :** {value}")
    if not lines:
        return "_Aucune donnée disponible (étape 3)._"
    return "\n\n".join(lines)


def _format_grounding_block(topic: SynthesisTopic, field_values: FieldValues) -> str:
    lines = []
    for field_id in topic.grounding_field_ids:
        pair = field_values.get(field_id)
        if pair is None or not pair[1]:
            continue
        libelle, value = pair
        lines.append(f"- {libelle} : {value}")
    if not lines:
        return ""
    return "Données déjà validées à l'étape 3 (base à ne pas contredire sans le signaler) :\n" + "\n".join(lines)


def _build_topic_user_prompt(*, topic: SynthesisTopic, grounding: str, context: str) -> str:
    grounding_block = f"\n{grounding}\n" if grounding else ""
    return f"""Thème à développer : {topic.titre}
Format attendu : {topic.format}

Consigne de rédaction :
{topic.instructions}
{grounding_block}
Documents pivots fournis (texte natif ou OCR) :
---
{context}
---

Rédige le contenu Markdown de cette section."""


def _no_documents_message(topic: SynthesisTopic) -> str:
    categories = ", ".join(topic.pivot_categories)
    return f"_Aucun document pivot trouvé pour ce thème ({categories}) — section non renseignée._"


def generate_topic(
    topic: SynthesisTopic, *, documents: list[DocumentSignal], field_values: FieldValues
) -> TopicOutcome:
    """Un seul appel LLM par thème (aucun pour `source: extraction_fields`, simple reformatage
    déterministe des valeurs déjà résolues à l'étape 3)."""
    if topic.source == "extraction_fields":
        content = _format_extraction_fields_topic(topic, field_values)
        return TopicOutcome(topic_id=topic.id, content_md=content, model_name=None, error=None)

    candidates = select_topic_documents(topic, documents)
    if not candidates:
        return TopicOutcome(topic_id=topic.id, content_md=_no_documents_message(topic), model_name=None, error=None)

    context = _build_documents_context(candidates)
    grounding = _format_grounding_block(topic, field_values)
    documents_used = [d.filename for d in candidates]

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_TOPIC_SYSTEM_PROMPT,
            user_prompt=_build_topic_user_prompt(topic=topic, grounding=grounding, context=context),
            response_model=_TopicResponse,
            what=f"synthèse projet — thème {topic.id}",
        )
    except Exception as exc:
        logger.exception("Échec de la génération du thème %s de la synthèse projet", topic.id)
        return TopicOutcome(
            topic_id=topic.id, content_md=None, model_name=None, error=str(exc), documents_used=documents_used
        )

    return TopicOutcome(
        topic_id=topic.id,
        content_md=parsed.contenu,
        model_name=api_model_name,
        error=None,
        documents_used=documents_used,
    )


def build_documents_cartography(documents: list[DocumentSignal], taxonomy: Taxonomy) -> str:
    """Section "Phase 0" du rapport (§00_PROTOCOLE.md, cartographie documentaire) : reformatage
    déterministe, sans appel LLM, des documents déjà classifiés à l'étape 1 — groupés par type de
    document (pas un tableau ligne par fichier, pour rester lisible sur un gros dossier)."""
    counts: dict[str, int] = {}
    for d in documents:
        if d.final_category:
            counts[d.final_category] = counts.get(d.final_category, 0) + 1
    if not counts:
        return "_Aucun document classifié pour ce dossier._"

    rows = [
        (category.label, counts[category.path], category.is_pivot)
        for category in taxonomy.categories
        if category.path in counts
    ]
    rows.sort(key=lambda r: (not r[2], -r[1]))

    lines = ["| Type de document | Nombre de fichiers | Document pivot ? |", "|---|---|---|"]
    lines += [f"| {label} | {count} | {'Oui' if is_pivot else 'Non'} |" for label, count, is_pivot in rows]
    return "\n".join(lines)


def assemble_report(
    outcomes: list[TopicOutcome], schema: SynthesisSchema, *, cartography_md: str | None = None
) -> str:
    sections: list[str] = ["# Synthèse projet — Phase 1"]
    if cartography_md:
        sections.append("## Cartographie des documents pivots\n\n" + cartography_md)

    by_id = {o.topic_id: o for o in outcomes}
    for topic in schema.topics:
        outcome = by_id.get(topic.id)
        if outcome is None:
            continue
        if outcome.error:
            body = f"_Section non générée (erreur : {outcome.error})._"
        else:
            body = outcome.content_md or "_Aucune donnée disponible._"
            if outcome.documents_used:
                body += "\n\n_Sources consultées : " + ", ".join(outcome.documents_used) + "_"
        sections.append(f"## {topic.titre}\n\n{body}")

    return "\n\n".join(sections) + "\n"
