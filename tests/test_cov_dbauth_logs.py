"""Coverage-focused tests for backend/db/logs.py's error paths and job context.

Happy-path insert/update behavior is implicitly exercised elsewhere; this
file targets the swallowed-exception branch in add_log() plus
set_job_context()'s contextvar plumbing and job_id persistence.
"""
import logging

import aiosqlite
import pytest

import backend.db.logs as logs_mod
from backend.db.logs import (
    add_log,
    set_job_context,
    add_job_run,
    finish_job_run,
    _current_job_id,
)


@pytest.fixture
async def db_path(monkeypatch, tmp_path):
    import backend.db.schema as _db_schema
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.engine as _db_engine

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_ss, "DB_PATH", path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


async def test_add_log_writes_row_and_defaults_source(db_path):
    await add_log("INFO", "hello")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT level, source, message FROM logs") as cur:
            row = await cur.fetchone()
    assert row == ("INFO", "system", "hello")


async def test_add_log_suppresses_debug_when_level_is_info(db_path):
    await add_log("DEBUG", "should be dropped")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM logs") as cur:
            (n,) = await cur.fetchone()
    assert n == 0


async def test_add_log_writes_debug_when_configured_level_is_debug(db_path, monkeypatch):
    async def fake_get_setting(key):
        return "DEBUG"
    monkeypatch.setattr(logs_mod, "get_setting", fake_get_setting)
    await add_log("DEBUG", "kept")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM logs") as cur:
            (n,) = await cur.fetchone()
    assert n == 1


async def test_add_log_writes_anyway_when_get_setting_raises(db_path, monkeypatch):
    async def boom(key):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(logs_mod, "get_setting", boom)
    await add_log("INFO", "still written")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT message FROM logs") as cur:
            row = await cur.fetchone()
    assert row[0] == "still written"


async def test_add_log_swallows_db_write_failure(db_path, monkeypatch, caplog):
    def boom_get_db():  # get_db() itself is sync — it returns an async CM, never awaited directly
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(logs_mod, "get_db", boom_get_db)
    with caplog.at_level(logging.ERROR):
        await add_log("INFO", "db is down")  # must not raise
    assert any("Failed to write log" in r.message for r in caplog.records)


async def test_add_log_persists_job_id_when_set(db_path):
    token = set_job_context(42)
    try:
        await add_log("INFO", "job-scoped")
    finally:
        _current_job_id.reset(token)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT job_id FROM logs WHERE message = 'job-scoped'") as cur:
            row = await cur.fetchone()
    assert row[0] == 42


async def test_add_log_leaves_job_id_null_when_not_set(db_path):
    await add_log("INFO", "no job")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT job_id FROM logs WHERE message = 'no job'") as cur:
            row = await cur.fetchone()
    assert row[0] is None


async def test_set_job_context_token_can_be_used_to_reset(db_path):
    assert _current_job_id.get() is None
    token = set_job_context(7)
    assert _current_job_id.get() == 7
    _current_job_id.reset(token)
    assert _current_job_id.get() is None


async def test_add_job_run_and_finish_job_run_round_trip(db_path):
    run_id = await add_job_run("scan")
    await finish_job_run(run_id, "success", "all good")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT status, message, finished_at FROM job_history WHERE id=?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "success"
    assert row[1] == "all good"
    assert row[2] is not None
