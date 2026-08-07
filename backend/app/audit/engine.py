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
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.audit.georisques import GeorisquesReport, format_aspects_grounding, format_full_md
from app.audit.schema import LOT_FILTERED_CATEGORIES, AuditSchema, AuditSection
from app.ingestion.document_signal import DocumentSignal
from app.mistral import call_log
from app.mistral.client import call_structured_chat, document_summary_max_tokens

logger = logging.getLogger(__name__)

# Plafonds du SEUL chemin de repli (§_build_section_context) : quand le relevé d'un document n'a pas
# pu être produit à l'étape de map, on réinjecte son texte brut tronqué plutôt que de le perdre.
#
# C'était auparavant le régime nominal, et il coûtait cher : sur le run e2e du 2026-07-29, la Phase 2
# a perdu 369 661 caractères en 10 troncatures — le RICT rogné de 84 423 à 60 000 caractères dans
# CINQ sections, et purement absent d'une sixième (budget total épuisé), l'étude de sol G2PRO
# ramenée de 180 562 à 60 000. Le map-reduce supprime ces pertes ET envoie moins : le RICT était
# renvoyé 5 fois tronqué (5 × 60 000) au lieu d'une fois en entier, d'où 1 007 762 caractères
# d'entrée contre 1 203 026 auparavant sur ce dossier (-16 %).
AUDIT_FALLBACK_TOTAL_MAX_CHARS = 180_000
AUDIT_FALLBACK_PER_DOCUMENT_MAX_CHARS = 60_000

# Seuil indicatif : au-delà, un document seul commence à peser dans la fenêtre du modèle. On ne
# tronque PAS pour autant (c'est le défaut corrigé ici), on veut juste le voir dans les logs.
AUDIT_LARGE_DOCUMENT_WARN_CHARS = 350_000

_STATUTS = ("🔴", "🟠", "🟡", "🟢")


class RiskItem(BaseModel):
    statut: Literal["🔴", "🟠", "🟡", "🟢"]
    element_ouvrage: str
    risque: str
    alea: str
    synoptique_description: str
    synoptique_preconisation: str
    expose_situation: str
    # Listes, et non de longs textes multi-lignes : demander des puces séparées par des \n À
    # L'INTÉRIEUR d'une chaîne JSON déclenche une boucle dégénérée du décodage contraint — une fois
    # la chaîne refermée, le whitespace reste licite entre deux tokens JSON, donc la grammaire
    # n'oblige jamais le modèle à en sortir, et la génération part en espaces/tabulations jusqu'à
    # être avortée par le serveur (finish_reason='error') ou coupée par max_tokens, laissant un JSON
    # tronqué. C'est ce qui a fait tomber 9 des 30 appels "map" de la Phase 1 (§synthesis/engine.py
    # `constats`, run e2e du 2026-07-29). La Phase 2 n'avait pas encore cassé, mais elle produisait
    # déjà le motif déclencheur : sur ce même run, 30/30 `recommandation` et 20/30 `analyse_expert`
    # contenaient des sauts de ligne (jusqu'à 19 dans un seul champ).
    #
    # Le passage en liste ne raccourcit rien : ces deux champs SONT déjà des listes (un point de
    # vérification « → … » / une action par élément), chaque élément restant un paragraphe dense.
    # On ne supprime que les \n ENTRE les items — et la grammaire attend « , » ou « ] » après
    # chacun, ce qui donne au modèle un signal de sortie fort au lieu d'une zone de whitespace libre.
    analyse_expert: list[str]
    impact_assurabilite: str
    recommandations: list[str]
    source: str


class SectionRisks(BaseModel):
    risques: list[RiskItem]


# --- Étape « map » : un relevé par document pivot ----------------------------------------------


class _SectionConstats(BaseModel):
    section_id: str
    concerne_cette_section: bool
    constats: list[str]


class _DocumentAuditResponse(BaseModel):
    releves: list[_SectionConstats]


@dataclass(frozen=True)
class DocumentAuditSummary:
    """Résultat de l'étape « map » pour UN document pivot : ce qu'il apporte à chacune des sections
    d'audit dont il est pivot.

    `constats_by_section` ne contient que les sections réellement documentées ;
    `covered_section_ids` liste celles que le LLM a traitées — la différence, c'est « ce document
    n'a rien à dire sur cette section » (information en soi) et non « ce document n'a pas pu être
    analysé » (repli sur extrait brut)."""

    document_id: str
    filename: str
    final_category: str | None
    final_lot: str | None = None
    constats_by_section: dict[str, str] = field(default_factory=dict)
    covered_section_ids: frozenset[str] = frozenset()
    model_name: str | None = None
    error: str | None = None


