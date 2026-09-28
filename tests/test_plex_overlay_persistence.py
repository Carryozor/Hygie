"""Plex overlay tracking (plex_overlays table, m017) must survive a process
restart and be visible to every worker.

Root cause: plex_collection.py tracked which Plex ratingKeys had the
"deleted in Xj" poster overlay applied in a module-level dict
(`_overlay_applied: dict[str, set]`). That's per-process memory: lost on
every restart, and with WORKERS=2 not shared with whichever worker wins the
scheduler lock on a later run. An item leaving the pending queue was only
detected as "needs its poster restored" by diffing against that dict — if
the dict didn't remember it was ever applied, the restore never fired and
the item's poster stayed permanently overlaid.

These tests exercise sync_plex_overlays() against a real (temp) SQLite DB
end to end: apply → verify persisted → "restart" (nothing to clear — the
fix's whole point is that there is no module state left to lose) → item
leaves the queue → verify restore is attempted and the DB row is cleared.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "plex_overlay_test.db")
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
    await _db_ss.set_setting("plex_overlay_enabled", "true")
    yield db_path


async def _insert_library(server_id: str, lib_id: str = "lib1") -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id) VALUES (?, ?, ?, ?)",
            (lib_id, "Films", "1", server_id),
        )
        await db.commit()


async def _insert_pending_item(emby_id: str, rating_key: str, lib_id: str = "lib1") -> None:
    from backend.db.engine import get_db
    from backend.db.utils import now_utc
    now = now_utc().isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status, plex_rating_key)
               VALUES (?, ?, 'Movie', ?, 'Films', '', 'http://poster', ?, ?, 'pending', ?)""",
            (emby_id, "Test Movie", lib_id, now, now, rating_key),
        )
        await db.commit()


async def _delete_item(emby_id: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue WHERE emby_id=?", (emby_id,))
        await db.commit()


def _fake_plex_server() -> dict:
    return {"id": "srv1", "type": "plex", "enabled": True}


async def test_apply_records_overlay_key_in_db():
    """After applying an overlay, the (server_id, rating_key) pair must be
    persisted — not just held in a process-local structure."""
    await _insert_library("srv1")
    await _insert_pending_item("emby-1", "rk-1")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()) as mock_apply,
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()

    mock_apply.assert_awaited_once()

    from backend.plex_collection import _get_overlay_keys
    assert await _get_overlay_keys("srv1") == {"rk-1"}


async def test_overlay_tracking_has_no_process_local_state_to_lose():
    """The fix removes the module-level dict entirely — overlay tracking
    only exists in the DB, so a process restart (which we can't literally
    perform in-test, but which would reset any module dict to {}) cannot
    lose it. This pins that no such attribute exists anymore."""
    import backend.plex_collection as pc
    assert not hasattr(pc, "_overlay_applied")


async def test_item_leaving_queue_after_simulated_restart_triggers_restore():
    """Apply an overlay, forget any process state (nothing to forget — that
    is the point), have the item leave the pending queue, and verify the
    next sync restores its poster and forgets the DB row."""
    await _insert_library("srv1")
    await _insert_pending_item("emby-1", "rk-1")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()),
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()

    from backend.plex_collection import _get_overlay_keys
    assert await _get_overlay_keys("srv1") == {"rk-1"}

    # "Restart": a fresh call into the module sees only DB state — there is
    # no in-memory dict to reset, which is exactly what this fix guarantees.

    # The item is deleted (watched, expired, whatever) — it leaves the pending queue.
    await _delete_item("emby-1")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._restore_plex_posters", new=AsyncMock()) as mock_restore,
    ):
        await sync_plex_overlays()

    mock_restore.assert_awaited_once()
    restored_keys = mock_restore.await_args.args[1]
    assert list(restored_keys) == ["rk-1"]

    assert await _get_overlay_keys("srv1") == set()


async def test_restore_persists_across_two_separate_calls_even_without_shared_process_state():
    """Two independent calls into sync_plex_overlays (standing in for two
    different worker processes) must agree on overlay state via the DB."""
    await _insert_library("srv1")
    await _insert_pending_item("emby-1", "rk-1")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()),
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()  # "worker A" applies

    await _delete_item("emby-1")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._restore_plex_posters", new=AsyncMock()) as mock_restore,
    ):
        await sync_plex_overlays()  # "worker B" — never saw worker A's in-memory state

    mock_restore.assert_awaited_once()
