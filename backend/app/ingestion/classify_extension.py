"""Classification d'un fichier par extension : catégorie + analysable ou non.

Ceci est un premier tri « grossier » (extension seule), indépendant du moteur de
classification LLM de l'étape 1 (§4.3) qui, lui, combine nom + OCR + LLM.
"""
from __future__ import annotations

from app.store.models import FileCategory

# Dépôt dématérialisé (plateformes marchés publics) : jamais analysable tel quel.
DEMATERIALISE_EXTENSIONS = {".cle", ".cry", ".iv", ".pli", ".xml", ".pde", ".pdp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".csv"}
CAO_EXTENSIONS = {".dwg", ".dxf"}
SYSTEM_NOISE_EXTENSIONS = {".db", ".ini", ".ds_store"}


def classify_extension(ext: str) -> tuple[FileCategory, bool, str | None]:
    """Retourne (catégorie, is_analyzable, motif_si_non_analysable)."""
    ext = ext.lower()
    if ext in DEMATERIALISE_EXTENSIONS:
        return FileCategory.DEMATERIALISE, False, "Fichier de dépôt dématérialisé (non analysable)"
    if ext == ".pdf":
        return FileCategory.PDF, True, None
    if ext == ".docx":
        return FileCategory.DOCX, True, None
    if ext == ".doc":
        return FileCategory.DOC, True, None
    if ext in IMAGE_EXTENSIONS:
        return FileCategory.IMAGE, True, None
    if ext in SPREADSHEET_EXTENSIONS:
        return FileCategory.SPREADSHEET, True, None
    if ext == ".zip":
        return FileCategory.ARCHIVE, False, None  # motif précisé au cas par cas par l'appelant
    if ext in CAO_EXTENSIONS:
        return (
            FileCategory.OTHER,
            False,
            "Plan CAO (DWG/DXF) hors périmètre OCR — équivalent PDF généralement présent",
        )
    if ext in SYSTEM_NOISE_EXTENSIONS:
        return FileCategory.OTHER, False, "Fichier système (non analysable)"
    if ext == "":
        return FileCategory.OTHER, False, "Extension inconnue"
    return FileCategory.OTHER, False, f"Extension non prise en charge ({ext})"
