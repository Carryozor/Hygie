"""init_db() + run_migrations() must be idempotent on SQLite.

Prerequisite safety net for the "single schema" refactor: a second pass of
init_db()/run_migrations() must not change the schema (PRAGMA table_info +
index_list + foreign_key_list + sqlite_master DDL identical before/after)
and must leave no pending migration.
"""
import sqlite3

import pytest

import backend.db.engine as engine


@pytest.fixture
def sqlite_path(tmp_path, monkeypatch):
    path = str(tmp_path / "idem.db")
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", path)
    import backend.db.utils as db_utils
    monkeypatch.setattr(db_utils, "DB_PATH", path)
    import backend.db.schema as schema
    monkeypatch.setattr(schema, "DB_PATH", path, raising=False)
    import backend.db.settings_store as ss
    ss._settings_cache.clear()
    ss._settings_cache_ts = 0.0
    return path


def snapshot(path: str) -> dict:
    """Full structural fingerprint of a SQLite file (no data)."""
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        snap = {
            "master": sorted(conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(),
                key=lambda r: (r[0], r[1])),
        }
        for t in tables:
            snap[f"{t}.table_info"] = conn.execute(f"PRAGMA table_info({t})").fetchall()
            snap[f"{t}.index_list"] = sorted(conn.execute(f"PRAGMA index_list({t})").fetchall(),
                                             key=lambda r: r[1])
            snap[f"{t}.fk"] = conn.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        return snap
    finally:
        conn.close()


async def _init_and_migrate() -> int:
    from backend.db.migrations import run_migrations
    from backend.db.schema import init_db
    await engine.init_db_pool()
    await init_db()
    return await run_migrations()


async def test_second_init_and_migrate_pass_changes_nothing(sqlite_path):
    from backend.db.migrations import pending_migration_ids

    first_applied = await _init_and_migrate()
    after_first = snapshot(sqlite_path)
    assert first_applied > 0, "a fresh DB must apply migrations on the first pass"

    second_applied = await _init_and_migrate()
    after_second = snapshot(sqlite_path)

    assert second_applied == 0
    assert await pending_migration_ids() == []
    changed = {k for k in after_first.keys() | after_second.keys()
               if after_first.get(k) != after_second.get(k)}
    assert not changed, f"schema changed on 2nd pass: {sorted(changed)}"


async def test_third_pass_also_stable(sqlite_path):
    await _init_and_migrate()
    await _init_and_migrate()
    before = snapshot(sqlite_path)
    await _init_and_migrate()
    assert snapshot(sqlite_path) == before


async def test_snapshot_detects_a_schema_change(sqlite_path):
    """The fingerprint must be able to fail: add a column / an index and it differs."""
    await _init_and_migrate()
    base = snapshot(sqlite_path)
    conn = sqlite3.connect(sqlite_path)
    conn.execute("ALTER TABLE settings ADD COLUMN zz_probe TEXT")
    conn.commit()
    with_column = snapshot(sqlite_path)
    conn.execute("CREATE INDEX zz_probe_idx ON settings(value)")
    conn.commit()
    conn.close()
    assert with_column != base
    assert snapshot(sqlite_path) != with_column


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN DRIFT: schema._SQLITE_INDEXES lists idx_plex_overlays_server but "
    "_init_db_sqlite() (inline index statements) never creates it, nor does m017 — "
    "a real SQLite install has no index on plex_overlays(server_id). Only "
    "backend/tools/migrate_to_sqlite.py consumes the list. Fix = single index list."))
async def test_known_drift_init_db_lacks_plex_overlays_server_index(sqlite_path):
    """init_db() hard-codes its index statements a second time; they must stay
    equal to schema._SQLITE_INDEXES (which the structural parity test relies on)."""
    from backend.db.schema import _SQLITE_INDEXES
    from tests.schema_introspect import _parse_index

    await _init_and_migrate()
    conn = sqlite3.connect(sqlite_path)
    try:
        real = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    finally:
        conn.close()
    declared = {_parse_index(sql)[0] for sql in _SQLITE_INDEXES}
    assert real == declared, (
        f"only in init_db: {sorted(real - declared)}; only in _SQLITE_INDEXES: {sorted(declared - real)}")
