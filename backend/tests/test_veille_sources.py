"""Normalisation des réponses BOAMP et TED, et dédoublonnage entre les deux sources.

Les charges utiles utilisées ici sont des extraits RÉELS des deux API (relevés le 11/08/2026),
réduits aux champs exploités : c'est ce qui donne au test sa valeur — il casse si la forme
renvoyée change, ce qu'un jeu de données inventé ne détecterait pas.
"""
from __future__ import annotations

import datetime as dt

from app.veille.criteria import VeilleCriteria
from app.veille.dedup import merge_notices
from app.veille.notice import Notice
from app.veille.sources.boamp import _build_where, _to_notice as boamp_to_notice
from app.veille.sources.ted import _build_query, _to_notice as ted_to_notice

_BOAMP_RECORD = {
    "idweb": "26-79114",
    "objet": "Assurance Dommage Ouvrage (DO) et Assurance Tous Risques Chantier (TRC)",
    "nomacheteur": "CA MULHOUSE ALSACE AGGLOMERATION",
    "dateparution": "2026-08-09",
    "datelimitereponse": "2026-09-15T12:00:00+00:00",
    "url_avis": "https://www.boamp.fr/pages/avis/?q=idweb:26-79114",
    "code_departement": ["68"],
    "procedure_libelle": "Procédure Adaptée",
    "nature_libelle": "Avis de marché",
    "nature": "APPEL_OFFRE",
    "donnees": {
        "MAPA": {
            "organisme": {
                "acheteurPublic": "CA Mulhouse Alsace Agglomération",
                "urlProfilAcheteur": "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&id=1&orgAcronyme=m8h",
            },
            "initial": {
                "description": {"objet": "Réhabilitation du centre nautique"},
                "caracteristiques": {"principales": "Assurance dommages ouvrage de l'opération."},
            },
        }
    },
}

_TED_NOTICE = {
    "publication-number": "443934-2026",
    "notice-title": {"fra": ["Assurances de la construction"]},
    "buyer-name": {"fra": ["HABELLIS (21)"]},
    "description-lot": {
        "fra": ['Assurance "Dommages Ouvrage - Responsabilité Décennale CNR et Tous Risques Chantier"']
    },
    # Répété par lot, et échappé en HTML : les deux particularités eForms à absorber.
    "document-url-lot": [
        "https://www.marches-publics.info/index.cfm?fuseaction=dematEnt.login&amp;type=DCE&amp;IDM=1",
        "https://www.marches-publics.info/index.cfm?fuseaction=dematEnt.login&amp;type=DCE&amp;IDM=1",
    ],
    "deadline-receipt-tender-date-lot": ["2026-09-30+02:00", "2026-08-06+02:00"],
    "publication-date": "2026-06-29+02:00",
    "classification-cpv": ["66510000", "66510000", "66516000"],
    "notice-type": "cn-standard",
    "procedure-type": "open",
}


def test_boamp_extrait_le_profil_acheteur_enfoui_dans_donnees():
    notice = boamp_to_notice(_BOAMP_RECORD)
    assert notice is not None
    assert notice.source == "boamp"
    assert notice.source_id == "26-79114"
    assert notice.buyer_name == "CA MULHOUSE ALSACE AGGLOMERATION"
    assert notice.dce_url is not None and "orgAcronyme=m8h" in notice.dce_url
    assert notice.deadline_at == dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    assert notice.departments == ["68"]


def test_boamp_description_agrege_les_blocs_descriptifs_sans_doublon():
    notice = boamp_to_notice(_BOAMP_RECORD)
    assert notice is not None
    assert "Réhabilitation du centre nautique" in notice.description
    assert "Assurance dommages ouvrage" in notice.description
    assert notice.description.count("Réhabilitation du centre nautique") == 1


def test_boamp_record_sans_objet_est_ignore():
    assert boamp_to_notice({"idweb": "26-1"}) is None


def test_boamp_where_combine_fenetre_et_termes():
    criteria = VeilleCriteria(
        lookback_days=30,
        require_any=[],
        exclude_any=[],
        boamp={"search_terms": ['tous risques "chantier"']},
        ted={},
    )
    where = _build_where(criteria, dt.date(2026, 7, 1))
    # Apostrophes, pas guillemets doubles : l'API rejette la seconde forme (400).
    assert "dateparution >= date'2026-07-01'" in where
    # Le guillemet du terme doit être échappé, sinon la clause ODSQL est cassée.
    assert r'search(objet, "tous risques \"chantier\"")' in where


