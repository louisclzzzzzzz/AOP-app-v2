"""Dézippage récursif : gère les zips imbriqués (ex. « ASSURANCES LOT 1 ET 2.zip », « OS.zip »).

La source (workspace/<id>/source/) doit rester la trace fidèle de ce qui a été déposé :
- le zip racine uploadé est extrait DANS source/ (il devient la source).
- tout zip trouvé ensuite À L'INTÉRIEUR de source/ est extrait dans un dossier frère
  `<nom>__extrait/`, sans supprimer ni modifier le zip d'origine (immutabilité, rien n'est perdu).
- récursion bornée (zips de zips de zips...) avec garde anti-boucle / zip bomb.
"""
from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from pathlib import Path

EXTRACTED_SUFFIX = "__extrait"
MAX_NESTED_DEPTH = 8
# Garde-fou zip bomb : taille totale décompressée max par archive (2 Go)
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024

# Caractères de dessin de boîte / blocs (U+2500-259F) et caractère de remplacement Unicode :
# quasi jamais présents intentionnellement dans un nom de fichier réel. Leur apparition après
# décodage signale presque toujours une page de code mal devinée plutôt qu'un vrai caractère
# voulu (ex. un octet 0xB0 destiné à « ° » en cp1252 redécodé en « ░ » via cp850, ces deux
# pages de code partageant ce point de code pour des caractères différents).
_MOJIBAKE_CHARS = re.compile(r"[─-▟�]")


def _looks_like_mojibake(text: str) -> bool:
    return bool(_MOJIBAKE_CHARS.search(text))


def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """zipfile décode en UTF-8 si le bit 0x800 est posé ; sinon `zipfile` a déjà décodé les
    octets bruts en cp437 en interne (comportement CPython non paramétrable). On les
    ré-encode donc d'abord en cp437 pour récupérer les octets bruts d'origine, puis on
    retente cp850 (accents français, page OEM historique) en priorité, cp1252 (Windows
    ANSI) ensuite.

    Ambiguïté irréductible : un même octet peut être un caractère valide dans plusieurs de
    ces pages de code à la fois (ex. 0xB0 = « ░ » en cp850 mais « ° » en cp1252), donc
    l'absence d'erreur de décodage ne garantit pas un résultat correct. On rejette les
    résultats contenant des caractères de dessin de boîte/blocs (quasi jamais présents
    intentionnellement dans un nom de fichier réel) et on passe à la page de code suivante —
    ça a concrètement révélé et corrigé un cas réel où « N°1 » ressortait en « N░1 »."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    best_effort: str | None = None
    for enc in ("cp850", "cp1252", "utf-8", "latin-1"):
        try:
            decoded = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if best_effort is None:
            best_effort = decoded
        if not _looks_like_mojibake(decoded):
            return decoded
    return best_effort if best_effort is not None else info.filename


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