_MAP_SYSTEM_PROMPT = """Tu es un Expert Senior en Ingénierie des Risques Construction (souscription \
Dommages-Ouvrage et Tous Risques Chantier, SMABTP). On te donne le texte intégral d'UN document \
d'un dossier de consultation des entreprises (DCE), et les sections d'audit pour lesquelles ce \
document fait référence.

Ta tâche : pour CHAQUE section demandée, relever ce que CE document — et lui seul — apporte à \
l'évaluation des risques de cette section. Ces relevés seront ensuite confrontés entre documents \
pour identifier les risques : ils doivent être fidèles et vérifiables, pas rédigés.

Ce document a DÉJÀ été rattaché au périmètre de chacune des sections listées ci-dessous, par la \
classification du dossier. Ne remets pas ce rattachement en cause et ne juge pas de sa pertinence : \
ta question n'est pas « ce document concerne-t-il cette section ? » mais « que contient-il qui \
intéresse cette section ? ». Si l'ouvrage traité par le document relève du domaine de la section \
sans correspondre exactement à ses points de vérification, relève quand même ce qu'il prescrit sur \
le thème de la section — matériaux et produits, performances, dispositions constructives, \
exigences de maintenance et d'exploitation, procédés sous avis technique, et les lacunes.

Commence TOUJOURS par ce que le document dit, avant de regarder ce qu'il ne dit pas :
- les prescriptions et spécifications techniques : matériaux, produits nommés, classes de \
performance, épaisseurs, cotes, résistances, dispositions constructives, référentiels invoqués \
(DTU, Eurocodes, normes NF) ;
- les exigences d'exécution, d'essai, de réception, de maintenance et d'exploitation ;
- les procédés sous Avis Technique (ATec), ATEx ou Pass'Innovation, et tout procédé inhabituel — \
ce sont des Techniques Non Courantes potentielles pour l'assureur ;
- les réserves, avis suspendus ou défavorables, points de vigilance et prescriptions du contrôleur \
technique, avec leur numéro.

Puis, seulement ensuite, les LACUNES — et uniquement sur ce que CE document aurait dû préciser au \
vu de son PROPRE objet : performance annoncée mais non chiffrée, détail d'exécution renvoyé à une \
étude ultérieure, prescription attendue pour l'ouvrage qu'il décrit et absente. Une telle lacune \
est un signal de risque aussi important qu'une prescription.

Ne relève JAMAIS l'absence d'un sujet étranger à l'objet du document. Qu'un CCTP Ascenseurs ne \
parle pas de photovoltaïque n'est pas une lacune : c'est hors de son périmètre, et un constat de ce \
genre est du bruit qui noiera les vrais signaux. Les points de vérification de la section te disent \
sous quel angle lire le document, ils ne sont pas une liste de questions à cocher.

Règles impératives :
- N'utilise QUE le contenu du document fourni. Aucune connaissance extérieure, aucune déduction, \
aucune valeur « habituelle », aucune complétion de ce qui manque.
- Conserve les données telles qu'écrites : chiffres, unités, classes, épaisseurs, noms de produits \
et de sociétés, ainsi que les références internes du document (numéro d'article, d'avis, de lot, \
titre de section) chaque fois qu'il les donne.
- Ne conclus pas, ne qualifie pas le risque, ne recommande rien : c'est l'étape suivante qui le \
fera en confrontant tous les documents. Relève les faits.
- Ne cite pas le nom du document dans tes constats : il est déjà attribué à sa source.
- `constats` est une LISTE : un élément = un fait. Chaque élément tient sur une seule ligne, sans \
retour à la ligne et sans puce (« - ») en tête. N'utilise pas de guillemet droit (") : pour citer \
un passage, encadre-le par des guillemets français « … ».
- Sois EXHAUSTIF : ce relevé remplace le document pour la suite de l'analyse, tout ce que tu \
n'écris pas sera perdu. Compte en dizaines de constats, pas en unités — de l'ordre de 15 à 40 pour \
un CCTP de lot, davantage pour un rapport de contrôle technique ou une étude géotechnique. Un \
relevé d'un ou deux constats sur un document de plusieurs dizaines de milliers de caractères \
signifie que tu n'as pas fait le travail.
- Ne produis jamais de constat « méta » sur le document (« ce document traite des ascenseurs », \
« aucun équipement ENR n'est mentionné ») : relève son CONTENU technique.
- Énonce chaque fait seul, sans le commenter au regard des points de vérification. N'ajoute jamais \
de clause du type « … sans mention de X » ou « … sans rapport avec Y » à la fin d'un constat \
factuel : ce commentaire est du bruit, et c'est l'étape suivante qui jugera de ce qui manque.
- `concerne_cette_section` à false est un cas RARE et un dernier recours : réserve-le au document \
qui, après lecture complète, ne contient objectivement rien d'exploitable pour la section (ex. une \
pièce purement administrative). Un document dont l'objet relève du domaine de la section en \
contient forcément quelque chose — au minimum ses prescriptions techniques et ses lacunes.
- Réponds avec exactement une entrée par section demandée, en reprenant son `section_id` à \
l'identique."""


