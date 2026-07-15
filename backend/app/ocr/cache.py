"""Cache persistant de texte extrait (SQLite + fichiers .md), clé par hash de contenu.

`workspace/cache/text/<hash[:2]>/<hash>.md` porte le texte ; `<hash>.ocr.json` (si présent)
porte la réponse OCR brute (bounding boxes, blocks) pour une citation précise ultérieure.
"""
from __future__ import annotations

from pathlib import Path

from app.settings import get_settings


def _cache_dir_for_hash(content_hash: str) -> Path:
    settings = get_settings()
    d = settings.workspace_dir / "cache" / "text" / content_hash[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_text_cache_files(
    content_hash: str, markdown: str, raw_json: str | None
) -> tuple[str, str | None]:
    """Écrit le(s) fichier(s) de cache et retourne des chemins RELATIFS à workspace_dir
    (c'est ce qui est stocké en DB, pour rester portable si workspace_dir change)."""
    settings = get_settings()
    d = _cache_dir_for_hash(content_hash)

    md_path = d / f"{content_hash}.md"
    md_path.write_text(markdown, encoding="utf-8")

    json_rel: str | None = None
    if raw_json is not None:
        json_path = d / f"{content_hash}.ocr.json"
        json_path.write_text(raw_json, encoding="utf-8")
        json_rel = str(json_path.relative_to(settings.workspace_dir))

    return str(md_path.relative_to(settings.workspace_dir)), json_rel


def read_text_cache(text_path_relative: str) -> str:
    settings = get_settings()
    return (settings.workspace_dir / text_path_relative).read_text(encoding="utf-8")
