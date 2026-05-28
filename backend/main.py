"""
Hygie — FastAPI application.

Endpoints:
  GET  /                       — SPA (index.html)
  GET  /health                 — public healthcheck
  GET  /api/version            — public version info
  GET  /api/proxy/image        — image proxy (SSRF-protected)
  WS   /ws                     — log stream
  *    /api/...                — authenticated API
"""
import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import (
    DB_PATH,
    add_log,
    get_bool_setting,
    get_int_setting,
    get_setting,
    init_db,
    register_ws,
    set_setting,
    unregister_ws,
)
from .auth import verify_token
from .scheduler import (
    run_deletion,
    run_ignored_cleanup,
    run_scan,
    sync_emby_collection,
)
from . import proxy


from .version import VERSION


def _static_version() -> str:
    """Compute a short hash of app.js for cache-busting on each deploy."""
    try:
        path = os.path.join(os.path.dirname(__file__), "..", "frontend", "static", "js", "app.js")
        h = hashlib.md5(open(path, "rb").read()).hexdigest()[:10]
        return f"{VERSION}-{h}"
    except Exception:
        return VERSION

STATIC_VERSION = _static_version()


async def _internal_cleanup():
    """Run cleanup jobs silently — no job_history entry, no UI clutter."""
    try:
        await run_ignored_cleanup()
    except Exception as e:
        logger.debug(f"_internal_cleanup: run_ignored_cleanup: {e}")
    try:
        await sync_emby_collection()
    except Exception as e:
        logger.debug(f"_internal_cleanup: sync_emby_collection: {e}")
