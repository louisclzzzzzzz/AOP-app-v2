"""Retrait du DCE : aiguillage vers la bonne plateforme, et remplissage du formulaire Atexo.

Le HTML de `_ATEXO_FORM_HTML` reproduit la structure réelle du formulaire de retrait de PLACE
(noms de contrôles PRADO relevés le 11/08/2026 sur marches-publics.gouv.fr), réduite aux
éléments que l'adaptateur exploite.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.veille.retrieval import RetrievalStatus, fetch_dce, plan_retrieval
from app.veille.retrieval.atexo import (
    AtexoAdapter,
    _find_field,
    _parse_inputs,
    _radio_value,
    _to_dce_request_url,
)
from app.veille.retrieval.direct import DirectArchiveAdapter
from app.veille.retrieval.http import filename_from_response, is_archive

_P = "ctl0$CONTENU_PAGE$EntrepriseFormulaireDemande$"

_ATEXO_FORM_HTML = f"""
<html><body><form method="post">
  <input type="hidden" name="_csrf_token" value="tok-123" />
  <input type="text" style="display:none" name="PRADO_PAGESTATE" value="state-abc" />
  <input type="radio" id="ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_choixTelechargement"
         name="{_P}RadioGroup" value="{_P}choixTelechargement" />
  <input type="radio" id="ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_choixAnonyme"
         name="{_P}RadioGroup" value="{_P}choixAnonyme" />
  <input type="text" name="{_P}nom" value="" />
  <input type="text" name="{_P}prenom" value="" />
  <input type="text" name="{_P}email" value="" />
  <input type="text" name="{_P}raisonSocial" value="" />
  <input type="text" name="{_P}siret" value="" />
  <input type="text" name="{_P}tel" value="" />
  <input type="checkbox" name="{_P}accepterConditions" />
  <input type="submit" name="ctl0$CONTENU_PAGE$validateButton" value="Valider" />
</form></body></html>
"""

_ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 64


@pytest.fixture
def contact_identity(monkeypatch):
    """Identité de retrait configurée, comme elle le serait dans `.env`."""
    from app.settings import get_settings

    monkeypatch.setenv("AOP_VEILLE_CONTACT_NOM", "Cluzel")
    monkeypatch.setenv("AOP_VEILLE_CONTACT_PRENOM", "Louis")
    monkeypatch.setenv("AOP_VEILLE_CONTACT_EMAIL", "louis@example.com")
    monkeypatch.setenv("AOP_VEILLE_CONTACT_RAISON_SOCIALE", "SMABTP")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Aiguillage ------------------------------------------------------------


def test_plan_annonce_une_plateforme_a_captcha_comme_non_automatisable():
    platform, automatable = plan_retrieval(
        "https://www.marches-publics.info/mpiaws/index.cfm?fuseaction=dematEnt.login&type=DCE&IDM=1"
    )
    assert platform == "AWS / marches-publics.info"
    assert automatable is False


def test_plan_reconnait_atexo_sur_la_signature_d_url_pas_sur_le_domaine():
    """Les portails Atexo sont hébergés sous des dizaines de domaines : c'est l'URL qui
    identifie le moteur, pas une liste de noms de domaine impossible à tenir à jour."""
    for host in ("www.marches-publics.gouv.fr", "marchespublics.oise.fr", "portail-inconnu.fr"):
        platform, automatable = plan_retrieval(f"https://{host}/?page=Entreprise.EntrepriseDetailsConsultation&id=X")
        assert (platform, automatable) == ("Atexo / PLACE", True)


def test_plan_sans_lien_dce():
    assert plan_retrieval(None) == ("inconnue", False)


def test_fetch_sans_lien_demande_un_retrait_manuel(tmp_path):
    outcome = fetch_dce(None, tmp_path / "dce.zip")
    assert outcome.status == RetrievalStatus.MANUAL_REQUIRED
    assert not outcome.ok


def test_fetch_sur_plateforme_a_captcha_ne_tente_rien(tmp_path):
    """Aucune requête n'est émise : le captcha est une protection délibérée de l'éditeur, la
    seule issue honnête est d'annoncer un retrait manuel."""
    outcome = fetch_dce("https://www.marches-securises.fr/entreprise/?dce=1", tmp_path / "dce.zip")
    assert outcome.status == RetrievalStatus.MANUAL_REQUIRED
    assert "captcha" in outcome.message


def test_fetch_sur_plateforme_inconnue_demande_un_retrait_manuel(tmp_path):
    outcome = fetch_dce("https://www.megalis.bretagne.bzh", tmp_path / "dce.zip")
    assert outcome.status == RetrievalStatus.MANUAL_REQUIRED
    assert "non prise en charge" in outcome.message


# --- Utilitaires HTTP ------------------------------------------------------


def test_is_archive_reconnait_la_signature_pas_l_extension():
    assert is_archive(b"PK\x03\x04rest")
    assert not is_archive(b"<html>")


def test_filename_from_response_neutralise_une_remontee_d_arborescence():
    response = httpx.Response(
        200, headers={"content-disposition": 'attachment; filename="../../etc/passwd"'}
    )
    assert filename_from_response(response) == "passwd"


def test_filename_from_response_gere_le_format_encode():
    response = httpx.Response(
        200, headers={"content-disposition": "attachment; filename*=UTF-8''DCE%20march%C3%A9.zip"}
    )
    assert filename_from_response(response) == "DCE marché.zip"


# --- Adaptateur Atexo ------------------------------------------------------


