"""Basic tests for deletion.py — the most critical module (deletes real media files)."""
import pytest
from datetime import datetime, timezone, timedelta

import backend.db.engine as _db_engine
import backend.db.utils as _db_utils
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss

from backend.db.schema import init_db
from backend.db.engine import get_db


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    """Each test gets its own temporary DB to prevent state leakage."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


@pytest.mark.asyncio
async def test_pending_queue_empty_on_fresh_db(fresh_db):
    """Fresh DB has no pending deletions."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT * FROM media_queue WHERE status='pending'"
        )
    assert rows == []


@pytest.mark.asyncio
async def test_queue_entry_visible_before_delete_at(fresh_db):
    """Items with delete_at in the future are NOT in pending deletion list."""
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("id1", "Test Movie", "Movie", "lib1", "Library", "/files/test.mkv",
             datetime.now(timezone.utc).isoformat(), future, "pending"),
        )
        await db.commit()
        rows = await db.fetch_all(
            "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
    assert rows == []


@pytest.mark.asyncio
async def test_queue_entry_picked_up_after_delete_at(fresh_db):
    """Items with delete_at in the past ARE in pending deletion list."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("id2", "Old Movie", "Movie", "lib1", "Library", "/files/old.mkv",
             past, past, "pending"),
        )
        await db.commit()
        rows = await db.fetch_all(
            "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
    assert len(rows) == 1
    assert rows[0]["emby_id"] == "id2"


@pytest.mark.asyncio
async def test_status_update_to_deleted(fresh_db):
    """Marking an item as deleted updates its status correctly."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("id3", "Movie3", "Movie", "lib1", "Library", "/f/m.mkv", past, past, "pending"),
        )
        await db.commit()
        await db.execute(
            "UPDATE media_queue SET status='deleted' WHERE emby_id='id3'"
        )
        await db.commit()
        row = await db.fetch_one("SELECT status FROM media_queue WHERE emby_id='id3'")
    assert row["status"] == "deleted"


@pytest.mark.asyncio
async def test_notification_dedup_constraint(fresh_db):
    """Cannot insert duplicate notification for same media + threshold."""
    import aiosqlite
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("id4", "Movie4", "Movie", "lib1", "Library", "/f/m4.mkv", past, past, "pending"),
        )
        await db.commit()
        row = await db.fetch_one("SELECT id FROM media_queue WHERE emby_id='id4'")
        media_id = row["id"]
        await db.execute(
            "INSERT INTO notifications (media_id, threshold, sent_at) VALUES (?,?,?)",
            (media_id, "7d", past),
        )
        await db.commit()
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO notifications (media_id, threshold, sent_at) VALUES (?,?,?)",
                (media_id, "7d", past),
            )
            await db.commit()


@pytest.mark.asyncio
async def test_ignored_media_excludes_from_deletion(fresh_db):
    """Items in ignored_media should not appear in deletion queue."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        # Insert in both tables
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("id5", "Movie5", "Movie", "lib1", "Library", "/f/m5.mkv", past, past, "pending"),
        )
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, ignored_at) VALUES (?,?,?)",
            ("id5", "Movie5", past),
        )
        await db.commit()
        # Query simulating deletion logic that excludes ignored items
        rows = await db.fetch_all(
            """SELECT mq.* FROM media_queue mq
               LEFT JOIN ignored_media im ON im.emby_id = mq.emby_id
               WHERE mq.status='pending' AND mq.delete_at <= ?
               AND im.emby_id IS NULL""",
            (datetime.now(timezone.utc).isoformat(),),
        )
    assert all(r["emby_id"] != "id5" for r in rows)
