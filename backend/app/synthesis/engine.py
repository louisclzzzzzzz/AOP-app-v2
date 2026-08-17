"""Moteur de génération de la synthèse projet (Phase 1 du protocole d'analyse).

Contrairement à `app/extraction/engine.py` (un champ = une valeur atomique courte), ici un
THÈME = une section narrative (prose, tableau ou liste) qui repose sur la lecture INTÉGRALE des
documents pivots concernés (`config/taxonomy.yaml` `is_pivot` + `pivot_categories` du thème) —
jamais une simple reformulation des valeurs déjà extraites, sauf pour les thèmes
`source: extraction_fields` (identité de l'opération), qui sont de simples reformatages
déterministes, sans appel LLM.

Cette lecture se fait en **map-reduce**, et non plus en concaténant les textes bruts dans un
unique prompt par thème :

- **map** (`summarize_document`) — un appel LLM par DOCUMENT pivot (pas par document × thème),
  sur son texte intégral et sans troncature : un seul document par appel tient largement dans la
  fenêtre du modèle. Il en sort, pour chacun des thèmes dont ce document est pivot, un relevé
  factuel dense de ce que ce document apporte à ce thème (ou l'absence d'information). Un document
  partagé par plusieurs thèmes (le RICT est pivot de 4 thèmes) n'est traité qu'une fois puis
  réutilisé partout — même dédup que celle déjà appliquée à l'OCR dans ce pipeline.
- **reduce** (`generate_topic`) — le même unique appel LLM par thème qu'avant, mais alimenté par
  les relevés du map au lieu d'extraits bruts tronqués.

Ce que ça corrige (§data/resultats_synthese_test/RAPPORT_TECHNIQUE_ANALYSE.md) : l'ancien
assemblage plafonnait chaque document à 60 000 caractères — tronqués à plat depuis le début, sans
sélection par pertinence — et s'arrêtait à 300 000 caractères par thème, au-delà desquels les
documents pivots restants étaient purement et simplement absents du prompt, jamais vus par le
LLM. Un document pivot n'est désormais plus jamais tronqué ni exclu par manque de budget.

Chaque relevé reste attribué à son fichier source dans le prompt de reduce : c'est ce qui permet
au thème de continuer à comparer les documents entre eux et à signaler une divergence (cas réel
rencontré : classement ERP différent entre le CCTP et l'arrêté de permis de construire).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.classify.taxonomy import Taxonomy
from app.ingestion.document_signal import DocumentSignal
from app.mistral import call_log
from app.mistral.client import call_structured_chat, document_summary_max_tokens
from app.reports.citations import REFS_PROMPT_RULES, CitationAllocator, CitationRef
from app.synthesis.schema import SynthesisSchema, SynthesisTopic

logger = logging.getLogger(__name__)

# Seuil purement indicatif : au-delà, un document seul commence à peser lourd dans la fenêtre de
# mistral-large (~128k tokens, ~3 caractères/token sur du technique français). On ne tronque PAS
# pour autant — c'est justement le défaut corrigé ici — mais on veut le voir dans les logs, car
# c'est le seul cas où l'appel de map peut échouer pour cause de contexte et basculer sur le repli
# "extrait brut tronqué" ci-dessous.
SYNTHESIS_LARGE_DOCUMENT_WARN_CHARS = 350_000

# Plafonds du SEUL chemin de repli (§_build_topic_context) : quand le relevé d'un document n'a pas
# pu être produit (échec LLM, thème non traité dans la réponse), on réinjecte son texte brut
# tronqué dans le prompt du thème plutôt que de perdre le document. C'est le comportement
# historique, désormais cantonné aux cas dégradés — d'où un budget total volontairement plus bas
# que l'ancien (le cas nominal ne consomme plus que des relevés courts).
SYNTHESIS_FALLBACK_PER_DOCUMENT_MAX_CHARS = 60_000
SYNTHESIS_FALLBACK_TOTAL_MAX_CHARS = 180_000

# (libellé, valeur finale) d'un champ déjà résolu à l'étape 3, indexé par field_id.
FieldValues = dict[str, tuple[str, str]]


@dataclass(frozen=True)
class DocumentSummary:
    """Résultat de l'étape "map" pour UN document pivot.

    `summaries_by_topic` ne contient que les thèmes que ce document informe réellement ;
    `covered_topic_ids` liste tous les thèmes que le LLM a bel et bien traités — la différence
    entre les deux, c'est "ce document n'a rien à dire sur ce thème" (information utile en soi),
    à ne pas confondre avec "ce document n'a pas pu être analysé" (repli sur extrait brut)."""

    document_id: str
    filename: str
    final_category: str | None
    summaries_by_topic: dict[str, str] = field(default_factory=dict)
    covered_topic_ids: frozenset[str] = frozenset()
    model_name: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TopicOutcome:
    topic_id: str
    content_md: str | None
    model_name: str | None
    error: str | None
    documents_used: list[str] = field(default_factory=list)
    candidates_count: int = 0
    documents_degraded: list[str] = field(default_factory=list)
    # Étiquettes (`D1`…) proposées au LLM pour CE thème : locales au thème, donc ré-attribuées
    # globalement à l'assemblage (§`assemble_report`).
    citation_refs: dict[str, CitationRef] = field(default_factory=dict)


class _TopicResponse(BaseModel):
    contenu: str


class _DocumentTopicSummary(BaseModel):
    theme_id: str
    apporte_des_informations: bool
    # Liste de constats courts, et non un seul texte multi-lignes : demander des puces séparées par
    # des \n À L'INTÉRIEUR d'une chaîne JSON déclenchait une pathologie du décodage contraint —
    # après le \n final et la fermeture de la chaîne, le modèle partait en boucle dégénérée sur du
    # whitespace (toujours licite entre deux tokens JSON, donc jamais interrompue par la grammaire)
    # et n'atteignait jamais la virgule suivante ; le serveur finissait par avorter la génération
    # (finish_reason='error') ou saturer max_tokens, laissant un JSON tronqué non parsable. 9 des 30
    # appels "map" du run e2e du 2026-07-29 sont tombés là-dedans, sans jamais rattraper en 3
    # tentatives. Avec une liste, la grammaire attend « , » ou « ] » après chaque élément : le
    # modèle a un signal de sortie fort au lieu d'une zone de whitespace libre.
    constats: list[str]


class _DocumentSummaryResponse(BaseModel):
    resumes: list[_DocumentTopicSummary]


# --- Étape "map" : un relevé factuel par document pivot ----------------------------------------

_MAP_SYSTEM_PROMPT = """Tu es un expert en audit technique et souscription assurance \
construction (SMABTP). On te donne le texte intégral d'UN document d'un dossier de consultation \
des entreprises (DCE), et la liste des thèmes d'analyse pour lesquels ce document fait référence.