def _build_map_user_prompt(*, doc: DocumentSignal, sections: list[AuditSection]) -> str:
    section_blocks = "\n\n".join(
        f"- section_id : {s.id}\n"
        f"  Section : {s.titre}\n"
        f"  Périmètre : {(s.instructions or '').strip()}"
        + (f"\n  Points de vérification (→) : {s.points_verification.strip()}" if s.points_verification else "")
        for s in sections
    )
    lot = f", lot : {doc.final_lot}" if doc.final_lot else ""
    return f"""Document analysé : {doc.filename} (catégorie : {doc.final_category or 'inconnue'}{lot})

Sections d'audit pour lesquelles ce document fait référence ({len(sections)}) :
{section_blocks}

Texte intégral du document (natif ou OCR) :
---
{doc.content_excerpt}
---

Produis un relevé factuel par section."""


def summarize_document_for_audit(doc: DocumentSignal, sections: list[AuditSection]) -> DocumentAuditSummary:
    """Étape « map » : un seul appel LLM pour ce document, couvrant d'un coup toutes les sections
    dont il est pivot, sur son texte INTÉGRAL et sans troncature.

    Best-effort : un échec ne lève jamais — il renseigne `error`, et l'étape « reduce » retombe
    alors sur un extrait brut tronqué (§_build_section_context) plutôt que de perdre le document."""
    assert sections, "summarize_document_for_audit appelé sans section"

    if len(doc.content_excerpt) > AUDIT_LARGE_DOCUMENT_WARN_CHARS:
        logger.warning(
            "Audit risques — document %s : %d caractères envoyés en un seul appel (seuil d'alerte %d)",
            doc.filename,
            len(doc.content_excerpt),
            AUDIT_LARGE_DOCUMENT_WARN_CHARS,
        )

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_MAP_SYSTEM_PROMPT,
            user_prompt=_build_map_user_prompt(doc=doc, sections=sections),
            response_model=_DocumentAuditResponse,
            what=f"audit risques — relevé du document {doc.filename}",
            max_tokens=document_summary_max_tokens(),
        )
    except Exception as exc:
        logger.exception("Échec du relevé du document %s (audit risques)", doc.filename)
        return DocumentAuditSummary(
            document_id=doc.document_id,
            filename=doc.filename,
            final_category=doc.final_category,
            final_lot=doc.final_lot,
            error=str(exc),
        )

    requested = {s.id for s in sections}
    constats_by_section: dict[str, str] = {}
    covered: set[str] = set()
    for item in parsed.releves:
        section_id = item.section_id.strip()
        if section_id not in requested:
            logger.warning(
                "Audit risques — document %s : section_id inattendu %r dans le relevé (ignoré)",
                doc.filename,
                item.section_id,
            )
            continue
        covered.add(section_id)
        constats = _clean_items(item.constats)
        if item.concerne_cette_section and constats:
            constats_by_section[section_id] = "\n".join(f"- {c}" for c in constats)

    missing = requested - covered
    if missing:
        logger.warning(
            "Audit risques — document %s : %d section(s) absente(s) du relevé (%s) — repli sur extrait brut",
            doc.filename,
            len(missing),
            sorted(missing),
        )

    return DocumentAuditSummary(
        document_id=doc.document_id,
        filename=doc.filename,
        final_category=doc.final_category,
        final_lot=doc.final_lot,
        constats_by_section=constats_by_section,
        covered_section_ids=frozenset(covered),
        model_name=api_model_name,
        error=None,
    )


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

    if len(source.content_excerpt) > _ADDRESS_EXCERPT_MAX_CHARS:
        call_log.log_truncation(
            source="audit_address_fallback",
            document=source.filename,
            original_chars=len(source.content_excerpt),
            kept_chars=_ADDRESS_EXCERPT_MAX_CHARS,
            limit_name="_ADDRESS_EXCERPT_MAX_CHARS",
            limit_value=_ADDRESS_EXCERPT_MAX_CHARS,
        )
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
    documents_degraded: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = """Tu es un Expert Senior en Ingénierie des Risques Construction, en charge de \
la souscription des polices Dommages-Ouvrage (DO) et Tous Risques Chantier (TRC) chez SMABTP. Tu \
réalises la Phase 2 (évaluation des risques) d'un audit technique de dossier de consultation des \
entreprises (DCE).

Le contexte fourni n'est pas le texte brut du dossier : c'est, pour chaque document pivot de la \
section, un relevé factuel de ses prescriptions, réserves et LACUNES, établi lors d'une première \
passe de lecture intégrale, document par document. Chaque relevé reste attribué à son fichier \
source — c'est ce qui te permet de confronter les lots entre eux et de nommer précisément la \
source de chaque contradiction. Une lacune relevée (prescription absente, performance non \
chiffrée, détail non défini) est un signal de risque à part entière, à traiter comme tel.

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
RICT, en un seul paragraphe continu.
- analyse_expert : LISTE des points de vérification, un élément par point. Chaque élément est un \
paragraphe dense (2 à 4 phrases) qui analyse le point au regard des DTU / Eurocodes / règles de \
l'art — commence-le par « → » suivi du titre du point en gras.
- impact_assurabilite : nature du désordre potentiel (décennal, esthétique, fonctionnel, \
impropriété à destination) et avis sur l'acceptation du risque en souscription.
- recommandations : LISTE d'actions, une action par élément (plans d'exécution, notes de calcul, \
certificats, essais, avis du bureau de contrôle à réclamer). Chaque élément est précis et \
actionnable, et se suffit à lui-même.
- source : nom des fichiers sources et articles/avis cités.

Format des valeurs texte (impératif) : n'insère JAMAIS de retour à la ligne, de puce (« - ») en \
tête ni de numérotation (« 1. ») à l'intérieur d'une valeur — ce sont les listes `analyse_expert` \
et `recommandations` qui structurent le propos, un élément par point. N'utilise pas non plus de \
guillemet droit (") : pour citer un passage, encadre-le par des guillemets français « … ».

N'invente jamais une prescription absente des documents fournis."""


