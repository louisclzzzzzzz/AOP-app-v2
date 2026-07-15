"""Validation golden-set (§9 du PLAN) du signal 1 (nom de fichier) : noms réels tirés des 6
dossiers de référence triés par l'expert (arborescence.md). Le signal filename est
volontairement ambigu sur certains noms (ex. "RC 2024.pdf" ne dit pas si c'est le RC de la
consultation ou de l'assurance) — c'est exactement pour ces cas que les signaux 2 (contenu) et
3 (LLM) existent (§4.3). Ce test couvre les noms où le signal filename doit, à lui seul,
pointer vers la bonne catégorie de façon fiable."""
from __future__ import annotations

import pytest

from app.classify.engine import extract_lot_signal, score_filename
from app.classify.taxonomy import load_taxonomy

# (nom de fichier réel, catégorie attendue en tête de classement) — extraits des 6 dossiers de
# arborescence.md : AO24_MARLY LE ROI, AO25_TAVERNY, AO25_HERBLAY, AO26_CH LEON BINET,
# AO26_VAL DU LOING, 2026_BASIC_ACCORD CADRE.
GOLDEN_FILENAME_CASES = [
    ("AAPC.pdf", "ADMIN/AAPC"),
    ("AAPC 26-52993.pdf", "ADMIN/AAPC"),
    ("C.DC1_SMABTP.pdf", "ENVOI DEMAT/CANDIDATURE"),
    ("C.DC2_SMABTP.pdf", "ENVOI DEMAT/CANDIDATURE"),
    ("C.KBIS 14032024_SMABTP.pdf", "ENVOI DEMAT/CANDIDATURE"),
    ("C.URSSAF-Attestationdevigilance6mois-31102024_SMABTP.pdf", "ENVOI DEMAT/CANDIDATURE"),
    ("C.Pouvoir signature_A-Lesur_SMABTP.pdf", "ENVOI DEMAT/CANDIDATURE"),
    ("O.Memoire_de_gestion_construction_2024_SMABTP.pdf", "ENVOI DEMAT/OFFRE"),
    ("O.Declaration sur honneur _SMABTP.pdf", "ENVOI DEMAT/OFFRE"),
    ("Preuve depot.pdf", "ENVOI DEMAT/COPIE DEPOT"),
    ("Mail confirmation depot.pdf", "ENVOI DEMAT/COPIE DEPOT"),
    ("CRC 48.pdf", "ASS/LISTE INTERVENANTS"),
    ("COMMUNE MARLY LE ROI Derogation IARD 2024 DO 20%.pdf", "ASS/DEROG COM"),
    ("COMMUNE DE TAVERNY Derogation IARD 2025 DO 10%.pdf", "ASS/DEROG COM"),
    ("MLR  _ DCE _ B.3.0 _ CCTP COMMUN.pdf", "ASS/CCTP"),
    ("31433 A Marly le Roi _78_ G2 PRO.pdf", "TECH/ETUDE DE SOL"),
    ("CONSERVATOIRE ETUDES DE SOL G1.pdf", "TECH/ETUDE DE SOL"),
    ("Rapport SAGA 13490 P2 V1 _ Mission G2 PRO _ HERBLAY SUR SEINE _95_.pdf", "TECH/ETUDE DE SOL"),
    ("MLR CONSERVATOIRE _ DCE _ A.3 _ STRUCTURE BETON.pdf", "TECH/PLANS"),
    ("53864563_RIT_10.PDF", "TECH/RICT"),
    ("RICT W0_6098_004_PRO.pdf", "TECH/RICT"),
    ("ETUDE DE RISQUE - fiche SOCABAT.pdf", "TECH/SOCABAT"),
    ("Avis socabat.pdf", "TECH/SOCABAT"),
    ("ARRETE ET AVIS PC 078 372 24 G 0002.pdf", "TECH/ARRETE PC"),
    ("Avis PC Commune de Taverny.pdf", "TECH/ARRETE PC"),
    ("ACTE D ENGAGEMENT.pdf", "1.ETUDE BD"),
    ("ECO 01 - Notice descriptive.pdf", "TECH/NOTICE"),
    ("Taverny - Centre sportif - Planning TCE indice A - Format A3.pdf", "TECH/PLANNING"),
    ("questions_reponses du 200325.pdf", "QR"),
    ("16122025 questions_reponses.pdf", "QR"),
    ("Fiche de missionnement pour Útude AOP.pdf", "ADMIN/GAN"),
    ("144659160 PHILIPPON Attestation_RC_dÚcennale_2024.pdf", "ASS/ATT ASS/ENT"),
    ("TURBO ENERGY Attestation ResponsabilitÚ civile et dÚcennale 2024.pdf", "ASS/ATT ASS/ENT"),
]


@pytest.mark.parametrize("filename,expected_category", GOLDEN_FILENAME_CASES)
def test_filename_signal_top_match(filename: str, expected_category: str) -> None:
    taxonomy = load_taxonomy()
    matches = score_filename(filename, taxonomy)
    assert matches, f"aucune correspondance filename pour {filename!r} (attendu {expected_category})"
    assert matches[0].category_path == expected_category, (
        f"{filename!r} -> {matches[0].category_path}, attendu {expected_category} "
        f"(tous : {[m.category_path for m in matches]})"
    )


def test_ambiguous_filenames_yield_no_or_multiple_candidates():
    """Ces noms réels sont volontairement ambigus au niveau du nom seul (ex. "RC 2024.pdf"
    existe à la fois côté ADMIN et côté ASS) — le signal filename ne doit pas trancher tout
    seul avec une fausse confiance ; c'est le rôle du contenu + LLM (§4.3)."""
    taxonomy = load_taxonomy()
    matches = score_filename("RC 2024.pdf", taxonomy)
    assert matches == []


@pytest.mark.parametrize(
    "text,expected_lot",
    [
        ("MLR _ DCE _ B.3.1.1 _ LOT 01 _ CAHIER 01 _ INSTAL_CURAGE_GROS OEUVRE.pdf", "01"),
        ("AE LOT 1 ET 2  DO CONSERVATOIRE.docx", "1 ET 2"),
        ("LOT 3-4 dossier.pdf", "3-4"),
        # Sans connecteur explicite (virgule/tiret/"et"), seul le premier nombre est capturé —
        # comportement volontairement conservateur : ce signal alimente le LLM, il ne tranche
        # jamais seul, donc mieux vaut sous-capturer que fusionner un numéro de lot avec un
        # numéro de cahier/année qui suit par hasard.
        ("Lot 3 4.pdf", "3"),
        ("Aucun numéro ici.pdf", None),
    ],
)
def test_extract_lot_signal(text: str, expected_lot: str | None) -> None:
    assert extract_lot_signal(text) == expected_lot