Ta tâche : pour CHAQUE thème demandé, produire un relevé factuel dense de ce que CE document — et \
lui seul — apporte à CE thème. Ces relevés seront ensuite recoupés entre documents pour rédiger \
le rapport final : ils doivent rester fidèles et vérifiables, pas élégants.

Règles impératives :
- N'utilise QUE le contenu du document fourni. Aucune connaissance extérieure, aucune déduction, \
aucune estimation, aucun ordre de grandeur « habituel », aucune complétion de ce qui manque.
- Conserve les données telles qu'écrites : chiffres, unités, dates, cotes, surfaces, montants, \
noms de sociétés, classements réglementaires — ainsi que les références internes du document \
(numéro d'article, d'avis, de lot, de mission, titre de section) chaque fois qu'il les donne.
- Pour toute affirmation qu'un autre document du dossier pourrait contredire (classement ERP et \
catégorie, nombre de niveaux, surfaces, montants, missions du bureau de contrôle, avis émis), \
reprends la formulation exacte du document, encadrée par des guillemets français « … », au lieu \
de la reformuler.
- N'utilise JAMAIS le caractère guillemet droit (") dans tes réponses, y compris pour citer : \
utilise « … » ou aucun guillemet. Emploie de même « pouce » ou « ″ » plutôt que " pour une unité.
- Ne cite pas le nom du document dans ton relevé : il est déjà attribué à sa source.
- `constats` est une LISTE de constats courts : un élément = un fait. Chaque élément tient sur une \
seule ligne, sans retour à la ligne et sans puce (« - ») en tête — c'est la liste elle-même qui \
fait office de puces.
- Si le document ne dit rien d'utile pour un thème, mets `apporte_des_informations` à false et \
renvoie une liste `constats` vide. N'invente pas de contenu de remplissage et ne recopie pas \
l'énoncé du thème.
- Sois dense et factuel, sans phrase de liaison, sans introduction, sans commentaire sur ta propre \
démarche.
- Sois EXHAUSTIF : ce relevé remplace le document pour la suite de l'analyse, tout ce que tu \
n'écris pas sera définitivement perdu. Compte en dizaines de constats, pas en unités — de l'ordre \
de 15 à 40 pour un document technique de plusieurs dizaines de milliers de caractères. Un relevé \
d'un ou deux constats sur un tel document signifie que tu n'as pas fait le travail.
- Relève TOUTES les valeurs d'une énumération, jamais un échantillon : si le document donne cinq \
classements, cinq types ou cinq capacités, les cinq doivent figurer dans ton relevé.
- Ne produis jamais de constat « méta » sur le document (« ce document décrit une résidence de \
tourisme ») là où tu peux relever son CONTENU (typologies, capacités, classements, cotes).
- Réponds avec exactement une entrée par thème demandé, en reprenant son `theme_id` à \
l'identique."""


