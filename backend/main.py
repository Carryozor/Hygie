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
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import aiosqlite
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import (
    DB_PATH,
    add_log,
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


async def _internal_cleanup():
    """Run cleanup jobs silently — no job_history entry, no UI clutter."""
    try:
        await run_ignored_cleanup()
    except Exception:
        pass
    try:
        await sync_emby_collection()
    except Exception:
        pass
from .version import VERSION
from .routers import (
    auth,
    calendar,
    ignored,
    libraries,
    logs,
    media,
    seerr_rules,
    settings,
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

    # Schedule jobs
    try:
        scan_h = int(await get_setting("scan_interval_hours") or "6")
        del_h = int(await get_setting("deletion_check_interval_hours") or "1")
    except ValueError:
        scan_h, del_h = 6, 1

    scheduler.add_job(run_scan, "interval", hours=scan_h, id="scan_job", replace_existing=True)
    scheduler.add_job(
        run_deletion, "interval", hours=del_h, id="deletion_job", replace_existing=True
    )
    # ignored_cleanup and overlay_daily are internal — they run silently without job_history entries
    scheduler.add_job(
        _internal_cleanup, "interval", hours=12, id="ignored_cleanup", replace_existing=True
    )
    scheduler.add_job(
        _internal_cleanup, "cron", hour=3, minute=0, id="overlay_daily", replace_existing=True
    )
    scheduler.start()
    app.state.scheduler = scheduler

    logger.info(f"Hygie {VERSION} started — scan={scan_h}h, deletion={del_h}h")
    await add_log("INFO", f"Hygie {VERSION} démarré", "system")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Hygie shutdown")


# ─── Image proxy whitelist cache (TTL: 5 min) ────────────────────────────────
_proxy_whitelist: set = set()
_proxy_whitelist_ts: float = 0.0
_PROXY_WHITELIST_TTL = 300


async def _get_proxy_whitelist() -> set:
    global _proxy_whitelist, _proxy_whitelist_ts
    if _proxy_whitelist and time.time() - _proxy_whitelist_ts < _PROXY_WHITELIST_TTL:
        return _proxy_whitelist
    allowed = {
        "image.tmdb.org",
        "artworks.thetvdb.com",
        "thetvdb.com",
        "fanart.tv",
        "assets.fanart.tv",
    }
    for setting_key in ("emby_url", "emby_external_url", "radarr_url", "sonarr_url"):
        s = await get_setting(setting_key)
        if s:
            try:
                h = (urlparse(s).hostname or "").lower()
                if h:
                    allowed.add(h)
            except Exception:
                pass
    _proxy_whitelist = allowed
    _proxy_whitelist_ts = time.time()
    return allowed


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
app.include_router(storage.router)
app.include_router(seerr_rules.router)
app.include_router(calendar.router)
app.include_router(unmonitored.router)


# ─── Static & templates ───────────────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory="frontend/static"),
    name="static",
)
templates = Jinja2Templates(directory="frontend/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "version": VERSION})


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

    # Scheduler check
    try:
        status_info["scheduler"] = f"{len(scheduler.get_jobs())} jobs"
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

    code = 200 if status_info["status"] == "healthy" else 503
    return JSONResponse(status_info, status_code=code)


# ─── Version ──────────────────────────────────────────────────────────────────
@app.get("/api/version")
async def version_info():
    """Public — version display in UI."""
    return {"version": VERSION}


@app.get("/api/stats/global")
async def global_stats(user: str = Depends(auth.require_auth)):
    """Global lifetime statistics for the dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Total ever deleted (from stats_history + current deleted status)
        cur = await db.execute("SELECT COALESCE(SUM(total_deleted),0) FROM stats_history")
        from_history = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM media_queue WHERE status='deleted'")
        in_queue = (await cur.fetchone())[0]

        # If stats_history is empty (before this feature), fall back to queue count
        total_deleted = max(from_history, in_queue)

        # Deletions by month (last 12 months)
        cur = await db.execute(
            "SELECT month, SUM(total_deleted) FROM stats_history "
            "GROUP BY month ORDER BY month DESC LIMIT 12"
        )
        by_month = [{"month": r[0], "deleted": r[1]} for r in await cur.fetchall()]

        # Current queue breakdown
        cur = await db.execute("SELECT status, COUNT(*) FROM media_queue GROUP BY status")
        queue_counts = {r[0]: r[1] for r in await cur.fetchall()}

        # Total scans run
        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type IN ('scan','scan_library')"
        )
        total_scans = (await cur.fetchone())[0]

        # Total deletion checks
        cur = await db.execute(
            "SELECT COUNT(*) FROM job_history WHERE job_type='deletion_check'"
        )
        total_checks = (await cur.fetchone())[0]

        # Ignored count
        cur = await db.execute("SELECT COUNT(*) FROM ignored_media")
        total_ignored = (await cur.fetchone())[0]

    return {
        "total_deleted": total_deleted,
        "total_ignored": total_ignored,
        "total_scans": total_scans,
        "total_deletion_checks": total_checks,
        "queue": queue_counts,
        "by_month": list(reversed(by_month)),
    }


# ─── Image proxy ──────────────────────────────────────────────────────────────
@app.get("/api/proxy/image")
async def proxy_image(request: Request):
    """
    Proxy images from configured services (Emby/Radarr/Sonarr) and TMDB CDN.

    SSRF protection: only hosts matching configured services or known image CDNs.
    No auth requirement — img src can't send Bearer tokens.
    """
    # Extract `url` param manually from raw query string to handle nested & properly
    raw = request.url.query
    if not raw.startswith("url="):
        return Response(status_code=400)

    encoded = raw[4:]
    target_url = unquote(encoded)
    if not target_url:
        return Response(status_code=400)

    try:
        parsed = urlparse(target_url)
        if parsed.scheme not in ("http", "https"):
            return Response(status_code=400)

        host = (parsed.hostname or "").lower()

        allowed = await _get_proxy_whitelist()

        if host not in allowed:
            logger.warning(f"Proxy: host {host!r} not in whitelist")
            return Response(status_code=403, content=f"Host not allowed: {host}")

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(target_url)
            if r.status_code == 200:
                ct = r.headers.get("content-type", "image/jpeg")
                if not ct.startswith("image/"):
                    return Response(status_code=415)
                return Response(
                    content=r.content,
                    media_type=ct,
                    headers={"Cache-Control": "public, max-age=3600"},
                )
            # Don't warn on 500 (Emby returns this for items without posters)
            if r.status_code != 500:
                logger.warning(f"Proxy: upstream HTTP {r.status_code} for {target_url[:60]}")
    except Exception as e:
        logger.error(f"Proxy error: {e}")
    return Response(status_code=404)


# ─── WebSocket — log stream ───────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # First message must carry the auth token (sent by frontend onopen).
        # 10-second window — reject if auth is missing or invalid.
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
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
        d["job_name"] = d.get("job_type", "")
        d["result"] = d.get("message", "")
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
