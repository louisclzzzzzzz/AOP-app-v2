"""Localisation d'une citation dans le PDF d'origine — la preuve visuelle de l'étape 3.

À l'écran de validation, l'expert relit une cinquantaine de valeurs. Jusqu'ici il voyait la
valeur, un nom de fichier et, dans le rapport, une citation textuelle : vérifier réellement une
donnée supposait d'ouvrir le PDF et d'y chercher le passage à la main. Ce module trouve la page et
les coordonnées exactes du passage, que le frontend surligne par-dessus son propre rendu de la
page (PDF.js) — ce qui ramène la vérification à un coup d'œil.

Deux décisions structurantes :

1. **Seule la LOCALISATION reste côté serveur** (pdfplumber, pour extraire les mots et leurs
   coordonnées), le RENDU de la page appartient au frontend (PDF.js sur le fichier servi par
   `/file`). Un rendu serveur (pypdfium2 + Pillow, l'approche initiale) empaquetait bien mais
   figeait la preuve en image plate — pas de zoom vectoriel, pas de navigation dans le reste du
   document. pdfplumber reste nécessaire dans les deux approches : lui seul sait faire
   correspondre une citation reformulée par le LLM au texte réel du PDF ; PDF.js sait rendre une
   page mais pas y localiser un passage approximativement cité.

2. **La correspondance est faite sur du texte NORMALISÉ**, jamais littéral. La citation vient du
   LLM : il rétablit les accents, remplace « … » par "…", recolle les mots coupés en fin de ligne,
   normalise les espaces insécables des PDF. Une recherche exacte échouerait sur la majorité des
   citations pourtant parfaitement fidèles.

Quand le PDF n'a pas de couche de texte (document scanné, lu par OCR), les coordonnées n'existent
pas : on retombe sur la recherche de la PAGE dans le texte OCR mis en cache. Le frontend rend
alors la page sans rectangle (une page scannée reste un PDF valide, PDF.js la rend comme les
autres) — mieux vaut ouvrir l'expert à la bonne page sans surlignage que de ne rien montrer.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Au-delà, la citation est presque sûrement une paraphrase tronquée : on cherche sur son début.
_MATCH_PREFIX_LENGTHS = (400, 200, 120, 60, 40)
# En deçà, un fragment devient trop générique pour désigner un passage sans ambiguïté.
_MIN_MATCH_CHARS = 25


@dataclass(frozen=True)
class Rect:
    """Rectangle en points PDF, origine en haut à gauche (convention pdfplumber)."""

    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class CitationLocation:
    page: int  # index 0
    rects: list[Rect] = field(default_factory=list)
    # "pdf_text" : coordonnées exactes ; "ocr_page" : page seule, document scanné.
    method: str = "pdf_text"


# --- Normalisation ------------------------------------------------------------------------------

_QUOTES = str.maketrans({
    "’": "'", "‘": "'", "‚": "'", "‹": "'", "›": "'",
    # Les guillemets DOUBLES disparaissent au lieu d'être unifiés : les français s'écrivent avec
    # une espace interne (« A »), les droits non ("A"), et le LLM passe librement des uns aux
    # autres. Les unifier laissait cette espace parasite et faisait échouer la correspondance.
    "“": None, "”": None, "„": None, "«": None, "»": None, '"': None,
    "–": "-", "—": "-", "‑": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "﻿": " ",
})


def normalize(text: str) -> str:
    """Minuscules, sans accents, ponctuation typographique unifiée, espaces réduits.

    Les accents sautent volontairement : l'OCR et les couches de texte PDF les rendent de façon
    inconstante (parfois en caractères combinants), alors que le LLM les rétablit toujours."""
    text = text.translate(_QUOTES)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalized_words(words: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    """Texte normalisé d'une page + table de correspondance (début, fin, index du mot).

    Le texte est reconstruit mot à mot plutôt que via `page.extract_text()` : c'est le seul moyen
    de remonter d'une position dans la chaîne au rectangle du mot correspondant."""
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, word in enumerate(words):
        token = normalize(word["text"])
        if not token:
            continue
        if parts:
            parts.append(" ")
            cursor += 1
        parts.append(token)
        spans.append((cursor, cursor + len(token), index))
        cursor += len(token)
    return "".join(parts), spans


def _merge_rects_by_line(words: list[dict], indexes: list[int]) -> list[Rect]:
    """Un rectangle par ligne plutôt qu'un par mot : un surlignage continu se lit comme un coup de
    marqueur, une grappe de rectangles mot à mot fait un damier illisible."""
    lines: dict[int, list[dict]] = {}
    for i in indexes:
        word = words[i]
        # Regroupe par ligne à ~2 points près : deux mots d'une même ligne n'ont pas toujours un
        # `top` rigoureusement identique (indices, exposants, changements de police).
        key = round(float(word["top"]) / 2.0)
        lines.setdefault(key, []).append(word)
    rects = []
    for line_words in lines.values():
        rects.append(
            Rect(
                x0=min(float(w["x0"]) for w in line_words),
                top=min(float(w["top"]) for w in line_words),
                x1=max(float(w["x1"]) for w in line_words),
                bottom=max(float(w["bottom"]) for w in line_words),
            )
        )
    return sorted(rects, key=lambda r: (r.top, r.x0))


