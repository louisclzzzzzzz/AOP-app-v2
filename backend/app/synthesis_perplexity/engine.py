"""Moteur de génération de la synthèse projet (Phase 1 du protocole d'analyse) via Perplexity
Deep Research — variante expérimentale de `app/synthesis/engine.py` (Mistral, un appel LLM par
thème).

Ici un SEUL appel Deep Research reçoit tous les thèmes non-déterministes du protocole à la fois,
avec le texte complet de tous les documents pivots réunis : `sonar-deep-research` fait son propre
raisonnement multi-étapes et peut donc recouper les sources lui-même en un seul passage, plutôt
que de recevoir un sous-ensemble pré-sélectionné thème par thème (13 appels Deep Research
parallèles par dossier serait de toute façon irréaliste : plusieurs minutes par appel, quelques
requêtes/minute de quota — cf. `config/models.yaml` §perplexity).

Réutilise volontairement le schéma (`app/synthesis/schema.py`) et deux briques déterministes du
moteur Mistral (`_format_extraction_fields_topic`, `build_documents_cartography`) : ce sont de
simples reformatages sans appel LLM, indépendants du fournisseur, qu'il serait absurde de
dupliquer pour ce test comparatif.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.classify.taxonomy import Taxonomy
from app.ingestion.document_signal import DocumentSignal
from app.synthesis.engine import FieldValues, _format_extraction_fields_topic, build_documents_cartography
from app.synthesis.schema import SynthesisSchema, SynthesisTopic
from app.synthesis_perplexity.client import run_deep_research

logger = logging.getLogger(__name__)

# Budget de contexte total — première estimation prudente (même démarche itérative que
# `SYNTHESIS_TOTAL_CONTEXT_MAX_CHARS` côté Mistral, à ajuster une fois des runs réels observés).
# `sonar-deep-research` consomme lui-même du contexte pour ses propres étapes de recherche web
# internes, en plus du prompt fourni : marge volontairement large plutôt que calibrée à l'octet.
DOCUMENTS_TOTAL_CONTEXT_MAX_CHARS = 220_000
DOCUMENTS_PER_DOCUMENT_MAX_CHARS = 50_000


@dataclass(frozen=True)
class ProjectSynthesisResult:
    report_md: str
    model_name: str | None
    documents_used: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = """Tu es un expert en audit technique et souscription assurance construction \
(SMABTP), en train de rédiger la Phase 1 (synthèse projet) d'un rapport d'analyse de dossier de \
consultation des entreprises (DCE).

