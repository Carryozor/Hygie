"""Tests for the SQLite→MariaDB migration script (dry-run mode only — no live MariaDB)."""
import asyncio
import pytest
import aiosqlite
from backend.tools.migrate_to_mariadb import read_sqlite_table, validate_sqlite_db


@pytest.fixture
def sqlite_db(tmp_path):
    db_path = str(tmp_path / "source.db")
    asyncio.get_event_loop().run_until_complete(_bootstrap_sqlite(db_path))
    return db_path


async def _bootstrap_sqlite(path: str):
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await db.execute("INSERT INTO settings VALUES ('test_key', 'test_val')")
        await db.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password_hash TEXT, created_at TEXT)"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_read_sqlite_table(sqlite_db):
    rows = await read_sqlite_table(sqlite_db, "settings")
    assert len(rows) == 1
    assert rows[0]["key"] == "test_key"
    assert rows[0]["value"] == "test_val"


@pytest.mark.asyncio
async def test_validate_sqlite_db_ok(sqlite_db):
    tables = await validate_sqlite_db(sqlite_db)
    assert "settings" in tables
    assert "users" in tables


@pytest.mark.asyncio
async def test_validate_sqlite_db_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        await validate_sqlite_db(str(tmp_path / "nonexistent.db"))
