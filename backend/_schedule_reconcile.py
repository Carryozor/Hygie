"""Deterministic, worker-independent scheduling of the recurring jobs.

Every uvicorn worker owns its own APScheduler. A settings change reaches one
worker through the route; the others converge through the periodic reconcile.
Both paths build the SAME trigger here: an IntervalTrigger anchored on the end
of the last known run (job_history), or on a fixed date when there is none,
with no worker-specific next_run_time. APScheduler derives the next fire time
from `start_date` alone, so independent schedulers compute identical due dates
even when the anchored one is already in the past.

Kept out of main.py so routers can import it without a circular import.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.triggers.interval import IntervalTrigger

from . import _scheduler_instance
from .db.settings_store import get_bool_setting, get_int_setting, get_setting
from .db.utils import parse_iso_dt

logger = logging.getLogger(__name__)

# Shared phase when a job has never run: identical for every worker.
FIXED_ANCHOR = datetime(2000, 1, 1, tzinfo=timezone.utc)

_MAX_INTERVAL_MIN = 10080
_DEFAULT_SCAN_MIN = 360
_DEFAULT_DELETION_MIN = 60


async def read_intervals() -> tuple[int, int]:
    """(scan_min, deletion_min) from settings, clamped to [1, 10080]; defaults on bad values."""
    try:
        scan_min = max(1, min(_MAX_INTERVAL_MIN, int(
            await get_setting("scan_interval_minutes") or str(_DEFAULT_SCAN_MIN))))
        del_min = max(1, min(_MAX_INTERVAL_MIN, int(
            await get_setting("deletion_check_interval_minutes") or str(_DEFAULT_DELETION_MIN))))
    except (ValueError, TypeError):
        return _DEFAULT_SCAN_MIN, _DEFAULT_DELETION_MIN
    return scan_min, del_min


async def last_run_anchor(job_type: Optional[str]) -> datetime:
    """End of the last known run of `job_type`, else the fixed shared anchor."""
    if job_type is None:
        return FIXED_ANCHOR
    try:
        from .db.engine import get_db
        async with get_db() as db:
            row = await db.fetch_one(
                "SELECT started_at, finished_at FROM job_history WHERE job_type=? "
                "ORDER BY started_at DESC LIMIT 1",
                (job_type,),
            )
        anchor = parse_iso_dt((row or {}).get("finished_at")) or parse_iso_dt(
            (row or {}).get("started_at"))
        if anchor is not None:
            return anchor
    except Exception as e:
        logger.debug("last_run_anchor(%s): %s", job_type, e)
    return FIXED_ANCHOR


async def anchored_trigger(job_type: Optional[str], delta: timedelta) -> IntervalTrigger:
    return IntervalTrigger(
        seconds=int(delta.total_seconds()),
        start_date=await last_run_anchor(job_type),
        timezone=timezone.utc,
    )


async def apply_interval(sched, job_id: str, job_type: Optional[str], delta: timedelta) -> bool:
    """Move an existing job to `delta` on the shared anchor. No-op when unchanged.

    Returns True when the job was modified.
    """
    job = sched.get_job(job_id)
    if job is None or getattr(job.trigger, "interval", None) == delta:
        return False
    # reschedule_job (not modify_job, which keeps the old next_run_time) derives
    # the next run from the trigger alone.
    sched.reschedule_job(job_id, trigger=await anchored_trigger(job_type, delta))
    logger.info("%s moved to %s interval on the shared anchor", job_id, delta)
    return True


async def apply_backup(sched, hours: int, enabled: bool) -> None:
    """Add, move or remove the backup job so it matches the settings."""
    from .backup import run_backup

    job = sched.get_job("backup_job")
    if not enabled or hours <= 0:
        if job is not None:
            sched.remove_job("backup_job")
        return
    delta = timedelta(hours=hours)
    if job is not None and getattr(job.trigger, "interval", None) == delta:
        return
    sched.add_job(
        run_backup, await anchored_trigger(None, delta),
        id="backup_job", replace_existing=True,
    )


async def apply_interval_changes(
    scan_minutes: Optional[int] = None, deletion_minutes: Optional[int] = None,
) -> None:
    """Route path: apply a changed scan/deletion interval on this worker's scheduler."""
    sched = _scheduler_instance.scheduler
    for job_id, job_type, minutes in (
        ("scan_job", "scan", scan_minutes),
        ("deletion_job", "deletion_check", deletion_minutes),
    ):
        if minutes is None:
            continue
        try:
            await apply_interval(sched, job_id, job_type, timedelta(minutes=max(1, minutes)))
        except Exception as e:
            logger.warning("reschedule %s: %s", job_id, e)


async def reconcile_schedules(sched) -> None:
    """Converge this worker's jobs on the settings (changed by another worker)."""
    scan_min, del_min = await read_intervals()
    await apply_interval(sched, "scan_job", "scan", timedelta(minutes=scan_min))
    await apply_interval(sched, "deletion_job", "deletion_check", timedelta(minutes=del_min))
    from .backup import _DEFAULT_INTERVAL_HOURS
    hours = await get_int_setting("backup_interval_hours", _DEFAULT_INTERVAL_HOURS)
    await apply_backup(sched, hours, await get_bool_setting("backup_enabled"))
