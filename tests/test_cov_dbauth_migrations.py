"""Coverage-focused tests for backend/db/migrations.py.

Existing tests/test_migrations_runner.py exercises the runner (run_migrations,
idempotence, failure handling) against an already-current schema produced by
schema.init_db(), so every individual migration's "if column/table missing"
branch is a no-op there (the column already exists).

This file instead constructs the PRE-migration schema state by hand for each
migration and asserts:
  - the migration upgrades that state to the expected post-state
  - existing data survives the upgrade
  - running it twice is a no-op the second time (idempotence)

MariaDB-only branches (different DDL/column types) are not executable without
a real MariaDB server. Where the branch is pure SQL-string selection (m004,
m006, m015, m017's CREATE TABLE), we verify the DDL that would be emitted via
a simulated DbConn (records executed SQL, never touches a real DB) rather than
skipping the branch outright.
"""
import json

import aiosqlite
import pytest

import backend.db.migrations as migrations_mod
from backend.db.migrations import (
    _m002_ensure_seen_status_on_logs,
    _m003_ensure_grace_days_on_expert_rules,
    _m004_ensure_refresh_tokens_table,
    _m005_normalize_library_server_id,
    _m006_fix_mariadb_expert_rules_schema,
    _m007_migrate_notification_columns,
    _m008_add_job_id_to_logs,
    _m009_migrate_legacy_emby_to_media_servers,
    _m010_v2_to_v3_data,
    _m011_libraries_to_expert_rules,
    _m012_interval_hours_to_minutes,
    _m013_purge_verbose_scan_logs,
    _m014_add_library_ids_to_seerr_user_rules,
    _m015_fix_remaining_mariadb_column_gaps,
    _m016_add_arr_server_url_to_media_queue,
    _m017_ensure_plex_overlays_table,
    migration_lock,
    pending_migration_ids,
    _MIGRATIONS,
)


@pytest.fixture(autouse=True)
def _point_engine_at_tmp(monkeypatch, tmp_path):
    """Point db.engine (and .utils/.settings_store, used transitively by
    encryption/settings helpers) at a throwaway SQLite file — mirrors
    tests/test_migrations_runner.py's fresh_db fixture, minus init_db()
    (each test below builds only the pre-migration tables it needs)."""
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
    return db_path


async def _cols(db_path, table):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
    return {r[1] for r in rows}


async def _exec(db_path, *statements):
    async with aiosqlite.connect(db_path) as db:
        for s in statements:
            await db.execute(s)
        await db.commit()


# ─── Simulated MariaDB DbConn (no network, records emitted SQL) ───────────────

class _FakeMariaDbConn:
    def __init__(self, existing_tables=(), existing_cols=None):
        self.existing_tables = set(existing_tables)
        self.existing_cols = existing_cols or {}
        self.executed: list[str] = []
        self.committed = False

    async def table_exists(self, table):
        return table in self.existing_tables

    async def table_columns(self, table):
        return set(self.existing_cols.get(table, set()))

    async def execute(self, sql, params=()):
        self.executed.append(sql)
        return 0

    async def execute_write(self, sql, params=()):
        self.executed.append(sql)
        return 0

    async def fetch_one(self, sql, params=()):
        return None

    async def fetch_all(self, sql, params=()):
        return []

    async def commit(self):
        self.committed = True


class _FakeGetDb:
    """Async context manager factory standing in for engine.get_db()."""

    def __init__(self, conn):
        self._conn = conn

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _simulate_mariadb(monkeypatch, existing_tables=(), existing_cols=None):
    conn = _FakeMariaDbConn(existing_tables, existing_cols)
    monkeypatch.setattr(migrations_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(migrations_mod, "get_db", _FakeGetDb(conn))
    return conn


# ─── m002: logs.seen_status ────────────────────────────────────────────────────

async def test_m002_adds_seen_status_column_and_preserves_rows(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, ts TEXT, message TEXT)",
        "INSERT INTO logs (id, ts, message) VALUES (1, 't', 'hello')",
    )
    await _m002_ensure_seen_status_on_logs()
    assert "seen_status" in await _cols(db_path, "logs")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT message FROM logs WHERE id=1") as cur:
            row = await cur.fetchone()
    assert row[0] == "hello"  # pre-existing data survived the ALTER

    # idempotent: running again must not raise (column already present)
    await _m002_ensure_seen_status_on_logs()


