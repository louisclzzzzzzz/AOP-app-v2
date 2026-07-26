"""Intégration de l'API publique Géorisques (georisques.gouv.fr) + géocodage BAN pour l'audit
des risques (Phase 2 du protocole d'analyse).

Fournit un référentiel officiel des risques naturels du site (zonage sismique, retrait-gonflement
d'argile, radon, inondation/PPRN, cavités, mouvements de terrain) que l'audit confronte aux
prescriptions des documents (étude de sol, CCTP, RICT) — le protocole demande explicitement de
« vérifier le zonage Séisme, Inondation (PPRI) et Argiles (RGA) » et de croiser la « carte du
risque RGA de Géorisques » avec l'étude de sol.

Tout ici est best-effort et JAMAIS bloquant : aucune fonction ne lève, une panne réseau / un
service indisponible / une adresse non géocodable produit un rapport partiel (champs à None) et
une entrée dans `errors`, jamais une exception qui ferait échouer l'audit. Les données publiques
ne sont qu'un complément aux documents du dossier, pas une dépendance dure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://api-adresse.data.gouv.fr/search/"
_GEORISQUES_BASE = "https://georisques.gouv.fr/api/v1"
_TIMEOUT_SECONDS = 12.0
_CAVITE_MVT_RAYON_M = 1000

# classe_potentiel radon (Géorisques) → libellé métier. 3 = zone à potentiel significatif, seule
# catégorie imposant des dispositions constructives (mise en dépression du soubassement…).
_RADON_LABELS = {
    "1": "catégorie 1 — potentiel faible",
    "2": "catégorie 2 — potentiel faible mais facteurs géologiques particuliers",
    "3": "catégorie 3 — potentiel significatif (dispositions constructives à prévoir)",
}


@dataclass(frozen=True)
class GeorisquesReport:
    address_queried: str
    resolved_label: str | None = None
    lon: float | None = None
    lat: float | None = None
    code_insee: str | None = None
    seisme: str | None = None
    rga: str | None = None
    radon: str | None = None
    gaspar_risques: list[str] = field(default_factory=list)
    cavites_count: int | None = None
    mvt_count: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def geocoded(self) -> bool:
        return self.lon is not None and self.lat is not None


@dataclass
class _GeocodeResult:
    lon: float
    lat: float
    code_insee: str | None
    label: str | None


def _get_json(client: httpx.Client, url: str, params: dict) -> dict | list | None:
    """GET tolérant : renvoie le JSON décodé, ou None sur toute erreur (réseau, HTTP, décodage)."""
    resp = client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _geocode(client: httpx.Client, address: str) -> _GeocodeResult | None:
    data = _get_json(client, _GEOCODE_URL, {"q": address, "limit": 1, "autocomplete": 0})
    if not isinstance(data, dict):
        return None
    features = data.get("features") or []
    if not features:
        return None
    feature = features[0]
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    props = feature.get("properties") or {}
    return _GeocodeResult(
        lon=float(coords[0]),
        lat=float(coords[1]),
        code_insee=props.get("citycode"),
        label=props.get("label"),
    )


def _first_row(data: dict | list | None) -> dict | None:
    """Extrait la 1re ligne de données, quel que soit l'emballage renvoyé par Géorisques (certains
    endpoints répondent `{"data": [...]}`, d'autres directement l'objet)."""
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows[0] if rows else None
        return data
    if isinstance(data, list):
        return data[0] if data else None
    return None


def _fetch_seisme(client: httpx.Client, geo: _GeocodeResult) -> str | None:
    data = _get_json(client, f"{_GEORISQUES_BASE}/zonage_sismique", {"latlon": f"{geo.lon},{geo.lat}"})
    row = _first_row(data)
    return row.get("zone_sismicite") if row else None


def _fetch_rga(client: httpx.Client, geo: _GeocodeResult) -> str | None:
    data = _get_json(client, f"{_GEORISQUES_BASE}/rga", {"latlon": f"{geo.lon},{geo.lat}"})
    row = _first_row(data)
    return row.get("exposition") if row else None


def _fetch_radon(client: httpx.Client, geo: _GeocodeResult) -> str | None:
    if not geo.code_insee:
        return None
    data = _get_json(client, f"{_GEORISQUES_BASE}/radon", {"code_insee": geo.code_insee})
    row = _first_row(data)
    if not row:
        return None
    classe = str(row.get("classe_potentiel", "")).strip()
    return _RADON_LABELS.get(classe, f"classe {classe}" if classe else None)


def _fetch_gaspar_risques(client: httpx.Client, geo: _GeocodeResult) -> list[str]:
    if not geo.code_insee:
        return []
    data = _get_json(
        client, f"{_GEORISQUES_BASE}/gaspar/risques", {"code_insee": geo.code_insee, "page": 1, "page_size": 50}
    )
    row = _first_row(data)
    if not row:
        return []
    details = row.get("risques_detail") or []
    libelles: list[str] = []
    for d in details:
        libelle = d.get("libelle_risque_long")
        if libelle and libelle not in libelles:
            libelles.append(libelle)
    return libelles


def _fetch_count(client: httpx.Client, endpoint: str, geo: _GeocodeResult) -> int | None:
    data = _get_json(
        client,
        f"{_GEORISQUES_BASE}/{endpoint}",
        {"latlon": f"{geo.lon},{geo.lat}", "rayon": _CAVITE_MVT_RAYON_M, "page": 1, "page_size": 1},
    )
    if isinstance(data, dict) and "results" in data:
        try:
            return int(data["results"])
        except (TypeError, ValueError):
            return None
    return None


# aspect (schéma) → fonction de récupération. `radon`/`inondation` dépendent du code INSEE ;
# `inondation` est dérivé de la liste GASPAR (voir build_report).
_LATLON_FETCHERS = {
    "seisme": _fetch_seisme,
    "rga": _fetch_rga,
}


def build_georisques_report(address: str | None) -> GeorisquesReport | None:
    """Géocode l'adresse du chantier puis interroge tous les endpoints Géorisques. Retourne None si
    aucune adresse n'est disponible (l'audit se poursuit alors sans référentiel officiel)."""
    if not address or not address.strip():
        return None

    errors: list[str] = []
    try:
        client = httpx.Client(timeout=_TIMEOUT_SECONDS, headers={"accept": "application/json"})
    except Exception as exc:  # pragma: no cover - création de client
        logger.warning("Géorisques : impossible de créer le client HTTP : %s", exc)
        return GeorisquesReport(address_queried=address, errors=[f"client HTTP : {exc}"])

    with client:
        try:
            geo = _geocode(client, address)
        except Exception as exc:
            logger.warning("Géorisques : géocodage de %r échoué : %s", address, exc)
            return GeorisquesReport(address_queried=address, errors=[f"géocodage : {exc}"])

        if geo is None:
            return GeorisquesReport(
                address_queried=address, errors=["adresse non géocodable (aucun résultat BAN)"]
            )

        seisme = rga = radon = None
        gaspar: list[str] = []
        cavites = mvt = None

        for label, fn in (
            ("seisme", lambda: _fetch_seisme(client, geo)),
            ("rga", lambda: _fetch_rga(client, geo)),
            ("radon", lambda: _fetch_radon(client, geo)),
            ("gaspar", lambda: _fetch_gaspar_risques(client, geo)),
            ("cavites", lambda: _fetch_count(client, "cavites", geo)),
            ("mvt", lambda: _fetch_count(client, "mvt", geo)),
        ):
            try:
                value = fn()
            except Exception as exc:
                errors.append(f"{label} : {exc}")
                logger.warning("Géorisques : récupération %s échouée : %s", label, exc)
                continue
            if label == "seisme":
                seisme = value
            elif label == "rga":
                rga = value
            elif label == "radon":
                radon = value
            elif label == "gaspar":
                gaspar = value or []
            elif label == "cavites":
                cavites = value
            elif label == "mvt":
                mvt = value

    return GeorisquesReport(
        address_queried=address,
        resolved_label=geo.label,
        lon=geo.lon,
        lat=geo.lat,
        code_insee=geo.code_insee,
        seisme=seisme,
        rga=rga,
        radon=radon,
        gaspar_risques=gaspar,
        cavites_count=cavites,
        mvt_count=mvt,
        errors=errors,
    )


def _inondation_summary(report: GeorisquesReport) -> str | None:
    matches = [r for r in report.gaspar_risques if "inondation" in r.lower()]
    if not matches:
        return None
    return "risques recensés sur la commune : " + " ; ".join(matches)


# aspect → (libellé lisible, extracteur de la valeur du rapport)
_ASPECT_RENDERERS = {
    "seisme": ("Zonage sismique (Géorisques)", lambda r: r.seisme),
    "rga": ("Retrait-gonflement des argiles / RGA (Géorisques)", lambda r: r.rga),
    "radon": ("Potentiel radon (Géorisques)", lambda r: r.radon),
    "inondation": ("Inondation / PPRN (Géorisques GASPAR)", _inondation_summary),
    "cavites": (
        "Cavités souterraines dans un rayon de 1 km (Géorisques)",
        lambda r: None if r.cavites_count is None else f"{r.cavites_count} recensée(s)",
    ),
    "mvt": (
        "Mouvements de terrain dans un rayon de 1 km (Géorisques)",
        lambda r: None if r.mvt_count is None else f"{r.mvt_count} recensé(s)",
    ),
}


def format_aspects_grounding(report: GeorisquesReport | None, aspects: list[str]) -> str:
    """Bloc de grounding injecté dans le prompt d'une section : uniquement les aspects Géorisques
    déclarés par cette section, avec la valeur officielle à confronter aux documents. Chaîne vide
    si rien d'exploitable (géocodage échoué ou aucun aspect renseigné)."""
    if report is None or not report.geocoded or not aspects:
        return ""
    lines: list[str] = []
    for aspect in aspects:
        renderer = _ASPECT_RENDERERS.get(aspect)
        if renderer is None:
            continue
        label, extractor = renderer
        value = extractor(report)
        if value:
            lines.append(f"- {label} : {value}")
    if not lines:
        return ""
    header = (
        f"Données publiques Géorisques pour le site géolocalisé "
        f"({report.resolved_label or report.address_queried}) — référentiel officiel à confronter "
        f"aux documents (signale toute divergence ou toute mission manquante) :"
    )
    return header + "\n" + "\n".join(lines)