def test_reecriture_de_l_url_vers_la_page_de_retrait():
    """Un avis pointe indifféremment vers la recherche, le détail ou le règlement : tous
    portent le même couple id/orgAcronyme, seule l'action change."""
    rewritten = _to_dce_request_url(
        "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&id=Mzk=&orgAcronyme=d4t"
    )
    assert "page=Entreprise.EntrepriseDemandeTelechargementDce" in rewritten
    assert "orgAcronyme=d4t" in rewritten
    assert rewritten.count("page=") == 1


def test_champs_prado_retrouves_par_suffixe():
    """Le préfixe PRADO change d'une version d'Atexo à l'autre : seul le suffixe est stable."""
    inputs = _parse_inputs(_ATEXO_FORM_HTML)
    assert _find_field(inputs, "nom") == f"{_P}nom"
    assert _find_field(inputs, "accepterConditions") == f"{_P}accepterConditions"
    assert _find_field(inputs, "validateButton") == "ctl0$CONTENU_PAGE$validateButton"
    assert _radio_value(inputs, "choixTelechargement") == (f"{_P}RadioGroup", f"{_P}choixTelechargement")


def test_sans_identite_configuree_le_retrait_devient_manuel(tmp_path, monkeypatch):
    """On ne soumet jamais une identité fabriquée à un acheteur public."""
    from app.settings import get_settings

    for suffix in ("NOM", "PRENOM", "EMAIL"):
        monkeypatch.setenv(f"AOP_VEILLE_CONTACT_{suffix}", "")
    get_settings.cache_clear()
    try:
        outcome = AtexoAdapter().fetch(
            "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X",
            tmp_path / "dce.zip",
        )
    finally:
        get_settings.cache_clear()
    assert outcome.status == RetrievalStatus.MANUAL_REQUIRED
    assert "AOP_VEILLE_CONTACT_NOM" in outcome.message


def _mock_atexo_transport(captured: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_ATEXO_FORM_HTML)
        captured["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200,
            content=_ZIP_BYTES,
            headers={"content-disposition": 'attachment; filename="DCE-2026.zip"'},
        )

    return httpx.MockTransport(handler)


def test_retrait_atexo_soumet_l_identite_configuree_et_ecrit_l_archive(
    tmp_path, monkeypatch, contact_identity
):
    captured: dict = {}
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(**{**kwargs, "transport": _mock_atexo_transport(captured)}),
    )

    destination = tmp_path / "dce.zip"
    outcome = AtexoAdapter().fetch(
        "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X&orgAcronyme=d4t",
        destination,
    )

    assert outcome.status == RetrievalStatus.DOWNLOADED
    assert outcome.filename == "DCE-2026.zip"
    assert destination.read_bytes() == _ZIP_BYTES

    form = captured["form"]
    assert form["PRADO_PAGESTATE"] == "state-abc"
    assert form["_csrf_token"] == "tok-123"
    assert form[f"{_P}nom"] == "Cluzel"
    assert form[f"{_P}email"] == "louis@example.com"
    assert form[f"{_P}raisonSocial"] == "SMABTP"
    # Retrait identifié : inscrit au registre, donc notifié en cas de modification du DCE.
    assert form[f"{_P}RadioGroup"] == f"{_P}choixTelechargement"
    assert form[f"{_P}accepterConditions"] == "on"


def test_retrait_atexo_signale_un_echec_quand_la_plateforme_renvoie_du_html(
    tmp_path, monkeypatch, contact_identity
):
    """Consultation close ou formulaire refusé : c'est un ÉCHEC (on savait faire), pas un
    retrait manuel (on ne sait pas faire) — la distinction oriente le diagnostic."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_ATEXO_FORM_HTML)
        return httpx.Response(200, text="<html><body>Erreur de saisie.</body></html>")

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}),
    )

    outcome = AtexoAdapter().fetch(
        "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X",
        tmp_path / "dce.zip",
    )
    assert outcome.status == RetrievalStatus.FAILED


def test_retrait_atexo_suit_le_lien_de_telechargement_d_une_page_de_confirmation(
    tmp_path, monkeypatch, contact_identity
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                text='<a href="/index.php?page=Entreprise.EntrepriseDownloadCompleteDce&id=X">Télécharger</a>',
            )
        if "DownloadCompleteDce" in str(request.url):
            return httpx.Response(200, content=_ZIP_BYTES)
        return httpx.Response(200, text=_ATEXO_FORM_HTML)

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}),
    )

    destination = tmp_path / "dce.zip"
    outcome = AtexoAdapter().fetch(
        "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseDetailsConsultation&id=X",
        destination,
    )
    assert outcome.status == RetrievalStatus.DOWNLOADED
    assert destination.read_bytes() == _ZIP_BYTES


# --- Adaptateur lien direct ------------------------------------------------


def test_lien_direct_telecharge_l_archive(tmp_path, monkeypatch):
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            **{
                **kwargs,
                "transport": httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, content=_ZIP_BYTES, headers={"content-type": "application/zip"}
                    )
                ),
            }
        ),
    )
    destination = tmp_path / "dce.zip"
    outcome = DirectArchiveAdapter().fetch("https://ville.fr/dce/consultation.zip", destination)
    assert outcome.status == RetrievalStatus.DOWNLOADED
    assert destination.read_bytes() == _ZIP_BYTES


def test_lien_direct_refuse_une_page_html_deguisee(tmp_path, monkeypatch):
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            **{
                **kwargs,
                "transport": httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, text="<html>connexion requise</html>", headers={"content-type": "text/html"}
                    )
                ),
            }
        ),
    )
    outcome = DirectArchiveAdapter().fetch("https://ville.fr/dce/consultation.zip", tmp_path / "dce.zip")
    assert outcome.status == RetrievalStatus.FAILED
    assert not Path(tmp_path / "dce.zip").exists()
