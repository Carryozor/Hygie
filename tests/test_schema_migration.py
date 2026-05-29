"""Tests for schema migration: notifications table and expert_rules table."""
import pytest
import pytest_asyncio
import aiosqlite
from backend.db.schema import init_db


@pytest_asyncio.fixture
async def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "migration.db")
    monkeypatch.setattr("backend.db.schema.DB_PATH", path)
    monkeypatch.setattr("backend.db.utils.DB_PATH", path)
    await init_db()
    return path


@pytest.mark.asyncio
async def test_notifications_table_created(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_notifications_table_schema(db_path):
    """Verify notifications table has the expected columns."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(notifications)") as cur:
            cols = {r[1] async for r in cur}
    assert {"id", "media_id", "threshold", "sent_at"}.issubset(cols)


@pytest.mark.asyncio
async def test_notifications_index_created(db_path):
    """Verify idx_notif_media index exists."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_notif_media'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_expert_rules_table_created(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='expert_rules'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_notifications_insert_and_query(db_path):
    """Verify notifications table accepts inserts and deduplication with INSERT OR IGNORE."""
    async with aiosqlite.connect(db_path) as db:
        # Insert a media_queue row first (FK constraint)
        await db.execute(
            """INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             detected_at, delete_at, status)
            VALUES ('emby-test-1', 'Test Movie', 'Movie', 'lib1', 'Movies', '/path',
                    '2026-01-01T00:00:00Z', '2026-06-01T00:00:00Z', 'pending')"""
        )
        await db.commit()
        async with db.execute("SELECT id FROM media_queue WHERE emby_id='emby-test-1'") as cur:
            media_row = await cur.fetchone()
        media_id = media_row[0]

        # Insert a notification
        await db.execute(
            "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
            (media_id, "7d")
        )
        await db.commit()

        # Check it was inserted
        async with db.execute(
            "SELECT 1 FROM notifications WHERE media_id=? AND threshold=?", (media_id, "7d")
        ) as cur:
            row = await cur.fetchone()
        assert row is not None

        # Verify not-sent threshold returns None
        async with db.execute(
            "SELECT 1 FROM notifications WHERE media_id=? AND threshold=?", (media_id, "1d")
        ) as cur:
            row = await cur.fetchone()
        assert row is None

        # Verify INSERT OR IGNORE prevents duplicates
        await db.execute(
            "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
            (media_id, "7d")
        )
        await db.commit()
        async with db.execute(
            "SELECT COUNT(*) FROM notifications WHERE media_id=? AND threshold=?",
            (media_id, "7d")
        ) as cur:
            count_row = await cur.fetchone()
        assert count_row[0] == 1


@pytest.mark.asyncio
async def test_notifications_cascade_delete(db_path):
    """Verify notifications are deleted when media_queue row is deleted (ON DELETE CASCADE)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             detected_at, delete_at, status)
            VALUES ('emby-cascade-1', 'Cascade Movie', 'Movie', 'lib1', 'Movies', '/path',
                    '2026-01-01T00:00:00Z', '2026-06-01T00:00:00Z', 'pending')"""
        )
        await db.commit()
        async with db.execute("SELECT id FROM media_queue WHERE emby_id='emby-cascade-1'") as cur:
            media_row = await cur.fetchone()
        media_id = media_row[0]

        await db.execute(
            "INSERT INTO notifications (media_id, threshold) VALUES (?,?)",
            (media_id, "30d")
        )
        await db.commit()

        # Delete the media row
        await db.execute("DELETE FROM media_queue WHERE id=?", (media_id,))
        await db.commit()

        # Notification should be gone
        async with db.execute(
            "SELECT 1 FROM notifications WHERE media_id=?", (media_id,)
        ) as cur:
            row = await cur.fetchone()
        assert row is None
