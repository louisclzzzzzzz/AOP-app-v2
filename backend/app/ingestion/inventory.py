"""Inventaire : parcourt workspace/<id>/source/ et crée une ligne Document par fichier.

Chaque fichier reçoit : id, hash SHA256, taille, extension, chemin d'origine (relatif),
catégorie, et un statut analysable/non-analysable. Les archives déjà extraites par
`unzip.extract_zip_recursive` sont elles-mêmes inventoriées (non analysables, avec
pointeur vers leur contenu extrait) et leurs enfants portent `parent_archive_id`
pour la traçabilité (§9 : rien n'est perdu, tout est tracé).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.classify.taxonomy import load_taxonomy
from app.ingestion.classify_extension import classify_extension
from app.ingestion.unzip import EXTRACTED_SUFFIX
from app.store.models import Dossier, Document, DocumentStage, FileCategory
from app.store.repository import create_document

_HASH_CHUNK_SIZE = 1024 * 1024
_PLANS_TAXONOMY_PATH = "TECH/PLANS"
_OCR_SKIPPABLE_CATEGORIES = {FileCategory.PDF, FileCategory.IMAGE}
_PLAN_FILENAME_REASON = (
    "Plan identifié par nom de fichier — OCR non nécessaire, classification par nom uniquement"
)
_MACOS_JUNK_REASON = "Fichier de métadonnées macOS (non analysable)"


def _is_macos_junk(filename: str) -> bool:
    """Métadonnées macOS jamais issues d'un vrai document, à exclure de l'analyse quelle que
    soit leur extension apparente : ressources AppleDouble (`._nom`, y compris sous
    `__MACOSX/` où macOS les place systématiquement lors d'une compression) et `.DS_Store`
    (dont le nom n'a PAS de suffixe au sens de `Path.suffix` — un nom commençant par un point
    sans autre point ensuite n'est pas traité comme une extension par pathlib, d'où un
    contrôle par nom plutôt que par `classify_extension`, qui ne verrait jamais ce cas)."""
    return filename.startswith("._") or filename.lower() == ".ds_store"


def _looks_like_plan(filename: str) -> bool:
    """Signal nom de fichier seul (taxonomie TECH/PLANS n'utilise que ce signal, cf.
    `content_indices: []` dans taxonomy.yaml) : évite l'OCR sur les plans, dont le contenu
    graphique n'apporte rien à l'analyse et dont le volume de pages peut être important."""
    plans_category = load_taxonomy().by_path(_PLANS_TAXONOMY_PATH)
    if plans_category is None:
        return False
    return any(p.search(filename) for p in plans_category.filename_patterns)


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _find_extrait_owners(source_dir: Path) -> dict[Path, Path]:
    """Associe chaque dossier `<stem>__extrait/` à son zip d'origine `<stem>.zip`."""
    owners: dict[Path, Path] = {}
    for d in source_dir.rglob(f"*{EXTRACTED_SUFFIX}"):
        if not d.is_dir():
            continue
        stem = d.name[: -len(EXTRACTED_SUFFIX)]
        zip_candidate = d.parent / f"{stem}.zip"
        if zip_candidate.exists():
            owners[d] = zip_candidate
    return owners


def build_inventory(session: Session, dossier: Dossier, source_dir: Path) -> list[Document]:
    all_files = sorted(p for p in source_dir.rglob("*") if p.is_file())
    extrait_owners = _find_extrait_owners(source_dir)
    extracted_zip_paths = set(extrait_owners.values())

    documents: list[Document] = []
    zip_doc_id_by_path: dict[Path, str] = {}

    # 1) Archives d'abord, pour que leurs enfants puissent référencer parent_archive_id — une
    # ressource AppleDouble d'archive (`._nom.zip`) n'est pas une archive, cf. `_is_macos_junk`.
    zip_files = [p for p in all_files if p.suffix.lower() == ".zip" and not _is_macos_junk(p.name)]
    for zpath in zip_files:
        extrait_dir = zpath.parent / f"{zpath.stem}{EXTRACTED_SUFFIX}"
        if zpath in extracted_zip_paths:
            reason = (
                f"Archive extraite : contenu disponible dans "
                f"{extrait_dir.relative_to(source_dir).as_posix()}"
            )
            at_risk = False  # contenu bien analysé, juste référencé sous son dossier __extrait/
        else:
            reason = "Archive non extraite (protégée par mot de passe ou corrompue)"
            at_risk = True  # contenu potentiellement pertinent, totalement inaccessible au pipeline
        doc = create_document(
            session,
            dossier_id=dossier.id,
            relative_path=zpath.relative_to(source_dir).as_posix(),
            filename=zpath.name,
            extension=".zip",
            size_bytes=zpath.stat().st_size,
            sha256=hash_file(zpath),
            category=FileCategory.ARCHIVE.value,
            is_analyzable=False,
            non_analyzable_reason=reason,
            non_analyzable_at_risk=at_risk,
            stage=DocumentStage.NON_ANALYZABLE.value,
        )
        documents.append(doc)
        zip_doc_id_by_path[zpath] = doc.id

    # 2) Tous les autres fichiers (y compris les métadonnées macOS, tracées mais jamais
    # analysées, et les ressources AppleDouble d'archive `._nom.zip` exclues de `zip_files`
    # ci-dessus)
    for p in all_files:
        if p.suffix.lower() == ".zip" and not _is_macos_junk(p.name):
            continue
        parent_archive_id = None
        for extrait_dir, owner_zip in extrait_owners.items():
            if extrait_dir in p.parents:
                parent_archive_id = zip_doc_id_by_path.get(owner_zip)
                break

        ext = p.suffix.lower()
        if _is_macos_junk(p.name):
            category, is_analyzable, reason, at_risk = FileCategory.OTHER, False, _MACOS_JUNK_REASON, False
        else:
            category, is_analyzable, reason, at_risk = classify_extension(ext)
            if is_analyzable and category in _OCR_SKIPPABLE_CATEGORIES and _looks_like_plan(p.name):
                is_analyzable = False
                reason = _PLAN_FILENAME_REASON
                at_risk = False
        doc = create_document(
            session,
            dossier_id=dossier.id,
            relative_path=p.relative_to(source_dir).as_posix(),
            filename=p.name,
            extension=ext,
            size_bytes=p.stat().st_size,
            sha256=hash_file(p),
            category=category.value,
            is_analyzable=is_analyzable,
            non_analyzable_reason=reason,
            non_analyzable_at_risk=at_risk if not is_analyzable else False,
            parent_archive_id=parent_archive_id,
            stage=(
                DocumentStage.DISCOVERED.value
                if is_analyzable
                else DocumentStage.NON_ANALYZABLE.value
            ),
        )
        documents.append(doc)

    return documents
