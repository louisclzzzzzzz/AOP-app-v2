"""Adaptateur pour les plateformes de dématérialisation de la famille Atexo MPE.

C'est le moteur le plus répandu du marché public français : PLACE (marches-publics.gouv.fr),
Maximilien, Mégalis Bretagne, les portails de nombreux départements et métropoles… tous
exposent le même applicatif sous des noms de domaine différents. La détection se fait donc sur
la SIGNATURE D'URL (`index.php?page=Entreprise.…`) et non sur une liste de domaines, qu'il
serait impossible de tenir à jour.

Le retrait suit le parcours qu'un humain effectue :
  1. ouvrir la page « demande de téléchargement du DCE » de la consultation ;
  2. renseigner ses coordonnées et accepter les conditions ;
  3. valider, ce qui déclenche le téléchargement de l'archive.

Deux points importants :

  - **L'identité est obligatoire et n'est jamais inventée.** Même l'option « téléchargement
    anonyme » de PLACE refuse la validation sans nom, prénom et e-mail (vérifié sur le
    formulaire réel). Ces coordonnées sont donc lues dans la configuration
    (`AOP_VEILLE_CONTACT_*`) et, si elles manquent, le retrait renvoie MANUAL_REQUIRED plutôt
    que de soumettre des données fabriquées à un acheteur public.

  - **Le retrait identifié est le bon défaut, pas seulement le seul possible.** Il inscrit le
    retrait au registre de la consultation, ce qui oblige l'acheteur à notifier toute
    modification du DCE — information critique quand on chiffre un risque sur ces pièces.

Les noms de champs PRADO sont préfixés par le chemin du contrôle dans la page
(`ctl0$CONTENU_PAGE$…`), qui varie d'une version d'Atexo à l'autre : ils sont donc retrouvés
par SUFFIXE dans le HTML, jamais codés en dur.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from app.settings import get_settings
from app.veille.retrieval.base import RetrievalOutcome, RetrievalStatus
from app.veille.retrieval.http import USER_AGENT, filename_from_response, is_archive, write_archive

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 180.0
_DCE_REQUEST_PAGE = "Entreprise.EntrepriseDemandeTelechargementDce"
# Signature du moteur Atexo dans l'URL : toutes ses pages entreprise sont routées par ce
# paramètre `page=Entreprise.<Action>`.
_ATEXO_URL_SIGNATURE = re.compile(r"[?&]page=Entreprise\.", re.IGNORECASE)

_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""")

# Champ logique -> suffixe du nom PRADO. L'ordre n'a pas d'importance, l'appariement se fait
# par suffixe exact après le dernier `$`.
_IDENTITY_FIELDS = {
    "nom": "nom",
    "prenom": "prenom",
    "email": "email",
    "raison_sociale": "raisonSocial",
    "siret": "siret",
    "telephone": "tel",
    "adresse": "address",
    "code_postal": "cp",
    "ville": "ville",
}


def _parse_inputs(html: str) -> list[dict[str, str]]:
    return [dict(_ATTR_RE.findall(tag)) for tag in _INPUT_RE.findall(html)]


def _find_field(inputs: list[dict[str, str]], suffix: str) -> str | None:
    """Nom complet du champ dont le nom PRADO se termine par `$<suffix>`."""
    for attrs in inputs:
        name = attrs.get("name")
        if name and name.rsplit("$", 1)[-1] == suffix:
            return name
    return None


def _hidden_value(inputs: list[dict[str, str]], name: str) -> str | None:
    for attrs in inputs:
        if attrs.get("name") == name:
            return unescape(attrs.get("value", ""))
    return None


def _radio_value(inputs: list[dict[str, str]], suffix: str) -> tuple[str, str] | None:
    """(nom du groupe radio, valeur) de l'option dont l'identifiant se termine par `suffix`."""
    for attrs in inputs:
        if attrs.get("type", "").lower() != "radio":
            continue
        identifier = attrs.get("id") or ""
        if identifier.rsplit("_", 1)[-1] == suffix:
            name = attrs.get("name")
            if name:
                return name, unescape(attrs.get("value", ""))
    return None


def _to_dce_request_url(url: str) -> str:
    """Réécrit n'importe quelle URL de consultation Atexo vers sa page de retrait de DCE.

    Les avis pointent indifféremment vers la recherche (`EntrepriseAdvancedSearch`), le détail
    (`EntrepriseDetailsConsultation`) ou le règlement de consultation : tous ces écrans portent
    le même couple `id` + `orgAcronyme`, seule l'action change."""
    parsed = urlparse(url)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "page"]
    params.insert(0, ("page", _DCE_REQUEST_PAGE))
    path = parsed.path or "/index.php"
    return urlunparse(parsed._replace(path=path, query=urlencode(params), fragment=""))


