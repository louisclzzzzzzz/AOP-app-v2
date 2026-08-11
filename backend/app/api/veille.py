"""Endpoints REST de la veille BOAMP / JOUE.

  GET  /api/veille/avis                     liste des avis repérés
  GET  /api/veille/etat                     état de la veille (dernier balayage, réglages)
  POST /api/veille/scan                     balayage à la demande
  POST /api/veille/avis/{id}/retrait        (re)tentative de retrait du DCE
  POST /api/veille/avis/{id}/ecarter        écarter un avis hors périmètre
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import VeilleNotice, VeilleNoticeStatus
from app.store.veille_repository import (
    get_notice,
    last_scan,
    list_notices,
    load_json,
    set_notice_status,
)
from app.veille.pipeline import retrieve_notice_dce, run_scan
from app.veille.retrieval import RetrievalStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/veille", tags=["veille"])


class PublicationOut(BaseModel):
    source: str
    source_id: str
    notice_url: str | None = None


class NoticeOut(BaseModel):
    id: str
    source: str
    source_id: str
    objet: str
    buyer_name: str | None
    description: str | None
    published_at: dt.datetime | None
    deadline_at: dt.datetime | None
    notice_url: str | None
    dce_url: str | None
    cpv_codes: list[str]
    departments: list[str]
    procedure: str | None
    notice_type: str | None
    also_published: list[PublicationOut]
    matched_terms: list[str]
    status: str
    retrieval_platform: str | None
    retrieval_message: str | None
    retrieval_attempted_at: dt.datetime | None
    dossier_id: str | None
    first_seen_at: dt.datetime


class ScanOut(BaseModel):
    scan_id: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    triggered_by: str | None = None
    notices_seen: int = 0
    notices_retained: int = 0
    notices_new: int = 0
    dce_retrieved: int = 0
    errors: list[str] = []


class VeilleStateOut(BaseModel):
    daily_scan_enabled: bool
    scan_hour: int
    auto_retrieval_enabled: bool
    retrieval_identity_configured: bool
    """Faux tant que nom/prénom/e-mail de retrait ne sont pas renseignés : sans eux, aucun
    retrait automatique n'est possible sur les plateformes qui exigent une identité."""
    last_scan: ScanOut | None = None


def _notice_to_out(notice: VeilleNotice) -> NoticeOut:
    return NoticeOut(
        id=notice.id,
        source=notice.source,
        source_id=notice.source_id,
        objet=notice.objet,
        buyer_name=notice.buyer_name,
        description=notice.description,
        published_at=notice.published_at,
        deadline_at=notice.deadline_at,
        notice_url=notice.notice_url,
        dce_url=notice.dce_url,
        cpv_codes=load_json(notice.cpv_codes) or [],
        departments=load_json(notice.departments) or [],
        procedure=notice.procedure,
        notice_type=notice.notice_type,
        also_published=[PublicationOut(**p) for p in (load_json(notice.also_published_json) or [])],
        matched_terms=load_json(notice.matched_terms) or [],
        status=notice.status,
        retrieval_platform=notice.retrieval_platform,
        retrieval_message=notice.retrieval_message,
        retrieval_attempted_at=notice.retrieval_attempted_at,
        dossier_id=notice.dossier_id,
        first_seen_at=notice.first_seen_at,
    )


@router.get("/avis", response_model=list[NoticeOut])
async def list_veille_notices(include_dismissed: bool = False) -> list[NoticeOut]:
    """Avis repérés, les plus urgents d'abord. Les avis écartés sont masqués par défaut."""
    statuses = None if include_dismissed else [
        s.value for s in VeilleNoticeStatus if s is not VeilleNoticeStatus.DISMISSED
    ]
    with session_scope() as session:
        return [_notice_to_out(n) for n in list_notices(session, statuses=statuses)]


@router.get("/etat", response_model=VeilleStateOut)
async def get_veille_state() -> VeilleStateOut:
    settings = get_settings()
    with session_scope() as session:
        scan = last_scan(session)
        scan_out = (
            ScanOut(
                scan_id=scan.id,
                started_at=scan.started_at,
                finished_at=scan.finished_at,
                triggered_by=scan.triggered_by,
                notices_seen=scan.notices_seen,
                notices_retained=scan.notices_retained,
                notices_new=scan.notices_new,
                dce_retrieved=scan.dce_retrieved,
                errors=load_json(scan.errors) or [],
            )
            if scan is not None
            else None
        )
    return VeilleStateOut(
        daily_scan_enabled=settings.veille_daily_scan,
        scan_hour=settings.veille_scan_hour,
        auto_retrieval_enabled=settings.veille_auto_retrieval,
        retrieval_identity_configured=all(
            value.strip()
            for value in (
                settings.veille_contact_nom,
                settings.veille_contact_prenom,
                settings.veille_contact_email,
            )
        ),
        last_scan=scan_out,
    )


@router.post("/scan", response_model=ScanOut)
async def trigger_scan() -> ScanOut:
    """Balayage à la demande. Synchrone côté client (le balayage prend quelques dizaines de
    secondes) pour que le résultat soit affiché tout de suite, sans page à rafraîchir."""
    report = await run_scan(triggered_by="manual")
    return ScanOut(
        scan_id=report.scan_id,
        notices_seen=report.notices_seen,
        notices_retained=report.notices_retained,
        notices_new=report.notices_new,
        dce_retrieved=report.dce_retrieved,
        errors=report.errors,
    )


@router.post("/avis/{notice_id}/retrait", response_model=NoticeOut)
async def trigger_retrieval(notice_id: str) -> NoticeOut:
    """(Re)tente le retrait du DCE d'un avis.

    Utile aussi après coup : une identité de retrait renseignée entre-temps, ou une plateforme
    momentanément indisponible, rendent une seconde tentative légitime."""
    with session_scope() as session:
        notice = get_notice(session, notice_id)
        if notice is None:
            raise HTTPException(404, "Avis introuvable")
        if notice.dossier_id:
            raise HTTPException(409, "Le DCE de cet avis a déjà été récupéré.")

    status = await asyncio.to_thread(retrieve_notice_dce, notice_id)
    if status == RetrievalStatus.FAILED:
        logger.info("Retrait DCE en échec pour l'avis %s", notice_id)

    with session_scope() as session:
        notice = get_notice(session, notice_id)
        assert notice is not None
        return _notice_to_out(notice)


@router.post("/avis/{notice_id}/ecarter", response_model=NoticeOut)
async def dismiss_notice(notice_id: str) -> NoticeOut:
    """Écarte un avis. Le statut est conservé (et non supprimé) pour qu'un balayage ultérieur
    ne le remonte pas comme neuf (§upsert_notice)."""
    with session_scope() as session:
        notice = get_notice(session, notice_id)
        if notice is None:
            raise HTTPException(404, "Avis introuvable")
        set_notice_status(session, notice, VeilleNoticeStatus.DISMISSED)
        return _notice_to_out(notice)