async def test_m002_is_noop_when_column_already_present(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE logs (id INTEGER PRIMARY KEY, seen_status TEXT)")
    await _m002_ensure_seen_status_on_logs()  # must not raise "duplicate column"
    assert await _cols(db_path, "logs") == {"id", "seen_status"}


# ─── m003: expert_rules.grace_days ─────────────────────────────────────────────

async def test_m003_adds_grace_days_with_default_and_preserves_rows(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY, name TEXT)",
        "INSERT INTO expert_rules (id, name) VALUES (1, 'My Rule')",
    )
    await _m003_ensure_grace_days_on_expert_rules()
    assert "grace_days" in await _cols(db_path, "expert_rules")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name, grace_days FROM expert_rules WHERE id=1") as cur:
            row = await cur.fetchone()
    assert row == ("My Rule", 7)


# ─── m004: refresh_tokens table ────────────────────────────────────────────────

async def test_m004_creates_refresh_tokens_table_sqlite(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE users (id INTEGER PRIMARY KEY)")
    await _m004_ensure_refresh_tokens_table()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='refresh_tokens'"
        ) as cur:
            assert await cur.fetchone() is not None

    # idempotent — must not attempt CREATE TABLE again in a way that errors
    await _m004_ensure_refresh_tokens_table()


async def test_m004_skips_when_table_already_exists(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE refresh_tokens (id INTEGER PRIMARY KEY)")
    await _m004_ensure_refresh_tokens_table()  # no-op, must not raise


async def test_m004_mariadb_ddl_uses_auto_increment_and_fk(monkeypatch):
    conn = _simulate_mariadb(monkeypatch, existing_tables=())
    await _m004_ensure_refresh_tokens_table()
    assert len(conn.executed) == 1
    ddl = conn.executed[0]
    assert "AUTO_INCREMENT" in ddl
    assert "ENGINE=InnoDB" in ddl
    assert "FOREIGN KEY (user_id)" in ddl
    assert conn.committed


# ─── m005: normalize library server_id ─────────────────────────────────────────

async def test_m005_normalizes_null_and_empty_server_id_only(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, server_id TEXT)",
        "INSERT INTO libraries (id, server_id) VALUES (1, NULL)",
        "INSERT INTO libraries (id, server_id) VALUES (2, '')",
        "INSERT INTO libraries (id, server_id) VALUES (3, '2')",
    )
    await _m005_normalize_library_server_id()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT id, server_id FROM libraries ORDER BY id") as cur:
            rows = await cur.fetchall()
    assert rows == [(1, "0"), (2, "0"), (3, "2")]  # row 3 untouched


# ─── m006: MariaDB expert_rules column gaps ────────────────────────────────────

async def test_m006_adds_library_ids_and_grace_days_sqlite(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY)")
    await _m006_fix_mariadb_expert_rules_schema()
    cols = await _cols(db_path, "expert_rules")
    assert {"library_ids", "grace_days"} <= cols


async def test_m006_skips_columns_already_present(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY, library_ids TEXT, grace_days INTEGER)")
    await _m006_fix_mariadb_expert_rules_schema()  # must not raise duplicate column


async def test_m006_mariadb_ddl_uses_longtext_and_int_types(monkeypatch):
    conn = _simulate_mariadb(monkeypatch, existing_tables=(), existing_cols={"expert_rules": set()})
    await _m006_fix_mariadb_expert_rules_schema()
    joined = "\n".join(conn.executed)
    assert "LONGTEXT DEFAULT NULL" in joined
    assert "INT NOT NULL DEFAULT 7" in joined


# ─── m007: legacy notified_* columns -> notifications table ───────────────────

