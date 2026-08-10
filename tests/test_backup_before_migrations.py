"""Tests for the pre-migration safety backup (backend/backup.py).

Context: CLAUDE.md documents a real incident (v4.1.1 regression — an
incomplete MariaDB migration) with no recovery path other than a manual
restore. backup.py already has a full backup implementation (run_backup),
but it was only ever wired as a periodic scheduler job / manual API trigger
— never invoked right before run_migrations() at startup. These tests cover
the new _db_already_exists() / backup_before_migrations() wiring, not the
pre-existing run_backup() internals (SQLite copy, mysqldump, rotation),
which remain untested elsewhere — out of scope here.
"""
import os

import pytest

os.environ.setdefault("DB_PATH", ":memory:")

import backend.backup as backup_mod


# ─── _db_already_exists() — SQLite ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sqlite_db_already_exists_true_when_file_present(monkeypatch, tmp_path):
    db_path = tmp_path / "hygie.db"
    db_path.write_bytes(b"")  # a fresh SQLite file created by a prior boot
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", str(db_path))

    assert await backup_mod._db_already_exists() is True


@pytest.mark.asyncio
async def test_sqlite_db_already_exists_false_when_file_absent(monkeypatch, tmp_path):
    db_path = tmp_path / "does_not_exist.db"
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", str(db_path))

    assert await backup_mod._db_already_exists() is False


@pytest.mark.asyncio
async def test_sqlite_db_already_exists_false_for_in_memory(monkeypatch):
    monkeypatch.setattr(backup_mod, "DIALECT", "sqlite")
    monkeypatch.setattr(backup_mod, "SQLITE_PATH", ":memory:")

    assert await backup_mod._db_already_exists() is False


# ─── _db_already_exists() — MariaDB ────────────────────────────────────────────

class _FakeDb:
    def __init__(self, exists: bool):
        self._exists = exists

    async def table_exists(self, table: str) -> bool:
        assert table == "schema_migrations"
        return self._exists


class _FakeGetDb:
    """Async context manager mimicking `async with get_db() as db:`."""
    def __init__(self, exists: bool):
        self._exists = exists

    async def __aenter__(self):
        return _FakeDb(self._exists)

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_mariadb_db_already_exists_true_when_schema_migrations_present(monkeypatch):
    monkeypatch.setattr(backup_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(backup_mod, "get_db", lambda: _FakeGetDb(True))

    assert await backup_mod._db_already_exists() is True


@pytest.mark.asyncio
async def test_mariadb_db_already_exists_false_when_schema_migrations_missing(monkeypatch):
    """A freshly created MariaDB database (empty schema, no tables yet) must
    not trigger a backup — nothing meaningful to snapshot."""
    monkeypatch.setattr(backup_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(backup_mod, "get_db", lambda: _FakeGetDb(False))

    assert await backup_mod._db_already_exists() is False


@pytest.mark.asyncio
async def test_mariadb_db_already_exists_fails_open_on_error(monkeypatch):
    """If the existence check itself errors (pool hiccup, etc.), never let
    that block startup — treat it as fresh/skip, matching run_backup()'s own
    fail-open policy elsewhere in this module."""
    def _broken_get_db():
        raise RuntimeError("pool not ready")

    monkeypatch.setattr(backup_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(backup_mod, "get_db", _broken_get_db)

    assert await backup_mod._db_already_exists() is False


# ─── backup_before_migrations() ────────────────────────────────────────────────

async def _true():
    return True


async def _false():
    return False


@pytest.mark.asyncio
async def test_backup_before_migrations_runs_backup_when_db_preexists(monkeypatch):
    called = {}

    async def _fake_run_backup(force=False):
        called["force"] = force
        return "hygie_20260810_100000.db"

    monkeypatch.setattr(backup_mod, "_db_already_exists", _true)
    monkeypatch.setattr(backup_mod, "get_bool_setting", lambda key: _true())
    monkeypatch.setattr(backup_mod, "run_backup", _fake_run_backup)

    await backup_mod.backup_before_migrations()

    assert called == {"force": True}


@pytest.mark.asyncio
async def test_backup_before_migrations_skips_on_fresh_install(monkeypatch):
    called = {}

    async def _fake_run_backup(force=False):
        called["ran"] = True
        return "should-not-run"

    monkeypatch.setattr(backup_mod, "_db_already_exists", _false)
    monkeypatch.setattr(backup_mod, "get_bool_setting", lambda key: _true())
    monkeypatch.setattr(backup_mod, "run_backup", _fake_run_backup)

    await backup_mod.backup_before_migrations()

    assert called == {}


@pytest.mark.asyncio
async def test_backup_before_migrations_skips_when_backups_disabled(monkeypatch):
    """An admin who explicitly turned backups off must not see one appear on
    every restart just because a migration is about to run — force=True
    bypasses the periodic *interval* throttle only, not this opt-out."""
    called = {}

    async def _fake_run_backup(force=False):
        called["ran"] = True
        return "should-not-run"

    monkeypatch.setattr(backup_mod, "_db_already_exists", _true)
    monkeypatch.setattr(backup_mod, "get_bool_setting", lambda key: _false())
    monkeypatch.setattr(backup_mod, "run_backup", _fake_run_backup)

    await backup_mod.backup_before_migrations()

    assert called == {}


@pytest.mark.asyncio
async def test_backup_before_migrations_never_raises_on_backup_failure(monkeypatch):
    """run_backup() already swallows its own errors (returns None), but this
    wiring must stay fail-open even if that contract is ever violated —
    startup must never crash because a safety backup failed."""
    async def _raising_run_backup(force=False):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup_mod, "_db_already_exists", _true)
    monkeypatch.setattr(backup_mod, "get_bool_setting", lambda key: _true())
    monkeypatch.setattr(backup_mod, "run_backup", _raising_run_backup)

    await backup_mod.backup_before_migrations()  # must not raise
