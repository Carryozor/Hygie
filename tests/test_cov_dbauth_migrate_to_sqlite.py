"""Coverage-focused tests for backend/tools/migrate_to_sqlite.py (MariaDB ->
SQLite reverse migration CLI tool). No real MariaDB is used: aiomysql.connect
is monkeypatched to a fake connection/cursor recording SQL and returning
canned rows, mirroring tests/test_migrate_to_mariadb.py's "dry-run only, no
live MariaDB" approach for the forward-direction tool.
"""
import os

import aiosqlite
import pytest

os.environ.setdefault("DB_PATH", ":memory:")

from backend.tools.migrate_to_sqlite import (
    _init_sqlite_schema,
    _write_sqlite_table,
    _read_mariadb_table,
    _mariadb_table_exists,
    migrate,
    main,
)


# ─── Fake aiomysql surface (mirrors test_cov_dbauth_rate_limit_backend.py) ─────

class _FakeCursor:
    def __init__(self, conn, fetchall_result=None, fetchone_result=None):
        self._conn = conn
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self._conn.queries.append((sql, params))

    async def fetchall(self):
        return self._fetchall_result

    async def fetchone(self):
        return self._fetchone_result


class _FakeConn:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.queries: list[tuple] = []
        self.closed = False
        self._fetchall_result = fetchall_result
        self._fetchone_result = fetchone_result

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self, self._fetchall_result, self._fetchone_result)

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


# ─── _init_sqlite_schema ────────────────────────────────────────────────────────

async def test_init_sqlite_schema_creates_all_tables_and_indexes(tmp_path):
    target = str(tmp_path / "out.db")
    await _init_sqlite_schema(target)
    async with aiosqlite.connect(target) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='media_queue'"
        ) as cur:
            assert await cur.fetchone() is not None
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_media_status'"
        ) as cur:
            assert await cur.fetchone() is not None


async def test_init_sqlite_schema_does_not_touch_live_db_engine_globals(tmp_path, monkeypatch):
    """Regression guard for the documented behavior: this must not reload
    engine/os.environ, so it's safe to call from inside a running process."""
    import backend.db.engine as engine
    before = engine.SQLITE_PATH
    await _init_sqlite_schema(str(tmp_path / "out.db"))
    assert engine.SQLITE_PATH == before


# ─── _write_sqlite_table ────────────────────────────────────────────────────────

async def test_write_sqlite_table_inserts_rows(tmp_path):
    target = str(tmp_path / "out.db")
    await _init_sqlite_schema(target)
    rows = [
        {"key": "a", "value": "1"},
        {"key": "b", "value": "2"},
    ]
    await _write_sqlite_table(target, "settings", rows)
    async with aiosqlite.connect(target) as db:
        async with db.execute("SELECT key, value FROM settings ORDER BY key") as cur:
            got = await cur.fetchall()
    assert got == [("a", "1"), ("b", "2")]


async def test_write_sqlite_table_skips_duplicates_via_insert_or_ignore(tmp_path):
    target = str(tmp_path / "out.db")
    await _init_sqlite_schema(target)
    await _write_sqlite_table(target, "settings", [{"key": "a", "value": "orig"}])
    # Same primary key, different value — INSERT OR IGNORE must keep "orig"
    await _write_sqlite_table(target, "settings", [{"key": "a", "value": "clobber"}])
    async with aiosqlite.connect(target) as db:
        async with db.execute("SELECT value FROM settings WHERE key='a'") as cur:
            (value,) = await cur.fetchone()
    assert value == "orig"


async def test_write_sqlite_table_noop_on_empty_rows(tmp_path):
    target = str(tmp_path / "out.db")
    await _init_sqlite_schema(target)
    await _write_sqlite_table(target, "settings", [])  # must not raise
    async with aiosqlite.connect(target) as db:
        async with db.execute("SELECT COUNT(*) FROM settings") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


async def test_write_sqlite_table_batches_across_batch_size_boundary(tmp_path, monkeypatch):
    import backend.tools.migrate_to_sqlite as mts
    monkeypatch.setattr(mts, "BATCH_SIZE", 2)
    target = str(tmp_path / "out.db")
    await _init_sqlite_schema(target)
    rows = [{"key": f"k{i}", "value": str(i)} for i in range(5)]
    await _write_sqlite_table(target, "settings", rows)
    async with aiosqlite.connect(target) as db:
        async with db.execute("SELECT COUNT(*) FROM settings") as cur:
            (n,) = await cur.fetchone()
    assert n == 5


