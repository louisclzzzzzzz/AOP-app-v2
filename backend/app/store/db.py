"""Moteur SQLAlchemy et gestion de session. SQLite local, pas de migrations (Alembic) :
le schéma est appliqué directement via create_all() au démarrage (app mono-instance locale)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings
from app.store.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # `timeout` (secondes) = busy_timeout sqlite3 : nécessaire depuis que l'ingestion
        # traite plusieurs documents en concurrence bornée (§4 OPTIMISATION.md) — sans ça,
        # deux threads qui écrivent en même temps peuvent se heurter à "database is locked".
        connect_args = (
            {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
        )
        _engine = create_engine(settings.database_url, connect_args=connect_args)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


_NEW_DOSSIER_COLUMNS = {
    "synthese_ia": "TEXT",
    "synthese_ia_model": "VARCHAR(128)",
    "synthese_ia_generated_at": "DATETIME",
}


def _ensure_new_dossier_columns(engine: Engine) -> None:
    """`create_all()` n'ajoute que les tables manquantes, jamais de colonnes sur une table déjà
    existante. Garde-fou additif (jamais destructif) pour les colonnes introduites après la
    création initiale d'une base SQLite locale déjà peuplée."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(dossiers)")}
        for column, ddl_type in _NEW_DOSSIER_COLUMNS.items():
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE dossiers ADD COLUMN {column} {ddl_type}")
        conn.commit()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_new_dossier_columns(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    """Force la recréation de l'engine (utilisé par les tests avec un workspace temporaire)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
