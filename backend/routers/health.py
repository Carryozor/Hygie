"""Public health check endpoint — DB, scheduler, disk, encryption."""
import logging
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..db.engine import get_db, DIALECT
from ..version import VERSION

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

_scheduler = None


def set_scheduler(scheduler) -> None:
    """Wire the APScheduler instance after it is created in main.py lifespan."""
    global _scheduler
    _scheduler = scheduler


@router.get("/health")
async def health():
    """Public healthcheck — for Uptime Kuma, Docker, etc."""
    status_info: dict = {
        "status": "healthy",
        "version": VERSION,
        "dialect": DIALECT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # DB check — dialect-aware via get_db() abstraction
    try:
        async with get_db() as db:
            if DIALECT == "mariadb":
                row = await db.fetch_one(
                    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE()"
                )
            else:
                row = await db.fetch_one(
                    "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'"
                )
            n = row["n"] if row else 0
            mq_exists = await db.table_exists("media_queue")
            if not mq_exists:
                status_info["database"] = f"{DIALECT} / missing required tables"
                status_info["status"] = "degraded"
            else:
                status_info["database"] = f"{DIALECT} / {n} tables"
    except Exception as e:
        status_info["database"] = f"error: {e}"
        status_info["status"] = "degraded"

    # Scheduler check
    try:
        if _scheduler is not None:
            jobs = {j.id: j for j in _scheduler.get_jobs()}
            critical = ("scan_job", "deletion_job")
            missing = [jid for jid in critical if jid not in jobs or jobs[jid].next_run_time is None]
            if missing:
                status_info["scheduler"] = f"degraded (no next_run: {', '.join(missing)})"
                status_info["status"] = "degraded"
            else:
                status_info["scheduler"] = f"{len(jobs)} jobs"
        else:
            status_info["scheduler"] = "unavailable"
    except Exception:
        status_info["scheduler"] = "unavailable"

    # Disk check — always check /app/data regardless of dialect
    try:
        disk_dir = os.path.dirname(os.environ.get("DB_PATH", "/app/data/hygie.db")) or "/app/data"
        _, _, free = shutil.disk_usage(disk_dir)
        free_mb = free // (1024 * 1024)
        status_info["disk_free_mb"] = free_mb
        status_info["disk"] = "low" if free_mb < 50 else "ok"
        if free_mb < 50:
            status_info["status"] = "degraded"
    except Exception:
        status_info["disk"] = "unavailable"

    # Encryption check
    if os.environ.get("HYGIE_ENCRYPTION_KEY"):
        status_info["encryption"] = "enabled"
    else:
        status_info["encryption"] = "disabled (HYGIE_ENCRYPTION_KEY not set)"
        if status_info["status"] == "healthy":
            status_info["status"] = "degraded"

    # Circuit breakers — expose state of all registered breakers
    try:
        from ..arr_clients.circuit_breaker import all_breaker_states
        breakers = all_breaker_states()
        if breakers:
            open_breakers = [n for n, s in breakers.items() if s["state"] == "open"]
            status_info["circuit_breakers"] = breakers
            if open_breakers:
                status_info["status"] = "degraded"
    except Exception:
        pass

    code = 200 if status_info["status"] == "healthy" else 503
    return JSONResponse(status_info, status_code=code)
