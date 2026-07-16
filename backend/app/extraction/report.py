"""Validation du checkpoint étape 3 (§6.4) : fige les résultats d'extraction et écrit un
rapport JSON + un rapport lisible (Markdown), miroir de `app/completeness/report.py`.

N'écrit rien sur le système de fichiers `organized/` — l'extraction ne copie aucun document,
elle documente la valeur, la source et la preuve de chaque donnée.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.extraction.extraction_schema import load_extraction_schema
from app.store.models import Dossier
from app.store.repository import list_documents, list_extraction_results, mark_extraction_validated

REPORT_JSON_FILENAME = "extraction_report.json"
REPORT_MD_FILENAME = "extraction_report.md"


@dataclass
class ExtractionReportEntry:
    field_id: str
    libelle: str
    section: str
    value: str | None
    justification: str | None
    citation: str | None
    sources: list[dict]
    cross_check_status: str | None
    manually_corrected: bool
    model: str | None
    model_version: str | None


def validate_extraction(session: Session, dossier: Dossier, *, dossier_dir: Path) -> dict:
    """Fige l'état actuel des `ExtractionResult` (proposition ou correction humaine) dans un
    rapport. Idempotent : peut être rappelé après une nouvelle correction, régénère
    entièrement le rapport à partir de l'état courant en base."""
    schema = load_extraction_schema()
    results = list_extraction_results(session, dossier.id)
    documents_by_id = {d.id: d for d in list_documents(session, dossier.id)}

    entries: list[ExtractionReportEntry] = []
    for result in results:
        f = schema.by_id(result.field_id)
        if f is None:
            continue
        sources = json.loads(result.proposed_sources_json) if result.proposed_sources_json else []
        for source in sources:
            doc = documents_by_id.get(source["document_id"])
            source["relative_path"] = doc.relative_path if doc else None
        entries.append(
            ExtractionReportEntry(
                field_id=f.id,
                libelle=f.libelle,
                section=f.section,
                value=result.final_value,
                justification=result.proposed_justification,
                citation=result.proposed_citation,
                sources=sources,
                cross_check_status=result.cross_check_status,
                manually_corrected=result.is_manually_corrected,
                model=result.extraction_model,
                model_version=result.extraction_model_version,
            )
        )

    report = _build_report(dossier, entries)
    json_path = dossier_dir / REPORT_JSON_FILENAME
    md_path = dossier_dir / REPORT_MD_FILENAME
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    mark_extraction_validated(session, dossier, json_path=REPORT_JSON_FILENAME, md_path=REPORT_MD_FILENAME)
    return report


def _build_report(dossier: Dossier, entries: list[ExtractionReportEntry]) -> dict:
    return {
        "dossier_id": dossier.id,
        "original_filename": dossier.original_filename,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_fields": len(entries),
        "entries": [
            {
                "field_id": e.field_id,
                "libelle": e.libelle,
                "section": e.section,
                "value": e.value,
                "justification": e.justification,
                "citation": e.citation,
                "sources": e.sources,
                "cross_check_status": e.cross_check_status,
                "manually_corrected": e.manually_corrected,
                "model": e.model,
                "model_version": e.model_version,
            }
            for e in entries
        ],
    }


_SECTION_LABELS = {"principal": "Données principales", "complementaire": "Informations complémentaires"}
_CROSS_CHECK_LABELS = {
    "coherent": "Recoupement cohérent",
    "incoherent": "⚠ Recoupement incohérent",
    "single_source": "Source unique",
    "not_applicable": None,
    None: None,
}


def _render_markdown(report: dict) -> str:
    by_section: dict[str, list[dict]] = defaultdict(list)
    for e in report["entries"]:
        by_section[e["section"]].append(e)

    lines = [
        f"# Rapport d'extraction — {report['original_filename']}",
        "",
        f"Généré le {report['generated_at']} — {report['total_fields']} champ(s).",
        "",
    ]
    for section in sorted(by_section, key=lambda s: (s != "principal", s)):
        lines.append(f"## {_SECTION_LABELS.get(section, section)}")
        lines.append("")
        for e in sorted(by_section[section], key=lambda x: x["libelle"]):
            value = e["value"] if e["value"] else "*(non trouvée)*"
            corrected = " (corrigé manuellement)" if e["manually_corrected"] else ""
            tag = _CROSS_CHECK_LABELS.get(e["cross_check_status"])
            tag_suffix = f" — {tag}" if tag else ""
            lines.append(f"- **{e['libelle']}** — {value}{corrected}{tag_suffix}")
            if e["justification"]:
                lines.append(f"  - {e['justification']}")
            for src in e["sources"]:
                lines.append(f"  - source : `{src.get('relative_path') or src['filename']}` — « {src['value']} »")
        lines.append("")
    return "\n".join(lines)