async def test_m007_migrates_flag_columns_to_notifications(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE media_queue (id INTEGER PRIMARY KEY, notified_30d INTEGER, "
        "notified_7d INTEGER, notified_1d INTEGER, notified_now INTEGER, "
        "notified_detected INTEGER, notified_thresholds TEXT)",
        "CREATE TABLE notifications (media_id INTEGER, threshold TEXT)",
        "INSERT INTO media_queue (id, notified_30d, notified_7d, notified_1d, "
        "notified_now, notified_detected, notified_thresholds) "
        "VALUES (1, 1, 0, 0, 1, 0, '[]')",
    )
    await _m007_migrate_notification_columns()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT threshold FROM notifications WHERE media_id=1 ORDER BY threshold"
        ) as cur:
            rows = await cur.fetchall()
    assert {r[0] for r in rows} == {"30d", "now"}


async def test_m007_migrates_notified_thresholds_json_skipping_migrated_marker(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE media_queue (id INTEGER PRIMARY KEY, notified_thresholds TEXT)",
        "CREATE TABLE notifications (media_id INTEGER, threshold TEXT)",
        "INSERT INTO media_queue (id, notified_thresholds) VALUES "
        "(1, '" + json.dumps([30, "7d", "migrated"]) + "')",
    )
    await _m007_migrate_notification_columns()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT threshold FROM notifications WHERE media_id=1 ORDER BY threshold"
        ) as cur:
            rows = await cur.fetchall()
    got = {r[0] for r in rows}
    assert got == {"30d", "7d"}  # "migrated" marker not turned into a row


async def test_m007_tolerates_missing_legacy_columns_entirely(_point_engine_at_tmp):
    """Very old DB with neither the notified_* flags nor notified_thresholds —
    every per-column INSERT..SELECT fails (no such column) and is swallowed."""
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE media_queue (id INTEGER PRIMARY KEY)",
        "CREATE TABLE notifications (media_id INTEGER, threshold TEXT)",
    )
    await _m007_migrate_notification_columns()  # must not raise
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM notifications") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


# ─── m008: logs.job_id ─────────────────────────────────────────────────────────

