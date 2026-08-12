"""Ciblage des avis : normalisation, bornage des abréviations, priorité de l'exclusion."""
from __future__ import annotations

import datetime as dt

from app.veille.criteria import VeilleCriteria, get_veille_criteria, normalize
from app.veille.notice import Notice


def _criteria(require: list[str], exclude: list[str] | None = None) -> VeilleCriteria:
    return VeilleCriteria(
        lookback_days=30, require_any=require, exclude_any=exclude or [], boamp={}, ted={}
    )


def _notice(objet: str, description: str | None = None) -> Notice:
    return Notice(source="boamp", source_id="1", objet=objet, description=description)


def test_normalize_gomme_accents_casse_et_ponctuation():
    assert normalize("Dommages-Ouvrage") == "dommages ouvrage"
    assert normalize("ASSURANCE DOMMAGES À L'OUVRAGE") == "assurance dommages a l ouvrage"
    assert normalize("Tous   Risques\nChantier") == "tous risques chantier"


def test_les_variantes_orthographiques_matchent_un_seul_terme():
    criteria = _criteria(["dommages ouvrage"])
    for variante in ("Dommages-Ouvrage", "DOMMAGES OUVRAGE", "Dommages   ouvrage"):
        assert criteria.match(_notice(f"Assurance {variante} pour le groupe scolaire")).retained


def test_abreviation_courte_ne_matche_pas_au_milieu_d_un_mot():
    """`trc` et `puc` sont assez courts pour matcher par accident sans bornage — c'est
    exactement ce que le bornage sur frontières de mots empêche."""
    criteria = _criteria(["trc", "puc"])
    assert not criteria.match(_notice("Travaux d'installation electrique et capucine")).retained
    assert criteria.match(_notice("Assurance TRC du chantier")).retained


def test_terme_trouve_dans_la_description_seulement():
    """Sur un marché d'assurances multi-lots, l'objet global ne dit rien de la DO : le terme
    n'apparaît que dans l'intitulé d'un lot."""
    criteria = _criteria(["dommages ouvrage"])
    notice = _notice(
        "Marché de services d'assurances de la commune",
        description="LOT 1 — flotte automobile\nLOT 2 — assurance dommages ouvrage",
    )
    result = criteria.match(notice)
    assert result.retained
    assert result.matched_terms == ["dommages ouvrage"]


def test_exclusion_prime_sur_inclusion():
    criteria = _criteria(["dommages ouvrage"], exclude=["avis d'attribution"])
    result = criteria.match(_notice("Avis d'attribution — assurance dommages ouvrage"))
    assert not result.retained
    assert result.excluded_by == "avis d'attribution"
    assert "écarté" in result.reason()


def test_avis_hors_perimetre_non_retenu():
    criteria = _criteria(["dommages ouvrage", "tous risques chantier"])
    assert not criteria.match(_notice("Assurance flotte automobile de la collectivité")).retained


def test_config_livree_retient_un_avis_do_reel_et_ecarte_une_flotte():
    """Vérifie le YAML réellement livré, pas seulement le moteur de filtrage."""
    get_veille_criteria.cache_clear()
    criteria = get_veille_criteria()
    assert criteria.lookback_days >= 1

    reel = Notice(
        source="boamp",
        source_id="26-79114",
        objet="Assurance Dommage Ouvrage (DO) et Assurance Tous Risques Chantier (TRC) - Réhabilitation",
        deadline_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
    )
    assert criteria.match(reel).retained
    assert not criteria.match(_notice("Assurance flotte automobile et risques statutaires")).retained
