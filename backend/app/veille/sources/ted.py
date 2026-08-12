"""Connecteur JOUE/TED — Search API v3 de l'Office des publications de l'Union européenne.

https://api.ted.europa.eu/v3/notices/search (documentation : docs.ted.europa.eu/api/latest)

Gratuite, sans clé ni authentification, réservée aux réutilisateurs de données. La requête
s'écrit dans un langage « expert » propre à TED (`champ=valeur AND champ IN (…) AND FT~(…)`) et
les champs demandés sont des BT eForms.

Intérêt décisif par rapport au BOAMP : TED publie le BT-15 `document-url-lot`, c'est-à-dire le
lien direct vers le dossier de consultation sur le profil d'acheteur — là où le BOAMP ne donne
que l'adresse générique de la plateforme. C'est ce lien qui rend le retrait automatique possible.

Deux particularités du format eForms à absorber ici :
  - les valeurs textuelles sont multilingues (`{"fra": ["…"]}`) ;
  - les valeurs sont répétées par LOT, donc listées et très souvent identiques d'un lot à
    l'autre (un avis à 9 lots répète 9 fois la même URL de DCE).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
from typing import Any

import httpx

from app.veille.criteria import VeilleCriteria
from app.veille.notice import Notice, NoticeSource

logger = logging.getLogger(__name__)

_API_URL = "https://api.ted.europa.eu/v3/notices/search"
_TIMEOUT_SECONDS = 25.0
_PAGE_SIZE = 100  # l'API plafonne à 250
_MAX_PAGES = 5

_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "description-lot",
    "document-url-lot",
    "deadline-receipt-tender-date-lot",
    "publication-date",
    "classification-cpv",
    "notice-type",
    "procedure-type",
    "links",
]


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _build_query(criteria: VeilleCriteria, *, since: dt.date) -> str:
    """Assemble la requête experte TED : CPV, pays acheteur, fenêtre de publication, plein texte.

    Le filtre CPV seul ne suffit pas (les acheteurs codent l'assurance construction sous le CPV
    générique 66510000 la plupart du temps) : le `FT~` plein texte fait le gros du tri côté
    serveur, `criteria.match()` finit le travail côté client."""
    clauses: list[str] = []

    cpv = criteria.ted.get("cpv") or []
    if cpv:
        clauses.append("classification-cpv IN (" + " ".join(str(c) for c in cpv) + ")")

    countries = criteria.ted.get("countries") or []
    if countries:
        clauses.append("buyer-country IN (" + " ".join(str(c) for c in countries) + ")")

    # TED n'accepte que `AAAAMMJJ` ou `today(±n)` sur les champs date — un ISO `AAAA-MM-JJ`
    # est refusé (400, pattern `[0-9]{8}|today\([+-]?[0-9]*\)`).
    clauses.append(f"publication-date>={since.strftime('%Y%m%d')}")

    full_text = criteria.ted.get("full_text") or []
    if full_text:
        clauses.append("FT~(" + " OR ".join(_quote(str(t)) for t in full_text) + ")")

    return " AND ".join(clauses)


def _text(value: Any) -> str | None:
    """Aplatit une valeur eForms multilingue et/ou répétée par lot en une seule chaîne.

    Priorité au français quand la valeur est multilingue ; à défaut, la première langue
    disponible (un avis français reste parfois publié en anglais seul)."""
    if value is None:
        return None
    if isinstance(value, str):
        return html.unescape(value).strip() or None
    if isinstance(value, dict):
        chosen = value.get("fra") or next((v for v in value.values() if v), None)
        return _text(chosen)
    if isinstance(value, list):
        parts = [t for t in (_text(item) for item in value) if t]
        # Dédoublonnage en conservant l'ordre : une valeur répétée à l'identique sur chaque lot
        # ne doit apparaître qu'une fois.
        seen: set[str] = set()
        unique = [p for p in parts if not (p in seen or seen.add(p))]
        return "\n".join(unique) or None
    return None


def _first_url(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("http"):
            return candidate
    return None


def _codes(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return sorted({line.strip() for line in text.splitlines() if line.strip()})


def _parse_ted_date(raw: Any) -> dt.date | None:
    """TED date les avis en `YYYY-MM-DD+HH:MM` (décalage horaire accolé, sans heure)."""
    text = _text(raw)
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text.splitlines()[0][:10])
    except ValueError:
        return None


def _parse_ted_deadline(raw: Any) -> dt.datetime | None:
    """Date limite : on retient la PLUS PROCHE quand les lots en ont plusieurs — c'est elle
    qui contraint réellement la réponse."""
    text = _text(raw)
    if not text:
        return None
    dates: list[dt.date] = []
    for line in text.splitlines():
        try:
            dates.append(dt.date.fromisoformat(line.strip()[:10]))
        except ValueError:
            continue
    if not dates:
        return None
    return dt.datetime.combine(min(dates), dt.time.min, tzinfo=dt.timezone.utc)


def _notice_html_url(raw: dict[str, Any]) -> str | None:
    """URL de la page web de l'avis, prise TELLE QUELLE dans `links` — jamais reconstruite.

    `publication-number` seul ne suffit pas à deviner l'URL : le site TED sert la page humaine
    sous `/notice/-/detail/{numéro}` (un routage SPA côté Angular, différent du chemin
    `/notice/{numéro}/xml|pdf` utilisé pour les autres formats) — un gabarit construit à la
    main s'est avéré 404 en pratique. `links` est toujours présent dans la réponse, qu'il soit
    demandé ou non ; on le demande explicitement pour que ça reste vrai si l'API change."""
    links = raw.get("links")
    if not isinstance(links, dict):
        return None
    for key in ("html", "htmlDirect", "pdf"):
        variants = links.get(key)
        if isinstance(variants, dict) and variants:
            return variants.get("FRA") or next(iter(variants.values()), None)
    return None


