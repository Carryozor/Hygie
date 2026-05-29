"""Tests for DbConn SQLite mode (MariaDB mode needs a real server — skipped here)."""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)  # force SQLite mode

@pytest_asyncio.fixture
async def db(tmp_path):
    import aiosqlite
    from backend.db.engine import get_db, DIALECT
    assert DIALECT == "sqlite"
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    await conn.commit()
    await conn.close()

    import backend.db.engine as eng
    orig = eng.SQLITE_PATH
    eng.SQLITE_PATH = str(tmp_path / "test.db")
    yield
    eng.SQLITE_PATH = orig


@pytest.mark.asyncio
async def test_execute_insert_and_fetch_one(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        last_id = await conn.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        await conn.commit()
        row = await conn.fetch_one("SELECT * FROM t WHERE id=?", (last_id,))
    assert row is not None
    assert row["val"] == "hello"


@pytest.mark.asyncio
async def test_fetch_all(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("a",))
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("b",))
        await conn.commit()
        rows = await conn.fetch_all("SELECT val FROM t ORDER BY id")
    assert [r["val"] for r in rows] == ["a", "b"]


@pytest.mark.asyncio
async def test_executemany(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.executemany("INSERT INTO t (val) VALUES (?)", [("x",), ("y",), ("z",)])
        await conn.commit()
        rows = await conn.fetch_all("SELECT val FROM t ORDER BY id")
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_rowcount_after_update(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        await conn.execute("INSERT INTO t (val) VALUES (?)", ("old",))
        await conn.commit()
        rowcount = await conn.execute_write("UPDATE t SET val=? WHERE val=?", ("new", "old"))
    assert rowcount == 1


@pytest.mark.asyncio
async def test_fetch_one_missing_returns_none(db):
    from backend.db.engine import get_db
    async with get_db() as conn:
        row = await conn.fetch_one("SELECT * FROM t WHERE id=?", (999,))
    assert row is None
