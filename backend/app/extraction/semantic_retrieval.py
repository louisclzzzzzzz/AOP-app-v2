"""Recherche sémantique (embeddings) pour la sélection des documents candidats de la COUCHE 2
de l'extraction (étape 3).

Pourquoi seulement la couche 2 : la couche 1 interroge les documents de référence du champ
(`reference_categories`), c'est-à-dire des documents dont la CATÉGORIE a déjà été validée par
l'expert à l'étape 1 — leur pertinence ne dépend d'aucun matching textuel. La couche 2, elle,
part à la pêche dans tout le dossier et ne disposait jusqu'ici que du comptage de correspondances
des `indices` du champ (`engine._score_candidate`), avec un filtre dur : score nul = document
écarté. Un document qui contient la bonne réponse mais la formule autrement que ne l'anticipent
les indices (synonyme, tournure, terme technique non prévu) n'était donc JAMAIS proposé au LLM —
le seul angle mort du run automatique, et le seul point où aucune validation humaine ne le
rattrape.

Principe retenu : ne pas remplacer les mots-clés, les FUSIONNER avec un classement sémantique.
- Les mots-clés restent excellents quand ils matchent (précision : « \\bTRC\\b » désigne
  exactement la garantie cherchée) ; les jeter appauvrirait la sélection.
- Le classement sémantique, lui, ne filtre rien : il ordonne TOUS les documents du dossier par
  proximité de sens, y compris ceux dont le score mots-clés est nul — c'est exactement la
  population que l'ancien filtre supprimait.
- La fusion se fait par Reciprocal Rank Fusion (`_RRF_K`), qui combine deux classements sans
  avoir à comparer des échelles incomparables (un compte de motifs et un cosinus). Un document
  bien classé par les deux passe devant ; à égalité, les mots-clés gardent la priorité (le
  comportement historique reste en tête de liste).

Le budget d'appels LLM ne bouge pas : la fusion est plafonnée au même `MAX_LLM_CANDIDATES`.

Découpage : chaque document est vectorisé PAR MORCEAUX (`chunk_max_chars`) et son score est le
meilleur de ses morceaux. Vectoriser un document entier d'un bloc dilue le passage pertinent dans
le reste du texte — un CCTP de 200 pages ressemble d'abord à un CCTP, quelle que soit la donnée
cherchée.

Dégradation : toute défaillance (option désactivée, clé absente, API indisponible, réponse
inattendue) est rattrapée ici et retombe sur le classement par mots-clés seul. Une recherche
élargie moins fine reste infiniment préférable à un run d'extraction interrompu.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import operator
import os
import re
import sys
import time
from array import array
from dataclasses import dataclass
from pathlib import Path

from app.extraction.engine import MAX_LLM_CANDIDATES, keyword_ranked_candidates, split_paragraphs
from app.extraction.extraction_schema import ExtractionField
from app.ingestion.document_signal import DocumentSignal
from app.mistral.client import call_embeddings
from app.settings import get_models_config, get_settings

logger = logging.getLogger(__name__)

# Constante usuelle de la Reciprocal Rank Fusion : amortit l'écart entre les premiers rangs, pour
# qu'un document classé 1er par un seul des deux classements ne balaie pas un document classé 2e
# par les deux.
_RRF_K = 60
# Profondeur retenue dans chaque classement avant fusion. Au-delà, un document n'a plus aucune
# chance d'entrer dans les `MAX_LLM_CANDIDATES` retenus, et le prendre en compte ne ferait
# qu'ajouter du bruit.
_RANK_DEPTH = 10
# Version du format du cache de vecteurs : un cache écrit par une version antérieure est ignoré
# (et réécrit) plutôt que mal relu.
_CACHE_FORMAT = 1


# --- Requête d'un champ -------------------------------------------------------------------------

# Traces de syntaxe regex à retirer des `indices` avant de les donner à vectoriser : elles ne
# véhiculent aucun sens et brouilleraient le vecteur de la requête.
_REGEX_NOISE = re.compile(r"\(\?[^)]*\)|\[[^\]]*\]\?|\.\{[^}]*\}|\\b|\\[dswDSW]|[\\\[\]{}()|^$*+?]")


def _pattern_to_text(pattern: str) -> str:
    """Texte lisible d'un motif compilé (`ExtractionField.indices`) : « dommage[s]? ouvrage » →
    « dommage ouvrage »."""
    return re.sub(r"\s+", " ", _REGEX_NOISE.sub(" ", pattern)).strip()


def field_query_text(extraction_field: ExtractionField) -> str:
    """Texte vectorisé pour représenter le champ cherché.

    On y met le libellé métier, le format attendu ET les indices nettoyés de leur syntaxe regex :
    « Nom du MOA » seul est un vecteur pauvre, alors que le vocabulaire métier du champ
    (« maître d'ouvrage », « MOA ») ancre la requête dans le bon voisinage sémantique — c'est
    précisément ce voisinage, et non la présence littérale des termes, qui fait remonter les
    formulations non anticipées."""
    parts = [extraction_field.libelle]
    if extraction_field.resultat_attendu:
        parts.append(extraction_field.resultat_attendu)
    terms = [t for t in (_pattern_to_text(p.pattern) for p in extraction_field.indices) if t]
    if terms:
        parts.append("Termes associés : " + ", ".join(dict.fromkeys(terms)))
    return " — ".join(parts)


# --- Vecteurs : découpage, normalisation, cache -------------------------------------------------

def chunk_text(text: str, max_chars: int) -> list[str]:
    """Découpe un document en morceaux d'au plus `max_chars`, en respectant les paragraphes.

    Un paragraphe plus long que le budget est tranché brutalement : mieux vaut un morceau coupé au
    milieu d'une phrase qu'un morceau hors gabarit refusé par l'API."""
    max_chars = max(1, max_chars)  # un budget nul ne découperait jamais rien (boucle infinie)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in split_paragraphs(text):
        while len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if current and current_len + len(paragraph) > max_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        if paragraph:
            current.append(paragraph)
            current_len += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _unit(vector: list[float]) -> array:
    """Vecteur ramené à la norme 1, stocké en float32 : la similarité cosinus se réduit alors à un
    produit scalaire (§`_cosine`), et le cache pèse 4 octets par dimension au lieu d'une vingtaine
    en JSON."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return array("f", vector)
    return array("f", [x / norm for x in vector])


def _cosine(a: array, b: array) -> float:
    """Vecteurs déjà normalisés (§`_unit`) : le produit scalaire EST le cosinus.

    Pur Python volontairement, pas de numpy : le projet est empaqueté en exécutable Windows et
    n'a aucune autre dépendance numérique. Le volume reste modeste — quelques milliers de
    morceaux × quelques dizaines de champs manquants, soit un ordre de grandeur de la seconde."""
    return sum(map(operator.mul, a, b))


def _cache_path(text_sha: str) -> Path:
    settings = get_settings()
    return settings.workspace_dir / "cache" / "embeddings" / text_sha[:2] / f"{text_sha}.json"


def _read_cached_vectors(text_sha: str, *, model: str, chunk_max_chars: int) -> list[array] | None:
    """Vecteurs déjà calculés pour ce TEXTE (clé = hash du contenu, comme le cache de texte
    `app/ocr/cache.py`) — donc réutilisés d'un run à l'autre, et entre deux dossiers contenant le
    même document. `None` si absent, illisible, ou calculé avec un autre modèle/découpage."""
    try:
        payload = json.loads(_cache_path(text_sha).read_text(encoding="utf-8"))
        if (
            payload["format"] != _CACHE_FORMAT
            or payload["model"] != model
            or payload["chunk_max_chars"] != chunk_max_chars
            or payload["byteorder"] != sys.byteorder
        ):
            return None
        dim = int(payload["dim"])
        flat = array("f")
        flat.frombytes(base64.b64decode(payload["vectors"]))
        if dim <= 0 or len(flat) % dim:
            return None  # cache tronqué (écriture interrompue) : on le recalcule
        return [flat[i : i + dim] for i in range(0, len(flat), dim)]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cached_vectors(text_sha: str, vectors: list[array], *, model: str, chunk_max_chars: int) -> None:
    """Best-effort : un cache non écrit ne coûte qu'une re-vectorisation au prochain run."""
    if not vectors:
        return
    flat = array("f")
    for vector in vectors:
        flat.extend(vector)
    payload = {
        "format": _CACHE_FORMAT,
        "model": model,
        "chunk_max_chars": chunk_max_chars,
        "byteorder": sys.byteorder,
        "dim": len(vectors[0]),
        "vectors": base64.b64encode(flat.tobytes()).decode("ascii"),
    }
    try:
        path = _cache_path(text_sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Écriture atomique : deux dossiers traités en parallèle peuvent contenir le même document,
        # donc viser le même fichier. Sans le passage par un temporaire propre au processus, un
        # lecteur pourrait tomber sur un fichier à moitié écrit.
        tmp_path = path.with_name(f"{text_sha}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        logger.warning("Cache de vecteurs non écrit pour %s", text_sha[:12], exc_info=True)


# --- Index sémantique du dossier ----------------------------------------------------------------

@dataclass(frozen=True)
class SemanticIndex:
    """Vecteurs des morceaux de chaque document, par id de document."""

    vectors_by_document: dict[str, list[array]]

    def similarity(self, document_id: str, query: array) -> float:
        """Score d'un document = son MEILLEUR morceau, jamais la moyenne : la réponse cherchée
        tient dans un passage, et une moyenne pénaliserait mécaniquement les documents longs —
        or ce sont précisément eux (CCTP, CCAP) qui contiennent les données rares."""
        vectors = self.vectors_by_document.get(document_id)
        if not vectors:
            return -1.0
        return max(_cosine(vector, query) for vector in vectors)


def build_semantic_index(documents: list[DocumentSignal]) -> SemanticIndex:
    """Vectorise (ou relit du cache) tous les documents du dossier ayant du texte.

    Une seule passe pour tout le dossier, quel que soit le nombre de champs manquants : les
    vecteurs de documents ne dépendent pas du champ cherché."""
    cfg = get_models_config().get("embeddings", {})
    model = cfg.get("model", "mistral-embed")
    chunk_max_chars = int(cfg.get("chunk_max_chars", 3000))

    # Tout est indexé par HASH DU TEXTE, pas par document : un même texte apparaissant dans
    # plusieurs documents (pièce jointe dupliquée dans un DCE) n'est vectorisé qu'une fois, comme
    # le cache de texte le fait déjà (§app/ocr/cache.py).
    sha_by_document: dict[str, str] = {}
    vectors_by_sha: dict[str, list[array]] = {}
    chunks_by_sha: dict[str, list[str]] = {}

    for doc in documents:
        if not doc.content_excerpt:
            continue
        text_sha = hashlib.sha256(doc.content_excerpt.encode("utf-8")).hexdigest()
        sha_by_document[doc.document_id] = text_sha
        if text_sha in vectors_by_sha or text_sha in chunks_by_sha:
            continue
        cached = _read_cached_vectors(text_sha, model=model, chunk_max_chars=chunk_max_chars)
        if cached is not None:
            vectors_by_sha[text_sha] = cached
        else:
            chunks_by_sha[text_sha] = chunk_text(doc.content_excerpt, chunk_max_chars)

    if chunks_by_sha:
        from_cache = len(vectors_by_sha)
        flat_chunks = [chunk for chunks in chunks_by_sha.values() for chunk in chunks]
        t0 = time.monotonic()
        raw = call_embeddings(flat_chunks, what=f"vectorisation de {len(chunks_by_sha)} document(s) du dossier")
        cursor = 0
        for text_sha, chunks in chunks_by_sha.items():
            vectors = [_unit(v) for v in raw[cursor : cursor + len(chunks)]]
            cursor += len(chunks)
            vectors_by_sha[text_sha] = vectors
            _write_cached_vectors(text_sha, vectors, model=model, chunk_max_chars=chunk_max_chars)
        logger.info(
            "Couche 2 : %d texte(s) vectorisé(s) en %d morceau(x) en %.1fs, %d relu(s) du cache",
            len(chunks_by_sha), len(flat_chunks), time.monotonic() - t0, from_cache,
        )

    return SemanticIndex(
        vectors_by_document={
            document_id: vectors_by_sha[text_sha]
            for document_id, text_sha in sha_by_document.items()
            if vectors_by_sha.get(text_sha)
        }
    )


def semantic_ranked_candidates(
    extraction_field: ExtractionField,
    documents: list[DocumentSignal],
    *,
    index: SemanticIndex,
    query: array,
    min_similarity: float,
) -> list[DocumentSignal]:
    """Documents du dossier classés par proximité de sens avec le champ, du plus proche au plus
    lointain. Aucun filtre par présence de mots-clés — c'est tout l'intérêt."""
    scored = [
        (index.similarity(doc.document_id, query), doc)
        for doc in documents
        if doc.document_id in index.vectors_by_document
    ]
    ranked = sorted(
        (item for item in scored if item[0] >= min_similarity),
        key=lambda item: item[0],
        reverse=True,
    )
    if ranked:
        logger.info(
            "Couche 2 sémantique — champ %s : %s",
            extraction_field.id,
            ", ".join(f"{doc.filename} ({score:.3f})" for score, doc in ranked[:3]),
        )
    return [doc for _score, doc in ranked]


# --- Fusion des deux classements ----------------------------------------------------------------

def fuse_rankings(rankings: list[list[DocumentSignal]], *, limit: int) -> list[DocumentSignal]:
    """Reciprocal Rank Fusion : score(d) = Σ 1/(k + rang(d)) sur les classements où d apparaît.

    Évite d'avoir à mettre sur la même échelle un compte de motifs et un cosinus. À score égal,
    l'ordre des classements fournis tranche — `select_layer2_candidates` passe les mots-clés en
    premier, de sorte que le comportement historique reste en tête."""
    scores: dict[str, float] = {}
    tiebreak: dict[str, tuple[int, int]] = {}
    docs: dict[str, DocumentSignal] = {}
    for ranking_index, ranking in enumerate(rankings):
        for rank, doc in enumerate(ranking[:_RANK_DEPTH], start=1):
            scores[doc.document_id] = scores.get(doc.document_id, 0.0) + 1.0 / (_RRF_K + rank)
            tiebreak.setdefault(doc.document_id, (ranking_index, rank))
            docs.setdefault(doc.document_id, doc)
    ordered = sorted(scores, key=lambda doc_id: (-scores[doc_id], tiebreak[doc_id]))
    return [docs[doc_id] for doc_id in ordered[:limit]]


def select_layer2_candidates(
    missing_fields: list[ExtractionField], documents: list[DocumentSignal]
) -> dict[str, list[DocumentSignal]]:
    """Documents candidats de la couche 2, par id de champ — mots-clés fusionnés avec la
    recherche sémantique.

    Ne lève jamais : tout échec de vectorisation retombe sur le classement par mots-clés seul,
    c'est-à-dire exactement le comportement d'avant."""
    keyword_rankings = {f.id: keyword_ranked_candidates(f, documents) for f in missing_fields}
    keyword_only = {field_id: ranking[:MAX_LLM_CANDIDATES] for field_id, ranking in keyword_rankings.items()}
    if not missing_fields:
        return keyword_only

    cfg = get_models_config().get("embeddings", {})
    if not cfg.get("enabled", False):
        return keyword_only
    min_similarity = float(cfg.get("min_similarity", 0.0))

    try:
        index = build_semantic_index(documents)
        if not index.vectors_by_document:
            return keyword_only
        queries = call_embeddings(
            [field_query_text(f) for f in missing_fields],
            what=f"vectorisation de {len(missing_fields)} champ(s) manquant(s)",
        )
        query_vectors = {f.id: _unit(v) for f, v in zip(missing_fields, queries)}
    except Exception:
        logger.warning(
            "Couche 2 : recherche sémantique indisponible — repli sur la recherche par mots-clés seule",
            exc_info=True,
        )
        return keyword_only

    return {
        f.id: fuse_rankings(
            [
                keyword_rankings[f.id],
                semantic_ranked_candidates(
                    f, documents, index=index, query=query_vectors[f.id], min_similarity=min_similarity
                ),
            ],
            limit=MAX_LLM_CANDIDATES,
        )
        for f in missing_fields
    }
