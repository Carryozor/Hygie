"""Public health check endpoint — DB, scheduler, disk, encryption."""
import logging
import os
import shutil
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..db.utils import DB_PATH
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # DB check
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'") as cur:
                row = await cur.fetchone()
                status_info["database"] = f"{row[0]} tables" if row else "empty"
    except Exception as e:
        status_info["database"] = f"error: {e}"
        status_info["status"] = "degraded"

    # Scheduler check — verify critical jobs exist and have a valid next_run_time
    try:
        if _scheduler is not None:
            jobs = {j.id: j for j in _scheduler.get_jobs()}
            critical = ("scan_job", "deletion_job")
            missing = [jid for jid in critical if jid not in jobs or jobs[jid].next_run_time is None]
            if missing:
                status_info["scheduler"] = f"degraded (jobs sans next_run: {', '.join(missing)})"
                status_info["status"] = "degraded"
            else:
                status_info["scheduler"] = f"{len(jobs)} jobs"
        else:
            status_info["scheduler"] = "unavailable"
    except Exception:
        status_info["scheduler"] = "unavailable"

    # Disk check
    try:
        _, _, free = shutil.disk_usage(os.path.dirname(DB_PATH))
        free_mb = free // (1024 * 1024)
        status_info["disk_free_mb"] = free_mb
        if free_mb < 50:
            status_info["disk"] = "low"
            status_info["status"] = "degraded"
        else:
            status_info["disk"] = "ok"
    except Exception:
        status_info["disk"] = "unavailable"

    # Encryption check
    if os.environ.get("HYGIE_ENCRYPTION_KEY"):
        status_info["encryption"] = "enabled"
    else:
        status_info["encryption"] = "disabled (HYGIE_ENCRYPTION_KEY not set)"
        if status_info["status"] == "healthy":
            status_info["status"] = "degraded"

    code = 200 if status_info["status"] == "healthy" else 503
    return JSONResponse(status_info, status_code=code)
