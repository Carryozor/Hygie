"""Unit tests for db/migrations.py's run_migrations() — the append-only runner
itself, not any individual migration. Previously untested (existing
test_schema_migration.py / test_migrate_to_mariadb.py exercise init_db() and
the separate SQLite<->MariaDB data-migration CLI tool, not this module).

The property under test that matters most: a migration that raises must NOT
be marked applied, so it retries on next boot instead of silently leaving the
DB half-migrated. The v4.1.1 regression (seerr_user_rules.name missing on
MariaDB) was exactly this class of bug — see CLAUDE.md piège 2.
"""
import pytest

import backend.db.migrations as migrations_mod
from backend.db.migrations import (
    run_migrations,
    _ensure_migrations_table,
    _is_applied,
    _mark_applied,
    _MIGRATIONS,
)


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    """Own temp DB per test — mirrors tests/test_database.py's fresh_db fixture."""
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


async def test_run_migrations_applies_all_registered_migrations():
    applied = await run_migrations()
    assert applied == len(_MIGRATIONS)
    for migration_id, _description, _fn in _MIGRATIONS:
        assert await _is_applied(migration_id)


async def test_run_migrations_is_idempotent_on_second_call():
    first = await run_migrations()
    assert first == len(_MIGRATIONS)

    second = await run_migrations()
    assert second == 0  # nothing left to apply


async def test_run_migrations_marks_individual_migration_applied():
    # Isolated from the registry — verifies the tracking primitives directly.
    await _ensure_migrations_table()
    assert await _is_applied("m999_does_not_exist") is False
    await _mark_applied("m999_does_not_exist", "test-only marker")
    assert await _is_applied("m999_does_not_exist") is True


async def test_failed_migration_is_not_marked_applied_and_propagates(monkeypatch):
    """The exact safety property behind CLAUDE.md piège 2: a migration that
    raises must retry on the next boot, never get silently marked done."""

    async def _boom():
        raise RuntimeError("simulated migration failure")

    fake_migrations = [
        ("m001", "Establish migration tracking baseline", migrations_mod._m001_no_op),
        ("m_test_boom", "Deliberately broken migration for the test", _boom),
    ]
    monkeypatch.setattr(migrations_mod, "_MIGRATIONS", fake_migrations)

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        await run_migrations()

    assert await _is_applied("m001") is True          # ran fine before the failure
    assert await _is_applied("m_test_boom") is False   # must NOT be marked applied


async def test_run_migrations_retries_previously_failed_migration(monkeypatch):
    """After a transient failure is fixed, the next run_migrations() call must
    pick the migration back up instead of skipping it forever."""
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")

    fake_migrations = [("m_flaky", "Fails once then succeeds", _flaky)]
    monkeypatch.setattr(migrations_mod, "_MIGRATIONS", fake_migrations)

    with pytest.raises(RuntimeError):
        await run_migrations()
    assert await _is_applied("m_flaky") is False

    applied = await run_migrations()  # retry
    assert applied == 1
    assert await _is_applied("m_flaky") is True
    assert calls["n"] == 2
