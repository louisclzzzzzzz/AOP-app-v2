from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.ingestion import text_extraction as te
from app.ocr.service import OcrCallOutcome, OcrPageOutcome
from app.store.models import TextExtractionMethod


def _make_pdf(path: Path, pages_text: list[str | None]) -> None:
    """pages_text[i] = texte dense à écrire sur la page i, ou None pour une page vide."""
    c = canvas.Canvas(str(path))
    for text in pages_text:
        if text:
            y = 800
            # répète le texte sur de nombreuses lignes pour dépasser largement le seuil de densité
            for _ in range(40):
                c.drawString(50, y, text)
                y -= 18
                if y < 50:
                    break
        c.showPage()
    c.save()


def test_dense_native_pdf_skips_ocr_entirely(tmp_path, isolated_workspace, monkeypatch):
    pdf_path = tmp_path / "dense.pdf"
    _make_pdf(pdf_path, ["Ceci est un paragraphe de règlement de consultation bien fourni."] * 2)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_ocr ne doit jamais être appelé sur un PDF nativement dense")

    monkeypatch.setattr(te, "run_ocr", _fail_if_called)

    outcome = te.extract_pdf(pdf_path)
    assert outcome.method == TextExtractionMethod.NATIVE_PDF.value
    assert outcome.avg_confidence == 1.0
    assert outcome.char_count > 0
    assert outcome.page_count == 2


def test_blank_pdf_triggers_full_ocr(tmp_path, isolated_workspace, monkeypatch):
    pdf_path = tmp_path / "blank.pdf"
    _make_pdf(pdf_path, [None, None])

    calls = []

    def _fake_run_ocr(path, *, pages=None):
        calls.append(pages)
        return OcrCallOutcome(
            model="mistral-ocr-test",
            pages=[
                OcrPageOutcome(index=0, markdown="texte OCR page 0", avg_confidence=0.95, min_confidence=0.9, char_count=16),
                OcrPageOutcome(index=1, markdown="texte OCR page 1", avg_confidence=0.92, min_confidence=0.85, char_count=16),
            ],
            combined_markdown="texte OCR page 0\n\ntexte OCR page 1",
            avg_confidence=0.935,
            raw_json="{}",
        )

    monkeypatch.setattr(te, "run_ocr", _fake_run_ocr)

    outcome = te.extract_pdf(pdf_path)
    assert outcome.method == TextExtractionMethod.OCR.value
    assert calls == [None]  # OCR intégral, pas de restriction de pages
    assert outcome.avg_confidence == pytest.approx(0.935)
    assert "texte OCR" in outcome.combined_text


def test_mixed_pdf_ocr_only_low_density_pages(tmp_path, isolated_workspace, monkeypatch):
    pdf_path = tmp_path / "mixed.pdf"
    dense_text = "Cahier des clauses administratives particulières applicables au marché."
    _make_pdf(pdf_path, [dense_text, None])  # page 0 dense, page 1 vide (scan noyé)

    calls = []

    def _fake_run_ocr(path, *, pages=None):
        calls.append(pages)
        return OcrCallOutcome(
            model="mistral-ocr-test",
            pages=[
                OcrPageOutcome(index=1, markdown="contenu OCR de la page scannée", avg_confidence=0.88, min_confidence=0.8, char_count=30),
            ],
            combined_markdown="contenu OCR de la page scannée",
            avg_confidence=0.88,
            raw_json="{}",
        )

    monkeypatch.setattr(te, "run_ocr", _fake_run_ocr)

    outcome = te.extract_pdf(pdf_path)
    assert outcome.method == TextExtractionMethod.MIXED_PDF.value
    assert calls == [[1]]  # seule la page 1 (faible densité) est envoyée à l'OCR
    assert dense_text in outcome.combined_text
    assert "contenu OCR de la page scannée" in outcome.combined_text
    # page native -> confiance 1.0 ; page OCR -> 0.88 -> moyenne = 0.94
    assert outcome.avg_confidence == pytest.approx((1.0 + 0.88) / 2)


def test_doc_without_libreoffice_fails_explicitly(tmp_path, isolated_workspace, monkeypatch):
    """Sans LibreOffice, on ne doit jamais inventer de texte : échec explicite et tracé."""
    monkeypatch.setattr(te, "_find_soffice", lambda: None)
    doc_path = tmp_path / "old.doc"
    doc_path.write_bytes(b"\xd0\xcf\x11\xe0")  # en-tête OLE factice

    outcome = te.extract_doc(doc_path)
    assert outcome.char_count == 0
    assert outcome.error is not None
    assert "LibreOffice" in outcome.error
    assert outcome.method == TextExtractionMethod.DOC_CONVERTED.value


def test_extract_text_for_file_dispatches_by_category(tmp_path, isolated_workspace, monkeypatch):
    called = {}
    monkeypatch.setattr(te, "extract_pdf", lambda p: called.setdefault("pdf", p) or _dummy_outcome())
    outcome = te.extract_text_for_file(tmp_path / "x.pdf", "pdf")
    assert "pdf" in called

    with pytest.raises(ValueError):
        te.extract_text_for_file(tmp_path / "x.zip", "archive")


def _dummy_outcome():
    return te.ExtractionOutcome(
        method="native_pdf", combined_text="x", avg_confidence=1.0, page_count=1, char_count=1
    )
