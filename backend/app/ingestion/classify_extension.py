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


def classify_extension(ext: str) -> tuple[FileCategory, bool, str | None, bool]:
    """Retourne (catégorie, is_analyzable, motif_si_non_analysable, at_risk).

    `at_risk` distingue, parmi les fichiers non analysables, ceux dont le contenu est
    potentiellement pertinent mais inaccessible au pipeline (extension inconnue/non prise en
    charge : pourrait être une pièce obligatoire dans un format inattendu) des cas anodins
    (plans, fichiers système, dépôt dématérialisé) où l'absence d'analyse n'est pas un signal
    d'alerte. Sert à ne pas noyer les cas à vérifier dans le compteur global "Non analysables"
    (cf. FRICTIONS_EXPERT_METIER.md §5)."""
    ext = ext.lower()
    if ext in DEMATERIALISE_EXTENSIONS:
        return FileCategory.DEMATERIALISE, False, "Fichier de dépôt dématérialisé (non analysable)", False
    if ext == ".pdf":
        return FileCategory.PDF, True, None, False
    if ext == ".docx":
        return FileCategory.DOCX, True, None, False
    if ext == ".doc":
        return FileCategory.DOC, True, None, False
    if ext in IMAGE_EXTENSIONS:
        return FileCategory.IMAGE, True, None, False
    if ext in SPREADSHEET_EXTENSIONS:
        return FileCategory.SPREADSHEET, True, None, False
    if ext == ".zip":
        return FileCategory.ARCHIVE, False, None, False  # motif précisé au cas par cas par l'appelant
    if ext in CAO_EXTENSIONS:
        return (
            FileCategory.OTHER,
            False,
            "Plan CAO (DWG/DXF) hors périmètre OCR — équivalent PDF généralement présent",
            False,
        )
    if ext in SYSTEM_NOISE_EXTENSIONS:
        return FileCategory.OTHER, False, "Fichier système (non analysable)", False
    if ext == "":
        return FileCategory.OTHER, False, "Extension inconnue", True
    return FileCategory.OTHER, False, f"Extension non prise en charge ({ext})", True