def topics_for_document(doc: DocumentSignal, schema: SynthesisSchema) -> list[SynthesisTopic]:
    """Thèmes dont ce document est pivot — l'inverse de `select_topic_documents`. C'est ce qui
    permet à l'étape "map" de ne lire chaque document qu'UNE fois, quel que soit le nombre de
    thèmes qu'il alimente, au lieu d'un appel par couple (document, thème)."""
    if not doc.content_excerpt or doc.final_category is None:
        return []
    return [
        topic
        for topic in schema.topics
        if topic.source == "documents" and doc.final_category in topic.pivot_categories
    ]


def _build_map_user_prompt(*, doc: DocumentSignal, topics: list[SynthesisTopic]) -> str:
    # Les `grounding_field_ids` du thème (valeurs déjà validées à l'étape 3) ne sont volontairement
    # PAS injectés ici : le map doit rester une lecture neutre du document, sinon le relevé tend à
    # confirmer la valeur déjà connue au lieu de rapporter ce que le document dit vraiment — et on
    # perdrait justement les divergences que le reduce doit signaler. Le grounding reste au reduce.
    topic_blocks = "\n\n".join(
        f"- theme_id : {topic.id}\n"
        f"  Thème : {topic.titre}\n"
        f"  Ce qu'il faut relever : {(topic.instructions or '').strip()}"
        for topic in topics
    )
    return f"""Document analysé : {doc.filename} (catégorie : {doc.final_category or 'inconnue'})

Thèmes pour lesquels ce document fait référence ({len(topics)}) :
{topic_blocks}

Texte intégral du document (natif ou OCR) :
---
{doc.content_excerpt}
---

Produis un relevé factuel par thème."""