def _normalize(text: str) -> str:
    """Minuscule + suppression des accents, pour un appariement robuste des mots-clés de lot sur
    des noms de fichiers variables (« GROS-OEUVRE », « Gros Œuvre », « gros-œuvre »…)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def document_matches_section(doc: DocumentSignal, section: AuditSection) -> bool:
    """Appariement document ↔ section : la catégorie doit être pivot de la section et, pour les
    catégories « par lot » (§schema.LOT_FILTERED_CATEGORIES), le nom de fichier ou le lot doit
    contenir l'un des `cctp_keywords`. Les autres pivots (RICT, étude de sol, notice) passent
    entiers.

    Volontairement sans condition sur `content_excerpt` : le pipeline s'en sert AVANT l'OCR pour
    décider quels documents OCRiser, et l'appariement ne porte que sur le nom de fichier/lot,
    disponible dès la classification."""
    if doc.final_category not in section.pivot_categories:
        return False
    if doc.final_category in LOT_FILTERED_CATEGORIES and section.cctp_keywords:
        haystack = _normalize(f"{doc.filename} {doc.final_lot or ''}")
        return any(_normalize(k) in haystack for k in section.cctp_keywords)
    return True


def sections_for_document(doc: DocumentSignal, schema: AuditSchema) -> list[AuditSection]:
    """Sections dont ce document est pivot — l'inverse de `select_section_documents`. C'est ce qui
    permet à l'étape « map » de ne lire chaque document qu'UNE fois quel que soit le nombre de
    sections qu'il alimente (le RICT alimentait 5 sections sur le dossier de test)."""
    return [s for s in schema.sections if document_matches_section(doc, s)]


