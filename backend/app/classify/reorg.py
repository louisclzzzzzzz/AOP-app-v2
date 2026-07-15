"""Application de la copie triée (§4.4) : copie (jamais déplacement) des fichiers de
workspace/<dossier_id>/source/ vers workspace/<dossier_id>/organized/ selon la classification
finale (proposition du moteur, éventuellement corrigée par l'utilisateur au checkpoint).

La source n'est JAMAIS modifiée — uniquement lue. Rien n'est perdu : tout document, même
classé "AUTRES", est copié. Un rapport JSON + un rapport lisible (Markdown) tracent
intégralement le mapping source -> cible avec confiance et justification.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.classify.naming import dedupe_target_filename
from app.classify.taxonomy import load_taxonomy
from app.store.models import Dossier, Document
from app.store.repository import list_documents, mark_reorg_applied, set_document_organized_path

REPORT_JSON_FILENAME = "organized_report.json"
REPORT_MD_FILENAME = "organized_report.md"


@dataclass
class ReorgEntry:
    document_id: str
    source_relative_path: str
    target_relative_path: str
    category: str
    lot: str | None
    doc_type: str | None
    confidence: float | None
    justification: str | None
    is_manually_corrected: bool
    classification_model: str | None
    classification_model_version: str | None


def _sanitize_lot_folder(lot: str) -> str:
    cleaned = lot.replace("/", "-").replace("\\", "-").strip()
    return f"LOT {cleaned}" if cleaned else "LOT"


def _target_dir_for(organized_root: Path, category: str, lot: str | None) -> Path:
    target_dir = organized_root / Path(category)
    if lot:
        target_dir = target_dir / _sanitize_lot_folder(lot)
    return target_dir


def apply_reorganization(session: Session, dossier: Dossier, *, source_dir: Path, organized_root: Path) -> dict:
    """Copie chaque document vers sa destination finale. Idempotent : reconstruit
    entièrement organized/ à partir de l'état actuel de classification (résumable sans
    accumuler d'anciennes copies obsolètes)."""
    taxonomy = load_taxonomy()
    fallback = taxonomy.fallback_category

    if organized_root.exists():
        shutil.rmtree(organized_root)
    organized_root.mkdir(parents=True, exist_ok=True)

    documents = list_documents(session, dossier.id)
    taken_names_by_dir: dict[Path, set[str]] = defaultdict(set)
    entries: list[ReorgEntry] = []

    for document in documents:
        category = document.final_category or fallback
        lot = document.final_lot
        doc_type = document.final_doc_type
        desired_name = document.final_filename or document.filename

        target_dir = _target_dir_for(organized_root, category, lot)
        target_dir.mkdir(parents=True, exist_ok=True)

        final_name = dedupe_target_filename(desired_name, taken_names_by_dir[target_dir])
        taken_names_by_dir[target_dir].add(final_name)

        source_path = source_dir / document.relative_path
        target_path = target_dir / final_name
        shutil.copy2(source_path, target_path)

        target_relative = str(target_path.relative_to(organized_root))
        set_document_organized_path(session, document, target_relative)

        entries.append(
            ReorgEntry(
                document_id=document.id,
                source_relative_path=document.relative_path,
                target_relative_path=target_relative,
                category=category,
                lot=lot,
                doc_type=doc_type,
                confidence=document.classification_confidence,
                justification=document.classification_justification,
                is_manually_corrected=document.is_manually_corrected,
                classification_model=document.classification_model,
                classification_model_version=document.classification_model_version,
            )
        )

    report = _build_report(dossier, entries)
    dossier_dir = organized_root.parent
    json_path = dossier_dir / REPORT_JSON_FILENAME
    md_path = dossier_dir / REPORT_MD_FILENAME
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    mark_reorg_applied(session, dossier, json_path=REPORT_JSON_FILENAME, md_path=REPORT_MD_FILENAME)
    return report


def _build_report(dossier: Dossier, entries: list[ReorgEntry]) -> dict:
    return {
        "dossier_id": dossier.id,
        "original_filename": dossier.original_filename,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_files": len(entries),
        "entries": [
            {
                "document_id": e.document_id,
                "source": e.source_relative_path,
                "target": e.target_relative_path,
                "category": e.category,
                "lot": e.lot,
                "doc_type": e.doc_type,
                "confidence": e.confidence,
                "justification": e.justification,
                "manually_corrected": e.is_manually_corrected,
                "model": e.classification_model,
                "model_version": e.classification_model_version,
            }
            for e in entries
        ],
    }


def _render_markdown(report: dict) -> str:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for e in report["entries"]:
        by_category[e["category"]].append(e)

    lines = [
        f"# Rapport de réorganisation — {report['original_filename']}",
        "",
        f"Généré le {report['generated_at']} — {report['total_files']} fichiers copiés.",
        "",
        "La source d'origine n'a jamais été modifiée ; ceci est une copie triée.",
        "",
    ]
    for category in sorted(by_category):
        lines.append(f"## {category}")
        lines.append("")
        for e in sorted(by_category[category], key=lambda x: x["target"]):
            conf = f"{e['confidence']:.2f}" if e["confidence"] is not None else "?"
            corrected = " (corrigé manuellement)" if e["manually_corrected"] else ""
            lines.append(f"- `{e['source']}` → `{e['target']}` — confiance {conf}{corrected}")
            if e["justification"]:
                lines.append(f"  - {e['justification']}")
        lines.append("")
    return "\n".join(lines)
