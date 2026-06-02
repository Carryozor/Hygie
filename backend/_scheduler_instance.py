# backend/_scheduler_instance.py
"""APScheduler singleton accessible by main.py and routers without circular imports."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def reschedule_jobs(scan_minutes: int | None = None, deletion_minutes: int | None = None) -> None:
    """Reschedule interval jobs live after settings change."""
    try:
        if scan_minutes is not None:
            scheduler.reschedule_job("scan_job", trigger="interval", minutes=max(1, scan_minutes))
            logger.info("scan_job rescheduled to %dm interval", max(1, scan_minutes))
    except Exception as e:
        logger.warning("reschedule scan_job: %s", e)
    try:
        if deletion_minutes is not None:
            scheduler.reschedule_job("deletion_job", trigger="interval", minutes=max(1, deletion_minutes))
            logger.info("deletion_job rescheduled to %dm interval", max(1, deletion_minutes))
    except Exception as e:
        logger.warning("reschedule deletion_job: %s", e)
