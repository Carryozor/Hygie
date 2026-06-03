"""Tests for DbConn SQLite mode (MariaDB mode needs a real server — skipped here)."""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)  # force SQLite mode

@pytest_asyncio.fixture
async def db(tmp_path):
    import aiosqlite
    from backend.db.engine import DIALECT
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


# ─── _parse_mariadb_url robustness ────────────────────────────────────────────

def test_parse_mariadb_url_standard():
    from backend.db.engine import _parse_mariadb_url
    r = _parse_mariadb_url("mysql+aiomysql://user:pass@localhost:3306/hygie")
    assert r == {"host": "localhost", "port": 3306, "user": "user", "password": "pass", "db": "hygie"}


def test_parse_mariadb_url_special_chars_in_password():
    from backend.db.engine import _parse_mariadb_url
    r = _parse_mariadb_url("mysql+aiomysql://user:p%40ss%21@db.example.com:3307/mydb")
    assert r["password"] == "p@ss!"
    assert r["port"] == 3307
    assert r["db"] == "mydb"


def test_parse_mariadb_url_default_port():
    from backend.db.engine import _parse_mariadb_url
    r = _parse_mariadb_url("mariadb+aiomysql://root:secret@db/hygie")
    assert r["port"] == 3306
    assert r["host"] == "db"


def test_parse_mariadb_url_mariadb_scheme():
    from backend.db.engine import _parse_mariadb_url
    r = _parse_mariadb_url("mariadb://user:pass@host:3306/dbname")
    assert r["host"] == "host"
    assert r["db"] == "dbname"
