"""Direct tests for backend/backup.py's core functions — run_backup(),
list_backups(), _do_sqlite_backup(), _do_mariadb_backup(), _validate_backup_path().

Gap found during the 2026-08-10 audit: backup.py had a full implementation
(SQLite online copy, MariaDB mysqldump+gzip, rotation, path validation) but
zero direct tests — only the pre-migration wiring (_db_already_exists,
backup_before_migrations, in tests/test_backup_before_migrations.py) was
covered. These tests exercise the implementation itself.
"""
import os
import sqlite3
import subprocess
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend.backup as backup_mod


# ─── _validate_backup_path() ───────────────────────────────────────────────────

def test_validate_backup_path_rejects_dotdot():
    with pytest.raises(ValueError, match=r"\.\."):
        backup_mod._validate_backup_path("/app/data/../etc/backups")


@pytest.mark.parametrize("forbidden", ["/etc", "/root", "/proc", "/sys", "/dev", "/run", "/boot", "/bin", "/sbin", "/usr/bin", "/usr/sbin"])
def test_validate_backup_path_rejects_system_dirs(forbidden):
    with pytest.raises(ValueError, match="system directory"):
        backup_mod._validate_backup_path(forbidden)
    with pytest.raises(ValueError, match="system directory"):
        backup_mod._validate_backup_path(f"{forbidden}/subdir")


def test_validate_backup_path_accepts_normal_path():
    backup_mod._validate_backup_path("/app/data/backups")  # must not raise


def test_validate_backup_path_does_not_false_positive_on_prefix_overlap():
    """A path that merely starts with a forbidden prefix's characters (not a
    real subdirectory) must not be rejected — e.g. /etcetera is not /etc."""
    backup_mod._validate_backup_path("/etcetera/backups")  # must not raise


# ─── _do_sqlite_backup() ────────────────────────────────────────────────────────

def test_do_sqlite_backup_creates_a_consistent_copy(tmp_path):
    src_path = str(tmp_path / "src.db")
    dst_path = str(tmp_path / "dst.db")

    src = sqlite3.connect(src_path)
    src.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    src.execute("INSERT INTO t (name) VALUES ('hello')")
    src.commit()
    src.close()

    backup_mod._do_sqlite_backup(src_path, dst_path)

    dst = sqlite3.connect(dst_path)
    rows = dst.execute("SELECT name FROM t").fetchall()
    dst.close()
    assert rows == [("hello",)]


# ─── run_backup() — SQLite ──────────────────────────────────────────────────────

@pytest.fixture
def sqlite_backup_env(tmp_path, monkeypatch):
    """A real SQLite source DB + an isolated backup_dir, with backup.py's
    settings resolved via monkeypatch (no DB round-trip needed)."""
    src_path = str(tmp_path / "hygie.db")
    conn = sqlite3.connect(src_path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

    backup_dir = str(tmp_path / "backups")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", src_path)
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")

    async def _fake_get_setting(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _fake_get_bool_setting(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _fake_get_int_setting(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 5}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _fake_get_setting)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _fake_get_bool_setting)
    monkeypatch.setattr(backup_mod, "get_int_setting", _fake_get_int_setting)
    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    return backup_dir


async def test_run_backup_sqlite_creates_a_file(sqlite_backup_env):
    backup_dir = sqlite_backup_env
    filename = await backup_mod.run_backup()

    assert filename is not None
    assert filename.startswith("hygie_") and filename.endswith(".db")
    assert os.path.exists(os.path.join(backup_dir, filename))


async def test_run_backup_returns_none_for_in_memory_db(monkeypatch):
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", ":memory:")
    assert await backup_mod.run_backup() is None


async def test_run_backup_skips_when_backup_disabled_and_not_forced(sqlite_backup_env, monkeypatch):
    async def _disabled(key, default=False):
        return {"backup_enabled": False}.get(key, default)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _disabled)

    assert await backup_mod.run_backup(force=False) is None
    assert backup_mod.list_backups(sqlite_backup_env) == []


async def test_run_backup_force_bypasses_disabled_setting(sqlite_backup_env, monkeypatch):
    async def _disabled(key, default=False):
        return {"backup_enabled": False}.get(key, default)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _disabled)

    filename = await backup_mod.run_backup(force=True)
    assert filename is not None


async def test_run_backup_rejects_forbidden_path(sqlite_backup_env, monkeypatch):
    async def _forbidden(key, default=None):
        return {"backup_path": "/etc"}.get(key, default)
    monkeypatch.setattr(backup_mod, "get_setting", _forbidden)
    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    assert await backup_mod.run_backup(force=True) is None


