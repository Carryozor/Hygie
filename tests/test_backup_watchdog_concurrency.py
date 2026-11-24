"""P3-6 (4.6.0): the stale-backup watchdog must never make a backup fail.

The watchdog (6 h) and the periodic backup (24 h) are anchored on the same
start, so they collide daily. They shared hygie_backup_lock, and a backup that
lost the race returned None with only a debug line.
"""
import asyncio
import logging
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend._job_health as job_health
import backend.backup as backup_mod

pytestmark = pytest.mark.usefixtures("trust_backup_content")


@pytest.fixture
def env(tmp_path, monkeypatch):
    backup_dir = str(tmp_path / "backups")

    async def _settings(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _bools(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _ints(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 5}.get(key, default)

    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", "/app/data/hygie.db")
    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bools)
    monkeypatch.setattr(backup_mod, "get_int_setting", _ints)
    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    def _fake_copy(src, dst):
        with open(dst, "wb") as f:
            f.write(b"x" * 512)

    monkeypatch.setattr(backup_mod, "_do_sqlite_backup", _fake_copy)

    async def _slow_status():
        await asyncio.sleep(0.1)
        return {"state": "ok"}

    monkeypatch.setattr(backup_mod, "backup_status", _slow_status)
    return backup_dir


async def test_watchdog_never_blocks_backup(env):
    _, name = await asyncio.gather(
        job_health.warn_if_backup_stale(), backup_mod.run_backup(force=True))
    assert name is not None and name.startswith("hygie_")


async def test_backup_never_blocks_watchdog(env):
    calls = []
    orig = backup_mod.backup_status

    async def _count():
        calls.append(1)
        return await orig()

    backup_mod.backup_status = _count
    try:
        await asyncio.gather(
            backup_mod.run_backup(force=True), job_health.warn_if_backup_stale())
    finally:
        backup_mod.backup_status = orig
    assert calls == [1]  # the watchdog really ran, it was not skipped


async def test_watchdog_does_not_touch_the_backup_flag(env):
    seen = []
    orig = backup_mod.backup_status

    async def _probe():
        seen.append(backup_mod._BackupGuard._busy)
        return await orig()

    backup_mod.backup_status = _probe
    try:
        await job_health.warn_if_backup_stale()
    finally:
        backup_mod.backup_status = orig
    assert seen == [False]


async def test_two_watchdogs_still_exclude_each_other(env):
    calls = []
    orig = backup_mod.backup_status

    async def _count():
        calls.append(1)
        return await orig()

    backup_mod.backup_status = _count
    try:
        await asyncio.gather(
            job_health.warn_if_backup_stale(), job_health.warn_if_backup_stale())
    finally:
        backup_mod.backup_status = orig
    assert len(calls) == 1


async def test_lost_backup_lock_is_visible_in_the_logs(env, monkeypatch, caplog):
    from backend._lock_backend import LockNotAvailable

    class _Busy:
        async def __aenter__(self):
            raise LockNotAvailable("hygie_backup_lock")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(backup_mod, "_backup_lock", lambda: _Busy())
    with caplog.at_level(logging.INFO, logger="backend.backup"):
        assert await backup_mod.run_backup(force=True) is None
    assert any(r.levelno >= logging.INFO and "lock" in r.getMessage() for r in caplog.records)
