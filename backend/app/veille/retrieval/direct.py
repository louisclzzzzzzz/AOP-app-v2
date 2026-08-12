"""Adaptateur « lien direct » : l'avis pointe déjà sur une archive téléchargeable.

Cas minoritaire mais réel — de petits acheteurs déposent le DCE en pièce jointe sur leur propre
site plutôt que sur une plateforme de dématérialisation. Aucun formulaire à franchir : un GET
suffit. C'est aussi l'adaptateur qui rattrape les plateformes qui redirigent directement vers
un zip sans page intermédiaire.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from app.veille.retrieval.base import RetrievalOutcome, RetrievalStatus
from app.veille.retrieval.http import (
    USER_AGENT,
    ARCHIVE_CONTENT_TYPES,
    filename_from_response,
    write_archive,
)

_TIMEOUT_SECONDS = 120.0
_ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")


class DirectArchiveAdapter:
    name = "lien direct"

    def matches(self, url: str) -> bool:
        path = unquote(urlparse(url).path or "").lower()
        return path.endswith(_ARCHIVE_SUFFIXES)

    def fetch(self, url: str, destination: Path) -> RetrievalOutcome:
        with httpx.Client(
            follow_redirects=True, timeout=_TIMEOUT_SECONDS, headers={"user-agent": USER_AGENT}
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type and content_type not in ARCHIVE_CONTENT_TYPES and response.content[:2] != b"PK":
                return RetrievalOutcome(
                    status=RetrievalStatus.FAILED,
                    platform=self.name,
                    message=f"Le lien ne renvoie pas une archive (content-type : {content_type}).",
                )
            filename = filename_from_response(response) or Path(urlparse(url).path).name or "dce.zip"
            write_archive(destination, response.content)

        return RetrievalOutcome(
            status=RetrievalStatus.DOWNLOADED,
            platform=self.name,
            message="DCE téléchargé directement depuis le lien de l'avis.",
            archive_path=destination,
            filename=filename,
        )
