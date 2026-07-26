"""Moteur de l'audit des risques (Phase 2 du protocole d'analyse).

Contrairement à `app/synthesis/engine.py` (Phase 1, un THÈME = une section narrative rédigée en
prose/tableau/liste), ici une SECTION d'ouvrage (A→G) produit, via un unique appel LLM, une LISTE
de risques structurés — chacun avec un statut (🔴/🟠/🟢), un exposé, une analyse d'expert
référencée aux DTU/Eurocodes, un impact assurabilité et une recommandation de levée de doute.

Les risques de toutes les sections sont ensuite assemblés en deux vues (§assemble_report) : (1) un
tableau récapitulatif synoptique, (2) l'analyse détaillée section par section — la structure
imposée par le protocole (« FORMAT DE RÉPONSE IMPÉRATIF » + prompt 7 « Rapport final »).

Sélection des documents : comme la Phase 1, on relit le texte complet des documents pivots de la
section (`pivot_categories`), mais les catégories « par lot » (TECH/CCTP TRAVAUX, TECH/DPGF) sont
restreintes aux lots pertinents via `cctp_keywords` — un audit de l'étanchéité n'a pas besoin des
25 CCTP du dossier, seulement du/des CCTP étanchéité/couverture (sinon le budget de contexte est
saturé par des lots hors sujet et l'analyse diluée).
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.audit.georisques import GeorisquesReport, format_aspects_grounding, format_full_md
from app.audit.schema import LOT_FILTERED_CATEGORIES, AuditSchema, AuditSection
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_structured_chat

logger = logging.getLogger(__name__)

# Même logique de budget que la Phase 1 (§synthesis/engine.py) : calibré sur la fenêtre de
# ~128k tokens de mistral-large, ~3 caractères/token sur du technique français. Chaque section
# n'envoie que ses documents pivots FILTRÉS (cctp_keywords), donc en pratique bien moins que le
# plafond — le plafond n'est là que comme garde-fou contre un dossier au corpus aberrant.
AUDIT_TOTAL_CONTEXT_MAX_CHARS = 280_000
AUDIT_PER_DOCUMENT_MAX_CHARS = 60_000

_STATUTS = ("🔴", "🟠", "🟡", "🟢")


class RiskItem(BaseModel):
    statut: Literal["🔴", "🟠", "🟡", "🟢"]
    element_ouvrage: str
    risque: str
    alea: str
    synoptique_description: str
    synoptique_preconisation: str
    expose_situation: str
    analyse_expert: str
    impact_assurabilite: str
    recommandation: str
    source: str


class SectionRisks(BaseModel):
    risques: list[RiskItem]


class _ChantierAddress(BaseModel):
    adresse: str  # adresse postale du chantier, ou "" si introuvable


# Catégories, par ordre de priorité, où trouver l'adresse/commune du terrain d'assiette du projet
# (le chantier — pas le siège du maître d'ouvrage). L'arrêté de permis de construire la porte
# presque toujours en clair ; la notice et le RICT en dépannage.
_ADDRESS_SOURCE_CATEGORIES = ["TECH/ARRETE PC", "TECH/NOTICE", "TECH/RICT"]
_ADDRESS_EXCERPT_MAX_CHARS = 30_000

_ADDRESS_SYSTEM_PROMPT = (
    "Tu extrais l'adresse du CHANTIER (terrain d'assiette du projet de construction) à partir "
    "d'un document. C'est l'adresse où seront réalisés les travaux, PAS l'adresse du maître "
    "d'ouvrage / du pétitionnaire / de l'architecte. Donne l'adresse la plus complète possible "
    "(numéro, voie, code postal, commune) ; à défaut, au moins la commune et son code postal. "
    "Si le document ne permet pas de la déterminer, renvoie une chaîne vide."
)


def extract_chantier_address(documents: list[DocumentSignal]) -> str | None:
    """Fallback de géolocalisation : quand le champ d'extraction `adresse_chantier` est vide, un
    petit appel LLM relit l'arrêté PC / la notice / le RICT pour en tirer l'adresse du chantier —
    sans quoi Géorisques ne pourrait jamais être interrogé sur les dossiers où l'étape 3 a manqué
    ce champ (cas réel du dossier Le Grand Pic). Best-effort : retourne None sur échec/absence."""
    source = None
    for category in _ADDRESS_SOURCE_CATEGORIES:
        source = next((d for d in documents if d.final_category == category and d.content_excerpt), None)
        if source is not None:
            break
    if source is None:
        return None

    try:
        parsed, _ = call_structured_chat(
            system_prompt=_ADDRESS_SYSTEM_PROMPT,
            user_prompt=(
                f"Document ({source.final_category}) : {source.filename}\n---\n"
                f"{source.content_excerpt[:_ADDRESS_EXCERPT_MAX_CHARS]}\n---\n"
                "Adresse du chantier :"
            ),
            response_model=_ChantierAddress,
            what="audit risques — adresse chantier (Géorisques)",
        )
    except Exception:
        logger.warning("Audit risques : extraction de l'adresse chantier échouée", exc_info=True)
        return None
    address = (parsed.adresse or "").strip()
    return address or None


@dataclass(frozen=True)
class SectionOutcome:
    section_id: str
    risks: list[RiskItem]
    model_name: str | None
    error: str | None
    documents_used: list[str] = field(default_factory=list)
    candidates_count: int = 0


_SYSTEM_PROMPT = """Tu es un Expert Senior en Ingénierie des Risques Construction, en charge de \
la souscription des polices Dommages-Ouvrage (DO) et Tous Risques Chantier (TRC) chez SMABTP. Tu \
réalises la Phase 2 (évaluation des risques) d'un audit technique de dossier de consultation des \
entreprises (DCE).

