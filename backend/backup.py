"""
Hygie backup — SQLite (online copy) and MariaDB (mysqldump).

Public API:
  run_backup()      — create a timestamped backup, prune old ones
  list_backups()    — list existing backup files (newest first)
"""
import asyncio
import errno
import gzip
import logging
import os
import posixpath
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ._lock_backend import LockNotAvailable, MariaDBAdvisoryLockBackend
from .db.engine import DIALECT, SQLITE_PATH, get_db
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.logs import add_log
from .logmsg import lm

logger = logging.getLogger(__name__)

_DEFAULT_PATH           = "/app/data/backups"
_DEFAULT_RETENTION      = 7
_DEFAULT_INTERVAL_HOURS = 24

# System directories that must never be used as backup destinations.
# An authenticated admin can set backup_path via the settings API — without
# this check, they could write files to /etc/, /root/, /proc/, etc.
_FORBIDDEN_BACKUP_PREFIXES = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/run", "/boot", "/bin",
    "/sbin", "/lib", "/lib32", "/lib64", "/usr/bin", "/usr/sbin", "/usr/lib",
    "/var/spool", "/var/lib/dpkg",
)

_BACKUP_LOCK_NAME = "hygie_backup_lock"


def _validate_backup_path(path: str) -> None:
    """Raise ValueError if path is relative or targets a sensitive system directory.

    The path is normalised (``//``, ``.``) and resolved through symlinks BEFORE
    the deny-list check, so ``//etc/cron.d`` and a symlink pointing into /etc
    are caught like their canonical form.
    """
    if not path or not path.startswith("/"):
        raise ValueError("Backup path must be absolute")
    if ".." in path.split("/"):
        raise ValueError("Backup path must not contain '..'")
    # posixpath.normpath keeps a leading "//" (POSIX), realpath collapses it.
    candidates = {posixpath.normpath("/" + path.lstrip("/")), os.path.realpath(path)}
    for resolved in candidates:
        for forbidden in _FORBIDDEN_BACKUP_PREFIXES:
            if resolved == forbidden or resolved.startswith(forbidden + "/"):
                raise ValueError(f"Backup path cannot target system directory: {forbidden}")


async def _backup_settings() -> tuple[str, int, int]:
    """Return (backup_dir, interval_hours, retention_count)."""
    backup_dir = (await get_setting("backup_path") or _DEFAULT_PATH).rstrip("/")
    interval   = await get_int_setting("backup_interval_hours", _DEFAULT_INTERVAL_HOURS)
    retention  = await get_int_setting("backup_retention_count", _DEFAULT_RETENTION)
    return backup_dir, max(0, interval), max(1, retention)


# ─── SQLite ───────────────────────────────────────────────────────────────────

def _remove_quietly(path: str) -> None:
    """Delete a failed/partial backup file so it is never listed as a backup."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove partial backup %s: %s", path, e)


def _do_sqlite_backup(src_path: str, dst_path: str) -> None:
    """Blocking SQLite online backup via sqlite3.backup() (runs in thread pool)."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    except BaseException:
        dst.close()
        _remove_quietly(dst_path)
        raise
    finally:
        src.close()
        dst.close()


def _scratch_path(backup_dir: str, ts: str, ext: str) -> str:
    """Unique scratch file in the backup dir.

    Ends in ``.part`` so list_backups()/prune globs never see it, and carries a
    random token so two concurrent runs can never share (or delete) each other's
    intermediate file.
    """
    return os.path.join(backup_dir, f"hygie_{ts}.{uuid.uuid4().hex[:8]}{ext}.part")


# errno values meaning "this filesystem cannot hard-link" (-> os.replace fallback).
_NO_HARDLINK_ERRNOS = frozenset(
    {errno.EPERM, errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK}
)
_STALE_PART_SECONDS = 3600


def _purge_stale_parts(backup_dir: str) -> None:
    """Remove ``hygie_*.part`` scratch files older than one hour (crashed runs)."""
    cutoff = time.time() - _STALE_PART_SECONDS
    for part in Path(backup_dir).glob("hygie_*.part"):
        try:
            if part.stat().st_mtime < cutoff:
                _remove_quietly(str(part))
        except OSError as e:
            logger.warning("Could not inspect scratch file %s: %s", part, e)