def summarize_document(doc: DocumentSignal, topics: list[SynthesisTopic]) -> DocumentSummary:
    """Étape "map" : un seul appel LLM pour ce document, couvrant d'un coup tous les thèmes dont il
    est pivot. Le texte est envoyé intégralement, sans troncature.

    Best-effort : un échec ne lève jamais — il renseigne `error`, et l'étape "reduce" retombe
    alors sur un extrait brut tronqué de ce document (§_build_topic_context) plutôt que de le
    perdre."""
    assert topics, "summarize_document appelé sans thème (le document n'est pivot d'aucun thème)"

    if len(doc.content_excerpt) > SYNTHESIS_LARGE_DOCUMENT_WARN_CHARS:
        logger.warning(
            "Synthèse projet — document %s : %d caractères envoyés en un seul appel (seuil d'alerte %d) ; "
            "en cas de dépassement de la fenêtre du modèle, le thème retombera sur un extrait brut tronqué",
            doc.filename,
            len(doc.content_excerpt),
            SYNTHESIS_LARGE_DOCUMENT_WARN_CHARS,
        )

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_MAP_SYSTEM_PROMPT,
            user_prompt=_build_map_user_prompt(doc=doc, topics=topics),
            response_model=_DocumentSummaryResponse,
            what=f"synthèse projet — relevé du document {doc.filename}",
            max_tokens=document_summary_max_tokens(),
        )
    except Exception as exc:
        logger.exception("Échec du relevé du document %s (synthèse projet)", doc.filename)
        return DocumentSummary(
            document_id=doc.document_id,
            filename=doc.filename,
            final_category=doc.final_category,
            error=str(exc),
        )

    requested = {topic.id for topic in topics}
    summaries: dict[str, str] = {}
    covered: set[str] = set()
    for item in parsed.resumes:
        topic_id = item.theme_id.strip()
        if topic_id not in requested:
            logger.warning(
                "Synthèse projet — document %s : theme_id inattendu %r dans le relevé (ignoré)",
                doc.filename,
                item.theme_id,
            )
            continue
        covered.add(topic_id)
        # Les constats redeviennent ici un bloc Markdown à puces : c'est la forme attendue par le
        # prompt de reduce, la liste n'existant que pour cadrer le décodage JSON du map.
        constats = [c.strip().lstrip("-•").strip() for c in item.constats]
        constats = [c for c in constats if c]
        if item.apporte_des_informations and constats:
            summaries[topic_id] = "\n".join(f"- {c}" for c in constats)

    missing = requested - covered
    if missing:
        logger.warning(
            "Synthèse projet — document %s : %d thème(s) demandé(s) absent(s) du relevé (%s) — "
            "repli sur extrait brut pour ces thèmes",
            doc.filename,
            len(missing),
            sorted(missing),
        )

    return DocumentSummary(
        document_id=doc.document_id,
        filename=doc.filename,
        final_category=doc.final_category,
        summaries_by_topic=summaries,
        covered_topic_ids=frozenset(covered),
        model_name=api_model_name,
        error=None,
    )


# --- Étape "reduce" : un appel LLM par thème, alimenté par les relevés --------------------------

