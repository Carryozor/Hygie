"""Coverage-focused tests for backend/tools/migrate_to_mariadb.py (SQLite ->
MariaDB forward migration CLI tool). tests/test_migrate_to_mariadb.py already
covers read_sqlite_table/validate_sqlite_db in "dry-run only" mode; this file
adds the write path (_mariadb_columns, _write_mariadb_table), the full
migrate() pipeline (dry-run and real), and main() — all against a simulated
aiomysql connection, no live MariaDB required.
"""
import os

import aiosqlite
import pytest

os.environ.setdefault("DB_PATH", ":memory:")

from backend.tools.migrate_to_mariadb import (
    _mariadb_columns,
    _write_mariadb_table,
    migrate,
    main,
)


# ─── Fake aiomysql surface ──────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, conn, fetchall_result=None):
        self._conn = conn
        self._fetchall_result = fetchall_result or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self._conn.queries.append(("execute", sql, params))

    async def executemany(self, sql, params_seq):
        self._conn.queries.append(("executemany", sql, list(params_seq)))

    async def fetchall(self):
        return self._fetchall_result


class _FakeConn:
    def __init__(self, fetchall_result=None):
        self.queries: list[tuple] = []
        self.closed = False
        self.committed = 0
        self._fetchall_result = fetchall_result

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self, self._fetchall_result)

    async def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


@pytest.fixture
def fake_aiomysql(monkeypatch):
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DATABASE_URL", "mysql+aiomysql://u:p@dbhost:3306/hygie")

    def _install(conn):
        import aiomysql

        async def fake_connect(**kwargs):
            return conn

        monkeypatch.setattr(aiomysql, "connect", fake_connect)

    return _install


async def _bootstrap_sqlite(path, rows):
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for k, v in rows:
            await db.execute("INSERT INTO settings VALUES (?, ?)", (k, v))
        await db.commit()


# ─── _mariadb_columns ────────────────────────────────────────────────────────────

async def test_mariadb_columns_parses_show_columns_result(fake_aiomysql):
    conn = _FakeConn(fetchall_result=[("key",), ("value",)])
    fake_aiomysql(conn)
    cols = await _mariadb_columns(conn, "settings")
    assert cols == {"key", "value"}
    kind, sql, _params = conn.queries[0]
    assert kind == "execute"
    assert "SHOW COLUMNS FROM `settings`" in sql


# ─── _write_mariadb_table ────────────────────────────────────────────────────────

async def test_write_mariadb_table_drops_columns_not_in_target_schema(fake_aiomysql, caplog):
    import logging
    conn = _FakeConn(fetchall_result=[("key",), ("value",)])  # no "legacy_col" on target
    fake_aiomysql(conn)
    rows = [{"key": "a", "value": "1", "legacy_col": "drop-me"}]
    with caplog.at_level(logging.INFO, logger="hygie.migrate"):
        await _write_mariadb_table("url", "settings", rows)

    kinds = [q[0] for q in conn.queries]
    assert kinds == ["execute", "executemany"]  # SHOW COLUMNS, then the batch insert
    _kind, sql, values = conn.queries[1]
    assert "REPLACE INTO `settings`" in sql
    assert "`legacy_col`" not in sql
    assert values == [("a", "1")]
    assert conn.committed == 1
    assert conn.closed is True
    assert any("dropping legacy columns" in r.message for r in caplog.records)


async def test_write_mariadb_table_noop_on_empty_rows(fake_aiomysql):
    conn = _FakeConn()
    fake_aiomysql(conn)
    await _write_mariadb_table("url", "settings", [])
    assert conn.queries == []  # never even opened a connection... but if it did:
    assert conn.closed is False  # confirms the early-return short-circuits before connect


async def test_write_mariadb_table_batches_across_batch_size_boundary(fake_aiomysql, monkeypatch):
    import backend.tools.migrate_to_mariadb as mtm
    monkeypatch.setattr(mtm, "BATCH_SIZE", 2)
    conn = _FakeConn(fetchall_result=[("key",), ("value",)])
    fake_aiomysql(conn)
    rows = [{"key": f"k{i}", "value": str(i)} for i in range(5)]
    await _write_mariadb_table("url", "settings", rows)
    executemany_calls = [q for q in conn.queries if q[0] == "executemany"]
    assert len(executemany_calls) == 3  # ceil(5/2)
    assert conn.committed == 3


async def test_write_mariadb_table_closes_connection_even_when_write_fails(fake_aiomysql):
    class _RaisingConn(_FakeConn):
        def cursor(self, *args, **kwargs):
            cur = super().cursor(*args, **kwargs)
            real_executemany = cur.executemany

            async def boom(sql, params_seq):
                await real_executemany(sql, params_seq)
                raise RuntimeError("write failed")

            cur.executemany = boom
            return cur

    conn = _RaisingConn(fetchall_result=[("key",), ("value",)])
    fake_aiomysql(conn)
    with pytest.raises(RuntimeError, match="write failed"):
        await _write_mariadb_table("url", "settings", [{"key": "a", "value": "1"}])
    assert conn.closed is True