def _identity_payload() -> dict[str, str] | None:
    """Coordonnées de retrait issues de la configuration, ou None si l'essentiel manque.

    Nom, prénom et e-mail sont les trois champs que le formulaire valide côté serveur ; le
    reste (raison sociale, SIRET, téléphone…) est envoyé quand il est renseigné, parce que
    certains acheteurs l'exigent, mais son absence ne bloque pas."""
    settings = get_settings()
    required = {
        "nom": settings.veille_contact_nom,
        "prenom": settings.veille_contact_prenom,
        "email": settings.veille_contact_email,
    }
    if not all(value.strip() for value in required.values()):
        return None
    optional = {
        "raison_sociale": settings.veille_contact_raison_sociale,
        "siret": settings.veille_contact_siret,
        "telephone": settings.veille_contact_telephone,
        "adresse": settings.veille_contact_adresse,
        "code_postal": settings.veille_contact_code_postal,
        "ville": settings.veille_contact_ville,
    }
    payload = {key: value.strip() for key, value in required.items()}
    payload.update({key: value.strip() for key, value in optional.items() if value.strip()})
    return payload


def _follow_download_link(client: httpx.Client, html: str, base_url: str) -> httpx.Response | None:
    """Certaines instances renvoient une page de confirmation portant le lien de l'archive au
    lieu de servir directement le zip."""
    for match in re.finditer(r"""href\s*=\s*["']([^"']+)["']""", html, re.IGNORECASE):
        href = unescape(match.group(1))
        if "DownloadCompleteDce" in href or "telechargerDce" in href.lower():
            response = client.get(urljoin(base_url, href))
            response.raise_for_status()
            return response
    return None


class AtexoAdapter:
    name = "Atexo / PLACE"

    def matches(self, url: str) -> bool:
        return bool(_ATEXO_URL_SIGNATURE.search(url))

    def fetch(self, url: str, destination: Path) -> RetrievalOutcome:
        identity = _identity_payload()
        if identity is None:
            return RetrievalOutcome(
                status=RetrievalStatus.MANUAL_REQUIRED,
                platform=self.name,
                message=(
                    "Retrait automatique impossible : la plateforme exige une identité de "
                    "retrait. Renseignez AOP_VEILLE_CONTACT_NOM, _PRENOM et _EMAIL dans .env."
                ),
            )

        request_url = _to_dce_request_url(url)
        with httpx.Client(
            follow_redirects=True, timeout=_TIMEOUT_SECONDS, headers={"user-agent": USER_AGENT}
        ) as client:
            page = client.get(request_url)
            page.raise_for_status()
            inputs = _parse_inputs(page.text)

            page_state = _hidden_value(inputs, "PRADO_PAGESTATE")
            csrf_token = _hidden_value(inputs, "_csrf_token")
            if page_state is None:
                return RetrievalOutcome(
                    status=RetrievalStatus.FAILED,
                    platform=self.name,
                    message="Formulaire de retrait introuvable sur la page de la consultation.",
                )

            form: dict[str, str] = {"PRADO_PAGESTATE": page_state}
            if csrf_token is not None:
                form["_csrf_token"] = csrf_token

            for logical, suffix in _IDENTITY_FIELDS.items():
                value = identity.get(logical)
                field = _find_field(inputs, suffix)
                if value and field:
                    form[field] = value

            # « Je souhaite télécharger le DCE en renseignant mes coordonnées » : le retrait est
            # inscrit au registre, donc notifié en cas de modification du dossier.
            radio = _radio_value(inputs, "choixTelechargement")
            if radio is not None:
                form[radio[0]] = radio[1]

            conditions_field = _find_field(inputs, "accepterConditions")
            if conditions_field:
                form[conditions_field] = "on"

            submit_field = _find_field(inputs, "validateButton")
            form[submit_field or "ctl0$CONTENU_PAGE$validateButton"] = "Valider"

            response = client.post(request_url, data=form)
            response.raise_for_status()

            if not is_archive(response.content):
                followed = _follow_download_link(client, response.text, request_url)
                if followed is None or not is_archive(followed.content):
                    return RetrievalOutcome(
                        status=RetrievalStatus.FAILED,
                        platform=self.name,
                        message=(
                            "La plateforme n'a pas renvoyé d'archive (formulaire refusé ou "
                            "consultation close). Retrait à faire manuellement."
                        ),
                    )
                response = followed

            filename = filename_from_response(response) or "dce.zip"
            write_archive(destination, response.content)

        return RetrievalOutcome(
            status=RetrievalStatus.DOWNLOADED,
            platform=self.name,
            message=f"DCE retiré sur {urlparse(request_url).hostname}.",
            archive_path=destination,
            filename=filename,
        )