_TOPIC_SYSTEM_PROMPT = """Tu es un expert en audit technique et souscription assurance \
construction (SMABTP), en train de rédiger la Phase 1 (synthèse projet) d'un rapport d'analyse \
de dossier de consultation des entreprises (DCE).

Le contexte fourni n'est pas le texte brut du dossier : c'est, pour chaque document pivot de ce \
thème, un relevé factuel de ce que CE document apporte à CE thème, établi lors d'une première \
passe de lecture intégrale, document par document. Chaque relevé reste donc attribué à son \
fichier source.

Règles impératives — fidélité :
- N'utilise QUE les informations présentes dans les relevés fournis ci-dessous et les données \
déjà validées — n'invente jamais une donnée absente.
- Si une information demandée est absente des relevés fournis, dis-le explicitement \
("non précisé dans les documents fournis") plutôt que de l'omettre silencieusement ou de \
l'inventer.
- Confronte les relevés entre eux : si deux documents se contredisent sur une même donnée \
(classement ERP, nombre de niveaux, surfaces, montants, avis émis…), signale la divergence \
explicitement en nommant les deux fichiers sources, au lieu de trancher en silence.
- Indique la source de chaque donnée factuelle. Dans un tableau, c'est le rôle de la colonne \
"Source" : n'y remets pas le mot « Source », juste le nom du fichier. Hors tableau, n'écris PAS le \
nom du fichier entre parenthèses — utilise les étiquettes de renvoi décrites ci-dessous, qui \
deviennent des liens vers le document à l'écran.

Règles impératives — forme. Ce rapport est un outil de travail pour un souscripteur, pas une \
note de synthèse rédigée : il doit se lire et se vérifier vite.
- Respecte strictement le format demandé et, quand des colonnes de tableau sont imposées, \
reprends-les à l'identique, dans l'ordre. Un tableau demandé se rend en tableau Markdown, jamais \
en prose sous un titre.
- N'écris AUCUNE introduction, AUCUNE phrase d'annonce de ce que tu vas dire, AUCUNE conclusion, \
AUCUN résumé ni récapitulatif final, AUCUN commentaire sur ta propre démarche. Commence \
directement par le contenu.
- Ne dis jamais deux fois la même chose : un fait donné dans un tableau ne doit pas être repris \
dans le texte qui l'entoure.
- Pas de gras ni d'italique décoratifs. Le gras ne sert qu'à signaler une divergence entre \
documents ou un point bloquant, jamais à mettre en valeur un mot au fil du texte.
- Pas de titre de niveau 1 ni 2 (# / ##). Si le thème a plusieurs blocs, enchaîne-les sans titre, \
ou avec un titre de niveau 4 (####) au maximum.
- Va droit au fait : une phrase = une information. Supprime les formules de liaison ("par \
ailleurs", "il convient de noter que", "en résumé"), les adjectifs d'appréciation ("ambitieux", \
"remarquable", "notable") et toute analyse que les documents ne portent pas.
- Réponds uniquement avec le contenu Markdown de cette section, sans reprendre le titre de la \
section (déjà affiché séparément dans le rapport).

""" + REFS_PROMPT_RULES


def select_topic_documents(topic: SynthesisTopic, documents: list[DocumentSignal]) -> list[DocumentSignal]:
    """Documents pivots pour ce thème, dans l'ordre de priorité de `pivot_categories` — même
    logique que `reference_candidates` côté extraction, gardée indépendante ici (chaque moteur
    reste autonome, cf. app/pipeline_support.py)."""
    selected: list[DocumentSignal] = []
    for category in topic.pivot_categories:
        selected.extend(d for d in documents if d.final_category == category and d.content_excerpt)
    return selected


@dataclass(frozen=True)
class _TopicContext:
    text: str
    documents_used: list[str]
    documents_degraded: list[str]
    documents_without_info: list[str]
    # Étiquette (`D1`…) → document cité, pour résoudre les renvois du LLM à l'assemblage.
    refs: dict[str, CitationRef] = field(default_factory=dict)


