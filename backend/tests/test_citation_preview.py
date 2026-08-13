"""Localisation d'une citation dans le PDF d'origine (page + coordonnées du passage).

Les PDF de test sont fabriqués avec reportlab (déjà une dépendance du projet, utilisée par
l'export), ce qui donne des documents à vraie couche de texte — donc des coordonnées réelles à
vérifier, pas des données simulées.
"""
from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraction.citation_preview import locate_in_ocr_pages, locate_in_pdf, normalize


def _pdf(tmp_path, pages: list[list[str]], name: str = "doc.pdf"):
    """PDF multi-pages, une chaîne par ligne."""
    path = tmp_path / name
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        y = 780
        for line in lines:
            c.drawString(60, y, line)
            y -= 20
        c.showPage()
    c.save()
    return path


# --- Normalisation ------------------------------------------------------------------------------

def test_normalize_neutralises_accents_typography_and_spacing():
    """La citation vient du LLM : il rétablit les accents et les guillemets français, là où la
    couche de texte du PDF est inconstante. Une comparaison littérale échouerait sur des citations
    pourtant fidèles."""
    assert normalize("Réception   échelonnée") == normalize("RECEPTION ECHELONNEE")
    assert normalize("l’ouvrage « A »") == normalize('l\'ouvrage "A"')
    assert normalize("montant : 1 000") == normalize("montant : 1 000")


# --- Localisation dans un PDF à couche de texte -------------------------------------------------

def test_locate_finds_the_citation_and_returns_its_rectangle(tmp_path):
    path = _pdf(tmp_path, [["Rapport de sondage", "La stratigraphie du sous-sol est decrite ici.", "Fin."]])

    location = locate_in_pdf(path, "La stratigraphie du sous-sol est decrite ici.")

    assert location is not None
    assert location.page == 0
    assert location.method == "pdf_text"
    assert len(location.rects) == 1
    rect = location.rects[0]
    assert rect.x0 > 0 and rect.x1 > rect.x0
    assert rect.bottom > rect.top
    # La ligne visée est la deuxième : son rectangle doit être sous celui du titre, pas dessus.
    titre = locate_in_pdf(path, "Rapport de sondage — introduction")
    assert titre is None or titre.rects[0].top < rect.top


def test_locate_matches_despite_accents_and_typography(tmp_path):
    """Le cas normal, pas un cas limite : le LLM cite « réception échelonnée » là où le PDF porte
    « RECEPTION ECHELONNEE »."""
    path = _pdf(tmp_path, [["Article 5 - RECEPTION ECHELONNEE DES OUVRAGES", "Suite du document."]])

    location = locate_in_pdf(path, "Réception échelonnée des ouvrages")

    assert location is not None
    assert location.page == 0


def test_locate_finds_the_right_page_in_a_multipage_document(tmp_path):
    path = _pdf(tmp_path, [
        ["Page liminaire sans interet particulier pour nous."],
        ["Sommaire general du present document technique."],
        ["Le montant total HT du marche est de 1 000 000 euros."],
    ])

    location = locate_in_pdf(path, "Le montant total HT du marche est de 1 000 000 euros.")

    assert location is not None
    assert location.page == 2


def test_locate_falls_back_to_a_prefix_when_the_citation_is_longer_than_the_document(tmp_path):
    """Le LLM recolle parfois une phrase qui court sur deux colonnes, ou prolonge la citation
    au-delà de ce que porte réellement la page. Le début suffit à désigner le passage."""
    path = _pdf(tmp_path, [["La stratigraphie du sous-sol est decrite au chapitre trois."]])

    location = locate_in_pdf(
        path,
        "La stratigraphie du sous-sol est decrite au chapitre trois, puis reprise en annexe B "
        "avec les coupes geologiques completes et les sondages pressiometriques associes.",
    )

    assert location is not None
    assert location.rects


def test_locate_returns_none_when_absent(tmp_path):
    path = _pdf(tmp_path, [["Un contenu totalement different de ce qui est recherche ici."]])
    assert locate_in_pdf(path, "La stratigraphie du sous-sol est decrite au chapitre trois.") is None


def test_locate_refuses_a_citation_too_short_to_be_unambiguous(tmp_path):
    """Un fragment trop court désignerait n'importe quoi : mieux vaut ne rien surligner que de
    surligner au hasard une preuve que l'expert croira vérifiée."""
    path = _pdf(tmp_path, [["Oui, le batiment B comporte un sous-sol complet."]])
    assert locate_in_pdf(path, "Oui") is None


def test_locate_spans_several_lines(tmp_path):
    path = _pdf(tmp_path, [[
        "La formation est prevue en deux fois : une premiere fois a la",
        "reception ; une deuxieme fois, 6 mois apres la prise en compte.",
    ]])

    location = locate_in_pdf(
        path,
        "La formation est prevue en deux fois : une premiere fois a la reception ; "
        "une deuxieme fois, 6 mois apres la prise en compte.",
    )

    assert location is not None
    assert len(location.rects) == 2  # un rectangle par ligne, pas un par mot


# --- Repli page seule (documents scannés) -------------------------------------------------------

def test_locate_in_ocr_pages_returns_the_page_without_rectangles():
    pages = ["Page de garde.", "Le montant total HT du marche est de 1 000 000 euros."]

    location = locate_in_ocr_pages(pages, "Le montant total HT du marché est de 1 000 000 euros.")

    assert location is not None
    assert location.page == 1
    assert location.rects == []
    assert location.method == "ocr_page"


def test_locate_in_ocr_pages_returns_none_when_absent():
    assert locate_in_ocr_pages(["Rien de tel ici."], "Une citation absente du document scanne.") is None


# --- Citations recollées par le LLM -------------------------------------------------------------

def test_locate_handles_a_citation_stitched_from_several_passages(tmp_path):
    """Cas réel (dce_grand_pic2, champ `ouvrage_bois`) : le LLM recolle des passages distincts
    avec « [...] ». Cherchée d'un bloc, la citation est introuvable alors que chacun de ses
    morceaux est bien dans le document — et chacun mérite d'être surligné."""
    path = _pdf(tmp_path, [[
        "Gardes corps bois : principe general du present lot.",
        "Une ligne intercalaire sans rapport avec la citation.",
        "Gardes corps realise en douglas massif rabote classe 3.",
    ]])

    location = locate_in_pdf(
        path,
        "Gardes corps bois : principe general du present lot. [...] "
        "Gardes corps realise en douglas massif rabote classe 3.",
    )

    assert location is not None
    assert len(location.rects) == 2  # les deux passages, pas la ligne intercalaire


def test_locate_handles_a_citation_prefixed_with_the_filename(tmp_path):
    """Cas réel (champ `etude_de_sol`) : le LLM préfixe la citation du nom du document, absent du
    texte de la page."""
    path = _pdf(tmp_path, [["Les elements geotechniques nouveaux pouvant avoir une influence."]])

    location = locate_in_pdf(
        path,
        "2022.08.01_-indA0-G2PRO.pdf / Les elements geotechniques nouveaux pouvant avoir une influence.",
    )

    assert location is not None
    assert location.rects


def test_locate_in_ocr_pages_also_accepts_a_stitched_citation():
    pages = ["Rien ici.", "Gardes corps realise en douglas massif rabote classe 3 selon plans."]

    location = locate_in_ocr_pages(pages, "Autre chose [...] Gardes corps realise en douglas massif rabote classe 3")

    assert location is not None
    assert location.page == 1
