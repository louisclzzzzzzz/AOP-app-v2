from __future__ import annotations

import httpx
import pytest

import app.audit.georisques as geo
from app.audit.georisques import (
    GeorisquesReport,
    build_georisques_report,
    format_aspects_grounding,
    format_full_md,
)

# --- réponses canoniques des endpoints (extraites de vraies réponses de l'API) ------------------

_GEOCODE_OK = {
    "features": [
        {
            "geometry": {"type": "Point", "coordinates": [2.290084, 49.897442]},
            "properties": {"label": "8 Boulevard du Port 80000 Amiens", "citycode": "80021"},
        }
    ]
}
_SEISME = {"data": [{"zone_sismicite": "3 - MODÉRÉE"}]}
_RGA = {"codeExposition": "2", "exposition": "Exposition moyenne"}
_RADON = {"data": [{"classe_potentiel": "3"}]}
_GASPAR = {"data": [{"risques_detail": [{"libelle_risque_long": "Inondation"}, {"libelle_risque_long": "Séisme"}]}]}
_COUNT_2 = {"results": 2, "data": []}
_COUNT_0 = {"results": 0, "data": []}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class _FakeClient:
    """Client httpx factice : route par URL. `routes` = dict substring→payload (ou Exception)."""

    def __init__(self, routes):
        self._routes = routes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        for fragment, payload in self._routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResponse(payload)
        raise AssertionError(f"URL inattendue : {url}")


def _install(monkeypatch, routes):
    monkeypatch.setattr(geo, "httpx", httpx)  # garde le vrai module pour les exceptions
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(routes))


_ALL_OK = {
    "api-adresse": _GEOCODE_OK,
    "zonage_sismique": _SEISME,
    "rga": _RGA,
    "radon": _RADON,
    "gaspar/risques": _GASPAR,
    "cavites": _COUNT_2,
    "mvt": _COUNT_0,
}


def test_build_report_happy_path(monkeypatch):
    _install(monkeypatch, _ALL_OK)
    report = build_georisques_report("8 bd du port Amiens")

    assert report is not None
    assert report.geocoded
    assert report.lon == 2.290084 and report.lat == 49.897442
    assert report.code_insee == "80021"
    assert report.seisme == "3 - MODÉRÉE"
    assert report.rga == "Exposition moyenne"
    assert "catégorie 3" in report.radon
    assert "Inondation" in report.gaspar_risques
    assert report.cavites_count == 2
    assert report.mvt_count == 0
    assert report.errors == []


def test_build_report_none_when_no_address():
    assert build_georisques_report(None) is None
    assert build_georisques_report("   ") is None


def test_build_report_ungeocodable_address(monkeypatch):
    _install(monkeypatch, {"api-adresse": {"features": []}})
    report = build_georisques_report("adresse bidon")
    assert report is not None
    assert not report.geocoded
    assert report.errors  # motif renseigné


def test_build_report_is_best_effort_on_partial_failures(monkeypatch):
    """Une panne d'un endpoint isolé (ex. RGA) ne doit pas casser le rapport : les autres aléas
    sont renseignés, l'échec est tracé dans `errors`."""
    routes = dict(_ALL_OK)
    routes["rga"] = httpx.ConnectError("timeout")
    _install(monkeypatch, routes)

    report = build_georisques_report("8 bd du port Amiens")
    assert report.geocoded
    assert report.seisme == "3 - MODÉRÉE"  # les autres passent
    assert report.rga is None
    assert any("rga" in e for e in report.errors)


def test_build_report_geocode_failure_returns_partial(monkeypatch):
    _install(monkeypatch, {"api-adresse": httpx.ConnectError("down")})
    report = build_georisques_report("8 bd du port Amiens")
    assert not report.geocoded
    assert any("géocodage" in e for e in report.errors)


# --- formatage ----------------------------------------------------------------------------------

def test_format_aspects_grounding_only_requested_aspects():
    report = GeorisquesReport(
        address_queried="x", resolved_label="Ville", lon=1.0, lat=2.0,
        seisme="3 - MODÉRÉE", rga="Exposition forte", radon="catégorie 1 — potentiel faible",
    )
    block = format_aspects_grounding(report, ["seisme", "rga"])
    assert "3 - MODÉRÉE" in block
    assert "Exposition forte" in block
    assert "radon" not in block.lower()  # non demandé


def test_format_aspects_grounding_empty_when_not_geocoded():
    report = GeorisquesReport(address_queried="x")
    assert format_aspects_grounding(report, ["seisme"]) == ""
    assert format_aspects_grounding(None, ["seisme"]) == ""


def test_format_full_md_variants():
    assert "non interrogées" in format_full_md(None)
    not_geo = GeorisquesReport(address_queried="rue X", errors=["adresse non géocodable"])
    assert "non géolocalisable" in format_full_md(not_geo)
    ok = GeorisquesReport(address_queried="x", resolved_label="Ville", lon=1.0, lat=2.0, seisme="3 - MODÉRÉE")
    md = format_full_md(ok)
    assert "Zonage sismique" in md
    assert "3 - MODÉRÉE" in md
