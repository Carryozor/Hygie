"""
End-to-end integration test: scan → queue → delete.

Tests the full Hygie workflow with mocked external services:
  1. A library is created with conditions matching a media item
  2. run_scan() queues the eligible item
  3. run_deletion() deletes the item when its grace period has elapsed
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
import backend.db.logs as _db_logs
from backend.db.schema import init_db


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "e2e_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    await init_db()

    # scheduler/deletion/conditions import DB_PATH at module level — patch their local copies too
    import backend.deletion as deletion_mod
    import backend.conditions as cond_mod
    monkeypatch.setattr(deletion_mod, "DB_PATH", db_path)
    monkeypatch.setattr(cond_mod, "DB_PATH", db_path)

    # Seed required settings
    settings = {
        "dry_run": "false",
        "discord_alert_deletion_error": "false",
        "discord_alert_scan_failure": "false",
        "discord_alert_seerr_failure": "false",
        "discord_alert_error_threshold": "0",
        "max_parallel_library_scans": "1",
        "emby_leaving_soon_overlay": "false",
        "qbit_action": "tag_only",
        "qbit_tag": "Supprimé",
        "ui_language": "fr",
        "media_servers": "[]",
        "media_server_type": "emby",
    }
    async with aiosqlite.connect(db_path) as db:
        for k, v in settings.items():
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        await db.commit()

    yield db_path


async def _create_library(db_path: str, *, grace_days: int = 0, deletion_unit: str = "episode") -> str:
    """Insert a test library that matches any item added > 1 day ago."""
    lib_id = str(uuid.uuid4())
    conditions = json.dumps([{"field": "days_since_added", "op": "gt", "value": 1}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id, "Test Library", "emby-lib-001",
                conditions, "AND", grace_days, "[]", 1, deletion_unit,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return lib_id


# ─── Shared mock data ──────────────────────────────────────────────────────────

def _make_emby_items(count: int = 1) -> list[dict]:
    """Return fake Emby item payloads compatible with _evaluate_item()."""
    added_2_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    return [
        {
            "Id": f"emby-item-{i}",
            "Name": f"Test Movie {i}",
            "Type": "Movie",
            "DateCreated": added_2_days_ago,
            "UserData": {"PlayCount": 0, "LastPlayedDate": None},
            "Path": f"/media/movies/test_movie_{i}.mkv",
            "ImageTags": {},
            "ProviderIds": {},
        }
        for i in range(count)
    ]


def _scan_patches(emby_items: list):
    """Return a context-manager stack that stubs all external calls for run_scan()."""
    from contextlib import ExitStack

    stack = ExitStack()
    # Patch functions at the submodule level where they are imported/used
    stack.enter_context(patch(
        "backend.scanner._emby_scanner.get_items_in_library",
        new_callable=AsyncMock,
        return_value=(emby_items, len(emby_items)),
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.get_users",
        new_callable=AsyncMock,
        return_value=[],  # no user data needed — conditions only use DateCreated
    ))
    stack.enter_context(patch(
        "backend.scanner._emby_scanner.get_library_user_data",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_radarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_sonarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_seerr_request_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch("backend.scanner._orchestrator.sync_emby_collection", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner._queue_entry.send_notification", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner._orchestrator._send_pending_notifications", new_callable=AsyncMock))
    # get_client needed by conditions._evaluate_item → get_client_ext_url
    stack.enter_context(patch(
        "backend.conditions.get_client",
        new_callable=AsyncMock,
        return_value=("http://emby:8096", "apikey"),
    ))
    stack.enter_context(patch(
        "backend.conditions.get_client_ext_url",
        new_callable=AsyncMock,
        return_value="",
    ))
    return stack


# ─── Test 1: scan queues an eligible item ─────────────────────────────────────

async def test_scan_queues_eligible_item(isolated_db):
    lib_id = await _create_library(isolated_db, grace_days=7)
    emby_items = _make_emby_items()

    with _scan_patches(emby_items):
        from backend.scheduler import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM media_queue WHERE status='pending'") as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    assert len(rows) == 1, f"Expected 1 queued item, got {len(rows)}"
    assert rows[0]["emby_id"] == "emby-item-0"
    assert rows[0]["library_id"] == lib_id


# ─── Test 2: scan does NOT queue ineligible item ──────────────────────────────

async def test_scan_skips_ineligible_item(isolated_db):
    """Item added today should not match 'days_since_added > 1'."""
    await _create_library(isolated_db)

    fresh_item = {
        "Id": "emby-fresh",
        "Name": "Brand New Movie",
        "Type": "Movie",
        "DateCreated": datetime.now(timezone.utc).isoformat(),
        "UserData": {"PlayCount": 0, "LastPlayedDate": None},
        "Path": "/media/movies/brand_new.mkv",
        "ImageTags": {},
        "ProviderIds": {},
    }

    with _scan_patches([fresh_item]):
        from backend.scheduler import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT COUNT(*) FROM media_queue") as cur:
            count = (await cur.fetchone())[0]

    assert count == 0, f"Expected 0 queued items, got {count}"


# ─── Test 3: deletion fires when grace period has elapsed ─────────────────────

async def test_delete_fires_after_grace(isolated_db):
    lib_id = await _create_library(isolated_db, grace_days=0)

    emby_id = "emby-overdue-001"
    delete_at = datetime.now(timezone.utc) - timedelta(hours=1)
    async with aiosqlite.connect(isolated_db) as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (emby_id, "Overdue Movie", "Movie", lib_id, "Test Library",
             "/media/movies/overdue.mkv", "",
             datetime.now(timezone.utc).isoformat(), delete_at.isoformat()),
        )
        await db.commit()

    emby_deleted = []

    async def _capture_delete(emby_id_, server_id=None):
        emby_deleted.append(emby_id_)

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock, side_effect=_capture_delete),
        patch("backend.deletion._delete_from_arr", new_callable=AsyncMock),
        patch("backend.deletion._delete_from_seerr", new_callable=AsyncMock),
        patch("backend.deletion._find_torrent_hash", new_callable=AsyncMock, return_value=None),
        patch("backend.deletion.get_client", new_callable=AsyncMock, return_value=("http://emby:8096", "apikey")),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
    ):
        from backend.scheduler import run_deletion
        await run_deletion()

    async with aiosqlite.connect(isolated_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)) as cur:
            row = dict(await cur.fetchone())

    assert row["status"] == "deleted", f"Expected 'deleted', got '{row['status']}'"
    assert emby_id in emby_deleted, "delete_item (Emby) was not called"


# ─── Test 4: deletion is skipped while grace period is active ─────────────────

async def test_delete_skips_during_grace(isolated_db):
    lib_id = await _create_library(isolated_db, grace_days=7)

    emby_id = "emby-grace-001"
    delete_at = datetime.now(timezone.utc) + timedelta(days=5)
    async with aiosqlite.connect(isolated_db) as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (emby_id, "Grace Period Movie", "Movie", lib_id, "Test Library",
             "/media/movies/grace.mkv", "",
             datetime.now(timezone.utc).isoformat(), delete_at.isoformat()),
        )
        await db.commit()

    emby_deleted = []

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock,
              side_effect=lambda eid, **kw: emby_deleted.append(eid)),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
    ):
        from backend.scheduler import run_deletion
        await run_deletion()

    assert emby_id not in emby_deleted, "Deletion should be skipped during grace period"

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)) as cur:
            row = await cur.fetchone()
    assert row[0] == "pending", "Status should remain 'pending' during grace period"


# ─── Test 5: full workflow scan → queue → delete ──────────────────────────────

async def test_full_scan_then_delete(isolated_db):
    """grace_days=0: scan queues item, deletion removes it immediately."""
    await _create_library(isolated_db, grace_days=0)
    emby_items = _make_emby_items()
    emby_id = emby_items[0]["Id"]

    # ── Phase 1: Scan ──
    with _scan_patches(emby_items):
        from backend.scheduler import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)) as cur:
            queued = dict(await cur.fetchone())
    assert queued["status"] == "pending"

    # Push delete_at 1 minute into the past to guarantee it's overdue
    async with aiosqlite.connect(isolated_db) as db:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        await db.execute("UPDATE media_queue SET delete_at=? WHERE emby_id=?", (past, emby_id))
        await db.commit()

    # ── Phase 2: Delete ──
    emby_deleted = []

    async def _capture_delete(eid, server_id=None):
        emby_deleted.append(eid)

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock, side_effect=_capture_delete),
        patch("backend.deletion._delete_from_arr", new_callable=AsyncMock),
        patch("backend.deletion._delete_from_seerr", new_callable=AsyncMock),
        patch("backend.deletion._find_torrent_hash", new_callable=AsyncMock, return_value=None),
        patch("backend.deletion.get_client", new_callable=AsyncMock, return_value=("http://emby:8096", "apikey")),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
    ):
        from backend.scheduler import run_deletion
        await run_deletion()

    async with aiosqlite.connect(isolated_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)) as cur:
            row = dict(await cur.fetchone())

    assert row["status"] == "deleted", f"Expected 'deleted', got '{row['status']}'"
    assert emby_id in emby_deleted, "delete_item (Emby) was not called"


# ─── Test 6: dry_run prevents actual deletion ─────────────────────────────────

async def test_dry_run_prevents_deletion(isolated_db):
    async with aiosqlite.connect(isolated_db) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dry_run', 'true')")
        await db.commit()

    lib_id = await _create_library(isolated_db, grace_days=0)
    emby_id = "emby-dryrun-001"
    async with aiosqlite.connect(isolated_db) as db:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (emby_id, "DryRun Movie", "Movie", lib_id, "Test Library",
             "/media/dry.mkv", "", datetime.now(timezone.utc).isoformat(), past),
        )
        await db.commit()

    emby_deleted = []

    with (
        patch("backend.deletion.delete_item", new_callable=AsyncMock,
              side_effect=lambda eid, **kw: emby_deleted.append(eid)),
        patch("backend.deletion.send_notification", new_callable=AsyncMock),
        patch("backend.deletion.sync_emby_collection", new_callable=AsyncMock),
        patch("backend.deletion._send_pending_notifications", new_callable=AsyncMock),
    ):
        from backend.scheduler import run_deletion
        await run_deletion()

    assert emby_id not in emby_deleted, "dry_run must prevent delete_item from being called"

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT status FROM media_queue WHERE emby_id=?", (emby_id,)) as cur:
            row = await cur.fetchone()
    assert row[0] == "deleted", "dry_run should still mark item as 'deleted' in DB"