def test_ted_aplati_le_multilingue_et_deduplique_les_valeurs_par_lot():
    notice = ted_to_notice(_TED_NOTICE)
    assert notice is not None
    assert notice.source_id == "443934-2026"
    assert notice.objet == "Assurances de la construction"
    assert notice.buyer_name == "HABELLIS (21)"
    # Une seule URL, désechappée, malgré la répétition sur chaque lot.
    assert notice.dce_url == "https://www.marches-publics.info/index.cfm?fuseaction=dematEnt.login&type=DCE&IDM=1"
    assert notice.cpv_codes == ["66510000", "66516000"]


def test_ted_retient_la_date_limite_la_plus_proche():
    """C'est la plus contraignante : c'est elle qui décide de l'urgence du dossier."""
    notice = ted_to_notice(_TED_NOTICE)
    assert notice is not None
    assert notice.deadline_at.date() == dt.date(2026, 8, 6)


def test_ted_query_assemble_cpv_pays_date_et_plein_texte():
    criteria = VeilleCriteria(
        lookback_days=30,
        require_any=[],
        exclude_any=[],
        boamp={},
        ted={"cpv": ["66510000", "66513200"], "countries": ["FRA"], "full_text": ["dommages ouvrage"]},
    )
    query = _build_query(criteria, since=dt.date(2026, 7, 1))
    assert "classification-cpv IN (66510000 66513200)" in query
    assert "buyer-country IN (FRA)" in query
    # TED n'accepte que AAAAMMJJ sur les champs date, jamais un ISO avec tirets.
    assert "publication-date>=20260701" in query
    assert 'FT~("dommages ouvrage")' in query


def _notice(source: str, source_id: str, buyer: str, deadline: dt.datetime | None, dce: str | None = None):
    return Notice(
        source=source,
        source_id=source_id,
        objet="Assurance dommages ouvrage",
        buyer_name=buyer,
        deadline_at=deadline,
        dce_url=dce,
    )


def test_dedup_fusionne_la_meme_consultation_publiee_aux_deux_endroits():
    deadline = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    merged = merge_notices(
        [
            _notice("boamp", "26-1", "Ville de Rennes", deadline, dce="https://profil.example"),
            _notice("ted", "443934-2026", "VILLE DE RENNES", deadline, dce="https://dce.example"),
        ]
    )
    assert len(merged) == 1
    # TED est retenu comme avis principal : lui seul publie le lien direct vers le DCE.
    assert merged[0].primary.source == "ted"
    assert sorted(merged[0].sources) == ["boamp", "ted"]
    assert merged[0].best_dce_url() == "https://dce.example"


def test_dedup_ne_fusionne_pas_sans_acheteur_ou_sans_date_limite():
    """Sans quoi rapprocher, mieux vaut un doublon visible qu'une fusion à tort."""
    merged = merge_notices(
        [
            _notice("boamp", "26-1", "Ville de Rennes", None),
            _notice("ted", "443934-2026", "Ville de Rennes", None),
        ]
    )
    assert len(merged) == 2


def test_dedup_ne_fusionne_pas_deux_acheteurs_differents():
    deadline = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    merged = merge_notices(
        [
            _notice("boamp", "26-1", "Ville de Rennes", deadline),
            _notice("ted", "443934-2026", "Ville de Brest", deadline),
        ]
    )
    assert len(merged) == 2


def test_dedup_recupere_le_lien_dce_de_la_publication_secondaire():
    """L'avis principal ne porte pas toujours le lien le plus exploitable."""
    deadline = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    merged = merge_notices(
        [
            _notice("boamp", "26-1", "Ville de Rennes", deadline, dce="https://profil.example"),
            _notice("ted", "443934-2026", "Ville de Rennes", deadline, dce=None),
        ]
    )
    assert len(merged) == 1
    assert merged[0].best_dce_url() == "https://profil.example"


def test_dedup_prefere_le_lien_exploitable_a_celui_de_la_source_principale():
    """Cas relevé sur le flux réel : pour une consultation PLACE, TED publie la racine de la
    plateforme (sans identifiant de consultation, donc inutilisable) alors que le jumeau BOAMP
    porte l'URL complète. C'est le lien exploitable qui doit gagner, pas la source."""
    deadline = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
    complet = "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&id=Mzk=&orgAcronyme=d4t"
    merged = merge_notices(
        [
            _notice("boamp", "26-1", "Ville de Rennes", deadline, dce=complet),
            _notice("ted", "443934-2026", "Ville de Rennes", deadline, dce="https://www.marches-publics.gouv.fr/entreprise"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].primary.source == "ted"
    assert merged[0].best_dce_url() == complet
