"""Régression pour un bug réel (Phase 2, PR #16) : les colonnes `audit_risques_*` avaient été
ajoutées au modèle ORM `Dossier` (app/store/models.py) sans être ajoutées à
`_NEW_DOSSIER_COLUMNS` (app/store/db.py) — le garde-fou additif qui rattrape, au démarrage, une
base SQLite locale déjà peuplée créée avant l'ajout d'une colonne (`create_all()` ne modifie
jamais une table existante). Conséquence en production : toute requête sur `Dossier` levait
`OperationalError: no such column: dossiers.audit_risques_md`, un 500 sur `/api/dossiers` — passé
inaperçu en tests car `isolated_workspace` construit toujours une base neuve (déjà à jour), qui
n'exerce jamais ce chemin.

Ce test simule une base "historique" (toutes les colonnes du modèle actuel SAUF celles déclarées
comme additives) puis vérifie que `_ensure_new_columns` les restaure toutes — si un futur ajout de
colonne au modèle oublie de l'enregistrer dans `_NEW_DOSSIER_COLUMNS`/`_NEW_DOCUMENT_COLUMNS`, ce
test échoue (la requête ORM lève la même erreur qu'en production)."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.store.db import _NEW_DOCUMENT_COLUMNS, _NEW_DOSSIER_COLUMNS, _ensure_new_columns
from app.store.models import Base, Dossier, Document


def test_ensure_new_columns_backfills_legacy_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")

    # Schéma "historique" : toutes les colonnes du modèle actuel, sauf celles gérées par le
    # garde-fou additif — reproduit l'état d'une base créée avant leur introduction.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, new_columns in (("dossiers", _NEW_DOSSIER_COLUMNS), ("documents", _NEW_DOCUMENT_COLUMNS)):
            for column in new_columns:
                # Retire d'abord un éventuel index auto-généré sur la colonne (ex.
                # `upload_sha256`, index=True) : SQLite ne le nettoie pas tout seul lors d'un
                # DROP COLUMN, ce qui laisserait un index orphelin et casserait la table.
                conn.exec_driver_sql(f"DROP INDEX IF EXISTS ix_{table}_{column}")
                conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")

    _ensure_new_columns(engine, "dossiers", _NEW_DOSSIER_COLUMNS)
    _ensure_new_columns(engine, "documents", _NEW_DOCUMENT_COLUMNS)

    inspector = inspect(engine)
    dossier_columns = {c["name"] for c in inspector.get_columns("dossiers")}
    document_columns = {c["name"] for c in inspector.get_columns("documents")}
    for column in _NEW_DOSSIER_COLUMNS:
        assert column in dossier_columns
    for column in _NEW_DOCUMENT_COLUMNS:
        assert column in document_columns

    # Reproduit exactement le symptôme de production : une requête ORM complète sur `Dossier`
    # (qui SELECT toutes les colonnes mappées, y compris les additives) ne doit jamais lever
    # "no such column".
    with Session(engine) as session:
        session.query(Dossier).all()
        session.query(Document).all()


def test_ensure_new_columns_is_idempotent_on_already_up_to_date_schema(tmp_path):
    """Une base déjà à jour (cas courant : `create_all()` vient de la créer de toutes pièces)
    ne doit déclencher aucun ALTER TABLE ni erreur."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)

    _ensure_new_columns(engine, "dossiers", _NEW_DOSSIER_COLUMNS)
    _ensure_new_columns(engine, "documents", _NEW_DOCUMENT_COLUMNS)

    with Session(engine) as session:
        session.query(Dossier).all()


# Schéma "cœur" figé (toutes les colonnes des modèles au moment où ce test a été écrit, MOINS
# celles déjà déclarées dans `_NEW_DOSSIER_COLUMNS`/`_NEW_DOCUMENT_COLUMNS`) : n'a volontairement
# pas vocation à évoluer — sert de référence indépendante pour détecter, ci-dessous, toute
# nouvelle colonne de modèle qui ne serait enregistrée NULLE PART (ni ici, ni dans le garde-fou
# additif). Les deux tests précédents ne peuvent PAS détecter cet oubli à eux seuls : ils dérivent
# leurs colonnes à droper de `_NEW_DOSSIER_COLUMNS` lui-même, donc un oubli dans ce dict resterait
# invisible pour eux (c'est exactement ainsi que le bug réel — colonnes `audit_risques_*`
# oubliées — est passé inaperçu).
_CORE_DOSSIER_COLUMNS = {
    "id", "original_filename", "status", "error_message", "current_step",
    "total_files", "files_text_extracted", "files_non_analyzable", "files_error", "files_classified",
    "reorg_report_json_path", "reorg_report_md_path", "reorg_applied_at",
    "pieces_selected", "pieces_checked", "pieces_present", "pieces_absent", "pieces_error",
    "completeness_report_json_path", "completeness_report_md_path", "completeness_validated_at",
    "fields_total", "fields_extracted", "fields_present", "fields_absent", "fields_incoherent", "fields_error",
    "extraction_report_json_path", "extraction_report_md_path", "extraction_validated_at",
    "created_at", "updated_at",
}
_CORE_DOCUMENT_COLUMNS = {
    "id", "dossier_id", "relative_path", "filename", "extension", "size_bytes", "sha256", "category",
    "is_analyzable", "non_analyzable_reason", "parent_archive_id", "stage", "stage_error",
    "text_extraction_method", "text_cache_id", "detected_title", "preview_text", "key_mentions_json",
    "classification_status", "classification_error", "proposed_category", "proposed_lot",
    "proposed_doc_type", "proposed_filename", "classification_confidence", "classification_justification",
    "classification_signals_json", "classification_model", "classification_model_version", "classified_at",
    "final_category", "final_lot", "final_doc_type", "final_filename", "is_manually_corrected",
    "organized_relative_path", "created_at", "updated_at",
}


def test_every_dossier_column_is_registered_core_or_additive():
    """Invariant structurel qui aurait attrapé le bug réel : toute colonne du modèle `Dossier`
    doit être soit dans le schéma cœur figé, soit dans `_NEW_DOSSIER_COLUMNS` — sinon une base
    SQLite créée avant son ajout ne la recevra jamais (`create_all()` ne modifie jamais une table
    existante) et toute requête ORM échouera en production avec "no such column"."""
    model_columns = {c.name for c in Dossier.__table__.columns}
    unregistered = model_columns - _CORE_DOSSIER_COLUMNS - set(_NEW_DOSSIER_COLUMNS)
    assert not unregistered, (
        f"Colonne(s) {unregistered} ajoutée(s) au modèle Dossier sans être enregistrée(s) dans "
        "_NEW_DOSSIER_COLUMNS (app/store/db.py) — une base SQLite locale existante ne les recevra "
        "jamais au démarrage."
    )


def test_every_document_column_is_registered_core_or_additive():
    model_columns = {c.name for c in Document.__table__.columns}
    unregistered = model_columns - _CORE_DOCUMENT_COLUMNS - set(_NEW_DOCUMENT_COLUMNS)
    assert not unregistered, (
        f"Colonne(s) {unregistered} ajoutée(s) au modèle Document sans être enregistrée(s) dans "
        "_NEW_DOCUMENT_COLUMNS (app/store/db.py) — une base SQLite locale existante ne les recevra "
        "jamais au démarrage."
    )
