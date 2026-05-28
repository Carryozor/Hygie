"""Integration tests for backend/db/repositories.py."""
import pytest
import pytest_asyncio
import aiosqlite
import backend.db.schema as _db_schema
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
from backend.db.repositories import (
    get_pending_queue,
    get_queued_and_ignored_ids,
    get_enabled_libraries,
    insert_queue_entry,
    mark_notified_detected,
    update_queue_status,
)

_ENTRY = {
    "emby_id": "e1", "title": "Test Movie", "media_type": "movie",
    "library_id": "lib1", "library_name": "Films",
    "file_path": "/srv/movies/test.mkv",
    "poster_url": "", "tmdb_id": "123",
    "seerr_id": None, "seerr_user_id": None, "seerr_username": None,
    "seerr_request_url": None, "radarr_id": None, "sonarr_id": None,
    "sonarr_series_id": None, "season_number": None,
    "detected_at": "2026-01-01T00:00:00+00:00",
    "delete_at": "2020-01-01T00:00:00+00:00",  # past — eligible for deletion
    "added_date": None, "last_played": None,
}


@pytest_asyncio.fixture
async def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_ss, "DB_PATH", path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


@pytest.mark.asyncio
async def test_insert_and_get_pending_queue(db_path):
    await insert_queue_entry(_ENTRY, db_path=db_path)
    rows = await get_pending_queue(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["emby_id"] == "e1"
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_queued_and_ignored_ids(db_path):
    await insert_queue_entry(_ENTRY, db_path=db_path)
    queued, ignored = await get_queued_and_ignored_ids(db_path=db_path)
    assert "e1" in queued
    assert "e1" not in ignored


@pytest.mark.asyncio
async def test_mark_notified_detected(db_path):
    await insert_queue_entry(_ENTRY, db_path=db_path)
    await mark_notified_detected("e1", db_path=db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT notified_detected FROM media_queue WHERE emby_id=?", ("e1",)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_update_queue_status(db_path):
    await insert_queue_entry(_ENTRY, db_path=db_path)
    rows = await get_pending_queue(db_path=db_path)
    item_id = rows[0]["id"]
    await update_queue_status(item_id, "deleted", db_path=db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT status, notified_now FROM media_queue WHERE id=?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "deleted"
    assert row[1] == 1


@pytest.mark.asyncio
async def test_get_enabled_libraries(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO libraries (name, emby_library_id, server_id, enabled) VALUES (?, ?, ?, ?)",
            ("Films", "lib1", "0", 1),
        )
        await db.commit()
    libs = await get_enabled_libraries("0", db_path=db_path)
    assert len(libs) == 1
    assert libs[0]["name"] == "Films"


@pytest.mark.asyncio
async def test_get_enabled_libraries_filters_disabled(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "INSERT INTO libraries (name, emby_library_id, server_id, enabled) VALUES (?, ?, ?, ?)",
            [("Films", "lib1", "0", 1), ("Séries", "lib2", "0", 0)],
        )
        await db.commit()
    libs = await get_enabled_libraries("0", db_path=db_path)
    assert len(libs) == 1
    assert libs[0]["name"] == "Films"