Méthode et raisonnement :
- Approche narrative : ne te limite pas à des constats brefs. Développe ton raisonnement, explique \
les phénomènes physiques (corrosion, poinçonnement, tassement différentiel, poussée hydrostatique, \
condensation…) et projette les scénarios de sinistres possibles.
- Analyse au regard des référentiels : DTU, Eurocodes, règles de l'art, avis techniques. Explique \
pourquoi chaque point est critique pour la pérennité de l'ouvrage.
- Audit transversal : vérifie la cohérence entre les lots et signale toute contradiction (ex. une \
prescription du CCTP Étanchéité qui contredit une attente du Gros-Œuvre), les points de greffe \
existant/neuf, le voisinage actif, la coactivité critique (hors d'eau/hors d'air) et la \
maintenabilité en exploitation.
- Techniques Non Courantes (TNC) : détecte tout procédé sous Avis Technique (ATec), ATEx ou \
Pass'Innovation et précise si cela constitue une TNC pour l'assureur.
- Rigueur : ne suppose rien. Si une information manque, signale-le comme une lacune à lever plutôt \
que de l'inventer. Confronte systématiquement les documents entre eux et aux données publiques \
Géorisques fournies.
- Matérialité : concentre-toi sur les risques réellement STRUCTURANTS pour la souscription — vise \
2 à 5 risques par section, pas un inventaire exhaustif de micro-points. Regroupe les aléas \
connexes en un seul risque plutôt que de les émietter. Chaque champ narratif doit être DENSE mais \
CIBLÉ (2 à 4 phrases) : développe le raisonnement essentiel sans délayer.

Codes couleur du statut (impératif) :
- 🔴 : risque critique / point de blocage en souscription (aléa décennal majeur, lacune \
bloquante, non-conformité réglementaire).
- 🟠 : risque modéré à élevé / point d'attention à lever avant engagement.
- 🟢 : risque maîtrisé (aléa purgé, conception conforme, avis favorable du contrôleur).
(N'utilise 🟡 qu'exceptionnellement, pour un risque faible mais non totalement purgé.)