from .routers import (
    auth,
    backup,
    calendar,
    ignored,
    libraries,
    logs,
    media,
    metrics,
    seerr_rules,
    settings,
    stats,
    storage,
    unmonitored,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hygie")

# Reduce httpx noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ─── APScheduler ──────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()


async def _job_next_run(job_type: str, interval_minutes: int) -> datetime:
    """Return next_run_time for a job, preserving the countdown across restarts.

    Looks up the last run in job_history. If a run exists and the next scheduled
    time is still in the future, return it — so a restart doesn't reset the timer.
    If overdue or no history, schedule 30 seconds after startup.
    """
    now = datetime.now(timezone.utc)
    try:
        from . import database as _dbmod  # live attribute lookup respects monkeypatching in tests
        async with aiosqlite.connect(_dbmod.DB_PATH) as db:
            async with db.execute(
                "SELECT started_at FROM job_history WHERE job_type=? "
                "ORDER BY started_at DESC LIMIT 1",
                (job_type,),
            ) as cur:
                row = await cur.fetchone()
        if row and row[0]:
            last_ran = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if last_ran.tzinfo is None:
                last_ran = last_ran.replace(tzinfo=timezone.utc)
            next_run = last_ran + timedelta(minutes=interval_minutes)
            if next_run > now:
                return next_run
    except Exception as e:
        logger.debug(f"_job_next_run({job_type}): {e}")
    return now + timedelta(seconds=30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    await init_db()

    # Configure log level from settings
    try:
        log_level = (await get_setting("log_level") or "INFO").upper()
        logging.getLogger("hygie").setLevel(getattr(logging, log_level, logging.INFO))
    except Exception:
        pass

    # Schedule jobs — intervals stored in minutes, clamped to [1, 10080]
    try:
        scan_min = max(1, min(10080, int(await get_setting("scan_interval_minutes") or "360")))
        del_min  = max(1, min(10080, int(await get_setting("deletion_check_interval_minutes") or "60")))
    except (ValueError, TypeError):
        scan_min, del_min = 360, 60

    # Preserve countdown across restarts: compute next_run from last job_history entry.
    # If overdue (last run + interval < now), schedule 30s from now so startup is not blocked.
    scan_next = await _job_next_run("scan", scan_min)
    del_next  = await _job_next_run("deletion_check", del_min)

    scheduler.add_job(run_scan, "interval", minutes=scan_min, id="scan_job",
                      next_run_time=scan_next, replace_existing=True)
    scheduler.add_job(run_deletion, "interval", minutes=del_min, id="deletion_job",
                      next_run_time=del_next, replace_existing=True)
    # ignored_cleanup and overlay_daily are internal — they run silently without job_history entries
    scheduler.add_job(
        _internal_cleanup, "interval", hours=12, id="ignored_cleanup", replace_existing=True
    )
    scheduler.add_job(
        _internal_cleanup, "cron", hour=3, minute=0, id="overlay_daily", replace_existing=True
    )

    # Backup job — interval from settings, default 24h. 0 or backup_enabled=false = disabled.
    try:
        from .backup import run_backup as _run_backup, _DEFAULT_INTERVAL_HOURS
        backup_hours = await get_int_setting("backup_interval_hours", _DEFAULT_INTERVAL_HOURS)
        backup_enabled = await get_bool_setting("backup_enabled")
        if backup_enabled and backup_hours > 0:
            scheduler.add_job(
                _run_backup, "interval", hours=backup_hours,
                id="backup_job", replace_existing=True,
            )
    except Exception as e:
        logger.warning(f"backup job setup: {e}")

    # Pre-warm storage cache in background so first navigation is instant
    try:
        from .routers.storage import _fetch_storage_data
        asyncio.create_task(_fetch_storage_data())
    except Exception:
        pass

    scheduler.start()
    app.state.scheduler = scheduler

    logger.info(f"Hygie {VERSION} started — scan={scan_min}min, deletion={del_min}min")
    if not os.environ.get("HYGIE_ENCRYPTION_KEY"):
        logger.warning("HYGIE_ENCRYPTION_KEY not set — sensitive settings stored in plaintext")
    await add_log("INFO", f"Hygie {VERSION} démarré", "system")

    yield

    # Shutdown
    try:
        scheduler.shutdown(wait=True)
    except Exception:
        scheduler.shutdown(wait=False)
    logger.info("Hygie shutdown")


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Hygie",
    description="Gestionnaire intelligent de bibliothèque média pour Emby",
    version=VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# ─── Security headers middleware ──────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(libraries.router)
app.include_router(media.router)
app.include_router(ignored.router)
app.include_router(logs.router)
app.include_router(stats.router)
app.include_router(storage.router)
app.include_router(seerr_rules.router)
app.include_router(calendar.router)
app.include_router(unmonitored.router)
app.include_router(metrics.router)
app.include_router(backup.router)
app.include_router(proxy.router)


# ─── Static & templates ───────────────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory="frontend/static"),
    name="static",
)
templates = Jinja2Templates(directory="frontend/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "version": STATIC_VERSION})


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Public healthcheck — for Uptime Kuma, Docker, etc."""
    status_info = {
        "status": "healthy",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # DB check
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ) as cur:
                row = await cur.fetchone()
                status_info["database"] = f"{row[0]} tables" if row else "empty"
    except Exception as e:
        status_info["database"] = f"error: {e}"
        status_info["status"] = "degraded"

    # Scheduler check — verify critical jobs exist and have a valid next_run_time
    try:
        jobs = {j.id: j for j in scheduler.get_jobs()}
        critical = ("scan_job", "deletion_job")
        missing = [jid for jid in critical if jid not in jobs or jobs[jid].next_run_time is None]
        if missing:
            status_info["scheduler"] = f"degraded (jobs sans next_run: {', '.join(missing)})"
            status_info["status"] = "degraded"
        else:
            status_info["scheduler"] = f"{len(jobs)} jobs"
    except Exception:
        status_info["scheduler"] = "unavailable"

    # Disk check
    try:
        import shutil

        total, used, free = shutil.disk_usage(os.path.dirname(DB_PATH))
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


# ─── Version ──────────────────────────────────────────────────────────────────
@app.get("/api/version")
async def version_info():
    """Public — version display in UI."""
    return {"version": VERSION}



# ─── WebSocket — log stream ───────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # First message must carry the auth token (sent by frontend onopen).
        # 10-second window — reject if auth is missing or invalid.
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        if len(raw) > 8192:
            raise ValueError("message too large")
        data = json.loads(raw)
        if not verify_token(data.get("token", "")):
            raise ValueError("invalid token")
    except Exception:
        try:
            await ws.close(code=1008)  # 1008 = Policy Violation
        except Exception:
            pass
        return

    register_ws(ws)
    try:
        await ws.send_json({"type": "hello", "version": VERSION})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unregister_ws(ws)


# ─── Scheduler control endpoints ──────────────────────────────────────────────
@app.get("/api/scheduler/status")
async def scheduler_status(user: str = Depends(auth.require_auth)):
    """List scheduled jobs and their next run times (returns array for frontend)."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return jobs


@app.post("/api/scheduler/run/{job_id}")
async def scheduler_run(
    job_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(auth.require_auth),
):
    """Trigger a job manually."""
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


# ─── Legacy endpoints (kept for frontend compatibility) ──────────────────────
@app.post("/api/scan/trigger")
async def scan_trigger(
    background_tasks: BackgroundTasks,
    user: str = Depends(auth.require_auth),
):
    """Manual scan trigger (legacy URL — use /api/scheduler/run/scan)."""
    background_tasks.add_task(run_scan)
    return {"status": "started"}


@app.post("/api/deletion/trigger")
async def deletion_trigger(
    background_tasks: BackgroundTasks,
    user: str = Depends(auth.require_auth),
):
    """Manual deletion check trigger."""
    background_tasks.add_task(run_deletion)
    return {"status": "started"}


@app.post("/api/scan/library/{library_id}")
async def scan_library_trigger(
    library_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(auth.require_auth),
):
    """Manual scan for a single library."""
    from .scheduler import run_scan_library
    background_tasks.add_task(run_scan_library, library_id)
    return {"status": "started"}


@app.post("/api/emby-collection/sync")
async def emby_collection_sync(
    background_tasks: BackgroundTasks,
    user: str = Depends(auth.require_auth),
):
    """Manual sync of the Emby 'Bientôt supprimé' collection."""
    background_tasks.add_task(sync_emby_collection)
    return {"status": "started"}


@app.get("/api/jobs/history")
async def jobs_history(user: str = Depends(auth.require_auth), limit: int = 100):
    """Recent job history, deduplicated. Returns job_name + result aliases."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM job_history ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    seen: set = set()
    result = []
    for row in rows:
        d = dict(row)
        d["job_name"] = d.get("job_type") or ""
        d["result"] = d.get("message") or ""
        d["status"] = d.get("status") or "interrupted"
        # Dedup key: same job type within the same minute
        key = f"{d['job_type']}|{(d.get('started_at') or '')[:16]}"
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


@app.get("/api/media/job-status")
async def media_job_status(user: str = Depends(auth.require_auth)):
    """Whether scan/deletion is currently running."""
    from .scheduler import is_deletion_running, is_scan_running
    return {
        "scan_running": is_scan_running(),
        "deletion_running": is_deletion_running(),
    }
