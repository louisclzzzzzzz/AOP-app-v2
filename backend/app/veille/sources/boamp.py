"""Connecteur BOAMP — API Explore v2.1 d'OpenDataSoft opérée par la DILA.

https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records

Gratuite, sans clé ni authentification. Un enregistrement = un avis publié au Bulletin officiel
des annonces des marchés publics. Les champs de tête (`objet`, `nomacheteur`, `datelimitereponse`)
sont exploitables directement ; tout le reste — et notamment l'URL du profil d'acheteur — est
enfoui dans `donnees`, un JSON DILA dont la forme dépend de la famille d'avis (MAPA, avis de
marché formalisé…), d'où l'extraction défensive par parcours récursif plutôt que par chemin fixe.

Limite structurelle, assumée : le BOAMP ne publie PAS de lien vers le DCE lui-même, seulement
`urlProfilAcheteur`, l'adresse de la plateforme de l'acheteur. TED est meilleur sur ce point
(cf. `app/veille/sources/ted.py`) — c'est une des raisons d'interroger les deux.

Comme pour Géorisques (`app/audit/georisques.py`), tout est best-effort : une source injoignable
renvoie une liste vide et une erreur remontée à l'appelant, jamais une exception qui ferait
échouer le passage de veille complet.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Iterator

import httpx

from app.veille.criteria import VeilleCriteria
from app.veille.notice import Notice, NoticeSource

logger = logging.getLogger(__name__)

_API_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
_TIMEOUT_SECONDS = 20.0
_PAGE_SIZE = 100
# Garde-fou : l'API plafonne de toute façon la pagination, et un ciblage correct ne doit jamais
# ramener autant d'avis sur 30 jours. Au-delà, c'est que les critères sont trop larges.
_MAX_RECORDS = 500

_NOTICE_URL_TEMPLATE = "https://www.boamp.fr/pages/avis/?q=idweb:{idweb}"


def _escape_ods_string(value: str) -> str:
    """Échappe une valeur pour le langage ODSQL, dont les littéraux sont entre guillemets."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_where(criteria: VeilleCriteria, since: dt.date) -> str:
    """Construit la clause `where` ODSQL : fenêtre de publication ET (terme1 OU terme2 …).

    `search(objet, "…")` fait une recherche plein texte sur le seul champ objet — le seul
    champ de tête qui porte l'intitulé de la consultation."""
    terms = criteria.boamp.get("search_terms") or []
    clauses = " or ".join(f'search(objet, "{_escape_ods_string(term)}")' for term in terms)
    # Littéral date ODSQL : `date'AAAA-MM-JJ'`, entre APOSTROPHES. La même expression entre
    # guillemets doubles est refusée par l'API (400), contrairement aux littéraux chaîne.
    window = f"dateparution >= date'{since.isoformat()}'"
    return f"{window} and ({clauses})" if clauses else window


