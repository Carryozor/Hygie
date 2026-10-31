"""Regression tests for the 2026-10-31 backup race.

Two uvicorn workers fired run_backup() at the same second, shared the same
hygie_<ts>.sql scratch file, and the loser deleted the winner's .sql.gz while
both still logged "Backup créé" — the backup directory stayed empty.
"""
import asyncio
import gzip
import os
import time
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend.backup as backup_mod

FAKE_DUMP = b"-- fake mysqldump\n" + b"INSERT INTO t VALUES (1);\n" * 400


@pytest.fixture
def mariadb_env(tmp_path, monkeypatch):
    """MariaDB dialect with a slow fake mysqldump writing to an isolated dir."""
    backup_dir = str(tmp_path / "backups")
    monkeypatch.setattr(backup_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", "/app/data/hygie.db")

    async def _settings(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _bools(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _ints(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 5}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bools)
    monkeypatch.setattr(backup_mod, "get_int_setting", _ints)
    logs = []
    monkeypatch.setattr(
        backup_mod, "add_log",
        AsyncMock(side_effect=lambda lvl, msg, cat: logs.append((lvl, msg))),
    )

    def _slow_dump(host, port, user, password, db, dst_path):
        time.sleep(0.05)
        with open(dst_path, "wb") as f:
            f.write(FAKE_DUMP)

    monkeypatch.setattr(backup_mod, "_do_mariadb_backup", _slow_dump)
    import backend.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "DATABASE_URL", "mysql+aiomysql://u:p@h:3306/hygie")
    return backup_dir, logs


def _listing(backup_dir):
    return sorted(os.listdir(backup_dir))


async def test_two_concurrent_backups_leave_exactly_one_valid_file(mariadb_env):
    backup_dir, logs = mariadb_env

    results = await asyncio.gather(
        backup_mod.run_backup(force=True), backup_mod.run_backup(force=True)
    )

    files = _listing(backup_dir)
    assert len(files) == 1, f"expected exactly one file, got {files}"
    assert files[0].endswith(".sql.gz")
    with gzip.open(os.path.join(backup_dir, files[0]), "rb") as f:
        assert f.read() == FAKE_DUMP
    assert [r for r in results if r] == [files[0]]
    created = [m for lvl, m in logs if lvl == "INFO"]
    assert len(created) == 1 and files[0] in created[0]


async def test_concurrent_backups_without_lock_never_clobber_each_other(mariadb_env, monkeypatch):
    """Even if the inter-worker lock is bypassed, scratch/final names are
    unique, so both produce a valid dump and nothing is left half-written."""
    backup_dir, _ = mariadb_env
    monkeypatch.setattr(backup_mod, "_backup_lock", lambda: backup_mod._NullLock())

    results = await asyncio.gather(
        backup_mod.run_backup(force=True), backup_mod.run_backup(force=True)
    )

    files = _listing(backup_dir)
    assert len(files) == 2 and all(f.endswith(".sql.gz") for f in files), files
    assert sorted(results) == files
    for name in files:
        with gzip.open(os.path.join(backup_dir, name), "rb") as f:
            assert f.read() == FAKE_DUMP


async def test_worker_not_getting_the_lock_skips_silently(mariadb_env, monkeypatch):
    backup_dir, logs = mariadb_env
    from backend._lock_backend import LockNotAvailable

    class _Busy:
        async def __aenter__(self):
            raise LockNotAvailable("hygie_backup_lock")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(backup_mod, "_backup_lock", lambda: _Busy())

    assert await backup_mod.run_backup(force=True) is None
    assert logs == []
    assert not os.path.exists(backup_dir) or _listing(backup_dir) == []


async def test_empty_dump_is_not_reported_as_created(mariadb_env, monkeypatch):
    backup_dir, logs = mariadb_env
    monkeypatch.setattr(
        backup_mod, "_do_mariadb_backup",
        lambda *a: open(a[-1], "wb").close(),
    )

    assert await backup_mod.run_backup(force=True) is None
    assert [lvl for lvl, _ in logs] == ["ERROR"]
    assert _listing(backup_dir) == []


async def test_prune_never_deletes_the_new_backup(mariadb_env, monkeypatch):
    backup_dir, _ = mariadb_env
    os.makedirs(backup_dir)
    # Pre-seeded files with FUTURE mtimes: mtime-sorted pruning would drop the new one.
    for i in range(3):
        p = os.path.join(backup_dir, f"hygie_2026010{i}_000000.sql.gz")
        with gzip.open(p, "wb") as f:
            f.write(b"old")
        os.utime(p, (time.time() + 1000 + i, time.time() + 1000 + i))

    async def _ints(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 1}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_int_setting", _ints)

    name = await backup_mod.run_backup(force=True)

    assert name in _listing(backup_dir)


async def test_done_message_shows_real_filename(mariadb_env):
    _, logs = mariadb_env
    name = await backup_mod.run_backup(force=True)
    assert any(name in m for lvl, m in logs if lvl == "INFO")


@pytest.mark.parametrize("bad", [
    "//etc/cron.d", "/etc/cron.d", "/lib", "/lib/x", "/var/spool/cron",
    "/usr/lib/../../etc", "relative/backups", "", "/app/data/../../etc",
    "/proc/1", "/sys/kernel", "/dev/shm", "/boot", "/root/.ssh",
])
def test_validate_backup_path_rejects(bad):
    with pytest.raises(ValueError):
        backup_mod._validate_backup_path(bad)


def test_validate_backup_path_rejects_symlink_into_system_dir(tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to("/etc")
    with pytest.raises(ValueError):
        backup_mod._validate_backup_path(str(link / "sub"))


@pytest.mark.parametrize("good", ["/app/data/backups", "/backup/hygie", "/etcetera/backups"])
def test_validate_backup_path_accepts(good):
    backup_mod._validate_backup_path(good)


def test_validate_backup_path_accepts_tmp(tmp_path):
    backup_mod._validate_backup_path(str(tmp_path / "backups"))
