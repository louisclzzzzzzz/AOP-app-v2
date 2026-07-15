"""Dézippage récursif : gère les zips imbriqués (ex. « ASSURANCES LOT 1 ET 2.zip », « OS.zip »).

La source (workspace/<id>/source/) doit rester la trace fidèle de ce qui a été déposé :
- le zip racine uploadé est extrait DANS source/ (il devient la source).
- tout zip trouvé ensuite À L'INTÉRIEUR de source/ est extrait dans un dossier frère
  `<nom>__extrait/`, sans supprimer ni modifier le zip d'origine (immutabilité, rien n'est perdu).
- récursion bornée (zips de zips de zips...) avec garde anti-boucle / zip bomb.
"""
from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

EXTRACTED_SUFFIX = "__extrait"
MAX_NESTED_DEPTH = 8
# Garde-fou zip bomb : taille totale décompressée max par archive (2 Go)
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """zipfile décode en UTF-8 si le bit 0x800 est posé ; sinon c'est historiquement du
    cp437, mais les zips Windows français utilisent souvent cp850. On tente cp850 en
    priorité (accents français), avec repli propre."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("cp850", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _safe_target(dest_dir: Path, member_name: str) -> Path | None:
    """Résout le chemin cible et rejette toute tentative de zip slip (../)."""
    target = (dest_dir / member_name).resolve()
    try:
        target.relative_to(dest_dir.resolve())
    except ValueError:
        return None
    return target


def extract_zip_flat(zip_path: Path, dest_dir: Path) -> None:
    """Extrait un zip dans dest_dir en corrigeant l'encodage des noms et en bloquant
    le zip slip. Lève zipfile.BadZipFile / RuntimeError si l'archive est corrompue ou
    protégée par mot de passe (laissé à l'appelant : l'archive reste alors non extraite).

    Extraction atomique : on écrit dans un dossier de staging, renommé vers dest_dir
    seulement en cas de succès complet. Ainsi un `__extrait` qui EXISTE veut toujours dire
    « extraction complète et réussie » — jamais un état partiel pris à tort pour un succès."""
    with zipfile.ZipFile(zip_path) as zf:
        total_uncompressed = sum(i.file_size for i in zf.infolist())
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"Archive {zip_path.name} dépasse la taille décompressée maximale autorisée"
            )
        staging = dest_dir.parent / f".{dest_dir.name}.staging-{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=True)
        try:
            for info in zf.infolist():
                name = _decode_member_name(info)
                if not name or name.endswith("/"):
                    continue  # entrée répertoire
                target = _safe_target(staging, name)
                if target is None:
                    continue  # chemin suspect (zip slip) : ignoré, rien d'autre à faire
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    out.write(src.read())
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if dest_dir.exists():
            # Reprise (§9) : on reconstruit dest_dir intégralement depuis le zip source,
            # qui reste la référence faisant foi.
            shutil.rmtree(dest_dir)
        staging.rename(dest_dir)


def extract_zip_recursive(zip_path: Path, dest_dir: Path) -> None:
    """Point d'entrée : extrait le zip racine uploadé dans dest_dir (= source/ du dossier),
    puis décompresse récursivement tout zip imbriqué trouvé à l'intérieur."""
    extract_zip_flat(zip_path, dest_dir)
    _extract_nested(dest_dir, depth=1)


def _extract_nested(root: Path, depth: int) -> None:
    if depth > MAX_NESTED_DEPTH:
        return
    nested_zips = sorted(p for p in root.rglob("*.zip") if p.is_file())
    new_dirs: list[Path] = []
    for zpath in nested_zips:
        extrait_dir = zpath.parent / f"{zpath.stem}{EXTRACTED_SUFFIX}"
        if extrait_dir.exists():
            continue  # déjà traité (idempotence en cas de reprise)
        try:
            extract_zip_flat(zpath, extrait_dir)
            new_dirs.append(extrait_dir)
        except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
            # Archive protégée par mot de passe / corrompue : elle reste telle quelle,
            # inventoriée comme archive non analysable (rien n'est perdu).
            continue
    for d in new_dirs:
        _extract_nested(d, depth + 1)
