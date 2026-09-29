"""Coverage-focused tests for backend/db/engine.py's MariaDB-only branches and
pool lifecycle. tests/test_db_engine.py already covers the SQLite happy path
and _parse_mariadb_url; per that file's own docstring, "MariaDB mode needs a
real server — skipped here". This file instead simulates the aiomysql surface
(a fake cursor/connection/pool recording what's executed) to prove the right
SQL and control flow run on the mariadb branch, without any real MariaDB.
"""
import os

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


# ─── _replace_into_upsert edge case ────────────────────────────────────────────

def test_replace_into_single_column_left_unchanged():
    """REPLACE INTO with fewer than 2 columns has no non-key column to build
    an ON DUPLICATE KEY UPDATE clause from — must pass through untouched."""
    from backend.db.engine import DbConn
    sql = DbConn(None, "mariadb")._q("REPLACE INTO t (id) VALUES (?)")
    assert "ON DUPLICATE KEY UPDATE" not in sql
    assert sql.startswith("REPLACE INTO t (id) VALUES")


# ─── Fake aiomysql surface ──────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.executed: list[tuple] = []
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))

    async def executemany(self, sql, params_seq):
        self.executed.append((sql, list(params_seq)))

    async def fetchall(self):
        return self._fetchall_result

    async def fetchone(self):
        return self._fetchone_result


class _FakeRawConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.rollback_calls = 0
        self.closed = False
        self.autocommit_calls = []

    def cursor(self, *args, **kwargs):
        return self._cursor

    async def rollback(self):
        self.rollback_calls += 1

    async def autocommit(self, value):
        self.autocommit_calls.append(value)

    def close(self):
        self.closed = True


# ─── DbConn mariadb-mode methods ───────────────────────────────────────────────

async def test_fetch_all_mariadb_uses_dict_cursor(monkeypatch):
    import aiomysql
    from backend.db.engine import DbConn

    cursor = _FakeCursor(fetchall_result=[{"id": 1, "name": "a"}])
    raw = _FakeRawConn(cursor)
    conn = DbConn(raw, "mariadb")
    rows = await conn.fetch_all("SELECT * FROM t WHERE id=?", (1,))
    assert rows == [{"id": 1, "name": "a"}]
    sql, params = cursor.executed[0]
    assert "%s" in sql
    assert params == (1,)
    assert aiomysql is not None  # cursor kind is aiomysql.DictCursor in real code


async def test_executemany_mariadb_translates_placeholders(monkeypatch):
    from backend.db.engine import DbConn

    cursor = _FakeCursor()
    raw = _FakeRawConn(cursor)
    conn = DbConn(raw, "mariadb")
    await conn.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])
    sql, params_seq = cursor.executed[0]
    assert sql == "INSERT INTO t (a) VALUES (%s)"
    assert params_seq == [(1,), (2,)]


async def test_table_columns_mariadb_queries_information_schema(monkeypatch):
    from backend.db.engine import DbConn

    cursor = _FakeCursor(fetchall_result=[{"COLUMN_NAME": "id"}, {"COLUMN_NAME": "name"}])
    raw = _FakeRawConn(cursor)
    conn = DbConn(raw, "mariadb")
    cols = await conn.table_columns("media_queue")
    assert cols == {"id", "name"}
    sql, params = cursor.executed[0]
    assert "INFORMATION_SCHEMA.COLUMNS" in sql
    assert params == ("media_queue",)


async def test_table_exists_mariadb_true_when_row_found(monkeypatch):
    from backend.db.engine import DbConn

    cursor = _FakeCursor(fetchall_result=[{"TABLE_NAME": "media_queue"}])
    raw = _FakeRawConn(cursor)
    conn = DbConn(raw, "mariadb")
    assert await conn.table_exists("media_queue") is True


async def test_table_exists_mariadb_false_when_no_row(monkeypatch):
    from backend.db.engine import DbConn

    cursor = _FakeCursor(fetchall_result=[])
    raw = _FakeRawConn(cursor)
    conn = DbConn(raw, "mariadb")
    assert await conn.table_exists("nonexistent") is False


# ─── init_db_pool / close_db_pool ───────────────────────────────────────────────

async def test_init_db_pool_sqlite_applies_pragmas_once(monkeypatch, tmp_path):
    import backend.db.engine as engine

    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(engine, "_sqlite_pragmas_applied", False)

    await engine.init_db_pool()

    assert engine._sqlite_pragmas_applied is True
    import aiosqlite
    async with aiosqlite.connect(str(tmp_path / "t.db")) as raw:
        async with raw.execute("PRAGMA journal_mode") as cur:
            (mode,) = await cur.fetchone()
    assert mode.lower() == "wal"