Pour CHAQUE risque identifié, tu renseignes :
- statut : 🔴, 🟠, 🟢 (ou 🟡).
- element_ouvrage : l'ouvrage concerné en majuscules (ex. FONDATIONS, INFRASTRUCTURE, \
SUPERSTRUCTURE, COUVERTURE, FAÇADES, ÉQUIPEMENTS, AMÉNAGEMENTS, ENVIRONNEMENT).
- risque : le nom du risque (ex. « Défaut de stabilité »).
- alea : le nom de l'aléa (ex. « Tassement du sol d'assise »).
- synoptique_description : UNE phrase concise décrivant le problème (pour le tableau récapitulatif).
- synoptique_preconisation : UNE phrase concise décrivant l'action attendue (pour le tableau).
- expose_situation : résumé détaillé des prescriptions relevées dans les CCTP, l'étude de sol et le \
RICT.
- analyse_expert : analyse approfondie au regard des DTU / Eurocodes / règles de l'art ; rappelle \
les points de vérification (→) concernés.
- impact_assurabilite : nature du désordre potentiel (décennal, esthétique, fonctionnel, \
impropriété à destination) et avis sur l'acceptation du risque en souscription.
- recommandation : actions précises ou documents complémentaires à réclamer (plans d'exécution, \
notes de calcul, certificats, essais, avis du bureau de contrôle).
- source : nom des fichiers sources et articles/avis cités.

N'invente jamais une prescription absente des documents fournis."""


def _normalize(text: str) -> str:
    """Minuscule + suppression des accents, pour un appariement robuste des mots-clés de lot sur
    des noms de fichiers variables (« GROS-OEUVRE », « Gros Œuvre », « gros-œuvre »…)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def select_section_documents(section: AuditSection, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Documents pivots de la section, dans l'ordre de priorité de `pivot_categories`. Les
    catégories « par lot » (§schema.LOT_FILTERED_CATEGORIES) sont restreintes aux documents dont le
    nom de fichier ou le lot contient l'un des `cctp_keywords` de la section ; les autres pivots
    (RICT, étude de sol, notice) sont pris en entier."""
    normalized_keywords = [_normalize(k) for k in section.cctp_keywords]
    selected: list[DocumentSignal] = []
    seen: set[str] = set()
    for category in section.pivot_categories:
        for d in documents:
            if d.final_category != category or not d.content_excerpt or d.document_id in seen:
                continue
            if category in LOT_FILTERED_CATEGORIES and normalized_keywords:
                haystack = _normalize(f"{d.filename} {d.final_lot or ''}")
                if not any(k in haystack for k in normalized_keywords):
                    continue
            selected.append(d)
            seen.add(d.document_id)
    return selected


def _build_documents_context(
    documents: list[DocumentSignal],
    *,
    total_budget: int = AUDIT_TOTAL_CONTEXT_MAX_CHARS,
    per_document_budget: int = AUDIT_PER_DOCUMENT_MAX_CHARS,
) -> tuple[str, list[str]]:
    """Contexte assemblé + liste des documents réellement inclus (distincte des candidats : au-delà
    du budget, un document candidat est purement absent du prompt — cf. Phase 1)."""
    blocks: list[str] = []
    included: list[str] = []
    remaining = total_budget
    for doc in documents:
        if remaining <= 0:
            break
        cap = min(per_document_budget, remaining)
        excerpt = doc.content_excerpt[:cap]
        blocks.append(
            f"### Document : {doc.filename} (catégorie : {doc.final_category or 'inconnue'}"
            + (f", lot : {doc.final_lot}" if doc.final_lot else "")
            + f")\n{excerpt}"
        )
        included.append(doc.filename)
        remaining -= len(excerpt)
    return "\n\n".join(blocks), included


def _build_user_prompt(*, section: AuditSection, grounding: str, context: str) -> str:
    points = f"\nPoints de vérification (→) à appliquer :\n{section.points_verification}\n" if section.points_verification else ""
    grounding_block = f"\n{grounding}\n" if grounding else ""
    return f"""Section d'audit : {section.titre}

Consigne de périmètre :
{section.instructions}
{points}{grounding_block}
Documents pivots fournis (texte natif ou OCR) :
---
{context}
---

Identifie tous les risques pertinents de cette section (aléas de la grille métier + tout autre \
risque saillant dans les documents). Pour chaque risque, produis une entrée structurée complète. \
S'il n'y a objectivement aucun risque à signaler pour cette section (documents absents ou sujet \
hors périmètre du dossier), renvoie une liste vide."""


def generate_section(
    section: AuditSection,
    *,
    documents: list[DocumentSignal],
    georisques: GeorisquesReport | None,
) -> SectionOutcome:
    """Un seul appel LLM par section → liste de risques structurés."""
    candidates = select_section_documents(section, documents)
    grounding = format_aspects_grounding(georisques, section.georisques_aspects)

    if not candidates and not grounding:
        return SectionOutcome(
            section_id=section.id, risks=[], model_name=None, error=None, documents_used=[], candidates_count=0
        )

    context, documents_used = _build_documents_context(candidates)
    if not context:
        context = "_Aucun document pivot dans le corpus pour cette section — statue uniquement à partir des données publiques Géorisques fournies ci-dessus, si présentes._"
    if len(documents_used) < len(candidates):
        logger.warning(
            "Audit risques — section %s : %d document(s) candidat(s) mais %d envoyé(s) (budget atteint) : %s ignoré(s)",
            section.id,
            len(candidates),
            len(documents_used),
            [d.filename for d in candidates if d.filename not in documents_used],
        )

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(section=section, grounding=grounding, context=context),
            response_model=SectionRisks,
            what=f"audit risques — section {section.id}",
        )
    except Exception as exc:
        logger.exception("Échec de la génération de la section %s de l'audit des risques", section.id)
        return SectionOutcome(
            section_id=section.id,
            risks=[],
            model_name=None,
            error=str(exc),
            documents_used=documents_used,
            candidates_count=len(candidates),
        )

    return SectionOutcome(
        section_id=section.id,
        risks=list(parsed.risques),
        model_name=api_model_name,
        error=None,
        documents_used=documents_used,
        candidates_count=len(candidates),
    )


