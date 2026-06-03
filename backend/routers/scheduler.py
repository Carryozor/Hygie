"""Scheduler control, scan/deletion triggers, job history."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from ..auth import require_auth
from .._scheduler_instance import scheduler
from ..scheduler import (
    run_deletion, run_ignored_cleanup, run_scan, run_scan_library,
    sync_emby_collection,
)
from ..db.engine import get_db
from .._job_state import is_deletion_running, is_scan_running
from ..version import VERSION

logger = logging.getLogger(__name__)
router = APIRouter(tags=["scheduler"])


@router.get("/api/version")
async def version_info():
    """Public — version display in UI."""
    return {"version": VERSION}


@router.get("/api/scheduler/status")
async def scheduler_status(user: str = Depends(require_auth)):
    """List scheduled jobs and their next run times."""
    running_map = {"scan_job": is_scan_running(), "deletion_job": is_deletion_running()}
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "is_running": running_map.get(job.id, False),
        })
    return jobs


@router.post("/api/scheduler/run/{job_id}")
async def scheduler_run(
    job_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    """Trigger a job manually."""
    from fastapi.responses import JSONResponse
    job_map = {
        "scan": run_scan,
        "deletion": run_deletion,
        "collection_sync": sync_emby_collection,
        "ignored_cleanup": run_ignored_cleanup,
    }
    fn = job_map.get(job_id)
    if not fn:
        return JSONResponse({"error": "Unknown job"}, status_code=404)
    background_tasks.add_task(fn)
    return {"status": "started"}


@router.post("/api/scan/trigger")
async def scan_trigger(
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    background_tasks.add_task(run_scan)
    return {"status": "started"}


@router.post("/api/deletion/trigger")
async def deletion_trigger(
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    background_tasks.add_task(run_deletion)
    return {"status": "started"}


@router.post("/api/scan/library/{library_id}")
async def scan_library_trigger(
    library_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    background_tasks.add_task(run_scan_library, library_id)
    return {"status": "started"}


@router.post("/api/emby-collection/sync")
async def emby_collection_sync(
    background_tasks: BackgroundTasks,
    user: str = Depends(require_auth),
):
    background_tasks.add_task(sync_emby_collection)
    return {"status": "started"}


@router.get("/api/jobs/history")
async def jobs_history(user: str = Depends(require_auth), limit: int = 100):
    """Recent job history, deduplicated."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT * FROM job_history ORDER BY started_at DESC LIMIT ?", (limit,)
        )
    seen: set = set()
    result = []
    for row in rows:
        d = dict(row)
        d["job_name"] = d.get("job_type") or ""
        d["result"]   = d.get("message") or ""
        d["status"]   = d.get("status") or "interrupted"
        key = f"{d['job_type']}|{(d.get('started_at') or '')[:16]}"
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


@router.get("/api/media/job-status")
async def media_job_status(user: str = Depends(require_auth)):
    return {"scan_running": is_scan_running(), "deletion_running": is_deletion_running()}
