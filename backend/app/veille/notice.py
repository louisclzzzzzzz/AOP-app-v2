"""Représentation commune d'un avis de marché, quelle que soit sa source (BOAMP ou JOUE/TED).

Les deux sources ne décrivent pas le même objet : le BOAMP publie un avis national au format
DILA (JSON maison, champ `donnees` imbriqué), TED publie un avis européen eForms (champs BT
normalisés, valeurs multilingues et répétées par lot). `Notice` est le dénominateur commun
que le reste de l'application manipule — les particularités de chaque source ne dépassent
jamais `app/veille/sources/`.
"""
from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field


class NoticeSource(str, enum.Enum):
    BOAMP = "boamp"
    TED = "ted"


@dataclass
class Notice:
    """Un avis de marché normalisé, prêt à être filtré, dédoublonné et persisté."""

    source: str
    """`boamp` ou `ted` — voir `NoticeSource`."""

    source_id: str
    """Identifiant chez la source : `idweb` BOAMP (ex. `26-79643`) ou numéro de publication
    TED (ex. `443934-2026`). Unique au sein d'une source, jamais entre sources."""

    objet: str
    """Intitulé de la consultation."""

    buyer_name: str | None = None
    description: str | None = None
    published_at: dt.date | None = None
    deadline_at: dt.datetime | None = None
    """Date limite de remise des offres. C'est le champ qui pilote l'urgence côté UI : un
    avis dont la date limite est passée n'a plus d'intérêt opérationnel."""

    notice_url: str | None = None
    """Page de l'avis chez la source (BOAMP ou TED) — toujours consultable par un humain."""

    dce_url: str | None = None
    """Lien vers le dossier de consultation sur le profil d'acheteur. TED le publie
    explicitement (BT-15 `document-url-lot`) ; le BOAMP ne donne le plus souvent que l'URL
    générique du profil d'acheteur, d'où un lien moins directement exploitable."""

    cpv_codes: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    procedure: str | None = None
    notice_type: str | None = None

    def matching_text(self) -> str:
        """Texte sur lequel les critères de ciblage sont évalués. On concatène objet ET
        description : sur un marché d'assurances multi-lots, « dommages-ouvrage » n'apparaît
        souvent que dans l'intitulé d'un lot, pas dans l'objet global de la consultation."""
        return " \n ".join(part for part in (self.objet, self.description) if part)