def _escape_cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "\\|").strip()


def _synoptic_table(outcomes: list[SectionOutcome]) -> str:
    lines = [
        "| Section / Élément d'ouvrage | Risque / Aléa | Description du problème | Préconisation / Action attendue | Statut |",
        "|---|---|---|---|---|",
    ]
    any_row = False
    for outcome in outcomes:
        for r in outcome.risks:
            any_row = True
            risque_alea = f"{r.risque} / {r.alea}" if r.alea else r.risque
            lines.append(
                f"| **{_escape_cell(r.element_ouvrage)}** | {_escape_cell(risque_alea)} | "
                f"{_escape_cell(r.synoptique_description)} | {_escape_cell(r.synoptique_preconisation)} | {r.statut} |"
            )
    if not any_row:
        return "_Aucun risque identifié — voir les sections ci-dessous et l'état des documents du dossier._"
    return "\n".join(lines)


def _render_risk_detail(r: RiskItem) -> str:
    header = f"[STATUT : {r.statut}] | [{r.element_ouvrage}] | [{r.risque} / {r.alea}]" if r.alea else f"[STATUT : {r.statut}] | [{r.element_ouvrage}] | [{r.risque}]"
    return (
        f"{header}\n\n"
        f"**Exposé de la situation :** {r.expose_situation}\n\n"
        f"**Analyse de l'Expert & Référentiel :** {r.analyse_expert}\n\n"
        f"**Impact Assurabilité :** {r.impact_assurabilite}\n\n"
        f"**Recommandation de levée de doute :** {r.recommandation} **Source :** {r.source}"
    )


def assemble_report(
    outcomes: list[SectionOutcome],
    schema: AuditSchema,
    *,
    georisques: GeorisquesReport | None = None,
) -> str:
    by_id = {o.section_id: o for o in outcomes}
    sections: list[str] = ["# Audit des risques — Phase 2"]

    sections.append("## Contexte réglementaire — Risques naturels (Géorisques)\n\n" + format_full_md(georisques))
    sections.append("## Tableau récapitulatif des risques\n\n" + _synoptic_table(outcomes))

    detail_parts = ["## Analyse détaillée par section"]
    for section in schema.sections:
        outcome = by_id.get(section.id)
        detail_parts.append(f"### {section.titre}")
        if outcome is None:
            detail_parts.append("_Section non générée._")
            continue
        if outcome.error:
            detail_parts.append(f"_Section non générée (erreur : {outcome.error})._")
            continue
        if not outcome.risks:
            detail_parts.append("_Aucun risque saillant identifié pour cette section dans le corpus fourni._")
        else:
            blocks = [_render_risk_detail(r) for r in outcome.risks]
            body = "\n\n--------------------------------------------------------------------------------\n\n".join(blocks)
            if outcome.documents_used:
                body += "\n\n_Sources consultées : " + ", ".join(outcome.documents_used) + "_"
                skipped = outcome.candidates_count - len(outcome.documents_used)
                if skipped > 0:
                    body += f" _(+{skipped} document(s) pivot(s) non envoyé(s) — budget de contexte atteint)_"
            detail_parts.append(body)

    sections.append("\n\n".join(detail_parts))
    return "\n\n".join(sections) + "\n"