def _to_notice(raw: dict[str, Any]) -> Notice | None:
    publication_number = _text(raw.get("publication-number"))
    objet = _text(raw.get("notice-title"))
    if not publication_number or not objet:
        return None
    return Notice(
        source=NoticeSource.TED.value,
        source_id=publication_number,
        objet=objet,
        buyer_name=_text(raw.get("buyer-name")),
        description=(_text(raw.get("description-lot")) or "")[:8000] or None,
        published_at=_parse_ted_date(raw.get("publication-date")),
        deadline_at=_parse_ted_deadline(raw.get("deadline-receipt-tender-date-lot")),
        notice_url=_notice_html_url(raw),
        dce_url=_first_url(raw.get("document-url-lot")),
        cpv_codes=_codes(raw.get("classification-cpv")),
        procedure=_text(raw.get("procedure-type")),
        notice_type=_text(raw.get("notice-type")),
    )


def search_ted(criteria: VeilleCriteria, *, since: dt.date) -> tuple[list[Notice], list[str]]:
    """Interroge TED et renvoie (avis normalisés, erreurs). Ne lève jamais — même contrat que
    `search_boamp`, pour que l'indisponibilité d'une source n'emporte pas l'autre."""
    if not criteria.ted.get("enabled", True):
        return [], []

    query = _build_query(criteria, since=since)
    excluded_prefixes = tuple(criteria.ted.get("exclude_notice_type_prefixes") or [])
    notices: list[Notice] = []
    errors: list[str] = []
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            for page in range(1, _MAX_PAGES + 1):
                response = client.post(
                    _API_URL,
                    json={"query": query, "fields": _FIELDS, "limit": _PAGE_SIZE, "page": page, "scope": "ALL"},
                )
                response.raise_for_status()
                payload = response.json()
                raw_notices = payload.get("notices") or []
                for raw in raw_notices:
                    notice = _to_notice(raw)
                    if notice is None:
                        continue
                    if notice.notice_type and notice.notice_type.startswith(excluded_prefixes):
                        continue
                    notices.append(notice)
                if len(raw_notices) < _PAGE_SIZE:
                    break
    except Exception as exc:  # noqa: BLE001 — best-effort, cf. docstring du module
        logger.warning("Veille TED indisponible : %s", exc)
        errors.append(f"JOUE/TED indisponible : {exc}")
    return notices, errors