async def test_m008_adds_job_id_column(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE logs (id INTEGER PRIMARY KEY)")
    await _m008_add_job_id_to_logs()
    assert "job_id" in await _cols(db_path, "logs")


# ─── m009: legacy emby settings -> media_servers[0] ────────────────────────────

async def test_m009_promotes_legacy_emby_settings(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('emby_url', 'http://emby.local:8096')",
        "INSERT INTO settings VALUES ('emby_api_key', 'secret123')",
        "INSERT INTO settings VALUES ('media_server_type', 'emby')",
    )
    await _m009_migrate_legacy_emby_to_media_servers()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE `key`='media_servers'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    from backend.db.encryption import _decrypt_value
    servers = json.loads(_decrypt_value(row[0]))
    assert servers[0]["url"] == "http://emby.local:8096"
    assert servers[0]["api_key"] == "secret123"
    assert servers[0]["type"] == "emby"


async def test_m009_noop_when_media_servers_already_populated(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('media_servers', '" + json.dumps([{"id": "0"}]) + "')",
        "INSERT INTO settings VALUES ('emby_url', 'http://should-not-be-used')",
    )
    await _m009_migrate_legacy_emby_to_media_servers()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE `key`='media_servers'") as cur:
            row = await cur.fetchone()
    assert row[0] == json.dumps([{"id": "0"}])  # untouched


async def test_m009_noop_when_no_emby_url(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
    await _m009_migrate_legacy_emby_to_media_servers()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE `key`='media_servers'") as cur:
            assert await cur.fetchone() is None


async def test_m009_skips_when_emby_url_already_encrypted(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('emby_url', 'enc:garbage')",
    )
    await _m009_migrate_legacy_emby_to_media_servers()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE `key`='media_servers'") as cur:
            assert await cur.fetchone() is None


# ─── m010: v2->v3 backfill + legacy key cleanup ────────────────────────────────

async def test_m010_backfills_server_id_and_deletion_unit_and_removes_legacy_keys(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, server_id TEXT, deletion_unit TEXT)",
        "INSERT INTO libraries (id, server_id, deletion_unit) VALUES (1, NULL, NULL)",
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('media_servers', '" + json.dumps([{"id": "0"}]) + "')",
        "INSERT INTO settings VALUES ('emby_url', 'http://x')",
        "INSERT INTO settings VALUES ('emby_api_key', 'k')",
    )
    await _m010_v2_to_v3_data()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT server_id, deletion_unit FROM libraries WHERE id=1") as cur:
            row = await cur.fetchone()
        async with db.execute("SELECT `key` FROM settings") as cur:
            keys = {r[0] for r in await cur.fetchall()}
    assert row == ("0", "episode")
    assert "emby_url" not in keys
    assert "emby_api_key" not in keys
    assert "media_servers" in keys


async def test_m010_is_safe_with_no_libraries_table(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
    await _m010_v2_to_v3_data()  # must not raise


# ─── m011: library conditions -> expert_rules ──────────────────────────────────

async def test_m011_skips_when_v3_migration_done_marker_present(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('v3_migration_done', '1')",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY)",
    )
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


async def test_m011_marks_done_without_creating_rules_when_tables_missing(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)")
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE `key`='v3_migration_done'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == "1"


async def test_m011_creates_expert_rule_from_legacy_library_conditions(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    old_conds = json.dumps([{"field": "days_not_watched", "op": "gt", "value": 30}])
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
        "logic TEXT, seerr_conditions TEXT, enabled INTEGER)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
        "priority INTEGER, created_at TEXT)",
        "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
        f"VALUES (1, 'Films', '{old_conds}', 'AND', '[]', 1)",
    )
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name FROM expert_rules") as cur:
            rows = await cur.fetchall()
        async with db.execute(
            "SELECT value FROM settings WHERE `key`='v3_migration_done'"
        ) as cur:
            done = await cur.fetchone()
    assert rows == [("Films (migré)",)]
    assert done is not None

    # idempotent: re-running is a no-op because v3_migration_done is now set
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 1


async def test_m011_skips_rows_with_empty_conditions_list(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
        "logic TEXT, seerr_conditions TEXT, enabled INTEGER)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
        "priority INTEGER, created_at TEXT)",
        "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
        "VALUES (1, 'Empty', '[]', 'AND', '[]', 1)",
    )
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 0  # `if not old_conds: continue` branch


async def test_m011_skips_rows_with_unmapped_condition_fields(_point_engine_at_tmp):
    """A condition field not in _EXPERT_RULE_FIELD_MAP (and not never_watched)
    produces an empty new_conds list — the row is skipped, not inserted blank."""
    db_path = _point_engine_at_tmp
    old_conds = json.dumps([{"field": "some_future_field", "op": "gt", "value": 1}])
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
        "logic TEXT, seerr_conditions TEXT, enabled INTEGER)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
        "priority INTEGER, created_at TEXT)",
        "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
        f"VALUES (1, 'Unmapped', '{old_conds}', 'AND', '[]', 1)",
    )
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 0  # `if not new_conds: continue` branch


async def test_m011_skips_row_when_matching_rule_name_already_exists(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    old_conds = json.dumps([{"field": "days_not_watched", "op": "gt", "value": 30}])
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
        "logic TEXT, seerr_conditions TEXT, enabled INTEGER)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
        "priority INTEGER, created_at TEXT)",
        "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
        f"VALUES (1, 'Films', '{old_conds}', 'AND', '[]', 1)",
        # Pre-existing rule with the exact name m011 would generate
        "INSERT INTO expert_rules (name, library_id) VALUES ('Films (migré)', '1')",
    )
    await _m011_libraries_to_expert_rules()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 1  # the pre-existing row — no duplicate was inserted


async def test_m011_skips_rows_with_invalid_json_conditions(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE libraries (id INTEGER PRIMARY KEY, name TEXT, conditions TEXT, "
        "logic TEXT, seerr_conditions TEXT, enabled INTEGER)",
        "CREATE TABLE expert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "library_id TEXT, conditions TEXT, operator TEXT, action TEXT, enabled INTEGER, "
        "priority INTEGER, created_at TEXT)",
        "INSERT INTO libraries (id, name, conditions, logic, seerr_conditions, enabled) "
        "VALUES (1, 'Broken', 'NOT-JSON{{', 'AND', '[]', 1)",
    )
    await _m011_libraries_to_expert_rules()  # must not raise
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM expert_rules") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


# ─── m012: hours -> minutes settings ───────────────────────────────────────────

async def test_m012_converts_digit_hours_to_minutes_and_removes_hours_key(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('scan_interval_hours', '2')",
    )
    await _m012_interval_hours_to_minutes()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT `key`, value FROM settings ORDER BY `key`") as cur:
            rows = await cur.fetchall()
    assert rows == [("scan_interval_minutes", "120")]


async def test_m012_skips_non_digit_hours_value_but_leaves_key(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('scan_interval_hours', 'abc')",
    )
    await _m012_interval_hours_to_minutes()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT `key` FROM settings") as cur:
            keys = {r[0] for r in await cur.fetchall()}
    assert keys == {"scan_interval_hours"}  # non-digit: continue, nothing deleted


async def test_m012_does_not_overwrite_existing_minutes_key(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)",
        "INSERT INTO settings VALUES ('scan_interval_hours', '2')",
        "INSERT INTO settings VALUES ('scan_interval_minutes', '999')",
    )
    await _m012_interval_hours_to_minutes()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE `key`='scan_interval_minutes'"
        ) as cur:
            (val,) = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM settings WHERE `key`='scan_interval_hours'"
        ) as cur:
            (n,) = await cur.fetchone()
    assert val == "999"  # pre-existing value not clobbered
    assert n == 0  # hours key still deleted regardless


