"""Convertit en PDF le même sous-ensemble Markdown que `app/reports/docx_export.py` (titres `#` à
`######`, tableaux GFM, listes à puces/numérotées avec un niveau de sous-puces indentées, gras
`**...**`, paragraphes, ainsi qu'une ligne de tirets `---` isolée, rendue en filet horizontal —
utilisée par `app/audit/engine.py` `assemble_report` pour séparer les risques d'une même section)
— même logique ligne par ligne, tenue volontairement en miroir pour que les trois exports (écran,
.docx, .pdf) restent cohérents entre eux.

Aucune dépendance à `pandoc` : `reportlab` suffit pour ce sous-ensemble borné, déjà une dépendance
du projet (génération de PDF de test, backend/tests)."""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.store.models import Dossier

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\d+\.\s+(.*)$")
_INDENTED_RE = re.compile(r"^\s+\S")
_BOLD_SPLIT_RE = re.compile(r"(\*\*[^*]+\*\*)")
_HR_RE = re.compile(r"^-{3,}$")

_RULE_COLOR = colors.HexColor("#c2c7d0")
_HEADER_BG = colors.HexColor("#333e70")
_ROW_ALT_BG = colors.HexColor("#f5f6f8")


def _inline_markup(text: str) -> str:
    """Convertit `**gras**` en balisage reportlab (`<b>...</b>`), en échappant le reste — miroir de
    `_add_inline` (docx_export.py) / `parseInline` (Markdown.tsx)."""
    parts = []
    for part in _BOLD_SPLIT_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            parts.append(f"<b>{escape(part[2:-2])}</b>")
        else:
            parts.append(escape(part))
    return "".join(parts)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {f"h{level}": base[f"Heading{level}"] for level in range(1, 7)}
    styles["title"] = base["Title"]
    styles["body"] = ParagraphStyle("ReportBody", parent=base["Normal"], fontSize=10, leading=14, spaceAfter=8)
    styles["bullet"] = ParagraphStyle("ReportBullet", parent=styles["body"], leftIndent=14)
    styles["bullet2"] = ParagraphStyle("ReportBullet2", parent=styles["body"], leftIndent=28)
    styles["table_header"] = ParagraphStyle(
        "ReportTableHeader", parent=base["Normal"], fontSize=9, leading=12, textColor=colors.white,
    )
    styles["table_cell"] = ParagraphStyle("ReportTableCell", parent=base["Normal"], fontSize=9, leading=12)
    return styles


def markdown_to_pdf(markdown_text: str, *, title: str | None = None) -> bytes:
    """Rend `markdown_text` en PDF, retourné en bytes (buffer mémoire, comme `markdown_to_docx`)."""
    styles = _build_styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    story: list = []
    if title:
        story.append(Paragraph(escape(title), styles["title"]))
        story.append(Spacer(1, 12))

    lines = [line.rstrip() for line in markdown_text.replace("\r\n", "\n").split("\n")]
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            story.append(Paragraph(_inline_markup(heading.group(2)), styles[f"h{level}"]))
            i += 1
            continue

        if _HR_RE.match(line.strip()):
            story.append(HRFlowable(width="100%", thickness=0.6, color=_RULE_COLOR, spaceBefore=6, spaceAfter=6))
            i += 1
            continue

        if line.strip().startswith("|") and i + 1 < n and _TABLE_SEPARATOR_RE.match(lines[i + 1].strip()):
            header = _split_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_table_row(lines[i]))
                i += 1

            col_count = len(header)
            table_data = [[Paragraph(_inline_markup(cell), styles["table_header"]) for cell in header]]
            for row_values in rows:
                table_data.append([Paragraph(_inline_markup(cell), styles["table_cell"]) for cell in row_values])

            col_width = document.width / col_count
            table = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT_BG]),
                        ("GRID", (0, 0), (-1, -1), 0.5, _RULE_COLOR),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 10))
            continue

        ul_match = _UL_RE.match(line)
        ol_match = _OL_RE.match(line)
        if ul_match or ol_match:
            ordered = ol_match is not None
            counter = 1
            while i < n:
                marker = _OL_RE.match(lines[i]) if ordered else _UL_RE.match(lines[i])
                if not marker:
                    break
                prefix = f"{counter}. " if ordered else "• "
                text = _inline_markup(marker.group(1))
                counter += 1
                i += 1
                while i < n and _INDENTED_RE.match(lines[i]):
                    continuation = lines[i].strip()
                    sub_ul = _UL_RE.match(continuation)
                    if sub_ul:
                        story.append(Paragraph(f"◦ {_inline_markup(sub_ul.group(1))}", styles["bullet2"]))
                    else:
                        # Suite du même item (texte continu, pas une sous-puce) — miroir des
                        # `extraLines` (`<br/>`) de Markdown.tsx / `add_break()` (docx_export.py).
                        text += f"<br/>{_inline_markup(continuation)}"
                    i += 1
                story.append(Paragraph(f"{prefix}{text}", styles["bullet"]))
            continue

        para_lines: list[str] = []
        while (
            i < n
            and lines[i].strip()
            and not _HEADING_RE.match(lines[i])
            and not _HR_RE.match(lines[i].strip())
            and not _UL_RE.match(lines[i])
            and not _OL_RE.match(lines[i])
            and not lines[i].strip().startswith("|")
        ):
            para_lines.append(lines[i].strip())
            i += 1
        story.append(Paragraph("<br/>".join(_inline_markup(l) for l in para_lines), styles["body"]))

    document.build(story)
    return buffer.getvalue()


def report_pdf_filename(dossier: Dossier) -> str:
    """Nom de fichier proposé au téléchargement — même schéma que `report_docx_filename`."""
    stem = (dossier.original_filename or dossier.id).rsplit(".", 1)[0]
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in stem).strip() or dossier.id
    return f"rapport_{safe}.pdf"