def select_section_documents(section: AuditSection, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Documents pivots de la section, dans l'ordre de priorité de `pivot_categories`."""
    selected: list[DocumentSignal] = []
    seen: set[str] = set()
    for category in section.pivot_categories:
        for d in documents:
            if d.final_category != category or not d.content_excerpt or d.document_id in seen:
                continue
            if not document_matches_section(d, section):
                continue
            selected.append(d)
            seen.add(d.document_id)
    return selected


def _document_header(doc: DocumentSignal) -> str:
    return (
        f"### Document : {doc.filename} (catégorie : {doc.final_category or 'inconnue'}"
        + (f", lot : {doc.final_lot}" if doc.final_lot else "")
        + ")"
    )


@dataclass(frozen=True)
class _SectionContext:
    text: str
    documents_used: list[str]
    documents_degraded: list[str]
    documents_without_info: list[str]


def _build_section_context(
    section: AuditSection,
    candidates: list[DocumentSignal],
    summaries: dict[str, DocumentAuditSummary],
    *,
    fallback_total_budget: int = AUDIT_FALLBACK_TOTAL_MAX_CHARS,
    fallback_per_document_budget: int = AUDIT_FALLBACK_PER_DOCUMENT_MAX_CHARS,
) -> _SectionContext:
    """Contexte de l'étape « reduce » : un bloc de relevés par document pivot, toujours nommé par
    son fichier source — indispensable pour que la section puisse continuer à confronter les lots
    entre eux et signaler une contradiction (ex. une prescription du CCTP Étanchéité qui contredit
    une attente du Gros-Œuvre), ce que le prompt système exige explicitement.

    Aucun budget ne s'applique aux relevés : tous les documents pivots sont représentés. Le budget
    ne cadre que le repli « extrait brut », emprunté quand le relevé n'a pas pu être produit."""
    blocks: list[str] = []
    used: list[str] = []
    degraded: list[str] = []
    without_info: list[str] = []
    remaining = fallback_total_budget

    for doc in candidates:
        summary = summaries.get(doc.document_id)
        header = _document_header(doc)

        if summary is not None and section.id in summary.constats_by_section:
            blocks.append(f"{header}\n{summary.constats_by_section[section.id]}")
            used.append(doc.filename)
            continue

        if summary is not None and section.id in summary.covered_section_ids:
            without_info.append(doc.filename)
            continue

        cap = min(fallback_per_document_budget, remaining)
        excerpt = doc.content_excerpt[:cap] if cap > 0 else ""
        if not excerpt:
            logger.warning(
                "Audit risques — section %s : document %s sans relevé ET budget de repli épuisé, absent du prompt",
                section.id,
                doc.filename,
            )
            if call_log.is_enabled():
                call_log.log_truncation(
                    source="audit_context_fallback",
                    document=doc.filename,
                    original_chars=len(doc.content_excerpt or ""),
                    kept_chars=0,
                    limit_name="AUDIT_FALLBACK_TOTAL_MAX_CHARS",
                    limit_value=fallback_total_budget,
                    extra={"section": section.id, "reason": "fallback_budget_exhausted"},
                )
            continue
        if call_log.is_enabled() and len(excerpt) < len(doc.content_excerpt or ""):
            call_log.log_truncation(
                source="audit_context_fallback",
                document=doc.filename,
                original_chars=len(doc.content_excerpt or ""),
                kept_chars=len(excerpt),
                limit_name=(
                    "AUDIT_FALLBACK_PER_DOCUMENT_MAX_CHARS"
                    if cap == fallback_per_document_budget
                    else "AUDIT_FALLBACK_TOTAL_MAX_CHARS"
                ),
                limit_value=cap,
                extra={"section": section.id},
            )
        blocks.append(f"{header} — relevé indisponible, extrait brut du document (tronqué) :\n{excerpt}")
        used.append(doc.filename)
        degraded.append(doc.filename)
        remaining -= len(excerpt)

    if without_info:
        blocks.append(
            "### Documents pivots lus sans élément pertinent pour cette section\n" + ", ".join(without_info)
        )

    return _SectionContext(
        text="\n\n".join(blocks),
        documents_used=used,
        documents_degraded=degraded,
        documents_without_info=without_info,
    )


def _build_user_prompt(*, section: AuditSection, grounding: str, context: str) -> str:
    points = f"\nPoints de vérification (→) à appliquer :\n{section.points_verification}\n" if section.points_verification else ""
    grounding_block = f"\n{grounding}\n" if grounding else ""
    return f"""Section d'audit : {section.titre}

Consigne de périmètre :
{section.instructions}
{points}{grounding_block}
Relevés factuels établis document par document sur l'ensemble des documents pivots de cette section :
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
    summaries: dict[str, DocumentAuditSummary],
) -> SectionOutcome:
    """Étape « reduce » : un seul appel LLM par section → liste de risques structurés, alimenté par
    les relevés produits en amont par `summarize_document_for_audit` (indexés par `document_id`)."""
    candidates = select_section_documents(section, documents)
    grounding = format_aspects_grounding(georisques, section.georisques_aspects)

    if not candidates and not grounding:
        return SectionOutcome(
            section_id=section.id, risks=[], model_name=None, error=None, documents_used=[], candidates_count=0
        )

    context = _build_section_context(section, candidates, summaries)
    context_text = context.text
    if not context_text:
        context_text = "_Aucun document pivot dans le corpus pour cette section — statue uniquement à partir des données publiques Géorisques fournies ci-dessus, si présentes._"
    if context.documents_degraded:
        logger.warning(
            "Audit risques — section %s : %d document(s) sans relevé exploitable, repli sur extrait brut tronqué : %s",
            section.id,
            len(context.documents_degraded),
            context.documents_degraded,
        )

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(section=section, grounding=grounding, context=context_text),
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
            documents_used=context.documents_used,
            candidates_count=len(candidates),
            documents_degraded=context.documents_degraded,
        )

    return SectionOutcome(
        section_id=section.id,
        risks=list(parsed.risques),
        model_name=api_model_name,
        error=None,
        documents_used=context.documents_used,
        candidates_count=len(candidates),
        documents_degraded=context.documents_degraded,
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


def _clean_items(items: list[str]) -> list[str]:
    """Nettoie une liste produite par le LLM : puces/numérotations résiduelles en tête d'élément
    (le prompt les interdit, mais un élément déjà préfixé ne doit pas se retrouver doublé) et
    éléments vides."""
    cleaned = []
    for item in items:
        text = re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _render_risk_detail(r: RiskItem) -> str:
    header = f"[STATUT : {r.statut}] | [{r.element_ouvrage}] | [{r.risque} / {r.alea}]" if r.alea else f"[STATUT : {r.statut}] | [{r.element_ouvrage}] | [{r.risque}]"
    # `analyse_expert` : un paragraphe par point de vérification (les éléments commencent déjà par
    # « → »), donc séparés par une ligne vide plutôt que rendus en puces.
    analyse = "\n\n".join(_clean_items(r.analyse_expert)) or "_Non renseigné._"
    recommandations = _clean_items(r.recommandations)
    reco_block = "\n".join(f"- {item}" for item in recommandations) if recommandations else "_Non renseignée._"
    return (
        f"{header}\n\n"
        f"**Exposé de la situation :** {r.expose_situation}\n\n"
        f"**Analyse de l'Expert & Référentiel :**\n\n{analyse}\n\n"
        f"**Impact Assurabilité :** {r.impact_assurabilite}\n\n"
        f"**Recommandation de levée de doute :**\n\n{reco_block}\n\n"
        f"**Source :** {r.source}"
    )


def _sources_note(outcome: SectionOutcome) -> str:
    """Traçabilité sous une section : les fichiers réellement exploités, ceux lus sans élément
    pertinent, et ceux tombés sur le repli « extrait brut »."""
    if not outcome.candidates_count:
        return ""
    parts: list[str] = []
    if outcome.documents_used:
        parts.append("_Sources consultées : " + ", ".join(outcome.documents_used) + "_")
    without_info = outcome.candidates_count - len(outcome.documents_used)
    if without_info > 0:
        parts.append(f"_(+{without_info} document(s) pivot(s) lu(s) sans élément pertinent pour cette section)_")
    if outcome.documents_degraded:
        parts.append(
            "_(relevé indisponible, extrait brut tronqué utilisé pour : "
            + ", ".join(outcome.documents_degraded)
            + ")_"
        )
    return " ".join(parts)


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
            note = _sources_note(outcome)
            if note:
                body += "\n\n" + note
            detail_parts.append(body)

    sections.append("\n\n".join(detail_parts))
    return "\n\n".join(sections) + "\n"
