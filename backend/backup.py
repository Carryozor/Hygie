"""
SQLite backup — online consistent copy via sqlite3.backup().

Public API:
  run_backup()      — create a timestamped backup, prune old ones
  list_backups()    — list existing backup files (newest first)
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db.utils import DB_PATH
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_log

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/app/data/backups"
_DEFAULT_RETENTION = 7
_DEFAULT_INTERVAL_HOURS = 24


async def _backup_settings() -> tuple[str, int, int]:
    """Return (backup_dir, interval_hours, retention_count). interval=0 means disabled."""
    backup_dir = (await get_setting("backup_path") or _DEFAULT_PATH).rstrip("/")
    interval = await get_int_setting("backup_interval_hours", _DEFAULT_INTERVAL_HOURS)
    retention = await get_int_setting("backup_retention_count", _DEFAULT_RETENTION)
    return backup_dir, max(0, interval), max(1, retention)


def _do_backup(src_path: str, dst_path: str) -> None:
    """Blocking SQLite backup (runs in thread pool)."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


async def run_backup() -> Optional[str]:
    """Create a timestamped backup of the DB. Returns the backup filename or None on error/disabled."""
    import asyncio

    if DB_PATH == ":memory:":
        return None

    backup_dir, interval, retention = await _backup_settings()
    if not await get_bool_setting("backup_enabled") or interval == 0:
        return None  # Backup disabled

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(backup_dir, f"hygie_{ts}.db")

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_backup, DB_PATH, dst)
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        await add_log("ERROR", f"Backup échoué : {e}", "system")
        return None

    await add_log("INFO", f"Backup créé : hygie_{ts}.db", "system")

    # Prune old backups
    backups = sorted(Path(backup_dir).glob("hygie_*.db"))
    excess = backups[: max(0, len(backups) - retention)]
    for old in excess:
        try:
            old.unlink()
            logger.debug(f"Backup pruned: {old.name}")
        except Exception as e:
            logger.warning(f"Prune backup error: {e}")

    return f"hygie_{ts}.db"


def list_backups(backup_dir: Optional[str] = None) -> list[dict]:
    """List existing backup files, newest first."""
    d = Path(backup_dir or _DEFAULT_PATH)
    if not d.exists():
        return []
    files = sorted(d.glob("hygie_*.db"), reverse=True)
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return result
