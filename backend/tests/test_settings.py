from __future__ import annotations

from app.settings import get_models_config


def test_models_config_has_all_required_sections():
    cfg = get_models_config()
    for section in (
        "ocr",
        "llm",
        "extraction",
        "completeness",
        "classification",
        "text_extraction",
        "feature_flags",
    ):
        assert section in cfg, f"section manquante : {section}"

    assert cfg["ocr"]["model"]
    assert cfg["llm"]["temperature"] == 0.0
    assert cfg["feature_flags"]["precompute_rcmo_trc"] is False
    assert cfg["text_extraction"]["scanned_pdf_density_threshold"] < cfg["text_extraction"]["native_text_density_threshold"]
