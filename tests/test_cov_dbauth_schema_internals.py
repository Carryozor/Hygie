"""Coverage-focused tests for backend/db/schema.py's internal helpers:

- _table_columns / _table_exists guard behavior
- _migrate_logs_table / _migrate_job_history_table (legacy-schema upgrades,
  data preservation)
- _ensure_columns' swallowed-exception branch
- _build_expert_conditions (pure mapping logic used by m011 and the on-demand
  migration endpoint)
- _init_db_mariadb (simulated DbConn — no real MariaDB available)
- _migrate_libraries_to_expert_rules_dbconn (on-demand /migrate-from-libraries
  endpoint logic; runs fine against real SQLite since it goes through DbConn)
"""
import json

import aiosqlite
import pytest

import backend.db.schema as schema_mod
from backend.db.schema import (
    _table_columns,
    _table_exists,
    _migrate_logs_table,
    _migrate_job_history_table,
    _ensure_columns,
    _build_expert_conditions,
    _init_db_mariadb,
    _migrate_libraries_to_expert_rules_dbconn,
    init_db,
)


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.engine as _db_engine

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(schema_mod, "DB_PATH", path)
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", path)
    return path


# ─── _table_columns / _table_exists ────────────────────────────────────────────

async def test_table_columns_raises_for_unknown_table(db_path):
    async with aiosqlite.connect(db_path) as db:
        with pytest.raises(ValueError, match="Unknown table"):
            await _table_columns(db, "not_a_real_table")


async def test_table_exists_false_for_missing_table(db_path):
    async with aiosqlite.connect(db_path) as db:
        assert await _table_exists(db, "logs") is False


async def test_table_exists_true_after_creation(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY)")
        await db.commit()
        assert await _table_exists(db, "logs") is True


# ─── _migrate_logs_table ────────────────────────────────────────────────────────

async def test_migrate_logs_table_renames_and_preserves_data(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "level TEXT, source TEXT, message TEXT)"
        )
        await db.execute(
            "INSERT INTO logs (timestamp, level, source, message) "
            "VALUES ('2020-01-01', 'INFO', 'scan', 'legacy entry')"
        )
        await db.commit()

        await _migrate_logs_table(db)

        cols = await _table_columns(db, "logs")
        assert cols == {"id", "ts", "level", "source", "message"}
        async with db.execute("SELECT ts, level, source, message FROM logs") as cur:
            row = await cur.fetchone()
        assert row == ("2020-01-01", "INFO", "scan", "legacy entry")

        # legacy table must be gone, not just renamed-and-forgotten
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='logs_legacy'"
        ) as cur:
            assert await cur.fetchone() is None


async def test_migrate_logs_table_defaults_missing_source_to_system(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "level TEXT, message TEXT)"  # no `source` column at all
        )
        await db.execute(
            "INSERT INTO logs (timestamp, level, message) VALUES ('t', 'WARN', 'no source col')"
        )
        await db.commit()
        await _migrate_logs_table(db)
        async with db.execute("SELECT source FROM logs") as cur:
            (source,) = await cur.fetchone()
    assert source == "system"


async def test_migrate_logs_table_noop_when_ts_column_already_present(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, ts TEXT, level TEXT, "
            "source TEXT, message TEXT)"
        )
        await db.commit()
        await _migrate_logs_table(db)  # must not rename an already-current table
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='logs_legacy'"
        ) as cur:
            assert await cur.fetchone() is None


async def test_migrate_logs_table_noop_when_table_absent(db_path):
    async with aiosqlite.connect(db_path) as db:
        await _migrate_logs_table(db)  # must not raise


# ─── _migrate_job_history_table ────────────────────────────────────────────────