def _build_topic_context(
    topic: SynthesisTopic,
    candidates: list[DocumentSignal],
    summaries: dict[str, DocumentSummary],
    *,
    fallback_total_budget: int = SYNTHESIS_FALLBACK_TOTAL_MAX_CHARS,
    fallback_per_document_budget: int = SYNTHESIS_FALLBACK_PER_DOCUMENT_MAX_CHARS,
) -> _TopicContext:
    """Assemble le contexte du reduce : un bloc par document pivot candidat, toujours nommé par son
    fichier source — c'est ce qui permet au thème de comparer les documents et de signaler une
    divergence (ex. classement ERP différent entre CCTP et arrêté PC).

    Aucun budget ne s'applique aux relevés (tous les documents pivots sont représentés, c'est tout
    l'objet du map-reduce). Le budget ne cadre que le chemin de repli "extrait brut", emprunté
    seulement quand le relevé d'un document n'a pas pu être produit."""
    blocks: list[str] = []
    used: list[str] = []
    degraded: list[str] = []
    without_info: list[str] = []
    refs: dict[str, CitationRef] = {}
    remaining = fallback_total_budget

    # Seuls les documents RÉELLEMENT présents dans le prompt reçoivent une étiquette : le LLM ne
    # peut renvoyer qu'à ce qu'il voit.
    def _header(doc: DocumentSignal, excerpt: str) -> str:
        ref = f"D{len(refs) + 1}"
        # Le nom normalisé (étape 1) est celui que l'expert reconnaît dans l'arborescence : le nom
        # d'origine dans l'archive lui est souvent opaque (export horodaté, sigle de l'auteur…).
        refs[ref] = CitationRef(
            document_id=doc.document_id, filename=doc.final_filename or doc.filename, excerpt=excerpt
        )
        return f"### [{ref}] {doc.filename} (catégorie : {doc.final_category or 'inconnue'})"

    for doc in candidates:
        summary = summaries.get(doc.document_id)

        if summary is not None and topic.id in summary.summaries_by_topic:
            releve = summary.summaries_by_topic[topic.id]
            blocks.append(f"{_header(doc, releve)}\n{releve}")
            used.append(doc.filename)
            continue

        if summary is not None and topic.id in summary.covered_topic_ids:
            # Le document a bien été lu, il n'a simplement rien à dire sur ce thème. On ne recopie
            # pas un bloc vide par document (25 CCTP muets noieraient les relevés utiles) : ils
            # sont regroupés en une seule ligne en fin de contexte, pour que le LLM puisse quand
            # même conclure à une absence d'information en connaissance de cause.
            without_info.append(doc.filename)
            continue

        cap = min(fallback_per_document_budget, remaining)
        excerpt = doc.content_excerpt[:cap] if cap > 0 else ""
        if not excerpt:
            logger.warning(
                "Synthèse projet — thème %s : document %s sans relevé ET budget de repli épuisé, document absent du prompt",
                topic.id,
                doc.filename,
            )
            if call_log.is_enabled():
                call_log.log_truncation(
                    source="synthesis_context_fallback",
                    document=doc.filename,
                    original_chars=len(doc.content_excerpt or ""),
                    kept_chars=0,
                    limit_name="SYNTHESIS_FALLBACK_TOTAL_MAX_CHARS",
                    limit_value=fallback_total_budget,
                    extra={"topic": topic.id, "reason": "fallback_budget_exhausted"},
                )
            continue
        if call_log.is_enabled() and len(excerpt) < len(doc.content_excerpt or ""):
            call_log.log_truncation(
                source="synthesis_context_fallback",
                document=doc.filename,
                original_chars=len(doc.content_excerpt or ""),
                kept_chars=len(excerpt),
                limit_name=(
                    "SYNTHESIS_FALLBACK_PER_DOCUMENT_MAX_CHARS"
                    if cap == fallback_per_document_budget
                    else "SYNTHESIS_FALLBACK_TOTAL_MAX_CHARS"
                ),
                limit_value=cap,
                extra={"topic": topic.id},
            )
        # Repli « extrait brut » : le document reste citable (l'expert doit pouvoir l'ouvrir), mais
        # sans relevé il n'y a pas de paragraphe propre à montrer au survol — d'où l'extrait vide.
        blocks.append(f"{_header(doc, '')} — relevé indisponible, extrait brut du document (tronqué) :\n{excerpt}")
        used.append(doc.filename)
        degraded.append(doc.filename)
        remaining -= len(excerpt)

    if without_info:
        blocks.append(
            "### Documents pivots lus sans information utile pour ce thème\n" + ", ".join(without_info)
        )

    return _TopicContext(
        text="\n\n".join(blocks),
        documents_used=used,
        documents_degraded=degraded,
        documents_without_info=without_info,
        refs=refs,
    )


def _escape_table_cell(value: str) -> str:
    """Une valeur d'extraction peut contenir un « | » (ex. un tableau garantie→montant) ou un
    retour à la ligne : les deux cassent une ligne de tableau Markdown."""
    return value.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()


