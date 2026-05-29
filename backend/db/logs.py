# backend/db/logs.py
"""Structured log persistence and job-history tracking."""
import logging

from .utils import DB_PATH, now_utc
from .engine import get_db
from .settings_store import get_setting
from .websocket import _broadcast

logger = logging.getLogger(__name__)

# ─── Logs ─────────────────────────────────────────────────────────────────────
_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


async def add_log(level: str, message: str, source: str = "system"):
    """Insert a log entry and broadcast it via WebSocket.

    Respects the configured log_level: DEBUG entries are suppressed when the
    configured level is INFO or higher (default). This prevents per-item scan
    debug messages from filling the DB.
    """
    try:
        configured = (await get_setting("log_level") or "INFO").upper()
        if _LOG_LEVEL_ORDER.get(level, 1) < _LOG_LEVEL_ORDER.get(configured, 1):
            return
    except Exception:
        pass  # Unable to read log level — write anyway

    ts = now_utc().isoformat()
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO logs (ts, level, source, message) VALUES (?, ?, ?, ?)",
                (ts, level, source, message),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to write log: {e}")

    # Broadcast to WebSocket subscribers
    try:
        await _broadcast({
            "type": "log",
            "ts": ts,
            "level": level,
            "source": source,
            "message": message,
        })
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


async def finish_job_run(run_id: int, status: str, message: str = ""):
    ts = now_utc().isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE job_history SET finished_at=?, status=?, message=? WHERE id=?",
            (ts, status, message, run_id),
        )
        await db.commit()