# ─── migrate() pipeline ─────────────────────────────────────────────────────────

async def test_migrate_dry_run_reads_but_writes_nothing(tmp_path, monkeypatch):
    sqlite_path = str(tmp_path / "source.db")
    await _bootstrap_sqlite(sqlite_path, [("k", "v")])

    calls = []

    async def fake_write(db_url, table, rows):
        calls.append(table)

    import backend.tools.migrate_to_mariadb as mtm
    monkeypatch.setattr(mtm, "_write_mariadb_table", fake_write)

    await migrate(sqlite_path, "mysql://irrelevant", dry_run=True)
    assert calls == []  # dry run must never write


@pytest.fixture
def restore_engine_module():
    """migrate() sets DATABASE_URL and importlib.reload()s backend.db.engine.
    Reloading again to "clean up" re-executes the module in the SAME namespace,
    silently swapping the DB that session fixtures (test_client) set up for
    every later test. Snapshot the module namespace and restore it verbatim."""
    import backend.db.engine as engine
    saved = dict(engine.__dict__)
    saved_env = os.environ.get("DATABASE_URL")
    yield
    engine.__dict__.clear()
    engine.__dict__.update(saved)
    if saved_env is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved_env


@pytest.fixture
def fake_aiomysql_create_pool(monkeypatch):
    """migrate()'s non-dry-run path does `importlib.reload(backend.db.engine)`
    AFTER setting DATABASE_URL, which re-executes the module and would wipe
    out a monkeypatch on engine.init_db_pool itself. Patching the underlying
    aiomysql.create_pool survives the reload (aiomysql is not reloaded) and
    lets the real init_db_pool() run without ever touching the network."""

    class _FakePool:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def fake_create_pool(**kwargs):
        return _FakePool()

    import aiomysql
    monkeypatch.setattr(aiomysql, "create_pool", fake_create_pool)


async def test_migrate_skips_tables_absent_from_source(
    tmp_path, monkeypatch, fake_aiomysql_create_pool, restore_engine_module
):
    sqlite_path = str(tmp_path / "source.db")
    await _bootstrap_sqlite(sqlite_path, [("k", "v")])  # only "settings" exists

    written = []

    async def fake_write(db_url, table, rows):
        written.append(table)

    import backend.tools.migrate_to_mariadb as mtm
    monkeypatch.setattr(mtm, "_write_mariadb_table", fake_write)

    import backend.db.schema as schema
    from unittest.mock import AsyncMock
    monkeypatch.setattr(schema, "_init_db_mariadb", AsyncMock())

    await migrate(sqlite_path, "mysql+aiomysql://u:p@h:3306/db", dry_run=False)

    assert written == ["settings"]  # every other ORDERED_TABLES entry skipped (not present)


async def test_migrate_real_run_initializes_schema_and_writes_present_tables(
    tmp_path, monkeypatch, fake_aiomysql_create_pool, restore_engine_module
):
    """Full non-dry-run pipeline: pool init + schema init + per-table write,
    with the actual MariaDB-touching driver call mocked out (no live server)."""
    sqlite_path = str(tmp_path / "source.db")
    await _bootstrap_sqlite(sqlite_path, [("k", "v")])

    from unittest.mock import AsyncMock
    import backend.db.schema as schema
    import backend.tools.migrate_to_mariadb as mtm

    init_schema = AsyncMock()
    written = []

    async def fake_write(db_url, table, rows):
        written.append((table, len(rows)))

    monkeypatch.setattr(schema, "_init_db_mariadb", init_schema)
    monkeypatch.setattr(mtm, "_write_mariadb_table", fake_write)

    await migrate(sqlite_path, "mysql+aiomysql://u:p@h:3306/db", dry_run=False)

    init_schema.assert_awaited_once()
    assert written == [("settings", 1)]


# ─── main() CLI entrypoint ──────────────────────────────────────────────────────

def test_main_parses_args_and_invokes_migrate(monkeypatch):
    import backend.tools.migrate_to_mariadb as mtm
    captured = {}

    async def fake_migrate(sqlite_path, db_url, dry_run=False):
        captured["args"] = (sqlite_path, db_url, dry_run)

    monkeypatch.setattr(mtm, "migrate", fake_migrate)
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_to_mariadb.py", "--sqlite-path", "/tmp/in.db",
         "--database-url", "mysql://x", "--dry-run"],
    )
    main()
    assert captured["args"] == ("/tmp/in.db", "mysql://x", True)