def _format_extraction_fields_topic(topic: SynthesisTopic, field_values: FieldValues) -> str:
    """Carte d'identité du dossier : reformatage déterministe, sans appel LLM, des valeurs déjà
    résolues et recoupées à l'étape 3.

    Rendu en TABLEAU et non plus en lignes « **libellé :** valeur » : à 17 champs, la forme en
    lignes occupait une page entière de rapport pour ce qui se lit d'un coup d'œil en deux
    colonnes. Les champs non trouvés sont affichés « non trouvé » au lieu d'être omis — l'absence
    d'une donnée de souscription est une information en soi, et une ligne manquante silencieusement
    se confond avec un champ qu'on aurait oublié de demander."""
    rows: list[str] = []
    for field_id in topic.extraction_field_ids:
        pair = field_values.get(field_id)
        if pair is None:
            continue
        libelle, value = pair
        rendered = _escape_table_cell(value) if value else "_non trouvé_"
        rows.append(f"| {_escape_table_cell(libelle)} | {rendered} |")
    if not rows:
        return "_Aucune donnée disponible (étape 3)._"
    return "\n".join(["| Donnée | Valeur |", "|---|---|", *rows])


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
    # Le plafond est répété en fin de prompt (juste avant la consigne de rédaction) : placé
    # uniquement en tête, il se fait oublier derrière les relevés, qui font l'essentiel du contexte.
    budget = (
        f"Volume maximum : {topic.max_lignes} lignes de contenu, lignes de tableau comprises "
        f"(hors ligne d'en-tête et hors ligne de séparation). Ce plafond est un maximum, pas une "
        f"cible : n'écris pas de remplissage pour l'atteindre. Si la matière dépasse ce volume, "
        f"garde les faits les plus déterminants pour la souscription et supprime le reste — jamais "
        f"en tronquant une phrase ou un tableau en cours."
        if topic.max_lignes
        else ""
    )
    budget_header = f"\n{budget}" if budget else ""
    budget_footer = f"\n{budget}\n" if budget else ""
    return f"""Thème à développer : {topic.titre}
Format attendu : {topic.format}{budget_header}

Consigne de rédaction :
{topic.instructions}
{grounding_block}
Relevés factuels établis document par document sur l'ensemble des documents pivots de ce thème :
---
{context}
---
{budget_footer}
Rédige le contenu Markdown de cette section, sans introduction ni conclusion."""


def _no_documents_message(topic: SynthesisTopic) -> str:
    categories = ", ".join(topic.pivot_categories)
    return f"_Aucun document pivot trouvé pour ce thème ({categories}) — section non renseignée._"