def _iter_records(client: httpx.Client, where: str) -> Iterator[dict[str, Any]]:
    """Pagine l'API jusqu'à épuisement des résultats ou `_MAX_RECORDS`."""
    offset = 0
    while offset < _MAX_RECORDS:
        response = client.get(
            _API_URL,
            params={
                "where": where,
                "limit": _PAGE_SIZE,
                "offset": offset,
                "order_by": "dateparution desc",
            },
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") or []
        yield from results
        if len(results) < _PAGE_SIZE:
            return
        offset += _PAGE_SIZE


def _walk_strings(node: Any) -> Iterator[tuple[str, str]]:
    """Parcourt récursivement le JSON DILA en produisant (clé, valeur) pour chaque chaîne.

    `donnees` n'a pas de schéma stable entre familles d'avis : chercher une clé par un chemin
    fixe casserait dès qu'un acheteur publie sous une autre famille. On cherche donc la clé
    par son nom, où qu'elle se trouve dans l'arbre."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                yield key, value
            else:
                yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def _extract_profil_acheteur(donnees: Any) -> str | None:
    """URL du profil d'acheteur, cherchée par nom de clé puis, à défaut, par forme d'URL."""
    if isinstance(donnees, str):
        try:
            donnees = json.loads(donnees)
        except json.JSONDecodeError:
            return None
    pairs = list(_walk_strings(donnees))
    for key, value in pairs:
        if "profil" in key.lower() and value.startswith("http"):
            return value
    # Repli : certains avis rangent le lien de retrait sous `rensgComplt` / `adresseRetrait`
    # plutôt que sous une clé contenant « profil ».
    for key, value in pairs:
        if value.startswith("http") and any(
            marker in key.lower() for marker in ("retrait", "dce", "dossier", "consultation")
        ):
            return value
    return None


def _extract_description(donnees: Any) -> str | None:
    """Concatène les blocs descriptifs (caractéristiques principales, intitulés de lots) sur
    lesquels les critères seront évalués — c'est souvent là, et non dans l'objet, qu'apparaît
    « dommages-ouvrage » sur un marché d'assurances multi-lots."""
    if isinstance(donnees, str):
        try:
            donnees = json.loads(donnees)
        except json.JSONDecodeError:
            return None
    wanted = ("principales", "objet", "denominationlot", "descriptionlot", "libelle", "txtlibre")
    chunks = [
        value
        for key, value in _walk_strings(donnees)
        if key.lower() in wanted and len(value) > 3
    ]
    if not chunks:
        return None
    # Dédoublonnage en conservant l'ordre : l'objet global est souvent répété à l'identique
    # dans plusieurs sous-blocs du JSON DILA.
    seen: set[str] = set()
    unique = [c for c in chunks if not (c in seen or seen.add(c))]
    return "\n".join(unique)[:8000]


def _parse_deadline(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _to_notice(record: dict[str, Any]) -> Notice | None:
    idweb = record.get("idweb")
    objet = record.get("objet")
    if not idweb or not objet:
        return None
    donnees = record.get("donnees")
    departments = record.get("code_departement")
    return Notice(
        source=NoticeSource.BOAMP.value,
        source_id=str(idweb),
        objet=str(objet),
        buyer_name=record.get("nomacheteur"),
        description=_extract_description(donnees),
        published_at=_parse_date(record.get("dateparution")),
        deadline_at=_parse_deadline(record.get("datelimitereponse")),
        notice_url=record.get("url_avis") or _NOTICE_URL_TEMPLATE.format(idweb=idweb),
        dce_url=_extract_profil_acheteur(donnees),
        departments=[str(d) for d in departments] if isinstance(departments, list) else [],
        procedure=record.get("procedure_libelle"),
        notice_type=record.get("nature_libelle"),
    )


def search_boamp(criteria: VeilleCriteria, *, since: dt.date) -> tuple[list[Notice], list[str]]:
    """Interroge le BOAMP et renvoie (avis normalisés, erreurs).

    Ne lève jamais : une panne réseau ou un changement d'API se traduit par une liste vide et
    un message d'erreur, que le passage de veille affiche sans échouer pour autant — l'autre
    source reste exploitable."""
    if not criteria.boamp.get("enabled", True):
        return [], []

    where = _build_where(criteria, since)
    excluded_natures = {str(n).upper() for n in (criteria.boamp.get("exclude_natures") or [])}
    notices: list[Notice] = []
    errors: list[str] = []
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS, headers={"accept": "application/json"}) as client:
            for record in _iter_records(client, where):
                nature = str(record.get("nature") or "").upper()
                if nature in excluded_natures:
                    continue
                notice = _to_notice(record)
                if notice is not None:
                    notices.append(notice)
    except Exception as exc:  # noqa: BLE001 — best-effort, cf. docstring du module
        logger.warning("Veille BOAMP indisponible : %s", exc)
        errors.append(f"BOAMP indisponible : {exc}")
    return notices, errors