def _find_in_page(page_text: str, needle: str) -> tuple[int, int] | None:
    """Position ET longueur RÉELLEMENT appariée de la citation dans la page, en essayant des
    préfixes de plus en plus courts.

    Une citation exacte est trouvée du premier coup. Les autres cas sont fréquents et légitimes :
    le LLM coupe une citation trop longue, ou recolle une phrase qui court sur deux colonnes.

    Rendre la longueur appariée n'est pas un détail : quand seul un préfixe correspond, surligner
    sur toute la longueur de la citation marquerait du texte qui n'a PAS été retrouvé — l'expert
    croirait vérifié un passage qui ne l'est pas."""
    if len(needle) >= _MIN_MATCH_CHARS:
        position = page_text.find(needle)
        if position >= 0:
            return position, len(needle)
    for length in _MATCH_PREFIX_LENGTHS:
        if length >= len(needle) or length < _MIN_MATCH_CHARS:
            continue
        position = page_text.find(needle[:length])
        if position >= 0:
            return position, length
    return None


# Marqueurs par lesquels le LLM recolle des passages distincts en une seule citation, ou la
# préfixe du nom du document. Constatés tels quels sur les dossiers réels : « gardes corps bois :
# [...] réalisé en douglas massif [...] » et « 2022.08.01_indA0-G2PRO.pdf / les éléments
# géotechniques… ». Cherchée d'un bloc, une citation pareille est introuvable alors que chacun de
# ses morceaux est bien dans le document.
_FRAGMENT_SEPARATORS = re.compile(r"\[\s*\.\.\.\s*\]|\[\s*…\s*\]|\.\.\.|…|\s/\s")


# Passage que le LLM signale comme repris mot pour mot du document. Les prompts de relevé le lui
# demandent explicitement pour toute donnée contestable (§app/audit/engine.py `_MAP_SYSTEM_PROMPT`,
# app/synthesis/engine.py idem) : c'est donc le seul morceau d'un constat dont on sait qu'il figure
# TEL QUEL dans le fichier. Extrait avant normalisation, qui efface les guillemets.
_QUOTED_RE = re.compile(r"«([^»]+)»")


def _citation_fragments(citation: str) -> list[str]:
    """Fragments à chercher, du plus fiable au plus permissif.

    Les passages entre guillemets français passent en premier : dans un constat de rapport, le
    reste est une reformulation du modèle, qui ne se retrouve pas littéralement dans le PDF. Sans
    cette priorité, une citation dont seul le verbatim est localisable échouait sur la recherche du
    constat entier, puis sur ses préfixes — qui commencent par la partie reformulée."""
    fragments: list[str] = []

    def _ajouter(brut: str) -> None:
        piece = normalize(brut)
        if len(piece) >= _MIN_MATCH_CHARS and piece not in fragments:
            fragments.append(piece)

    for quoted in _QUOTED_RE.findall(citation):
        _ajouter(quoted)
    _ajouter(citation)
    for part in _FRAGMENT_SEPARATORS.split(citation):
        _ajouter(part)
    return fragments


def _match_on_page(words: list[dict], fragments: list[str]) -> list[int]:
    """Indices des mots de la page couverts par au moins un fragment de la citation."""
    page_text, spans = _normalized_words(words)
    matched: set[int] = set()
    for fragment in fragments:
        found = _find_in_page(page_text, fragment)
        if found is None:
            continue
        position, length = found
        end = position + length
        matched.update(index for start, stop, index in spans if start < end and stop > position)
    return sorted(matched)


def locate_in_pdf(pdf_path: Path, citation: str) -> CitationLocation | None:
    """Première page portant la citation, avec les rectangles des mots correspondants.

    Une citation recollée à partir de plusieurs passages donne plusieurs zones surlignées sur la
    page — ce qui reflète fidèlement ce que le LLM a réellement lu.

    `None` si le PDF n'a pas de couche de texte exploitable ou si la citation reste introuvable —
    l'appelant décide alors quoi montrer."""
    fragments = _citation_fragments(citation)
    if not fragments:
        return None

    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            try:
                words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            except Exception:
                logger.warning("Extraction des mots impossible page %d de %s", page_index, pdf_path.name)
                continue
            if not words:
                continue
            matched = _match_on_page(words, fragments)
            if matched:
                return CitationLocation(page=page_index, rects=_merge_rects_by_line(words, matched))
    return None


def locate_in_ocr_pages(page_texts: list[str], citation: str) -> CitationLocation | None:
    """Repli pour les documents scannés : la page seule, cherchée dans le texte OCR mis en cache.

    Aucune coordonnée n'est disponible (le PDF n'a pas de couche de texte, et l'API OCR ne renvoie
    pas de boîtes englobantes en pratique — vérifié sur les dossiers de test, `blocks` toujours
    vide), d'où un surlignage impossible et une page rendue telle quelle."""
    fragments = _citation_fragments(citation)
    if not fragments:
        return None
    for page_index, text in enumerate(page_texts):
        normalized = normalize(text)
        if any(_find_in_page(normalized, fragment) is not None for fragment in fragments):
            return CitationLocation(page=page_index, rects=[], method="ocr_page")
    return None
