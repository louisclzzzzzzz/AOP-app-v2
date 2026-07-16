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


def init_db() -> None:
    Base.metadata.create_all(get_engine())


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
