"""P3-7: the 03:00 cleanup runs once across workers and its failures are visible."""
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import backend.main as main_mod
from backend._lock_backend import AsyncioLockBackend, LockNotAvailable


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng

    db_path = str(tmp_path / "cleanup_lock.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    from backend.db.schema import init_db
    await init_db()
    yield db_path


class _UnavailableLock:
    """Mimics MariaDBAdvisoryLockBackend when another worker holds the lock."""

    def locked(self):
        return False

    async def __aenter__(self):
        raise LockNotAvailable("hygie_cleanup_lock")

    async def __aexit__(self, *_):
        return None


def _steps():
    return (
        patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock()),
        patch.object(main_mod, "sync_emby_collection", new=AsyncMock()),
        patch.object(main_mod, "sync_plex_overlays", new=AsyncMock()),
    )


async def test_internal_cleanup_skips_when_lock_held_elsewhere(caplog):
    p1, p2, p3 = _steps()
    with p1 as m1, p2 as m2, p3 as m3, \
         patch.object(main_mod, "cleanup_lock", _UnavailableLock()), \
         caplog.at_level(logging.INFO, logger="hygie"):
        await main_mod._internal_cleanup()
    m1.assert_not_awaited()
    m2.assert_not_awaited()
    m3.assert_not_awaited()
    assert any("another worker" in r.getMessage() for r in caplog.records)


async def test_internal_cleanup_skips_when_lock_locked_in_process():
    lock = AsyncioLockBackend("cleanup")
    await lock.acquire()
    p1, p2, p3 = _steps()
    with p1 as m1, p2, p3, patch.object(main_mod, "cleanup_lock", lock):
        await asyncio.wait_for(main_mod._internal_cleanup(), timeout=2)
    m1.assert_not_awaited()


async def test_internal_cleanup_releases_lock_after_run():
    lock = AsyncioLockBackend("cleanup")
    p1, p2, p3 = _steps()
    with p1 as m1, p2, p3, patch.object(main_mod, "cleanup_lock", lock):
        await main_mod._internal_cleanup()
    m1.assert_awaited_once()
    assert not lock.locked()


async def test_cleanup_failure_logged_at_warning(caplog):
    from backend.db.engine import get_db
    with patch.object(main_mod, "run_ignored_cleanup", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(main_mod, "sync_emby_collection", new=AsyncMock()) as m2, \
         patch.object(main_mod, "sync_plex_overlays", new=AsyncMock()) as m3, \
         caplog.at_level(logging.WARNING, logger="hygie"):
        await main_mod._internal_cleanup()
    m2.assert_awaited_once()
    m3.assert_awaited_once()
    assert any(r.levelno == logging.WARNING and "boom" in r.getMessage() for r in caplog.records)
    async with get_db() as db:
        rows = await db.fetch_all("SELECT level, message FROM logs WHERE level='WARN'")
    assert any("boom" in r["message"] for r in rows)


def test_cleanup_lock_is_distinct_lock():
    from backend import _lock_backend as lb
    assert lb.cleanup_lock is not lb.scan_lock
    assert lb.cleanup_lock is not lb.deletion_lock


def test_cleanup_lock_mariadb_name_unique():
    # Subprocess: reloading _lock_backend in-process would redefine LockNotAvailable
    # and break `except LockNotAvailable` identity in other tests.
    code = (
        "from backend import _lock_backend as lb;"
        "n={lb.scan_lock._name, lb.deletion_lock._name, lb.cleanup_lock._name};"
        "print(lb.cleanup_lock._name, len(n))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        env={**os.environ, "HYGIE_LOCK_BACKEND": "mariadb"},
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout.split()
    assert out == ["hygie_cleanup_lock", "3"]
