"""Petits utilitaires HTTP partagés par les adaptateurs de retrait de DCE."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import httpx

# Identification honnête du client : les plateformes de dématérialisation ont le droit de
# savoir qui les interroge, et un User-Agent explicite permet à un éditeur de nous contacter
# plutôt que de nous bloquer en aveugle. Aucune tentative de se faire passer pour un navigateur.
# Strictement ASCII : httpx encode les en-têtes en ascii et lèverait sur un accent.
USER_AGENT = "AOP-v2/1.0 (veille marches publics assurance construction; retrait DCE)"

ARCHIVE_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar-compressed",
}

_FILENAME_STAR_RE = re.compile(r"filename\*=(?:UTF-8'')?(?P<name>[^;]+)", re.IGNORECASE)
_FILENAME_RE = re.compile(r'filename="?(?P<name>[^";]+)"?', re.IGNORECASE)


def filename_from_response(response: httpx.Response) -> str | None:
    """Nom de fichier annoncé par le serveur (`Content-Disposition`), si exploitable.

    Utile pour la traçabilité : le nom donné par la plateforme porte souvent la référence de
    la consultation, information qu'on ne veut pas perdre en renommant le zip nous-mêmes."""
    disposition = response.headers.get("content-disposition") or ""
    match = _FILENAME_STAR_RE.search(disposition) or _FILENAME_RE.search(disposition)
    if not match:
        return None
    name = unquote(match.group("name").strip().strip('"'))
    # On ne garde que le nom de base : un `Content-Disposition` malveillant ne doit pas pouvoir
    # remonter l'arborescence (même précaution que le dézippage, cf. app/ingestion/unzip.py).
    return Path(name.replace("\\", "/")).name or None


def is_archive(content: bytes) -> bool:
    """Reconnaît une archive à sa signature plutôt qu'à son extension ou son content-type :
    les plateformes annoncent volontiers `application/octet-stream`, voire `text/html`, sur un
    zip parfaitement valide."""
    return content[:2] == b"PK" or content[:6] == b"7z\xbc\xaf\x27\x1c" or content[:4] == b"Rar!"


def write_archive(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
