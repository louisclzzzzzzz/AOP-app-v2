"""Planificateur du balayage quotidien de veille.

Une tâche asyncio qui dort jusqu'à la prochaine occurrence de l'heure configurée, plutôt qu'une
dépendance de planification (APScheduler, Celery) : l'application est mono-instance et locale
(§PLAN « lancement en une commande »), un travail quotidien unique ne justifie ni un service
supplémentaire ni un broker à faire tourner sur le poste d'un souscripteur.

Désactivé par défaut (`AOP_VEILLE_DAILY_SCAN`) : rien ne doit sortir seul vers des API externes
sans que quelqu'un l'ait décidé — ni sur un poste de test, ni depuis l'exécutable Windows
distribué, ni pendant la suite de tests.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.settings import get_settings
from app.veille.pipeline import run_scan

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _seconds_until_next_run(now: dt.datetime, hour: int) -> float:
    """Secondes avant la prochaine occurrence de `hour:00` en heure locale."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


async def _loop(hour: int) -> None:
    while True:
        delay = _seconds_until_next_run(dt.datetime.now(), hour)
        logger.info("Veille : prochain balayage dans %.0f min", delay / 60)
        await asyncio.sleep(delay)
        try:
            report = await run_scan(triggered_by="schedule")
            logger.info(
                "Veille : %d avis vus, %d retenus, %d nouveaux, %d DCE récupérés",
                report.notices_seen,
                report.notices_retained,
                report.notices_new,
                report.dce_retrieved,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — un balayage raté ne doit jamais tuer la planification
            logger.exception("Veille : le balayage planifié a échoué")


def start_scheduler() -> None:
    """Démarre la tâche de fond si la planification est activée. Idempotent."""
    global _task
    settings = get_settings()
    if not settings.veille_daily_scan or _task is not None:
        return
    _task = asyncio.create_task(_loop(settings.veille_scan_hour))
    logger.info("Veille : balayage quotidien activé à %02dh00", settings.veille_scan_hour)


async def stop_scheduler() -> None:
    """Arrête proprement la tâche de fond (arrêt du serveur, tests)."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