Règles impératives :
- Base-toi EN PRIORITÉ sur les documents internes fournis ci-dessous et sur les données déjà \
validées — n'invente jamais une donnée spécifique à ce projet qui n'y figure pas.
- Tu peux utiliser ta capacité de recherche web UNIQUEMENT pour vérifier un point technique \
générique (définition d'un sigle, d'une norme, d'une classification réglementaire) — jamais \
pour déduire une donnée propre à ce projet (adresse, montants, dates, avis du contrôleur, etc.).
- Si une information demandée est absente des documents fournis, dis-le explicitement \
("non précisé dans les documents fournis") plutôt que de l'omettre silencieusement ou de \
l'inventer.
- Cite systématiquement le document source de chaque donnée factuelle entre parenthèses juste \
après (ex. "(Source : RICT SOCOTEC)") — ces documents internes ne sont pas des pages web, tes \
citations web automatiques ne les couvrent pas.
- Rédige un unique document Markdown, avec une section "## {titre du thème}" par thème listé \
ci-dessous, DANS L'ORDRE donné, chacune respectant le format demandé (prose, tableau Markdown, \
ou liste à puces). Ne saute aucun thème, même si tu dois répondre "non précisé"."""


def _build_topics_brief(topics: list[SynthesisTopic]) -> str:
    blocks = []
    for topic in topics:
        pivots = ", ".join(topic.pivot_categories) if topic.pivot_categories else "aucun en particulier"
        blocks.append(
            f"### {topic.titre}\nFormat attendu : {topic.format}\nDocuments pivots visés : {pivots}\n"
            f"Consigne :\n{topic.instructions}"
        )
    return "\n\n".join(blocks)


def _select_pivot_documents(schema: SynthesisSchema, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Union dédupliquée des documents pivots visés par au moins un thème `source: documents` —
    contrairement à Mistral (une sélection par thème), un seul appel reçoit tout à la fois."""
    pivot_categories = {c for topic in schema.topics if topic.source == "documents" for c in topic.pivot_categories}
    seen: set[str] = set()
    selected: list[DocumentSignal] = []
    for doc in documents:
        if doc.final_category in pivot_categories and doc.content_excerpt and doc.document_id not in seen:
            selected.append(doc)
            seen.add(doc.document_id)
    return selected


def _build_documents_context(
    documents: list[DocumentSignal],
    *,
    total_budget: int = DOCUMENTS_TOTAL_CONTEXT_MAX_CHARS,
    per_document_budget: int = DOCUMENTS_PER_DOCUMENT_MAX_CHARS,
) -> tuple[str, list[str]]:
    blocks: list[str] = []
    included: list[str] = []
    remaining = total_budget
    for doc in documents:
        if remaining <= 0:
            break
        cap = min(per_document_budget, remaining)
        excerpt = doc.content_excerpt[:cap]
        blocks.append(f"### Document : {doc.filename} (catégorie : {doc.final_category or 'inconnue'})\n{excerpt}")
        included.append(doc.filename)
        remaining -= len(excerpt)
    return "\n\n".join(blocks), included


def _format_grounding_block(schema: SynthesisSchema, field_values: FieldValues) -> str:
    field_ids = {fid for topic in schema.topics for fid in topic.grounding_field_ids}
    lines = []
    for field_id in field_ids:
        pair = field_values.get(field_id)
        if pair is None or not pair[1]:
            continue
        libelle, value = pair
        lines.append(f"- {libelle} : {value}")
    if not lines:
        return ""
    return "Données déjà validées à l'étape 3 (base à ne pas contredire sans le signaler) :\n" + "\n".join(
        sorted(lines)
    )


def generate_project_synthesis(
    schema: SynthesisSchema,
    taxonomy: Taxonomy,
    *,
    documents: list[DocumentSignal],
    field_values: FieldValues,
) -> ProjectSynthesisResult:
    deterministic_topics = [t for t in schema.topics if t.source == "extraction_fields"]
    llm_topics = [t for t in schema.topics if t.source == "documents"]

    cartography_md = build_documents_cartography(documents, taxonomy)
    deterministic_sections = "\n\n".join(
        f"## {topic.titre}\n\n{_format_extraction_fields_topic(topic, field_values)}"
        for topic in deterministic_topics
    )

    candidates = _select_pivot_documents(schema, documents)
    if not candidates:
        report_md = "\n\n".join(
            [
                "# Synthèse projet — Phase 1 (Perplexity Deep Research)",
                f"## Cartographie des documents pivots\n\n{cartography_md}",
                deterministic_sections,
                "_Aucun document pivot trouvé pour les thèmes restants — section non générée._",
            ]
        ) + "\n"
        return ProjectSynthesisResult(report_md=report_md, model_name=None)

    context, documents_used = _build_documents_context(candidates)
    if len(documents_used) < len(candidates):
        logger.warning(
            "Synthèse projet (Perplexity) : %d document(s) pivot(s) candidat(s) mais seuls %d envoyés "
            "au modèle (budget de contexte atteint) : %s ignoré(s)",
            len(candidates),
            len(documents_used),
            [d.filename for d in candidates if d.filename not in documents_used],
        )

    grounding = _format_grounding_block(schema, field_values)
    grounding_block = f"\n{grounding}\n" if grounding else ""
    user_prompt = f"""Thèmes à développer (dans cet ordre, un "## titre" par thème) :

{_build_topics_brief(llm_topics)}
{grounding_block}
Documents pivots fournis (texte natif ou OCR) :
---
{context}
---

Rédige le rapport Markdown complet, un thème par section."""

    content, citations, model_name = run_deep_research(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        what="synthèse projet (Perplexity Deep Research)",
    )

    sections = [
        "# Synthèse projet — Phase 1 (Perplexity Deep Research)",
        f"## Cartographie des documents pivots\n\n{cartography_md}",
    ]
    if deterministic_sections:
        sections.append(deterministic_sections)
    sections.append(content)

    note = "_Sources consultées : " + ", ".join(documents_used) + "_"
    skipped = len(candidates) - len(documents_used)
    if skipped > 0:
        note += (
            f" _(+{skipped} document(s) pivot(s) supplémentaire(s) trouvé(s) mais non envoyé(s) au "
            "modèle — budget de contexte atteint)_"
        )
    sections.append(note)

    if citations:
        sections.append("## Sources web consultées par Perplexity\n\n" + "\n".join(f"- {url}" for url in citations))

    report_md = "\n\n".join(sections) + "\n"
    return ProjectSynthesisResult(
        report_md=report_md, model_name=model_name, documents_used=documents_used, citations=citations
    )
