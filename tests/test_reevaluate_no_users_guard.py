"""reevaluate_library_queue() must refuse to run when get_users() returns [].

Root cause: unlike the main scan path (_orchestrator._run_scan_body, guarded
by _abort_scan_no_users — added after the 2026-09-15 incident where an empty
user list made a whole library look "never watched" and queued it for
deletion), reevaluate_library_queue() had no such guard. With an empty user
list, _aggregate_user_data([], ...) reports never_watched=True for every
pending item regardless of its real watch state. Since this function's job
is to REMOVE items from the pending-deletion queue once they no longer match
the library's conditions (e.g. because a user watched them), a false
never_watched=True means a genuinely-watched item keeps looking eligible and
stays queued — it will still get deleted once its grace period elapses.

Fix: the same _abort_scan_no_users guard used by the main scan.
"""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "reevaluate_guard.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()


async def _insert_library(lib_id: str = "lib1") -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, "
            "logic, grace_days, enabled, deletion_unit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (lib_id, "Films", "3", "0", '[{"field": "days_not_watched", "op": "gt", "value": 5}]',
             "AND", 7, 1, "movie", "2024-01-01"),
        )
        await db.commit()


async def _insert_pending(emby_id: str, lib_id: str = "lib1") -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, poster_url, tmdb_id, detected_at, delete_at, status, last_played) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (emby_id, f"Movie {emby_id}", "Movie", lib_id, "Films", f"/m/{emby_id}.mkv",
             "", "", "2024-01-01T00:00:00+00:00", "2024-01-08T00:00:00+00:00", "pending",
             "2026-09-27T00:00:00+00:00"),  # watched recently — should NOT still match "days_not_watched > 5"
        )
        await db.commit()


async def test_reevaluate_aborts_and_removes_nothing_when_no_users():
    await _insert_library()
    await _insert_pending("emby-1")

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock()) as mock_batch,
        patch("backend.scanner._emby_scanner._delete_queue_item", new=AsyncMock()) as mock_delete,
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()),
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        removed = await reevaluate_library_queue("lib1")

    assert removed == 0
    mock_delete.assert_not_awaited()
    # The abort must happen BEFORE any per-user data fetch — no partial work.
    mock_batch.assert_not_awaited()

    # The item must still be pending — a real watch would have made it stop
    # matching the condition and get removed from the queue; the guard must
    # leave it untouched (safe: still gets a fresh reevaluation next run)
    # rather than silently treating it as never-watched.
    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT status FROM media_queue WHERE emby_id='emby-1'")
    assert row["status"] == "pending"


async def test_reevaluate_logs_an_error_when_no_users():
    await _insert_library()
    await _insert_pending("emby-1")

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[])),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        await reevaluate_library_queue("lib1")

    assert mock_log.await_count >= 1
    level, message = mock_log.await_args_list[0].args[:2]
    assert level == "ERROR"


async def test_reevaluate_proceeds_normally_when_users_present():
    """Sanity check: the guard must not interfere with the normal path."""
    await _insert_library()
    await _insert_pending("emby-1")

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock(return_value={})),
        patch("backend.scanner._emby_scanner.sync_emby_collection", new=AsyncMock()),
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        removed = await reevaluate_library_queue("lib1")

    # No real watch data available (mocked to {}) → never_watched stays True from
    # aggregation, but last_played in DB overrides it — condition no longer
    # matches ("days_not_watched > 5" false since watched 2026-09-27) → removed.
    assert removed == 1
