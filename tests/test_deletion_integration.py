"""Integration tests for run_deletion — real SQLite (tmp_path), mocked external services."""
import pytest
import aiosqlite
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema
import backend.db.logs as _db_logs
import backend.deletion as _deletion_mod

from backend.db.schema import init_db
from backend.db.utils import STATUS_PENDING


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def deletion_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for deletion integration tests, with required settings seeded."""
    db_path = str(tmp_path / "deletion_test.db")

    # Patch DB_PATH in every module that uses it directly
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    monkeypatch.setattr(_deletion_mod, "DB_PATH", db_path)

    # Reset settings cache so it re-reads from the fresh DB
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0

    await init_db()

    # Seed the minimal settings needed by run_deletion
    settings = {
        "dry_run": "false",
        "discord_alert_deletion_error": "false",
        "discord_alert_error_threshold": "3",
        "qbit_action": "tag_only",
        "qbit_tag": "Supprimé",
    }
    async with aiosqlite.connect(db_path) as db:
        for k, v in settings.items():
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        await db.commit()

    yield db_path


# ─── Helper: seed a library and a media_queue entry eligible for immediate deletion ──────────

_TEST_LIBRARY_ID = "lib-test-001"


async def _seed_library(db_path: str) -> str:
    """Insert a minimal test library row and return its id."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _TEST_LIBRARY_ID,
                "Test Library",
                "emby-lib-001",
                "[]",
                "AND",
                0,
                "[]",
                1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return _TEST_LIBRARY_ID


async def _seed_queue_item(db_path: str, emby_id: str = "EMB-001") -> None:
    """Insert a pending queue entry with delete_at in the past."""
    lib_id = await _seed_library(db_path)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                emby_id,
                "The Batman",
                "Movie",
                lib_id,
                "Test Library",
                "/movies/batman.mkv",
                "",
                now,
                past,
                STATUS_PENDING,
            ),
        )
        await db.commit()


# ─── Test 1: run_deletion marks an overdue item as "deleted" ──────────────────

async def test_deletion_marks_item_deleted(deletion_db):
    """run_deletion() must set status='deleted' for a past-due pending item."""
    emby_id = "EMB-001"
    await _seed_queue_item(deletion_db, emby_id)

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
        patch("backend.deletion._delete_from_arr", new_callable=AsyncMock),
        patch("backend.deletion._delete_from_seerr", new_callable=AsyncMock),
        patch("backend.deletion._find_torrent_hash", new_callable=AsyncMock, return_value=None),
        patch("backend.deletion.get_client", new_callable=AsyncMock, return_value=("http://emby:8096", "apikey")),
        patch("backend.deletion.send_alert", new_callable=AsyncMock),
    ):
        from backend.deletion import run_deletion
        await run_deletion()

    async with aiosqlite.connect(deletion_db) as db:
        async with db.execute(
            "SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)
        ) as cur:
            row = await cur.fetchone()

    assert row is not None, "Queue entry not found in DB"
    assert row[0] == "deleted", f"Expected status='deleted', got '{row[0]}'"


# ─── Test 2: dry_run prevents delete_item from being called ───────────────────

async def test_dry_run_does_not_delete(deletion_db):
    """When dry_run is active, delete_item must never be called."""
    emby_id = "EMB-DRY-001"
    await _seed_queue_item(deletion_db, emby_id)

    mock_delete = AsyncMock()

    # Patch get_bool_setting so that dry_run returns True regardless of DB
    async def _fake_bool_setting(key: str, default: bool = False) -> bool:
        if key == "dry_run":
            return True
        return default

    with (
        patch("backend.deletion.get_bool_setting", side_effect=_fake_bool_setting),
        patch("backend.deletion.delete_item", mock_delete),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
        patch("backend.deletion.send_alert", new_callable=AsyncMock),
    ):
        from backend.deletion import run_deletion
        await run_deletion()

    mock_delete.assert_not_called()