async def test_run_backup_prunes_beyond_retention(sqlite_backup_env, monkeypatch):
    """Pre-seed 3 older backups (distinct mtimes) with retention=2, then run
    one more backup — pruning must drop the two oldest, keeping the newest 2
    plus the one just created."""
    async def _low_retention(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 2}.get(key, default)
    monkeypatch.setattr(backup_mod, "get_int_setting", _low_retention)

    os.makedirs(sqlite_backup_env, exist_ok=True)
    old_names = ["hygie_20260101_000001.db", "hygie_20260101_000002.db", "hygie_20260101_000003.db"]
    for i, name in enumerate(old_names):
        path = os.path.join(sqlite_backup_env, name)
        with open(path, "wb") as f:
            f.write(b"x")
        os.utime(path, (1000 + i, 1000 + i))

    new_filename = await backup_mod.run_backup(force=True)

    remaining_names = {b["filename"] for b in backup_mod.list_backups(sqlite_backup_env)}
    assert len(remaining_names) == 2
    assert new_filename in remaining_names
    assert old_names[0] not in remaining_names
    assert old_names[1] not in remaining_names
    assert old_names[2] in remaining_names


# ─── run_backup() — MariaDB ─────────────────────────────────────────────────────

async def test_run_backup_mariadb_missing_mysqldump_fails_gracefully(tmp_path, monkeypatch):
    """Regression coverage for the exact prod gap found 2026-08-10: mysqldump
    absent from the image must not crash run_backup(), just fail cleanly."""
    backup_dir = str(tmp_path / "backups")
    monkeypatch.setattr(backup_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", "/app/data/hygie.db")

    async def _settings(key, default=None):
        return {"backup_path": backup_dir}.get(key, default)

    async def _bool_settings(key, default=False):
        return {"backup_enabled": True}.get(key, default)

    async def _int_settings(key, default=0):
        return {"backup_interval_hours": 24, "backup_retention_count": 5}.get(key, default)

    monkeypatch.setattr(backup_mod, "get_setting", _settings)
    monkeypatch.setattr(backup_mod, "get_bool_setting", _bool_settings)
    monkeypatch.setattr(backup_mod, "get_int_setting", _int_settings)

    monkeypatch.setattr(backup_mod, "add_log", AsyncMock())

    def _raise_not_found(*a, **kw):
        raise FileNotFoundError("mysqldump")
    monkeypatch.setattr(backup_mod, "_do_mariadb_backup", _raise_not_found)

    import backend.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "DATABASE_URL", "mysql+aiomysql://user:pass@host:3306/hygie")

    result = await backup_mod.run_backup(force=True)
    assert result is None


def test_do_mariadb_backup_writes_secrets_to_a_0600_temp_file(monkeypatch, tmp_path):
    """The mysqldump password must never appear on the command line (visible
    in ps aux / /proc) — it's written to a temp --defaults-extra-file instead."""
    captured_cmd = {}
    captured_perms = {}

    def _fake_run(cmd, stdout, stderr, timeout):
        captured_cmd["cmd"] = cmd
        cnf_path = next(a.split("=", 1)[1] for a in cmd if a.startswith("--defaults-extra-file="))
        captured_perms["mode"] = oct(os.stat(cnf_path).st_mode & 0o777)
        with open(cnf_path) as f:
            captured_cmd["cnf_contents"] = f.read()
        stdout.write(b"-- dump --")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    dst = str(tmp_path / "out.sql")

    backup_mod._do_mariadb_backup("dbhost", 3306, "hygie", "s3cr3t", "hygie", dst)

    assert "--host=dbhost" in captured_cmd["cmd"]
    assert "--user=hygie" in captured_cmd["cmd"]
    assert not any("s3cr3t" in part for part in captured_cmd["cmd"]), "password leaked onto the command line"
    assert "password=s3cr3t" in captured_cmd["cnf_contents"]
    assert captured_perms["mode"] == "0o600"


def test_do_mariadb_backup_raises_on_nonzero_exit(monkeypatch, tmp_path):
    def _fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"Access denied")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    dst = str(tmp_path / "out.sql")

    with pytest.raises(RuntimeError, match="mysqldump failed"):
        backup_mod._do_mariadb_backup("dbhost", 3306, "hygie", "wrongpass", "hygie", dst)


# ─── list_backups() ─────────────────────────────────────────────────────────────

def test_list_backups_empty_when_dir_missing(tmp_path):
    assert backup_mod.list_backups(str(tmp_path / "does-not-exist")) == []


def test_list_backups_sorted_newest_first(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    for i, name in enumerate(["hygie_1.db", "hygie_2.sql", "hygie_3.sql.gz"]):
        (d / name).write_text("x")
        os.utime(d / name, (1000 + i, 1000 + i))

    result = backup_mod.list_backups(str(d))
    assert [b["filename"] for b in result] == ["hygie_3.sql.gz", "hygie_2.sql", "hygie_1.db"]
    assert all("size_bytes" in b and "created_at" in b for b in result)


def test_list_backups_ignores_unrelated_files(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    (d / "hygie_1.db").write_text("x")
    (d / "not-a-backup.txt").write_text("x")
    (d / ".hidden").write_text("x")

    result = backup_mod.list_backups(str(d))
    assert [b["filename"] for b in result] == ["hygie_1.db"]
