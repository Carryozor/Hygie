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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import utils as _db_utils
from .db.utils import DB_PATH
from .db.settings_store import get_setting, set_setting, get_bool_setting, get_int_setting
from .db.logs import add_log
from .logmsg import lm

from .db.schema import init_db
from .db.websocket import register_ws, unregister_ws
from .auth import verify_token
from .scheduler import (
    run_deletion,
    _run_deletion_guarded,
    run_ignored_cleanup,
    run_scan,
    sync_emby_collection,
    sync_plex_overlays,
)
from ._scheduler_instance import scheduler, reschedule_jobs  # noqa: F401 (used by routers/settings.py)
from . import proxy
from .version import VERSION
from .routers import (
    auth,
    backup,
    calendar,
    database as database_router,
    expert_rules,
    health as health_router,
    ignored,
    libraries,
    logs,
    media,
    metrics,
    public as public_router,
    scheduler as scheduler_router,
    seerr_rules,
    settings,
    stats,
    storage,
    unmonitored,
)




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
    try:
        await sync_plex_overlays()
    except Exception as e:
        logger.debug(f"_internal_cleanup: sync_plex_overlays: {e}")

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hygie")

# Reduce httpx noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ─── APScheduler (instance imported from _scheduler_instance) ─────────────────


async def _job_next_run(job_type: str, interval_minutes: int) -> datetime:
    """Return next_run_time for a job, preserving the countdown across restarts.

    Looks up the last run in job_history. If a run exists and the next scheduled
    time is still in the future, return it — so a restart doesn't reset the timer.
    If overdue or no history, schedule 30 seconds after startup.
    """
    now = datetime.now(timezone.utc)
    try:
        from .db.engine import get_db
        async with get_db() as db:
            row = await db.fetch_one(
                "SELECT started_at FROM job_history WHERE job_type=? "
                "ORDER BY started_at DESC LIMIT 1",
                (job_type,),
            )
        if row and row.get("started_at"):
            last_ran = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
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
    # init_db_pool() MUST run before init_db()/run_migrations(): on MariaDB the
    # schema init and migrations go through get_db(), which raises if the pool
    # is not yet initialized. (No-op for SQLite.)
    #
    # It can raise on bad DATABASE_URL (host unreachable, bad credentials,
    # malformed URL). Previously this crashed with a raw traceback before the
    # StartupValidator could report it as a structured CRITICAL. We capture the
    # error and inject it into the validator so the operator sees a clean message.
    from .db.engine import init_db_pool, close_db_pool
    _db_pool_init_error: str = ""
    try:
        await init_db_pool()
    except Exception as _pool_err:
        _db_pool_init_error = str(_pool_err)
        logger.critical("MariaDB pool initialization failed — startup will report CRITICAL: %s", _pool_err)

    # Skip schema init / migrations if the pool failed — they would raise the
    # same unhelpful error; the StartupValidator reports the real cause below.
    if not _db_pool_init_error:
        await init_db()
        from .db.migrations import run_migrations
        await run_migrations()

    # Configure log level from settings
    try:
        log_level = (await get_setting("log_level") or "INFO").upper()
        logging.getLogger("hygie").setLevel(getattr(logging, log_level, logging.INFO))
    except Exception:
        pass

    # Recover items left in 'deleting' by a crash mid-deletion — they would
    # otherwise be skipped by every future deletion run.
    try:
        from .deletion import reset_stale_deleting
        await reset_stale_deleting()
    except Exception as e:
        logger.warning(f"reset_stale_deleting: {e}")

    # Validate configuration — log WARN issues, block on CRITICAL
    from .startup_validator import StartupValidator
    _validator = StartupValidator(db_pool_init_error=_db_pool_init_error)
    _issues    = await _validator.run()
    _can_start = await _validator.log_results(_issues)
    if not _can_start:
        logger.critical(
            "Hygie startup aborted due to CRITICAL configuration issues. "
            "Fix the issues reported above and restart."
        )
        import sys
        sys.exit(1)

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
    scheduler.add_job(_run_deletion_guarded, "interval", minutes=del_min, id="deletion_job",
                      next_run_time=del_next, replace_existing=True)
    # internal_cleanup runs once daily at 3am (no job_history entries).
    # Previously double-scheduled (12h interval + cron 3am) which could overlap.
    scheduler.add_job(
        _internal_cleanup, "cron", hour=3, minute=0, id="internal_cleanup", replace_existing=True
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

    # Pre-warm storage cache in background so first navigation is instant.
    # Keep a reference so we can cancel it cleanly on shutdown (avoids pending-task
    # warnings and event-loop teardown hangs in Python 3.12 asyncio).
    _prewarm_task: asyncio.Task | None = None
    try:
        from .routers.storage import _fetch_storage_data
        _prewarm_task = asyncio.create_task(_fetch_storage_data())
    except Exception:
        pass

    scheduler.start()
    app.state.scheduler = scheduler
    health_router.set_scheduler(scheduler)

    logger.info(f"Hygie {VERSION} started — scan={scan_min}min, deletion={del_min}min")
    if not os.environ.get("HYGIE_ENCRYPTION_KEY"):
        logger.warning("HYGIE_ENCRYPTION_KEY not set — sensitive settings stored in plaintext")
    await add_log("INFO", lm("system.started", version=VERSION), "system")

    yield

    # Shutdown
    if _prewarm_task is not None and not _prewarm_task.done():
        _prewarm_task.cancel()
        try:
            await _prewarm_task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        scheduler.shutdown(wait=True)
    except Exception:
        scheduler.shutdown(wait=False)
    await close_db_pool()
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

# CORS — restrict to same origin by default (self-hosted).
# Override with HYGIE_ALLOWED_ORIGINS env var for reverse-proxy setups.
_allowed_origins = [
    o.strip() for o in
    os.environ.get("HYGIE_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
] or ["http://localhost:8000", "http://localhost:5173"]

# Wildcard origin with credentials is forbidden by the CORS spec and allows
# any site to make credentialed requests — refuse it at startup.
if "*" in _allowed_origins:
    import sys
    print(
        "FATAL: HYGIE_ALLOWED_ORIGINS contains '*' which is incompatible with "
        "allow_credentials=True. Set explicit origins instead.",
        file=sys.stderr,
    )
    sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Security headers middleware ──────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # CSP: disallow inline scripts (reduce XSS surface). The Vue SPA uses
    # compiled JS assets — no inline scripts needed. Adjust script-src if
    # third-party CDN scripts are added in the future.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: ws:; "
        "font-src 'self'; "
        "frame-ancestors 'self'; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    return response


# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(health_router.router)
app.include_router(settings.router)
app.include_router(libraries.router)
app.include_router(media.router)
app.include_router(ignored.router)
app.include_router(logs.router)
app.include_router(stats.router)
app.include_router(storage.router)
app.include_router(seerr_rules.router)
app.include_router(expert_rules.router)
app.include_router(calendar.router)
app.include_router(unmonitored.router)
app.include_router(metrics.router)
app.include_router(backup.router)
app.include_router(proxy.router)
app.include_router(public_router.router)
app.include_router(database_router.router)
app.include_router(scheduler_router.router)

from .routers import plex_webhook
app.include_router(plex_webhook.router)


# ─── Static & templates ───────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(__file__))
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(_ROOT, "frontend", "static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(_ROOT, "frontend", "templates"))

# ─── WebSocket — log stream (DB-poll) ────────────────────────────────────────
async def _ws_max_log_id() -> int:
    """Return the current max log id (0 if empty). Used to anchor the poll cursor."""
    try:
        from .db.engine import get_db
        async with get_db() as db:
            row = await db.fetch_one("SELECT MAX(id) AS m FROM logs")
            return int(row["m"] or 0) if row else 0
    except Exception:
        return 0


async def _ws_fetch_logs_since(cursor: int) -> list[dict]:
    """Fetch log rows with id > cursor, ordered oldest-first, max 200 per poll."""
    try:
        from .db.engine import get_db
        async with get_db() as db:
            rows = await db.fetch_all(
                "SELECT id, ts, level, source, message, job_id "
                "FROM logs WHERE id > ? ORDER BY id ASC LIMIT 200",
                (cursor,),
            )
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Reject cross-origin WebSocket connections before accepting to prevent
    # DNS-rebinding and CSRF-style attacks from hostile pages.
    origin = ws.headers.get("origin", "")
    if origin and origin not in _allowed_origins:
        await ws.close(code=1008)
        return

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
    # Anchor cursor to current max id so the client only receives new logs —
    # historical logs are loaded separately via GET /api/logs.
    cursor = await _ws_max_log_id()
    try:
        await ws.send_json({"type": "hello", "version": VERSION})
        while True:
            # DB-poll every 1 second — works across all workers since the DB is shared.
            new_logs = await _ws_fetch_logs_since(cursor)
            for entry in new_logs:
                payload: dict = {
                    "type": "log",
                    "ts": entry["ts"],
                    "level": entry["level"],
                    "source": entry["source"],
                    "message": entry["message"],
                }
                if entry.get("job_id") is not None:
                    payload["job_id"] = entry["job_id"]
                await ws.send_json(payload)
                cursor = max(cursor, entry["id"])
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unregister_ws(ws)


# ─── SPA fallback (must be last — catches all unmatched GET routes) ───────────
_DIST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")

if os.path.isdir(os.path.join(_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="vue-assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    from fastapi.responses import Response
    # Return 404 for unmatched /api/ routes instead of silently serving index.html,
    # which would make missing or mistyped API paths very hard to debug.
    if full_path.startswith("api/"):
        return Response(status_code=404, content=f"API route not found: /{full_path}")
    index = os.path.join(_DIST, "index.html")
    if os.path.isfile(index):
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return FileResponse("frontend/templates/index.html", headers={"Cache-Control": "no-store"})