# ─── _read_mariadb_table / _mariadb_table_exists (simulated) ──────────────────

async def test_read_mariadb_table_returns_rows_and_closes_connection(fake_aiomysql):
    conn = _FakeConn(fetchall_result=[{"id": 1, "name": "x"}])
    fake_aiomysql(conn)
    rows = await _read_mariadb_table("mysql+aiomysql://u:p@h:3306/db", "settings")
    assert rows == [{"id": 1, "name": "x"}]
    assert conn.closed is True
    sql, _params = conn.queries[0]
    assert "SELECT * FROM `settings`" in sql


async def test_mariadb_table_exists_true_and_false(fake_aiomysql):
    conn = _FakeConn(fetchone_result=("media_queue",))
    fake_aiomysql(conn)
    assert await _mariadb_table_exists("url", "media_queue") is True

    conn2 = _FakeConn(fetchone_result=None)
    fake_aiomysql(conn2)
    assert await _mariadb_table_exists("url", "nope") is False


# ─── migrate() pipeline ─────────────────────────────────────────────────────────

async def test_migrate_dry_run_reads_but_writes_nothing(tmp_path, monkeypatch):
    target = str(tmp_path / "out.db")

    async def fake_exists(db_url, table):
        return table == "settings"

    async def fake_read(db_url, table):
        return [{"key": "a", "value": "1"}]

    import backend.tools.migrate_to_sqlite as mts
    monkeypatch.setattr(mts, "_mariadb_table_exists", fake_exists)
    monkeypatch.setattr(mts, "_read_mariadb_table", fake_read)

    summary = await migrate("url", target, dry_run=True)
    assert summary == {"settings": 1}
    assert not os.path.exists(target)  # dry run must not create the target file


async def test_migrate_raises_when_target_already_exists_and_not_dry_run(tmp_path):
    target = tmp_path / "out.db"
    target.write_text("not empty")
    with pytest.raises(FileExistsError):
        await migrate("url", str(target), dry_run=False)


async def test_migrate_writes_data_for_each_present_table_and_skips_absent(tmp_path, monkeypatch):
    target = str(tmp_path / "out.db")

    async def fake_exists(db_url, table):
        return table in ("settings", "users")

    async def fake_read(db_url, table):
        if table == "settings":
            return [{"key": "a", "value": "1"}]
        return [{"id": 1, "username": "bob", "password_hash": "h", "created_at": "t"}]

    import backend.tools.migrate_to_sqlite as mts
    monkeypatch.setattr(mts, "_mariadb_table_exists", fake_exists)
    monkeypatch.setattr(mts, "_read_mariadb_table", fake_read)

    summary = await migrate("url", target, dry_run=False)
    assert summary == {"settings": 1, "users": 1}
    async with aiosqlite.connect(target) as db:
        async with db.execute("SELECT value FROM settings WHERE key='a'") as cur:
            row = await cur.fetchone()
        async with db.execute("SELECT username FROM users") as cur:
            urow = await cur.fetchone()
    assert row[0] == "1"
    assert urow[0] == "bob"


# ─── main() CLI entrypoint ──────────────────────────────────────────────────────

def test_main_parses_args_and_invokes_migrate(monkeypatch):
    import backend.tools.migrate_to_sqlite as mts
    captured = {}

    async def fake_migrate(db_url, sqlite_path, dry_run=False):
        captured["args"] = (db_url, sqlite_path, dry_run)
        return {}

    monkeypatch.setattr(mts, "migrate", fake_migrate)
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_to_sqlite.py", "--database-url", "mysql://x", "--sqlite-path", "/tmp/out.db", "--dry-run"],
    )
    main()
    assert captured["args"] == ("mysql://x", "/tmp/out.db", True)


def test_main_uses_default_sqlite_path_when_omitted(monkeypatch):
    import backend.tools.migrate_to_sqlite as mts
    captured = {}

    async def fake_migrate(db_url, sqlite_path, dry_run=False):
        captured["args"] = (db_url, sqlite_path, dry_run)
        return {}

    monkeypatch.setattr(mts, "migrate", fake_migrate)
    monkeypatch.setattr("sys.argv", ["migrate_to_sqlite.py", "--database-url", "mysql://x"])
    main()
    assert captured["args"] == ("mysql://x", "/app/data/hygie_migrated.db", False)
