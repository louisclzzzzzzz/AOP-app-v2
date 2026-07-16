"""Validation du checkpoint étape 2 (§5.5) : fige les résultats de complétude et écrit un
rapport JSON + un rapport lisible (Markdown), miroir de `app/classify/reorg.py`.

N'écrit rien sur le système de fichiers `organized/` — la complétude ne copie aucun
document, elle documente l'état de chaque pièce recherchée.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.completeness.pieces_checklist import load_pieces_checklist
from app.store.models import Dossier
from app.store.repository import (
    list_completeness_checks,
    list_documents,
    mark_completeness_validated,
)

REPORT_JSON_FILENAME = "completeness_report.json"
REPORT_MD_FILENAME = "completeness_report.md"


@dataclass
class CompletenessReportEntry:
    piece_id: str
    libelle: str
    phase: str
    obligatoire: bool
    is_selected: bool
    presence: str | None
    certainty: str | None
    justification: str | None
    matched_documents: list[dict]
    matched_lots: dict | None
    manually_corrected: bool
    model: str | None
    model_version: str | None


def validate_completeness(session: Session, dossier: Dossier, *, dossier_dir: Path) -> dict:
    """Fige l'état actuel des `CompletenessCheck` (proposition ou correction humaine) dans un
    rapport. Idempotent : peut être rappelé après une nouvelle correction, régénère
    entièrement le rapport à partir de l'état courant en base."""
    checklist = load_pieces_checklist()
    checks = list_completeness_checks(session, dossier.id)
    documents_by_id = {d.id: d for d in list_documents(session, dossier.id)}

    entries: list[CompletenessReportEntry] = []
    for check in checks:
        piece = checklist.by_id(check.piece_id)
        if piece is None:
            continue
        matched_ids = (
            json.loads(check.proposed_matched_document_ids_json)
            if check.proposed_matched_document_ids_json
            else []
        )
        matched_documents = [
            {
                "document_id": doc_id,
                "filename": documents_by_id[doc_id].filename,
                "relative_path": documents_by_id[doc_id].relative_path,
            }
            for doc_id in matched_ids
            if doc_id in documents_by_id
        ]
        entries.append(
            CompletenessReportEntry(
                piece_id=piece.id,
                libelle=piece.libelle,
                phase=piece.phase,
                obligatoire=piece.obligatoire,
                is_selected=check.is_selected,
                presence=check.final_presence,
                certainty=check.final_certainty,
                justification=check.proposed_justification,
                matched_documents=matched_documents,
                matched_lots=(
                    json.loads(check.proposed_matched_lots_json)
                    if check.proposed_matched_lots_json
                    else None
                ),
                manually_corrected=check.is_manually_corrected,
                model=check.completeness_model,
                model_version=check.completeness_model_version,
            )
        )

    report = _build_report(dossier, entries)
    json_path = dossier_dir / REPORT_JSON_FILENAME
    md_path = dossier_dir / REPORT_MD_FILENAME
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    mark_completeness_validated(session, dossier, json_path=REPORT_JSON_FILENAME, md_path=REPORT_MD_FILENAME)
    return report


def _build_report(dossier: Dossier, entries: list[CompletenessReportEntry]) -> dict:
    return {
        "dossier_id": dossier.id,
        "original_filename": dossier.original_filename,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_pieces_selected": sum(1 for e in entries if e.is_selected),
        "entries": [
            {
                "piece_id": e.piece_id,
                "libelle": e.libelle,
                "phase": e.phase,
                "obligatoire": e.obligatoire,
                "is_selected": e.is_selected,
                "presence": e.presence,
                "certainty": e.certainty,
                "justification": e.justification,
                "matched_documents": e.matched_documents,
                "matched_lots": e.matched_lots,
                "manually_corrected": e.manually_corrected,
                "model": e.model,
                "model_version": e.model_version,
            }
            for e in entries
        ],
    }


_PRESENCE_LABELS = {"present": "Présente", "partial": "Partielle", "absent": "Absente", None: "Non analysée"}
_CERTAINTY_LABELS = {
    "certain": "Certain",
    "probable": "Probable",
    "a_verifier": "À vérifier",
    None: "—",
}


def _render_markdown(report: dict) -> str:
    by_phase: dict[str, list[dict]] = defaultdict(list)
    for e in report["entries"]:
        if e["is_selected"]:
            by_phase[e["phase"]].append(e)

    lines = [
        f"# Rapport de complétude — {report['original_filename']}",
        "",
        f"Généré le {report['generated_at']} — {report['total_pieces_selected']} pièce(s) sélectionnée(s).",
        "",
    ]
    for phase in sorted(by_phase):
        lines.append(f"## Phase {phase}")
        lines.append("")
        for e in sorted(by_phase[phase], key=lambda x: x["libelle"]):
            presence = _PRESENCE_LABELS.get(e["presence"], e["presence"])
            certainty = _CERTAINTY_LABELS.get(e["certainty"], e["certainty"])
            corrected = " (corrigé manuellement)" if e["manually_corrected"] else ""
            lines.append(f"- **{e['libelle']}** — {presence} / sûreté {certainty}{corrected}")
            if e["justification"]:
                lines.append(f"  - {e['justification']}")
            for doc in e["matched_documents"]:
                lines.append(f"  - source : `{doc['relative_path']}`")
        lines.append("")
    return "\n".join(lines)
