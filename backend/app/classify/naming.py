"""Convention de renommage normalisé (§4.3) : [CATEGORIE]_[LOT]_[TYPE]_[libellé court].ext

Le nom d'origine n'est jamais écrasé sur la source (immuable) — cette fonction produit
uniquement le nom cible utilisé dans la copie triée (workspace/<id>/organized/).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def _slug(text: str, *, max_len: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    ascii_text = re.sub(r"-{2,}", "-", ascii_text)
    ascii_text = ascii_text[:max_len].strip("-")
    return ascii_text or "DOCUMENT"


def build_normalized_filename(
    *, category_path: str, lot: str | None, doc_type: str, original_filename: str
) -> str:
    extension = Path(original_filename).suffix.lower()
    stem = Path(original_filename).stem

    categorie_token = _slug(category_path.split("/")[0], max_len=20).upper()
    type_token = _slug(doc_type, max_len=20).upper()
    libelle_token = _slug(stem, max_len=50).upper()

    tokens = [categorie_token]
    if lot:
        tokens.append(_slug(f"LOT{lot}", max_len=20).upper())
    tokens.append(type_token)
    tokens.append(libelle_token)

    return "_".join(tokens) + extension


def dedupe_target_filename(desired_name: str, taken_names: set[str]) -> str:
    """Ajoute un suffixe numérique si `desired_name` est déjà pris dans son dossier cible."""
    if desired_name not in taken_names:
        return desired_name
    stem = Path(desired_name).stem
    extension = Path(desired_name).suffix
    counter = 2
    while True:
        candidate = f"{stem}-{counter}{extension}"
        if candidate not in taken_names:
            return candidate
        counter += 1
