"""Orchestration de la veille : balayage des sources, puis retrait des DCE.

Deux opérations distinctes, volontairement séparées :

  - `run_scan()` — interroge BOAMP et TED, filtre, dédoublonne, enregistre les avis. Purement
    documentaire : aucun téléchargement, aucun appel LLM, aucun coût.
  - `retrieve_notice_dce()` — tente le retrait du DCE d'UN avis et, s'il aboutit, crée le
    dossier correspondant. Le dossier est laissé au statut `uploaded` : l'ingestion et
    l'analyse ne démarrent que sur décision humaine (§`POST /api/dossiers/{id}/lancer`), pour
    qu'un balayage nocturne ne puisse jamais engager de dépense d'API sans arbitrage.

Le balayage peut enchaîner les retraits (`auto_retrieval`), mais jamais l'analyse.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.settings import get_settings
from app.store.db import session_scope
from app.store.models import VeilleNotice, VeilleNoticeStatus, VeilleScan
from app.store.repository import create_dossier, find_dossier_by_upload_hash, get_dossier, set_dossier_upload_info
from app.store.veille_repository import (
    create_scan,
    finish_scan,
    set_notice_status,
    upsert_notice,
)
from app.veille.criteria import VeilleCriteria, get_veille_criteria
from app.veille.dedup import MergedNotice, merge_notices
from app.veille.notice import Notice
from app.veille.retrieval import RetrievalStatus, fetch_dce, plan_retrieval
from app.veille.sources.boamp import search_boamp
from app.veille.sources.ted import search_ted

logger = logging.getLogger(__name__)


@dataclass
class ScanReport:
    scan_id: str
    notices_seen: int = 0
    notices_retained: int = 0
    notices_new: int = 0
    dce_retrieved: int = 0
    errors: list[str] = field(default_factory=list)


def _search_all(criteria: VeilleCriteria, since: dt.date) -> tuple[list[Notice], list[str]]:
    """Interroge les deux sources. Chacune est best-effort : l'indisponibilité de l'une laisse
    l'autre produire un résultat exploitable, avec l'erreur remontée telle quelle."""
    boamp_notices, boamp_errors = search_boamp(criteria, since=since)
    ted_notices, ted_errors = search_ted(criteria, since=since)
    return [*ted_notices, *boamp_notices], [*ted_errors, *boamp_errors]


def _persist(merged: MergedNotice, matched_terms: list[str]) -> tuple[str, bool]:
    """Enregistre un avis fusionné. Renvoie (id de l'avis, est_nouveau)."""
    primary = merged.primary
    with session_scope() as session:
        notice, is_new = upsert_notice(
            session,
            source=primary.source,
            source_id=primary.source_id,
            objet=primary.objet,
            buyer_name=primary.buyer_name,
            description=primary.description,
            published_at=(
                dt.datetime.combine(primary.published_at, dt.time.min, tzinfo=dt.timezone.utc)
                if primary.published_at
                else None
            ),
            deadline_at=primary.deadline_at,
            notice_url=primary.notice_url,
            dce_url=merged.best_dce_url(),
            cpv_codes=primary.cpv_codes,
            departments=primary.departments,
            procedure=primary.procedure,
            notice_type=primary.notice_type,
            also_published=[
                {"source": d.source, "source_id": d.source_id, "notice_url": d.notice_url}
                for d in merged.duplicates
            ],
            matched_terms=matched_terms,
        )
        if is_new:
            # Annonce d'emblée si le retrait sera automatisable : l'utilisateur voit dans la
            # liste ce qui l'attend, sans avoir à cliquer pour le découvrir.
            platform, automatable = plan_retrieval(notice.dce_url)
            set_notice_status(
                session,
                notice,
                VeilleNoticeStatus.NEW if automatable else VeilleNoticeStatus.MANUAL_REQUIRED,
                platform=platform,
                message=None if automatable else "Retrait à effectuer depuis la plateforme de l'acheteur.",
            )
        return notice.id, is_new


def _scan_sync(triggered_by: str) -> ScanReport:
    """Cœur synchrone du balayage (appels HTTP bloquants), exécuté hors boucle d'événements."""
    criteria = get_veille_criteria()
    since = dt.date.today() - dt.timedelta(days=criteria.lookback_days)

    with session_scope() as session:
        scan = create_scan(session, triggered_by)
        report = ScanReport(scan_id=scan.id)

    notices, errors = _search_all(criteria, since)
    report.notices_seen = len(notices)
    report.errors = list(errors)

    retained: list[tuple[Notice, list[str]]] = []
    for notice in notices:
        result = criteria.match(notice)
        if result.retained:
            retained.append((notice, result.matched_terms))

    merged_notices = merge_notices([n for n, _ in retained])
    terms_by_key = {(n.source, n.source_id): terms for n, terms in retained}
    report.notices_retained = len(merged_notices)

    new_notice_ids: list[str] = []
    for merged in merged_notices:
        key = (merged.primary.source, merged.primary.source_id)
        notice_id, is_new = _persist(merged, terms_by_key.get(key, []))
        if is_new:
            new_notice_ids.append(notice_id)
    report.notices_new = len(new_notice_ids)

    if get_settings().veille_auto_retrieval:
        for notice_id in new_notice_ids:
            outcome_status = retrieve_notice_dce(notice_id)
            if outcome_status == RetrievalStatus.DOWNLOADED:
                report.dce_retrieved += 1

    with session_scope() as session:
        scan = session.get(VeilleScan, report.scan_id)
        if scan is not None:
            finish_scan(
                session,
                scan,
                notices_seen=report.notices_seen,
                notices_retained=report.notices_retained,
                notices_new=report.notices_new,
                dce_retrieved=report.dce_retrieved,
                errors=report.errors,
            )
    return report


async def run_scan(triggered_by: str = "manual") -> ScanReport:
    """Un passage de veille complet. Les appels réseau sont synchrones (httpx.Client, comme
    Géorisques) : ils partent dans un thread pour ne pas bloquer la boucle d'événements du
    serveur pendant les dizaines de secondes que peut prendre un balayage."""
    return await asyncio.to_thread(_scan_sync, triggered_by)


def _create_dossier_from_archive(archive_path: Path, filename: str) -> str:
    """Crée le dossier correspondant à un DCE rapatrié, sans démarrer aucun traitement.

    Le zip est déplacé (et non copié) dans `workspace/<dossier_id>/upload.zip` pour épouser
    exactement la disposition attendue par le pipeline d'ingestion : un dossier issu de la
    veille est ensuite indiscernable d'un dossier déposé à la main."""
    settings = get_settings()
    label = filename or "dce.zip"

    with session_scope() as session:
        dossier = create_dossier(session, label)
        dossier_id = dossier.id

    dossier_dir = settings.workspace_dir / dossier_id
    dossier_dir.mkdir(parents=True, exist_ok=True)
    destination = dossier_dir / "upload.zip"
    shutil.move(str(archive_path), destination)

    hasher = hashlib.sha256()
    with open(destination, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    upload_sha256 = hasher.hexdigest()

    with session_scope() as session:
        dossier = get_dossier(session, dossier_id)
        assert dossier is not None
        # Même avertissement non bloquant qu'à l'upload manuel : un DCE déjà analysé peut
        # légitimement l'être à nouveau (avis rectificatif, taxonomie mise à jour).
        duplicate = find_dossier_by_upload_hash(session, upload_sha256, exclude_id=dossier_id)
        set_dossier_upload_info(session, dossier, upload_sha256=upload_sha256, duplicate_of=duplicate)

    return dossier_id


def retrieve_notice_dce(notice_id: str) -> RetrievalStatus:
    """Tente le retrait du DCE d'un avis et crée le dossier en cas de succès.

    Ne lève jamais : toute issue (y compris l'échec) est écrite sur l'avis, pour que la liste
    de veille dise toujours où en est chaque dossier."""
    with session_scope() as session:
        notice = session.get(VeilleNotice, notice_id)
        if notice is None:
            return RetrievalStatus.FAILED
        dce_url = notice.dce_url
        set_notice_status(session, notice, VeilleNoticeStatus.RETRIEVING, attempted=True)

    settings = get_settings()
    staging_dir = settings.workspace_dir / ".veille" / notice_id
    staging_path = staging_dir / "dce.zip"
    outcome = fetch_dce(dce_url, staging_path)

    try:
        if outcome.status == RetrievalStatus.DOWNLOADED and outcome.archive_path is not None:
            # Hors session : la création du dossier ouvre ses propres transactions, et SQLite
            # supporte mal deux sessions imbriquées sur la même connexion.
            dossier_id = _create_dossier_from_archive(outcome.archive_path, outcome.filename or "")
            with session_scope() as session:
                notice = session.get(VeilleNotice, notice_id)
                assert notice is not None
                set_notice_status(
                    session,
                    notice,
                    VeilleNoticeStatus.RETRIEVED,
                    platform=outcome.platform,
                    message=outcome.message,
                    dossier_id=dossier_id,
                )
        else:
            status = (
                VeilleNoticeStatus.MANUAL_REQUIRED
                if outcome.status == RetrievalStatus.MANUAL_REQUIRED
                else VeilleNoticeStatus.RETRIEVAL_FAILED
            )
            with session_scope() as session:
                notice = session.get(VeilleNotice, notice_id)
                assert notice is not None
                set_notice_status(
                    session, notice, status, platform=outcome.platform, message=outcome.message
                )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return outcome.status
