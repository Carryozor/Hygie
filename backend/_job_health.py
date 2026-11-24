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
import time
from typing import Optional

from .db.logs import add_log
from .db.repositories import get_last_job_run
from .db.settings_store import get_setting, set_setting
from .db.utils import now_utc, parse_iso_dt
from .discord_client import send_alert
from ._lock_backend import LockNotAvailable

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


_WATCHDOG_LOCK_NAME = "hygie_backup_watchdog_lock"
_watchdog_guard_cls = None


def _watchdog_lock():
    """Guard private to the watchdog: its own advisory lock and in-process flag.

    It must never share hygie_backup_lock / _BackupGuard._busy with run_backup:
    the 6 h watchdog and the 24 h backup are anchored on the same start, so
    they collide daily and the loser used to skip silently.
    """
    global _watchdog_guard_cls
    if _watchdog_guard_cls is None:
        from . import backup

        class _WatchdogGuard(backup._BackupGuard):
            _busy = False  # own flag: the class attribute is what is shared
            _LOCK_NAME = _WATCHDOG_LOCK_NAME

        _watchdog_guard_cls = _WatchdogGuard
    return _watchdog_guard_cls()


_BACKUP_ALERT_KEY = "backup_stale_alert_ts"
_BACKUP_ALERT_EVERY_S = 24 * 3600


async def warn_if_backup_stale() -> bool:
    """Alert (at most once a day) when the newest valid dump is too old.

    Returns True when an alert was raised. `never` (fresh install, first dump
    not yet due) stays silent; so does a disabled backup. The throttle lives in
    the settings table and the check runs under its own guard, so the two
    uvicorn workers cannot both alert.
    """
    from . import backup

    try:
        async with _watchdog_lock():
            status = await backup.backup_status()
            if status["state"] != "stale":
                return False
            try:
                last = float(await get_setting(_BACKUP_ALERT_KEY) or 0)
            except ValueError:
                last = 0.0
            if time.time() - last < _BACKUP_ALERT_EVERY_S:
                return False
            await set_setting(_BACKUP_ALERT_KEY, str(time.time()))
    except LockNotAvailable:
        return False
    except Exception as e:
        logger.warning("warn_if_backup_stale: %s", e)
        return False

    detail = (
        f"Dernière sauvegarde valide : {status['last_backup_at']} "
        f"({status['age_hours']:.0f} h, intervalle configuré : {status['interval_hours']} h). "
        "Vérifier les logs « Backup » et le dossier de sauvegarde."
    )
    await add_log("ERROR", f"Sauvegarde périmée — {detail}", "system")
    try:
        await send_alert(
            "💾 Sauvegarde de la base périmée", detail, "error",
            template_vars={"detail": detail},
        )
    except Exception as e:
        logger.warning("warn_if_backup_stale: alert failed: %s", e)
    return True