def _publish(tmp_path: str, backup_dir: str, ts: str, ext: str) -> str:
    """Atomically expose a finished scratch file as hygie_<ts><ext>; return its name.

    os.link() fails with FileExistsError instead of overwriting, so a backup
    produced in the same second by another run is never clobbered — a numeric
    suffix is used instead. Filesystems without hard links fall back to
    os.replace(). Readers never see a partial file.
    """
    if not _verify_backup_file(tmp_path):
        raise RuntimeError("backup file is missing, empty or unreadable")
    for n in range(100):
        name = f"hygie_{ts}{'' if n == 0 else f'_{n}'}{ext}"
        final = os.path.join(backup_dir, name)
        try:
            os.link(tmp_path, final)
        except FileExistsError:
            continue
        except OSError as e:
            if e.errno not in _NO_HARDLINK_ERRNOS:
                raise
            os.replace(tmp_path, final)
            return name
        _remove_quietly(tmp_path)  # final exists: a stale scratch must not fail the run
        return name
    raise RuntimeError(f"Could not find a free backup name for {ts}")


async def _sqlite_backup(backup_dir: str, ts: str) -> str:
    """Create a SQLite backup. Returns the filename."""
    tmp = _scratch_path(backup_dir, ts, ".db")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _do_sqlite_backup, SQLITE_PATH, tmp)
        return await loop.run_in_executor(None, _publish, tmp, backup_dir, ts, ".db")
    except BaseException:
        _remove_quietly(tmp)
        raise


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
            # No MySQL-only options (e.g. --set-gtid-purged): the image ships
            # the MariaDB client, which rejects them and exits 7.
            db,
        ]
        try:
            with open(dst_path, "wb") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=300)
        except BaseException:
            _remove_quietly(dst_path)
            raise
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    if result.returncode != 0:
        # A failed dump leaves an empty/truncated file that list_backups() would
        # show as a valid backup and that retention would count.
        _remove_quietly(dst_path)
        err = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"mysqldump failed (rc={result.returncode}): {err}")


def _gzip_file(src: str, dst: str) -> None:
    """Compress src into dst (blocking; runs in the thread pool)."""
    with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


async def _mariadb_backup(backup_dir: str, ts: str) -> str:
    """Create a MariaDB dump. Returns the filename.

    The dump and its compressed copy use private scratch names; only the
    finished file is published under its final name.
    """
    from .db.engine import DATABASE_URL, _parse_mariadb_url
    kwargs = _parse_mariadb_url(DATABASE_URL)
    plain = _scratch_path(backup_dir, ts, ".sql")
    packed = _scratch_path(backup_dir, ts, ".sql.gz")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, _do_mariadb_backup,
            kwargs["host"], kwargs["port"], kwargs["user"], kwargs["password"],
            kwargs["db"], plain,
        )
        try:
            await loop.run_in_executor(None, _gzip_file, plain, packed)
            src, ext = packed, ".sql.gz"
        except Exception as e:
            logger.debug("gzip compression failed (%s) — keeping plain SQL dump", e)
            src, ext = plain, ".sql"
        return await loop.run_in_executor(None, _publish, src, backup_dir, ts, ext)
    finally:
        _remove_quietly(plain)
        _remove_quietly(packed)


# ─── Pre-migration safety backup ───────────────────────────────────────────────

async def _db_already_exists() -> bool:
    """Whether the DB has been initialized by a prior boot (vs. a fresh install).

    Both dialects: the schema_migrations bookkeeping table exists. The file
    (SQLite) or database (MariaDB) existing is not enough — init_db_pool()
    creates the SQLite file before this runs, and the MariaDB container creates
    the empty database ahead of time; an empty schema means "fresh". Any check
    failure fails open (returns False, i.e. "treat as fresh, skip the backup")
    — never let this block startup.
    """
    if DIALECT == "sqlite" and (SQLITE_PATH == ":memory:" or not os.path.exists(SQLITE_PATH)):
        return False
    try:
        async with get_db() as db:
            return await db.table_exists("schema_migrations")
    except Exception as e:
        logger.warning("_db_already_exists check failed, assuming fresh install: %s", e)
        return False


async def backup_before_migrations() -> None:
    """Snapshot the DB before run_migrations() touches it, if it already exists.

    A migration that fails or misbehaves mid-run (see CLAUDE.md piège 2 — the
    v4.1.1 regression) currently has no recovery path short of a manual
    restore. force=True bypasses only the periodic *interval* throttle (a
    migration is a rare, high-value moment to snapshot regardless of the
    schedule) — it still honors backup_enabled: an admin who explicitly
    turned backups off (disk-constrained host, external backup solution,
    read-only storage) must not see one appear on every restart anyway.
    """
    if not await _db_already_exists():
        return
    if not await get_bool_setting("backup_enabled"):
        return
    # Every uvicorn worker runs startup: without this, each restart would dump
    # the DB once per worker even when there is nothing to migrate.
    from .db import migrations
    if not await migrations.pending_migration_ids():
        return
    try:
        await run_backup(force=True)
    except Exception as e:
        logger.error("Pre-migration backup failed, continuing startup anyway: %s", e)