# ─── m013: purge verbose scan logs ─────────────────────────────────────────────

async def test_m013_purges_only_matching_verbose_messages(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, message TEXT)",
        "INSERT INTO logs (message) VALUES ('Ignoré (non demandé sur Seerr) foo')",
        "INSERT INTO logs (message) VALUES ('Ignoré (utilisateur Seerr bar')",
        "INSERT INTO logs (message) VALUES ('Suppression effectuée')",
    )
    await _m013_purge_verbose_scan_logs()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT message FROM logs") as cur:
            rows = await cur.fetchall()
    assert rows == [("Suppression effectuée",)]


async def test_m013_noop_when_nothing_matches(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, message TEXT)",
        "INSERT INTO logs (message) VALUES ('normal log')",
    )
    await _m013_purge_verbose_scan_logs()  # n == 0 branch — must not raise
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM logs") as cur:
            (n,) = await cur.fetchone()
    assert n == 1


# ─── m014: seerr_user_rules.library_ids ────────────────────────────────────────

async def test_m014_adds_library_ids_column(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE seerr_user_rules (id INTEGER PRIMARY KEY)")
    await _m014_add_library_ids_to_seerr_user_rules()
    assert "library_ids" in await _cols(db_path, "seerr_user_rules")


# ─── m015: remaining MariaDB column gaps (also applied on SQLite) ─────────────

async def test_m015_adds_all_six_columns_across_tables(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE media_queue (id INTEGER PRIMARY KEY)",
        "CREATE TABLE logs (id INTEGER PRIMARY KEY)",
        "CREATE TABLE job_history (id INTEGER PRIMARY KEY)",
        "CREATE TABLE seerr_user_rules (id INTEGER PRIMARY KEY)",
    )
    await _m015_fix_remaining_mariadb_column_gaps()
    assert {"torrent_hash", "seerr_discord_id", "ignored"} <= await _cols(db_path, "media_queue")
    assert "category" in await _cols(db_path, "logs")
    assert "result" in await _cols(db_path, "job_history")
    assert "name" in await _cols(db_path, "seerr_user_rules")


# ─── m016: media_queue.arr_server_url ──────────────────────────────────────────

async def test_m016_adds_arr_server_url_column(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path, "CREATE TABLE media_queue (id INTEGER PRIMARY KEY)")
    await _m016_add_arr_server_url_to_media_queue()
    assert "arr_server_url" in await _cols(db_path, "media_queue")


# ─── m017: plex_overlays table ─────────────────────────────────────────────────

async def test_m017_creates_plex_overlays_table(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _m017_ensure_plex_overlays_table()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='plex_overlays'"
        ) as cur:
            assert await cur.fetchone() is not None


async def test_m017_skips_when_table_already_exists(_point_engine_at_tmp):
    db_path = _point_engine_at_tmp
    await _exec(db_path,
        "CREATE TABLE plex_overlays (id INTEGER PRIMARY KEY, marker TEXT)",
        "INSERT INTO plex_overlays (id, marker) VALUES (1, 'keep-me')",
    )
    await _m017_ensure_plex_overlays_table()  # must not DROP/recreate
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT marker FROM plex_overlays WHERE id=1") as cur:
            row = await cur.fetchone()
    assert row[0] == "keep-me"


async def test_m017_mariadb_ddl_creates_table_with_unique_constraint(monkeypatch):
    conn = _simulate_mariadb(monkeypatch, existing_tables=())
    await _m017_ensure_plex_overlays_table()
    assert len(conn.executed) == 1
    assert "UNIQUE KEY uq_plex_overlays (server_id, rating_key)" in conn.executed[0]


# ─── migration_lock() ──────────────────────────────────────────────────────────

async def test_migration_lock_is_a_noop_on_sqlite(_point_engine_at_tmp, monkeypatch):
    monkeypatch.setattr(migrations_mod, "DIALECT", "sqlite")
    entered = False
    async with migration_lock():
        entered = True
    assert entered


class _LockFakeCursor:
    def __init__(self, conn, get_lock_result):
        self._conn = conn
        self._get_lock_result = get_lock_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        self._conn.statements.append(sql)

    async def fetchone(self):
        return (self._get_lock_result,)


class _LockFakeConn:
    def __init__(self, get_lock_result=1):
        self.statements = []
        self._get_lock_result = get_lock_result

    def cursor(self):
        return _LockFakeCursor(self, self._get_lock_result)


class _LockFakePool:
    def __init__(self, get_lock_result=1):
        self.conn = _LockFakeConn(get_lock_result)
        self.released = False

    async def acquire(self):
        return self.conn

    async def release(self, conn):
        self.released = True


async def test_migration_lock_acquires_and_releases_on_mariadb(monkeypatch):
    import backend.db.engine as engine

    pool = _LockFakePool(get_lock_result=1)
    monkeypatch.setattr(migrations_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", pool)

    async with migration_lock():
        assert any("GET_LOCK" in s for s in pool.conn.statements)

    assert any("RELEASE_LOCK" in s for s in pool.conn.statements)
    assert pool.released


async def test_migration_lock_raises_when_lock_unavailable(monkeypatch):
    import backend.db.engine as engine

    pool = _LockFakePool(get_lock_result=0)
    monkeypatch.setattr(migrations_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", pool)

    with pytest.raises(RuntimeError, match="Could not acquire the migration lock"):
        async with migration_lock():
            pass  # pragma: no cover - must not be entered
    assert pool.released, "the borrowed connection must still be released"


# ─── pending_migration_ids() ────────────────────────────────────────────────────

async def test_pending_migration_ids_returns_all_when_table_absent(_point_engine_at_tmp):
    ids = await pending_migration_ids()
    assert ids == [mid for mid, _, _ in _MIGRATIONS]


async def test_pending_migration_ids_excludes_applied(_point_engine_at_tmp):
    from backend.db.migrations import _ensure_migrations_table, _mark_applied
    await _ensure_migrations_table()
    await _mark_applied("m001", "baseline")
    ids = await pending_migration_ids()
    assert "m001" not in ids
    assert "m002" in ids


# ─── Remaining MariaDB-only string-selection branches (simulated DbConn) ──────
# Each of these mirrors an already-tested SQLite behavior; only the DDL/DML
# dialect string differs, so a real MariaDB server is not needed to prove the
# right branch fires — only that the mariadb-flavored SQL is what gets sent.

async def test_mark_applied_uses_insert_ignore_on_mariadb(monkeypatch):
    from backend.db.migrations import _mark_applied
    conn = _simulate_mariadb(monkeypatch)
    await _mark_applied("m999", "desc")
    assert any("INSERT IGNORE INTO schema_migrations" in s for s in conn.executed)


async def test_migration_lock_release_failure_is_logged_not_raised(monkeypatch):
    """RELEASE_LOCK failing (e.g. connection already dropped) must not mask
    whatever happened inside the `async with` block — it's a best-effort
    cleanup, logged and swallowed (see migrations.py's `except Exception`)."""
    import backend.db.engine as engine

    class _RaisingCursor(_LockFakeCursor):
        async def execute(self, sql, params=()):
            self._conn.statements.append(sql)
            if "RELEASE_LOCK" in sql:
                raise RuntimeError("connection gone")

    class _RaisingConn(_LockFakeConn):
        def cursor(self):
            return _RaisingCursor(self, self._get_lock_result)

    class _RaisingPool(_LockFakePool):
        def __init__(self):
            self.conn = _RaisingConn(1)
            self.released = False

    pool = _RaisingPool()
    monkeypatch.setattr(migrations_mod, "DIALECT", "mariadb")
    monkeypatch.setattr(engine, "_pool", pool)

    async with migration_lock():
        pass  # body succeeds — the failure happens during release cleanup

    assert pool.released, "connection must still go back to the pool"


async def test_m007_mariadb_uses_insert_ignore_for_thresholds_json(monkeypatch):
    conn = _simulate_mariadb(monkeypatch)
    conn._fetch_all_queue = [
        [{"id": 1, "notified_thresholds": json.dumps([30])}],
    ]

    async def fetch_all(sql, params=()):
        if conn._fetch_all_queue:
            return conn._fetch_all_queue.pop(0)
        return []

    conn.fetch_all = fetch_all
    await _m007_migrate_notification_columns()
    assert any(
        "INSERT IGNORE INTO notifications" in s and "VALUES (?,?)" in s
        for s in conn.executed
    )


async def test_m009_mariadb_uses_replace_into(monkeypatch):
    conn = _simulate_mariadb(monkeypatch)

    async def fetch_one(sql, params=()):
        if "emby_url" in sql:
            return {"value": "http://x"}
        return None

    conn.fetch_one = fetch_one
    await _m009_migrate_legacy_emby_to_media_servers()
    assert any("REPLACE INTO settings" in s for s in conn.executed)


async def test_m011_mariadb_marks_done_with_insert_ignore_when_tables_missing(monkeypatch):
    conn = _simulate_mariadb(monkeypatch, existing_tables=())  # libraries/expert_rules absent
    await _m011_libraries_to_expert_rules()
    assert any("INSERT IGNORE INTO settings" in s for s in conn.executed)


async def test_m011_mariadb_creates_rule_and_marks_done_with_insert_ignore(monkeypatch):
    """Full success path on MariaDB: row with valid conditions -> new expert
    rule inserted, then the completion marker is written via INSERT IGNORE
    (not the SQLite INSERT OR IGNORE)."""
    conn = _simulate_mariadb(monkeypatch, existing_tables=("libraries", "expert_rules"))

    async def fetch_one(sql, params=()):
        if "v3_migration_done" in sql:
            return None  # not yet migrated
        if "expert_rules WHERE name" in sql:
            return None  # no pre-existing duplicate rule
        return None

    async def fetch_all(sql, params=()):
        return [{
            "id": 1, "name": "Films", "logic": "AND", "enabled": 1,
            "conditions": json.dumps([{"field": "days_not_watched", "op": "gt", "value": 30}]),
            "seerr_conditions": "[]",
        }]

    conn.fetch_one = fetch_one
    conn.fetch_all = fetch_all
    await _m011_libraries_to_expert_rules()

    assert any("INSERT INTO expert_rules" in s for s in conn.executed)
    assert any("INSERT IGNORE INTO settings" in s for s in conn.executed)


async def test_m012_mariadb_uses_replace_into_for_minutes(monkeypatch):
    conn = _simulate_mariadb(monkeypatch)
    calls = {"n": 0}

    async def fetch_one(sql, params=()):
        calls["n"] += 1
        if "scan_interval_hours" in sql or (params and params[0] == "scan_interval_hours"):
            return {"value": "2"}
        return None

    conn.fetch_one = fetch_one
    await _m012_interval_hours_to_minutes()
    assert any("REPLACE INTO settings" in s for s in conn.executed)