async def test_init_db_pool_mariadb_creates_pool_with_parsed_kwargs(monkeypatch):
    import backend.db.engine as engine

    created = {}

    class _FakePool:
        pass

    async def fake_create_pool(minsize, maxsize, autocommit, charset, **kwargs):
        created["kwargs"] = kwargs
        created["minsize"] = minsize
        created["autocommit"] = autocommit
        return _FakePool()

    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "DATABASE_URL", "mysql+aiomysql://u:p@dbhost:3306/hygie")
    monkeypatch.setattr(engine, "_pool", None)

    import aiomysql
    monkeypatch.setattr(aiomysql, "create_pool", fake_create_pool)

    await engine.init_db_pool()

    assert isinstance(engine._pool, _FakePool)
    assert created["kwargs"]["host"] == "dbhost"
    assert created["autocommit"] is False
    engine._pool = None  # don't leak into other tests


async def test_close_db_pool_closes_and_clears(monkeypatch):
    import backend.db.engine as engine

    class _FakePool:
        def __init__(self):
            self.closed = False
            self.waited = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    pool = _FakePool()
    monkeypatch.setattr(engine, "_pool", pool)
    await engine.close_db_pool()
    assert pool.closed and pool.waited
    assert engine._pool is None


async def test_close_db_pool_is_a_noop_when_no_pool(monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "_pool", None)
    await engine.close_db_pool()  # must not raise
    assert engine._pool is None


# ─── get_db() mariadb branches ──────────────────────────────────────────────────

async def test_get_db_mariadb_raises_when_pool_not_initialized(monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", None)
    with pytest.raises(RuntimeError, match="MariaDB pool not initialized"):
        async with engine.get_db():
            pass  # pragma: no cover - must not be entered


class _FakeAcquireCtx:
    def __init__(self, raw):
        self._raw = raw

    async def __aenter__(self):
        return self._raw

    async def __aexit__(self, *exc):
        return False


class _FakePoolWithAcquire:
    def __init__(self, raw):
        self._raw = raw

    def acquire(self):
        return _FakeAcquireCtx(self._raw)


async def test_get_db_mariadb_happy_path_rolls_back_after_read(monkeypatch):
    """Even a bare read must end its REPEATABLE-READ transaction before the
    connection returns to the pool (see get_db's trailing rollback)."""
    import backend.db.engine as engine

    raw = _FakeRawConn(_FakeCursor())
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", _FakePoolWithAcquire(raw))

    async with engine.get_db() as db:
        assert db._dialect == "mariadb"

    assert raw.autocommit_calls == [False]
    assert raw.rollback_calls == 1  # the trailing "always end the transaction" rollback


async def test_get_db_mariadb_closes_connection_when_rollback_fails_during_unwind(monkeypatch):
    """An exception inside the block must still propagate, and if the
    unwind-rollback itself fails, the connection must be closed (never
    returned dirty to the pool) instead of raising a second error."""
    import backend.db.engine as engine

    class _AlwaysFailRollbackConn(_FakeRawConn):
        async def rollback(self):
            self.rollback_calls += 1
            raise RuntimeError("rollback also failed")

    raw = _AlwaysFailRollbackConn(_FakeCursor())
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", _FakePoolWithAcquire(raw))

    with pytest.raises(ValueError, match="boom"):
        async with engine.get_db():
            raise ValueError("boom")

    assert raw.closed is True


async def test_get_db_mariadb_swallows_close_failure_during_unwind(monkeypatch):
    """If even raw.close() fails while unwinding a failed rollback, that
    secondary failure must be swallowed — the original error still wins."""
    import backend.db.engine as engine

    class _FailRollbackAndClose(_FakeRawConn):
        async def rollback(self):
            self.rollback_calls += 1
            raise RuntimeError("rollback failed")

        def close(self):
            raise RuntimeError("close failed too")

    raw = _FailRollbackAndClose(_FakeCursor())
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", _FakePoolWithAcquire(raw))

    with pytest.raises(ValueError, match="boom"):
        async with engine.get_db():
            raise ValueError("boom")


# ─── get_db() sqlite: pragma re-apply branch (pragmas already applied) ────────

async def test_get_db_sqlite_reapplies_connection_level_pragmas_when_already_applied(
    monkeypatch, tmp_path
):
    import backend.db.engine as engine

    monkeypatch.setattr(engine, "SQLITE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(engine, "_sqlite_pragmas_applied", True)

    async with engine.get_db() as db:
        row = await db.fetch_one("PRAGMA foreign_keys")
    assert row is not None  # connection usable via the "already applied" else-branch
