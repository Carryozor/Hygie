# backend/_job_health.py
"""Starvation detection for the scheduled jobs.

A scheduled job that is skipped because another worker holds the lock is
normal, and logging it at anything above debug would fire on every cycle in a
multi-worker deployment. A job skipped on *every* cycle is a different thing
entirely: on 2026-09-18 a stranded MariaDB advisory lock stopped the library
scan for 24 h — four consecutive misses — without producing a single visible
log line, because each individual skip looked ordinary.

This module turns "skipped again" into "has not run in N intervals", which is
a claim that can actually fail.
"""
import logging
from typing import Optional

from .db.logs import add_log
from .db.repositories import get_last_job_run
from .db.settings_store import get_setting
from .db.utils import now_utc, parse_iso_dt
from .discord_client import send_alert

logger = logging.getLogger(__name__)

# How many scheduled intervals may pass with no completed run before the job is
# considered starved. 3 tolerates a genuine long run plus one lost cycle.
_STARVATION_INTERVALS = 3


async def _interval_minutes(setting_key: str, default: int) -> int:
    try:
        return max(1, int(await get_setting(setting_key) or default))
    except (TypeError, ValueError):
        return default


async def warn_if_job_starved(
    job_type: str, setting_key: str, default_minutes: int, label: str,
) -> Optional[float]:
    """Log + alert when `job_type` has not completed for several intervals.

    Returns the age in minutes of the last completed run (None when the job has
    never run, or when it is healthy).
    """
    try:
        last = await get_last_job_run(job_type)
    except Exception as e:
        logger.warning("warn_if_job_starved(%s): %s", job_type, e)
        return None
    if not last:
        return None

    started = parse_iso_dt(last.get("started_at"))
    if started is None:
        return None

    interval    = await _interval_minutes(setting_key, default_minutes)
    age_minutes = (now_utc() - started).total_seconds() / 60
    if age_minutes < interval * _STARVATION_INTERVALS:
        return None

    detail = (
        f"{label} : aucune exécution depuis {int(age_minutes)} min "
        f"(intervalle configuré : {interval} min). Le job est systématiquement "
        "ignoré — verrou bloqué ou ordonnanceur en panne."
    )
    await add_log("ERROR", detail, "job")
    try:
        await send_alert(
            f"⏱️ {label} à l'arrêt", detail, "error",
            template_vars={"detail": detail},
        )
    except Exception as e:
        logger.warning("warn_if_job_starved(%s): alert failed: %s", job_type, e)
    return age_minutes