def format_full_md(report: GeorisquesReport | None) -> str:
    """Section « Contexte réglementaire — Risques naturels (Géorisques) » du rapport assemblé :
    transparence sur l'ensemble des données publiques récupérées et l'adresse géocodée."""
    if report is None:
        return (
            "_Aucune adresse de chantier exploitable — données publiques Géorisques non "
            "interrogées pour ce dossier._"
        )
    if not report.geocoded:
        detail = " ; ".join(report.errors) if report.errors else "raison inconnue"
        return (
            f"_Adresse « {report.address_queried} » non géolocalisable — données Géorisques "
            f"indisponibles ({detail})._"
        )

    lines = [
        f"Site géolocalisé : **{report.resolved_label or report.address_queried}** "
        f"(lon {report.lon:.5f}, lat {report.lat:.5f}"
        + (f", INSEE {report.code_insee}" if report.code_insee else "")
        + ").",
        "",
        "| Aléa | Donnée officielle Géorisques |",
        "|---|---|",
    ]
    rows = [
        ("Zonage sismique", report.seisme),
        ("Retrait-gonflement des argiles (RGA)", report.rga),
        ("Potentiel radon", report.radon),
        ("Inondation / PPRN (commune)", _inondation_summary(report) or None),
        (
            "Cavités souterraines (rayon 1 km)",
            None if report.cavites_count is None else f"{report.cavites_count} recensée(s)",
        ),
        (
            "Mouvements de terrain (rayon 1 km)",
            None if report.mvt_count is None else f"{report.mvt_count} recensé(s)",
        ),
    ]
    for label, value in rows:
        lines.append(f"| {label} | {value or 'non renseigné'} |")
    if report.errors:
        lines.append("")
        lines.append("_Récupérations partielles : " + " ; ".join(report.errors) + "._")
    return "\n".join(lines)
