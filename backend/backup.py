"""
Hygie backup — SQLite (online copy) and MariaDB (mysqldump).

Public API:
  run_backup()      — create a timestamped backup, prune old ones
  list_backups()    — list existing backup files (newest first)
"""
import asyncio
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db.engine import DIALECT, SQLITE_PATH
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_log
from .logmsg import lm

logger = logging.getLogger(__name__)

_DEFAULT_PATH           = "/app/data/backups"
_DEFAULT_RETENTION      = 7
_DEFAULT_INTERVAL_HOURS = 24


async def _backup_settings() -> tuple[str, int, int]:
    """Return (backup_dir, interval_hours, retention_count)."""
    backup_dir = (await get_setting("backup_path") or _DEFAULT_PATH).rstrip("/")
    interval   = await get_int_setting("backup_interval_hours", _DEFAULT_INTERVAL_HOURS)
    retention  = await get_int_setting("backup_retention_count", _DEFAULT_RETENTION)
    return backup_dir, max(0, interval), max(1, retention)


# ─── SQLite ───────────────────────────────────────────────────────────────────

def _do_sqlite_backup(src_path: str, dst_path: str) -> None:
    """Blocking SQLite online backup via sqlite3.backup() (runs in thread pool)."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


async def _sqlite_backup(backup_dir: str, ts: str) -> str:
    """Create a SQLite backup. Returns the filename."""
    dst = os.path.join(backup_dir, f"hygie_{ts}.db")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _do_sqlite_backup, SQLITE_PATH, dst)
    return f"hygie_{ts}.db"


# ─── MariaDB ──────────────────────────────────────────────────────────────────

def _do_mariadb_backup(host: str, port: int, user: str, password: str, db: str, dst_path: str) -> None:
    """Blocking mysqldump backup (runs in thread pool)."""
    import stat
    import tempfile
    # Write credentials to a temp file (0600) to avoid password appearing in ps aux / /proc
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as tmp:
        tmp.write(f"[client]\npassword={password}\n")
        tmp_path = tmp.name
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    try:
        cmd = [
            "mysqldump",
            f"--defaults-extra-file={tmp_path}",
            f"--host={host}",
            f"--port={port}",
            f"--user={user}",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--set-gtid-purged=OFF",
            db,
        ]
        with open(dst_path, "wb") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=300)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"mysqldump failed (rc={result.returncode}): {err}")


async def _mariadb_backup(backup_dir: str, ts: str) -> str:
    """Create a MariaDB dump. Returns the filename."""
    from .db.engine import DATABASE_URL, _parse_mariadb_url
    kwargs = _parse_mariadb_url(DATABASE_URL)
    dst    = os.path.join(backup_dir, f"hygie_{ts}.sql.gz")

    # Use gzip compression via shell if available; fallback to plain SQL
    dst_plain = os.path.join(backup_dir, f"hygie_{ts}.sql")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _do_mariadb_backup,
        kwargs["host"], kwargs["port"], kwargs["user"], kwargs["password"],
        kwargs["db"], dst_plain,
    )

    # Compress in a thread pool
    try:
        import gzip
        import shutil
        def _compress():
            with open(dst_plain, "rb") as f_in, gzip.open(dst, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(dst_plain)
        await loop.run_in_executor(None, _compress)
        return f"hygie_{ts}.sql.gz"
    except Exception as e:
        logger.debug("gzip compression failed (%s) — keeping plain SQL dump", e)
        return f"hygie_{ts}.sql"


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_backup(force: bool = False) -> Optional[str]:
    """Create a timestamped backup of the database.

    SQLite: uses sqlite3.backup() for an online consistent copy (.db file).
    MariaDB: uses mysqldump, compresses to .sql.gz if gzip is available.

    Returns the backup filename or None on error/skip.
    force=True bypasses the backup_enabled / interval=0 guard (manual trigger).
    """
    if SQLITE_PATH == ":memory:":
        return None

    backup_dir, interval, retention = await _backup_settings()
    if not force and (not await get_bool_setting("backup_enabled") or interval == 0):
        return None

    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    try:
        if DIALECT == "sqlite":
            filename = await _sqlite_backup(backup_dir, ts)
        else:
            filename = await _mariadb_backup(backup_dir, ts)
    except FileNotFoundError:
        msg = (
            "mysqldump not found — install mariadb-client inside the container "
            "or use your database provider's backup tools."
            if DIALECT != "sqlite" else
            "sqlite3 not available"
        )
        logger.error("Backup failed: %s", msg)
        await add_log("ERROR", lm("backup.error", detail=msg), "system")
        return None
    except Exception as e:
        logger.error("Backup failed: %s", e)
        await add_log("ERROR", lm("backup.error", detail=e), "system")
        return None

    await add_log("INFO", lm("backup.done", ts=ts), "system")

    # Prune old backups (SQLite .db + MariaDB .sql/.sql.gz)
    all_backups = sorted(
        list(Path(backup_dir).glob("hygie_*.db"))
        + list(Path(backup_dir).glob("hygie_*.sql"))
        + list(Path(backup_dir).glob("hygie_*.sql.gz")),
        key=lambda p: p.stat().st_mtime,
    )
    for old in all_backups[: max(0, len(all_backups) - retention)]:
        try:
            old.unlink()
            logger.debug("Backup pruned: %s", old.name)
        except Exception as e:
            logger.warning("Prune backup error: %s", e)

    return filename


def list_backups(backup_dir: Optional[str] = None) -> list[dict]:
    """List existing backup files (SQLite + MariaDB), newest first."""
    d = Path(backup_dir or _DEFAULT_PATH)
    if not d.exists():
        return []
    files = sorted(
        list(d.glob("hygie_*.db"))
        + list(d.glob("hygie_*.sql"))
        + list(d.glob("hygie_*.sql.gz")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "filename":   f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return result
