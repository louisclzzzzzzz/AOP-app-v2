"""Aiguillage du retrait de DCE : choix de l'adaptateur selon la plateforme, contrat commun.

Trois issues possibles, et une seule est un échec :
  - `DOWNLOADED` — le DCE est sur le disque, prêt à être ingéré ;
  - `MANUAL_REQUIRED` — la plateforme ne peut pas être automatisée (captcha, plateforme
    inconnue, identité de retrait non configurée). Ce n'est PAS une erreur : l'avis reste
    exploitable, l'utilisateur ouvre le lien et dépose le zip par le canal habituel ;
  - `FAILED` — l'automatisation a été tentée et n'a pas abouti (réseau, formulaire modifié).

La distinction compte : elle sépare « on ne sait pas faire, et on le dit d'avance » de « on
savait faire et ça a cassé ». Seul le second cas justifie d'aller regarder le code.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class RetrievalStatus(str, enum.Enum):
    DOWNLOADED = "downloaded"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"


@dataclass(frozen=True)
class RetrievalOutcome:
    status: RetrievalStatus
    platform: str
    """Nom lisible de la plateforme détectée (affiché dans l'UI)."""
    message: str
    archive_path: Path | None = None
    filename: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == RetrievalStatus.DOWNLOADED


class PlatformAdapter(Protocol):
    """Un adaptateur de plateforme de dématérialisation."""

    name: str

    def matches(self, url: str) -> bool: ...

    def fetch(self, url: str, destination: Path) -> RetrievalOutcome: ...


# Plateformes dont le retrait est protégé par un captcha. Elles sont reconnues explicitement
# pour annoncer d'emblée un retrait manuel plutôt que d'échouer après coup. Contourner ces
# captchas n'est pas envisagé : c'est une protection délibérée de l'éditeur, et la voie prévue
# pour un retrait automatisé y passe par un compte fournisseur, pas par du scraping.
_CAPTCHA_HOSTS = {
    "marches-publics.info": "AWS / marches-publics.info",
    "marches-securises.fr": "AWS / marches-sécurisés",
    "aws-achat.info": "AWS-Achat",
    # achatpublic.com cumule captcha et parcours de retrait piloté en JavaScript (popups
    # jQuery) : inatteignable sans navigateur, et de toute façon protégé.
    "achatpublic.com": "achatpublic.com",
}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _captcha_platform(url: str) -> str | None:
    host = _host(url)
    for domain, label in _CAPTCHA_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return None


def _adapters() -> list[PlatformAdapter]:
    """Import différé : les adaptateurs importent `plan_retrieval` indirectement via les
    settings, et un import au niveau module créerait un cycle."""
    from app.veille.retrieval.atexo import AtexoAdapter
    from app.veille.retrieval.direct import DirectArchiveAdapter

    return [AtexoAdapter(), DirectArchiveAdapter()]


def plan_retrieval(url: str | None) -> tuple[str, bool]:
    """Annonce, sans rien télécharger, (plateforme détectée, retrait automatisable ?).

    Appelé au moment de l'enregistrement d'un avis pour que l'UI affiche dès la liste si le
    DCE sera récupéré tout seul ou s'il faudra aller le chercher — l'utilisateur n'a pas à
    cliquer pour découvrir que ce n'est pas possible."""
    if not url:
        return "inconnue", False
    captcha_platform = _captcha_platform(url)
    if captcha_platform:
        return captcha_platform, False
    for adapter in _adapters():
        if adapter.matches(url):
            return adapter.name, True
    return _host(url) or "inconnue", False


def fetch_dce(url: str | None, destination: Path) -> RetrievalOutcome:
    """Tente le retrait du DCE vers `destination` (chemin du zip à écrire).

    Ne lève jamais : toute exception d'un adaptateur devient un `FAILED` documenté, pour que
    l'échec d'un retrait n'emporte pas le passage de veille."""
    if not url:
        return RetrievalOutcome(
            status=RetrievalStatus.MANUAL_REQUIRED,
            platform="inconnue",
            message="L'avis ne publie aucun lien vers le dossier de consultation.",
        )

    captcha_platform = _captcha_platform(url)
    if captcha_platform:
        return RetrievalOutcome(
            status=RetrievalStatus.MANUAL_REQUIRED,
            platform=captcha_platform,
            message=(
                f"{captcha_platform} protège le retrait du DCE par un captcha : "
                "récupérez le dossier depuis la plateforme, puis déposez le zip."
            ),
        )

    for adapter in _adapters():
        if not adapter.matches(url):
            continue
        try:
            return adapter.fetch(url, destination)
        except Exception as exc:  # noqa: BLE001 — cf. docstring
            logger.warning("Retrait DCE échoué sur %s (%s) : %s", url, adapter.name, exc)
            return RetrievalOutcome(
                status=RetrievalStatus.FAILED,
                platform=adapter.name,
                message=f"Retrait automatique échoué : {exc}",
            )

    return RetrievalOutcome(
        status=RetrievalStatus.MANUAL_REQUIRED,
        platform=_host(url) or "inconnue",
        message=(
            "Plateforme non prise en charge par le retrait automatique : "
            "téléchargez le DCE depuis le lien de l'avis, puis déposez le zip."
        ),
    )
