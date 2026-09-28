"""A brand-new SQLite install (empty data dir) must start.

Regression since v4.2.1: init_db_pool() creates the SQLite file (WAL PRAGMA)
before backup_before_migrations() runs, whose "does the DB already exist?"
check only looked at the file — so a fresh install was treated as an existing
one and read settings.backup_enabled from a table that did not exist yet:
`sqlite3.OperationalError: no such table: settings`, crashing the zero-config
quick start.
"""
import backend.db.engine as engine


async def test_fresh_sqlite_install_starts(tmp_path, monkeypatch):
    db_path = str(tmp_path / "hygie.db")
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", db_path)
    import backend.db.utils as db_utils
    monkeypatch.setattr(db_utils, "DB_PATH", db_path)
    import backend.db.settings_store as ss
    ss._settings_cache.clear()
    ss._settings_cache_ts = 0.0

    import backend.main as main_mod
    from unittest.mock import MagicMock
    monkeypatch.setattr(main_mod, "scheduler", MagicMock())

    from backend.main import _init_database_and_migrate
    error = await _init_database_and_migrate()

    assert error == ""
    async with engine.get_db() as db:
        assert await db.table_exists("settings")
        assert await db.table_exists("schema_migrations")


async def test_existing_sqlite_db_is_detected_by_schema_not_file(tmp_path, monkeypatch):
    db_path = str(tmp_path / "hygie.db")
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", db_path)
    import backend.backup as backup
    monkeypatch.setattr(backup, "SQLITE_PATH", db_path, raising=False)

    # File exists (as init_db_pool leaves it) but no schema yet → fresh.
    await engine.init_db_pool()
    assert await backup._db_already_exists() is False