def generate_topic(
    topic: SynthesisTopic,
    *,
    documents: list[DocumentSignal],
    field_values: FieldValues,
    summaries: dict[str, DocumentSummary],
) -> TopicOutcome:
    """Étape "reduce" : un seul appel LLM par thème (aucun pour `source: extraction_fields`, simple
    reformatage déterministe des valeurs déjà résolues à l'étape 3), alimenté par les relevés
    produits en amont par `summarize_document` — indexés par `document_id`."""
    if topic.source == "extraction_fields":
        content = _format_extraction_fields_topic(topic, field_values)
        return TopicOutcome(topic_id=topic.id, content_md=content, model_name=None, error=None)

    candidates = select_topic_documents(topic, documents)
    if not candidates:
        return TopicOutcome(topic_id=topic.id, content_md=_no_documents_message(topic), model_name=None, error=None)

    context = _build_topic_context(topic, candidates, summaries)
    grounding = _format_grounding_block(topic, field_values)
    if context.documents_degraded:
        logger.warning(
            "Synthèse projet — thème %s : %d document(s) pivot(s) sans relevé exploitable, repli sur extrait brut tronqué : %s",
            topic.id,
            len(context.documents_degraded),
            context.documents_degraded,
        )

    try:
        parsed, api_model_name = call_structured_chat(
            system_prompt=_TOPIC_SYSTEM_PROMPT,
            user_prompt=_build_topic_user_prompt(topic=topic, grounding=grounding, context=context.text),
            response_model=_TopicResponse,
            what=f"synthèse projet — thème {topic.id}",
        )
    except Exception as exc:
        logger.exception("Échec de la génération du thème %s de la synthèse projet", topic.id)
        return TopicOutcome(
            topic_id=topic.id,
            content_md=None,
            model_name=None,
            error=str(exc),
            documents_used=context.documents_used,
            candidates_count=len(candidates),
            documents_degraded=context.documents_degraded,
            citation_refs=context.refs,
        )

    return TopicOutcome(
        topic_id=topic.id,
        content_md=parsed.contenu,
        model_name=api_model_name,
        error=None,
        documents_used=context.documents_used,
        candidates_count=len(candidates),
        documents_degraded=context.documents_degraded,
        citation_refs=context.refs,
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


# Au-delà de ce nombre, la liste des fichiers exploités est tronquée dans la note de traçabilité.
# Sur un dossier à 24 CCTP par lot, cette ligne atteignait ~1 500 caractères PAR SECTION, soit à
# elle seule une part majeure du rapport — pour une information de second plan, la source précise
# de chaque donnée étant déjà portée par la colonne "Source" des tableaux. Le compte total reste
# affiché, donc rien n'est masqué silencieusement.
SOURCES_NOTE_MAX_FILENAMES = 6


def _format_filenames(filenames: list[str], *, limit: int = SOURCES_NOTE_MAX_FILENAMES) -> str:
    if len(filenames) <= limit:
        return ", ".join(filenames)
    hidden = len(filenames) - limit
    return ", ".join(filenames[:limit]) + f", +{hidden} autre(s)"


def _sources_note(outcome: TopicOutcome) -> str:
    """Ligne de traçabilité sous une section : les fichiers réellement exploités pour ce thème.
    C'est la trace de sourcing retenue en production — les relevés du map n'ont plus à porter une
    citation par phrase (besoin de vérification propre à la phase de test)."""
    if not outcome.candidates_count:
        return ""
    parts: list[str] = []
    if outcome.documents_used:
        parts.append(
            f"_Sources ({len(outcome.documents_used)}) : " + _format_filenames(outcome.documents_used) + "_"
        )
    without_info = outcome.candidates_count - len(outcome.documents_used)
    if without_info > 0:
        parts.append(f"_(+{without_info} pivot(s) sans information utile)_")
    if outcome.documents_degraded:
        # Jamais tronquée : c'est un signal de qualité dégradée, l'expert doit voir tous les
        # fichiers concernés pour juger s'il peut se fier à la section.
        parts.append(
            "_(relevé indisponible, extrait brut tronqué utilisé pour : "
            + ", ".join(outcome.documents_degraded)
            + ")_"
        )
    return " ".join(parts)


@dataclass(frozen=True)
class AssembledReport:
    markdown: str
    # Clé de marqueur (`c1`…) → {document_id, filename, excerpt}, livré à côté du Markdown —
    # même contrat que l'audit (§app/audit/engine.py `AssembledReport`).
    citations: dict[str, dict[str, str]]


def assemble_report(
    outcomes: list[TopicOutcome], schema: SynthesisSchema, *, cartography_md: str | None = None
) -> AssembledReport:
    allocator = CitationAllocator()
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
            # Les thèmes `extraction_fields` sont un reformatage déterministe sans appel LLM : ils
            # n'ont pas d'étiquette, `resolve` les traverse donc sans rien changer.
            body = allocator.resolve(
                outcome.content_md or "_Aucune donnée disponible._",
                scope=topic.id,
                refs=outcome.citation_refs,
            )
            note = _sources_note(outcome)
            if note:
                body += "\n\n" + note
        sections.append(f"## {topic.titre}\n\n{body}")

    return AssembledReport(markdown="\n\n".join(sections) + "\n", citations=allocator.registry)