# ─── Cross-worker exclusion ─────────────────────────────────────────────────────

class _BackupGuard:
    """Skip-if-busy guard: in-process flag, plus a MariaDB advisory lock across workers.

    Every uvicorn worker schedules run_backup() at the same instant. Entering
    raises LockNotAvailable when another run (this process or another worker)
    already holds the guard; callers skip the cycle. With SQLite (single
    worker) only the harmless in-process flag applies.
    """

    _busy = False

    def __init__(self) -> None:
        self._remote = MariaDBAdvisoryLockBackend(_BACKUP_LOCK_NAME) if DIALECT == "mariadb" else None

    async def __aenter__(self):
        if _BackupGuard._busy:
            raise LockNotAvailable(_BACKUP_LOCK_NAME)
        _BackupGuard._busy = True
        try:
            if self._remote is not None:
                await self._remote.__aenter__()
        except BaseException:
            _BackupGuard._busy = False
            raise
        return self

    async def __aexit__(self, *_):
        try:
            if self._remote is not None:
                await self._remote.__aexit__(*_)
        finally:
            _BackupGuard._busy = False


class _NullLock:
    """No-op async context manager (tests / explicit bypass)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _backup_lock():
    return _BackupGuard()


# ─── Public API ───────────────────────────────────────────────────────────────

def _verify_backup_file(path: str) -> bool:
    """True if path exists, is non-empty and (for .gz) has a readable gzip header."""
    try:
        if os.stat(path).st_size <= 0:
            return False
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as f:
                return len(f.read(1)) == 1
        return True
    except (OSError, EOFError, gzip.BadGzipFile):
        return False


def _prune_old_backups(backup_dir: str, retention: int, keep: str) -> None:
    """Delete the oldest backups beyond retention; never touches `keep`."""
    d = Path(backup_dir)
    all_backups = sorted(
        [p for pat in ("hygie_*.db", "hygie_*.sql", "hygie_*.sql.gz") for p in d.glob(pat)],
        key=lambda p: p.stat().st_mtime,
    )
    excess = max(0, len(all_backups) - retention)
    for old in [p for p in all_backups if p.name != keep][:excess]:
        try:
            old.unlink()
            logger.debug("Backup pruned: %s", old.name)
        except Exception as e:
            logger.warning("Prune backup error: %s", e)


async def _create_backup(backup_dir: str, retention: int) -> Optional[str]:
    """Write, verify, log and prune. Runs while holding the backup guard."""
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    _purge_stale_parts(backup_dir)
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

    if not _verify_backup_file(os.path.join(backup_dir, filename)):
        logger.error("Backup verification failed: %s missing, empty or unreadable", filename)
        _remove_quietly(os.path.join(backup_dir, filename))
        await add_log("ERROR", lm("backup.error", detail=f"{filename}: fichier absent, vide ou illisible"), "system")
        return None

    await add_log("INFO", lm("backup.done", filename=filename), "system")
    _prune_old_backups(backup_dir, retention, keep=filename)
    return filename


async def run_backup(force: bool = False) -> Optional[str]:
    """Create a timestamped backup of the database.

    SQLite: uses sqlite3.backup() for an online consistent copy (.db file).
    MariaDB: uses mysqldump, compresses to .sql.gz if gzip is available.

    Returns the backup filename or None on error/skip (including when another
    worker is already running the backup).
    force=True bypasses the backup_enabled / interval=0 guard (manual trigger).
    """
    if SQLITE_PATH == ":memory:":
        return None

    backup_dir, interval, retention = await _backup_settings()
    if not force and (not await get_bool_setting("backup_enabled") or interval == 0):
        return None

    try:
        _validate_backup_path(backup_dir)
    except ValueError as e:
        logger.error("Backup aborted — invalid backup_path: %s", e)
        await add_log("ERROR", f"Backup aborted: {e}", "system")
        return None

    try:
        async with _backup_lock():
            return await _create_backup(backup_dir, retention)
    except LockNotAvailable:
        logger.debug("run_backup: another run holds the backup lock — skipping")
        return None


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