async def test_migrate_job_history_table_renames_and_preserves_data(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE job_history (id INTEGER PRIMARY KEY, type TEXT, "
            "started TEXT, finished TEXT, status TEXT, message TEXT)"
        )
        await db.execute(
            "INSERT INTO job_history (type, started, finished, status, message) "
            "VALUES ('scan', 't0', 't1', 'ok', 'done')"
        )
        await db.commit()

        await _migrate_job_history_table(db)

        cols = await _table_columns(db, "job_history")
        assert {"job_type", "started_at", "finished_at", "status", "message"} <= cols
        async with db.execute(
            "SELECT job_type, started_at, finished_at, status, message FROM job_history"
        ) as cur:
            row = await cur.fetchone()
    assert row == ("scan", "t0", "t1", "ok", "done")


async def test_migrate_job_history_table_noop_when_current_schema(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE job_history (id INTEGER PRIMARY KEY, job_type TEXT, "
            "started_at TEXT, finished_at TEXT, status TEXT, message TEXT)"
        )
        await db.commit()
        await _migrate_job_history_table(db)
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_history_legacy'"
        ) as cur:
            assert await cur.fetchone() is None


async def test_migrate_job_history_table_noop_when_absent(db_path):
    async with aiosqlite.connect(db_path) as db:
        await _migrate_job_history_table(db)  # must not raise


# ─── _ensure_columns ────────────────────────────────────────────────────────────

async def test_ensure_columns_adds_missing_column(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY)")
        await db.commit()
        await _ensure_columns(db, "logs", [("job_id", "INTEGER DEFAULT NULL")])
        cols = await _table_columns(db, "logs")
    assert "job_id" in cols


async def test_ensure_columns_noop_when_table_absent(db_path):
    async with aiosqlite.connect(db_path) as db:
        await _ensure_columns(db, "logs", [("job_id", "INTEGER")])  # must not raise


async def test_ensure_columns_noop_when_expected_list_empty(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY)")
        await db.commit()
        await _ensure_columns(db, "logs", [])  # must not raise


async def test_ensure_columns_swallows_alter_table_failure(db_path, caplog):
    """A malformed column definition must not crash schema init — it's logged
    and skipped, matching the `except Exception` in _ensure_columns."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY)")
        await db.commit()
        # Unbalanced paren in the DEFAULT expression -> real sqlite3 syntax error
        await _ensure_columns(db, "logs", [("broken_col", "TEXT DEFAULT (")])
        cols = await _table_columns(db, "logs")
    assert "broken_col" not in cols
    assert any("Could not add column" in r.message for r in caplog.records)


# ─── _build_expert_conditions ──────────────────────────────────────────────────

def test_build_expert_conditions_never_watched_true_maps_to_play_count_eq_zero():
    result = _build_expert_conditions(
        [{"field": "never_watched", "op": "eq", "value": True}], []
    )
    assert result == [{"field": "play_count", "op": "eq", "value": 0}]


def test_build_expert_conditions_never_watched_false_maps_to_play_count_gte_one():
    result = _build_expert_conditions(
        [{"field": "never_watched", "op": "eq", "value": False}], []
    )
    assert result == [{"field": "play_count", "op": "gte", "value": 1}]


def test_build_expert_conditions_maps_known_fields_through():
    result = _build_expert_conditions(
        [{"field": "days_since_added", "op": "gt", "value": 90}], []
    )
    assert result == [{"field": "added_days_ago", "op": "gt", "value": 90}]


def test_build_expert_conditions_drops_unrecognized_fields():
    result = _build_expert_conditions(
        [{"field": "totally_unknown_field", "op": "gt", "value": 1}], []
    )
    assert result == []


def test_build_expert_conditions_includes_and_excludes_seerr_users():
    seerr = [
        {"type": "user_include", "user_id": 1},
        {"type": "user_include", "user_id": 2},
        {"type": "user_exclude", "user_id": 9},
        {"type": "something_else", "user_id": 99},
    ]
    result = _build_expert_conditions([], seerr)
    assert {"field": "seerr_user_id", "op": "in", "value": [1, 2]} in result
    assert {"field": "seerr_user_id", "op": "not_in", "value": [9]} in result
    assert len(result) == 2


def test_build_expert_conditions_empty_input_returns_empty():
    assert _build_expert_conditions([], []) == []


# ─── _init_db_mariadb (simulated DbConn) ───────────────────────────────────────

class _FakeMariaDbConn:
    def __init__(self, index_error=None):
        self.executed: list[str] = []
        self.committed = False
        self._index_error = index_error

    async def execute(self, sql, params=()):
        self.executed.append(sql)
        if "CREATE INDEX" not in sql and "CREATE TABLE" not in sql:
            return
        if self._index_error and sql in self._index_error:
            raise Exception(self._index_error[sql])

    async def fetch_one(self, sql, params=()):
        return None  # nothing pre-seeded — every default setting gets inserted

    async def commit(self):
        self.committed = True


class _FakeGetDb:
    def __init__(self, conn):
        self._conn = conn

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


async def test_init_db_mariadb_creates_tables_and_seeds_defaults(monkeypatch):
    conn = _FakeMariaDbConn()
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "get_db", _FakeGetDb(conn))

    await _init_db_mariadb()

    from backend.db.schema_mariadb import MARIADB_TABLES
    from backend.db.settings_store import DEFAULT_SETTINGS
    create_table_stmts = [s for s in conn.executed if "CREATE TABLE" in s]
    assert len(create_table_stmts) == len(MARIADB_TABLES)
    insert_settings = [s for s in conn.executed if s.startswith("INSERT INTO settings")]
    assert len(insert_settings) == len(DEFAULT_SETTINGS)
    assert conn.committed


async def test_init_db_mariadb_swallows_duplicate_index_error(monkeypatch):
    from backend.db.schema_mariadb import MARIADB_INDEXES
    conn = _FakeMariaDbConn(index_error={MARIADB_INDEXES[0]: "1061 Duplicate key name 'x'"})
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "get_db", _FakeGetDb(conn))

    await _init_db_mariadb()  # must not raise despite the simulated 1061 error


async def test_init_db_mariadb_reraises_non_duplicate_index_error(monkeypatch):
    from backend.db.schema_mariadb import MARIADB_INDEXES
    conn = _FakeMariaDbConn(index_error={MARIADB_INDEXES[0]: "1054 Unknown column 'bogus'"})
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "get_db", _FakeGetDb(conn))

    with pytest.raises(Exception, match="1054"):
        await _init_db_mariadb()


# ─── _migrate_libraries_to_expert_rules_dbconn ─────────────────────────────────

async def test_migrate_libraries_dbconn_returns_zero_when_already_done(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
        await db.execute("INSERT INTO settings VALUES ('v3_migration_done', '1')")
        await db.execute("CREATE TABLE libraries (id INTEGER PRIMARY KEY)")
        await db.commit()
    assert await _migrate_libraries_to_expert_rules_dbconn() == 0


async def test_migrate_libraries_dbconn_returns_zero_when_libraries_table_missing(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
        await db.commit()
    assert await _migrate_libraries_to_expert_rules_dbconn() == 0  # caught by except


async def test_migrate_libraries_dbconn_returns_zero_when_no_rows(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
            "logic TEXT, seerr_conditions TEXT, enabled INTEGER)"
        )
        await db.commit()
    assert await _migrate_libraries_to_expert_rules_dbconn() == 0


async def test_migrate_libraries_dbconn_creates_rule_and_skips_invalid_json(db_path):
    good = json.dumps([{"field": "days_not_watched", "op": "gt", "value": 30}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
            "logic TEXT, seerr_conditions TEXT, enabled INTEGER)"
        )
        await db.execute(
            "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
            "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
            "priority INTEGER, created_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "Films", good, "AND", "[]", 1),
                (2, "Broken", "NOT-JSON{{", "AND", "[]", 1),
            ],
        )
        await db.commit()

    created = await _migrate_libraries_to_expert_rules_dbconn()
    assert created == 1

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name FROM expert_rules") as cur:
            rows = await cur.fetchall()
    assert rows == [("Films (migré)",)]

    # idempotent-ish dedup: running again (still no v3_migration_done set by
    # this on-demand path) must skip the already-created rule by name match
    created_again = await _migrate_libraries_to_expert_rules_dbconn()
    assert created_again == 0
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 1


async def test_migrate_libraries_dbconn_skips_empty_conditions_and_unmapped_fields(db_path):
    unmapped = json.dumps([{"field": "no_such_field", "op": "gt", "value": 1}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
        await db.execute(
            "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
            "logic TEXT, seerr_conditions TEXT, enabled INTEGER)"
        )
        await db.execute(
            "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
            "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
            "priority INTEGER, created_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "Empty", "[]", "AND", "[]", 1),
                (2, "Unmapped", unmapped, "AND", "[]", 1),
            ],
        )
        await db.commit()

    created = await _migrate_libraries_to_expert_rules_dbconn()
    assert created == 0
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


# ─── init_db() dispatcher + orphaned-job marking ───────────────────────────────

async def test_init_db_dispatches_to_mariadb_when_dialect_is_mariadb(monkeypatch):
    from unittest.mock import AsyncMock
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DIALECT", "mariadb")
    fake = AsyncMock(return_value=None)
    monkeypatch.setattr(schema_mod, "_init_db_mariadb", fake)
    await init_db()
    fake.assert_awaited_once()


async def test_init_db_sqlite_marks_orphaned_job_history_as_interrupted(db_path):
    await init_db()  # first pass: creates schema
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status) "
            "VALUES ('scan', 't0', NULL, NULL)"
        )
        await db.commit()

    await init_db()  # second pass: must mark the orphaned row

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT status, message FROM job_history WHERE job_type='scan'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "interrupted"
    assert "Interrompu" in row[1]
