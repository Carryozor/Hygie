"""Coverage gaps in backend/backup.py not hit by tests/test_backup_core.py
and tests/test_backup_before_migrations.py:

- _remove_quietly()'s two exception branches (file already gone vs. a real
  OS error removing it)
- _do_mariadb_backup()'s finally block failing to unlink its 0600 credentials
  temp file
- _mariadb_backup()'s actual gzip-success path (existing tests only cover
  compression *failing*)
- run_backup()'s generic-exception branch (distinct from the FileNotFoundError
  branch already covered)
- a failure while pruning one specific old backup file, which must not abort
  pruning the rest
"""
import gzip
import os
import subprocess
from unittest.mock import AsyncMock

os.environ.setdefault("DB_PATH", ":memory:")

import backend.backup as backup_mod


# ─── _remove_quietly() ──────────────────────────────────────────────────────

def test_remove_quietly_is_silent_when_file_already_gone(tmp_path):
    missing = str(tmp_path / "already-gone.db")
    assert not os.path.exists(missing)

    backup_mod._remove_quietly(missing)  # must not raise


def test_remove_quietly_logs_a_warning_on_os_error(tmp_path, caplog):
    """A real removal failure (permissions, directory instead of file, disk
    error) must be surfaced at warning level, not silently swallowed like a
    missing file."""
    a_directory = tmp_path / "not_a_file"
    a_directory.mkdir()

    with caplog.at_level("WARNING"):
        backup_mod._remove_quietly(str(a_directory))  # IsADirectoryError, an OSError subclass

    assert os.path.exists(a_directory)  # never removed
    assert any("Could not remove partial backup" in r.message for r in caplog.records)


# ─── _do_mariadb_backup() — temp credentials file cleanup ──────────────────

def test_do_mariadb_backup_succeeds_even_if_temp_cnf_unlink_fails(monkeypatch, tmp_path):
    """The --defaults-extra-file temp cleanup is best-effort: if os.unlink()
    on it fails, the backup must still complete (the dump itself already
    succeeded) instead of raising from an unrelated cleanup step."""
    def _fake_run(cmd, stdout, stderr, timeout):
        stdout.write(b"-- dump --")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(subprocess, "run", _fake_run)

    real_unlink = os.unlink
    calls = []

    def _flaky_unlink(path, *a, **kw):
        calls.append(path)
        if path.endswith(".cnf"):
            raise OSError("temp fs read-only")
        return real_unlink(path, *a, **kw)
    monkeypatch.setattr(os, "unlink", _flaky_unlink)

    dst = str(tmp_path / "out.sql")
    backup_mod._do_mariadb_backup("dbhost", 3306, "hygie", "s3cr3t", "hygie", dst)  # must not raise

    assert os.path.exists(dst)
    assert any(p.endswith(".cnf") for p in calls)


# ─── _mariadb_backup() — successful compression (happy path) ───────────────

async def test_mariadb_backup_compresses_and_removes_the_plain_dump(tmp_path, monkeypatch):
    """When gzip compression succeeds: the .sql.gz must contain the dump, and
    the intermediate plain .sql file must be removed — leaving exactly one
    backup artifact, not two (which would double what retention counts)."""
    import backend.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "DATABASE_URL", "mysql+aiomysql://user:pass@host:3306/hygie")

    dump_content = b"-- MariaDB dump\nINSERT INTO t VALUES (1);\n-- Dump completed\n"

    def _fake_dump(host, port, user, password, db, dst_path):
        with open(dst_path, "wb") as f:
            f.write(dump_content)
    monkeypatch.setattr(backup_mod, "_do_mariadb_backup", _fake_dump)

    filename = await backup_mod._mariadb_backup(str(tmp_path), "20260101_000000")

    assert filename == "hygie_20260101_000000.sql.gz"
    gz_path = tmp_path / filename
    assert gz_path.exists()
    with gzip.open(gz_path, "rb") as f:
        assert f.read() == dump_content
    assert not (tmp_path / "hygie_20260101_000000.sql").exists(), \
        "the intermediate plain dump must be removed after successful compression"


# ─── run_backup() — generic exception branch ────────────────────────────────

async def test_run_backup_logs_and_returns_none_on_unexpected_backup_error(tmp_path, monkeypatch):
    """A non-FileNotFoundError exception from the backup implementation
    (disk full, permission denied, etc.) must be caught, logged, and reported
    as a failed backup — not propagate and crash the scheduler/caller."""
    backup_dir = str(tmp_path / "backups")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", str(tmp_path / "hygie.db"))
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")

    async def _settings(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _bool_settings(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _int_settings(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 5}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bool_settings)
    monkeypatch.setattr(backup_mod, "get_int_setting", _int_settings)

    logs = []
    monkeypatch.setattr(backup_mod, "add_log",
                         AsyncMock(side_effect=lambda lvl, msg, cat: logs.append((lvl, msg))))
    monkeypatch.setattr(backup_mod, "_sqlite_backup",
                         AsyncMock(side_effect=OSError("disk full")))

    result = await backup_mod.run_backup(force=True)

    assert result is None
    assert logs and logs[0][0] == "ERROR"


# ─── run_backup() — one old backup fails to prune ───────────────────────────

async def test_run_backup_keeps_pruning_after_one_deletion_fails(tmp_path, monkeypatch):
    """If one old backup can't be deleted (e.g. locked/permission), the
    others beyond retention must still be pruned instead of aborting the
    whole prune loop on the first failure."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", str(tmp_path / "hygie.db"))
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")

    import sqlite3
    src = sqlite3.connect(str(tmp_path / "hygie.db"))
    src.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    src.commit()
    src.close()

    async def _settings(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _bool_settings(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _int_settings(key, default=0):
        # retention=1: both pre-seeded old backups are prune candidates.
        return {"backup_interval_hours": 24, "backup_retention_count": 1}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bool_settings)
    monkeypatch.setattr(backup_mod, "get_int_setting", _int_settings)
    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    # Old backup #1 (oldest, pruned first): a directory (matches the glob,
    # but Path.unlink() on a directory raises IsADirectoryError) — simulates
    # an unremovable entry that must not abort the rest of the loop.
    unremovable = os.path.join(backup_dir, "hygie_20260101_000001.db")
    os.makedirs(unremovable)
    os.utime(unremovable, (1000, 1000))

    # Old backup #2 (pruned second): a real file that can be removed normally
    # — proves the loop keeps going after #1's failure instead of stopping.
    prunable = os.path.join(backup_dir, "hygie_20260101_000002.db")
    with open(prunable, "wb") as f:
        f.write(b"x")
    os.utime(prunable, (1001, 1001))

    new_filename = await backup_mod.run_backup(force=True)

    assert new_filename is not None
    assert not os.path.exists(prunable), "the removable old backup must still be pruned"
    assert os.path.isdir(unremovable), "the unremovable entry is left in place, not crashed on"
