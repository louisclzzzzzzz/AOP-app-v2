"""Fonctions CRUD des tables de veille (avis repérés, passages de veille).

Dans un module à part plutôt que dans `repository.py` : la veille est en amont du pipeline
d'analyse et n'en partage aucun modèle — les mélanger n'apporterait qu'un fichier plus long.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.store.models import VeilleNotice, VeilleNoticeStatus, VeilleScan


def _dump(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value else None


def load_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --- Avis ------------------------------------------------------------------


def get_notice(session: Session, notice_id: str) -> VeilleNotice | None:
    return session.get(VeilleNotice, notice_id)


def find_notice(session: Session, source: str, source_id: str) -> VeilleNotice | None:
    stmt = select(VeilleNotice).where(
        VeilleNotice.source == source, VeilleNotice.source_id == source_id
    )
    return session.scalars(stmt).first()


def list_notices(
    session: Session, *, statuses: list[str] | None = None, limit: int = 200
) -> list[VeilleNotice]:
    """Avis les plus actionnables d'abord, en trois groupes :

    1. échéance à venir, la plus proche en tête — c'est ce sur quoi il faut agir ;
    2. échéance inconnue — indéterminé, pas urgent ;
    3. échéance passée — plus rien à en faire, mais conservé (l'avis reste consultable).

    Un simple tri par date limite croissante mettrait au contraire les avis EXPIRÉS en tête,
    puisque leur date est la plus ancienne : exactement l'inverse de l'usage attendu."""
    now = dt.datetime.now(dt.timezone.utc)
    bucket = case(
        (VeilleNotice.deadline_at.is_(None), 1),
        (VeilleNotice.deadline_at < now, 2),
        else_=0,
    )
    stmt = select(VeilleNotice)
    if statuses:
        stmt = stmt.where(VeilleNotice.status.in_(statuses))
    stmt = stmt.order_by(
        bucket.asc(), VeilleNotice.deadline_at.asc(), VeilleNotice.first_seen_at.desc()
    ).limit(limit)
    return list(session.scalars(stmt))


def upsert_notice(
    session: Session,
    *,
    source: str,
    source_id: str,
    objet: str,
    buyer_name: str | None,
    description: str | None,
    published_at: dt.datetime | None,
    deadline_at: dt.datetime | None,
    notice_url: str | None,
    dce_url: str | None,
    cpv_codes: list[str],
    departments: list[str],
    procedure: str | None,
    notice_type: str | None,
    also_published: list[dict[str, Any]],
    matched_terms: list[str],
) -> tuple[VeilleNotice, bool]:
    """Crée l'avis, ou rafraîchit celui déjà connu. Renvoie (avis, est_nouveau).

    La mise à jour ne touche JAMAIS `status` ni les champs de retrait : un avis déjà écarté par
    l'utilisateur, ou dont le DCE a déjà été rapatrié, ne doit pas revenir à l'état neuf parce
    que la source l'a republié (rectificatif, prolongation de délai). Seules les données
    éditoriales de l'avis sont rafraîchies — dont la date limite, qui bouge réellement."""
    existing = find_notice(session, source, source_id)
    notice = existing or VeilleNotice(source=source, source_id=source_id)

    notice.objet = objet
    notice.buyer_name = buyer_name
    notice.description = description
    notice.published_at = published_at
    notice.deadline_at = deadline_at
    notice.notice_url = notice_url
    notice.dce_url = dce_url
    notice.cpv_codes = _dump(cpv_codes)
    notice.departments = _dump(departments)
    notice.procedure = procedure
    notice.notice_type = notice_type
    notice.also_published_json = _dump(also_published)
    notice.matched_terms = _dump(matched_terms)

    if existing is None:
        session.add(notice)
    session.flush()
    return notice, existing is None


def set_notice_status(
    session: Session,
    notice: VeilleNotice,
    status: VeilleNoticeStatus,
    *,
    platform: str | None = None,
    message: str | None = None,
    dossier_id: str | None = None,
    attempted: bool = False,
) -> None:
    notice.status = status.value
    if platform is not None:
        notice.retrieval_platform = platform
    if message is not None:
        notice.retrieval_message = message
    if dossier_id is not None:
        notice.dossier_id = dossier_id
    if attempted:
        notice.retrieval_attempted_at = dt.datetime.now(dt.timezone.utc)
    session.add(notice)
    session.flush()


# --- Passages de veille ----------------------------------------------------


def create_scan(session: Session, triggered_by: str) -> VeilleScan:
    scan = VeilleScan(triggered_by=triggered_by)
    session.add(scan)
    session.flush()
    return scan


def finish_scan(
    session: Session,
    scan: VeilleScan,
    *,
    notices_seen: int,
    notices_retained: int,
    notices_new: int,
    dce_retrieved: int,
    errors: list[str],
) -> None:
    scan.finished_at = dt.datetime.now(dt.timezone.utc)
    scan.notices_seen = notices_seen
    scan.notices_retained = notices_retained
    scan.notices_new = notices_new
    scan.dce_retrieved = dce_retrieved
    scan.errors = _dump(errors)
    session.add(scan)
    session.flush()


def last_scan(session: Session) -> VeilleScan | None:
    stmt = select(VeilleScan).order_by(VeilleScan.started_at.desc()).limit(1)
    return session.scalars(stmt).first()
