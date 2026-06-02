# backend/db/logs.py
"""Structured log persistence and job-history tracking."""
import contextvars
import logging

from .utils import now_utc
from .engine import get_db
from .settings_store import get_setting
from .websocket import _broadcast

logger = logging.getLogger(__name__)

# ─── Job context ──────────────────────────────────────────────────────────────
# Python contextvars propagate through async tasks spawned in the same context.
# Set this at the start of a job run so all add_log() calls within that async
# context automatically carry the job_id — no need to thread it manually.
_current_job_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "hygie_current_job_id", default=None
)


def set_job_context(job_id: int) -> contextvars.Token:
    """Set the current job ID for this async context. Store the returned Token
    and call _current_job_id.reset(token) in a finally block to restore the
    previous value (important for nested job calls).
    """
    return _current_job_id.set(job_id)


# ─── Logs ─────────────────────────────────────────────────────────────────────
_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


async def add_log(level: str, message: str, source: str = "system") -> None:
    """Insert a log entry and broadcast it via WebSocket.

    Automatically picks up the current job_id from the async context (set via
    set_job_context) so log entries can be correlated with their job run.

    Respects the configured log_level: DEBUG entries are suppressed when the
    configured level is INFO or higher (default).
    """
    try:
        configured = (await get_setting("log_level") or "INFO").upper()
        if _LOG_LEVEL_ORDER.get(level, 1) < _LOG_LEVEL_ORDER.get(configured, 1):
            return
    except Exception:
        pass  # Unable to read log level — write anyway

    ts     = now_utc().isoformat()
    job_id = _current_job_id.get()

    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO logs (ts, level, source, message, job_id) VALUES (?, ?, ?, ?, ?)",
                (ts, level, source, message, job_id),
            )
            await db.commit()
    except Exception as e:
        logger.error("Failed to write log: %s", e)

    try:
        payload: dict = {"type": "log", "ts": ts, "level": level, "source": source, "message": message}
        if job_id is not None:
            payload["job_id"] = job_id
        await _broadcast(payload)
    except Exception:
        pass


# ─── Job history ──────────────────────────────────────────────────────────────

async def add_job_run(job_type: str) -> int:
    ts = now_utc().isoformat()
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES (?, ?)",
            (job_type, ts),
        )
        await db.commit()
        return new_id


async def finish_job_run(run_id: int, status: str, message: str = "") -> None:
    ts = now_utc().isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE job_history SET finished_at=?, status=?, message=? WHERE id=?",
            (ts, status, message, run_id),
        )
        await db.commit()
