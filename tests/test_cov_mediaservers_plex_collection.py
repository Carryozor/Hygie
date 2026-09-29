"""Coverage tests for backend/plex_collection.py — the Plex "leaving soon"
poster overlay sync. The apply/restore paths write real poster bytes to a
(mocked) Plex server; a bug here either fails to warn users a title is about
to be deleted, or leaves a stale overlay on a poster forever.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "plex_collection_cov.db")
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


async def _insert_library(server_id: str, lib_id: str = "lib1") -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id) VALUES (?, ?, ?, ?)",
            (lib_id, "Films", "1", server_id),
        )
        await db.commit()


async def _insert_pending_item(
    emby_id: str, rating_key: str, lib_id: str = "lib1",
    poster_url: str = "http://poster.example/x.jpg", delete_at: str = "",
) -> None:
    from backend.db.engine import get_db
    from backend.db.utils import now_utc
    from datetime import timedelta
    now = now_utc().isoformat()
    if not delete_at:
        delete_at = (now_utc() + timedelta(days=3)).isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status, plex_rating_key)
               VALUES (?, ?, 'Movie', ?, 'Films', '', ?, ?, ?, 'pending', ?)""",
            (emby_id, "Test Movie", lib_id, poster_url, now, delete_at, rating_key),
        )
        await db.commit()


def _fake_plex_server(server_id: str = "srv1") -> dict:
    return {"id": server_id, "type": "plex", "enabled": True}


# ─── sync_plex_overlays: early-return branches ─────────────────────────────────

async def test_sync_plex_overlays_noop_when_disabled():
    await _db_ss.set_setting("plex_overlay_enabled", "false")
    with patch("backend.plex_collection.get_media_servers", new=AsyncMock()) as mock_get_servers:
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()
    mock_get_servers.assert_not_called()


async def test_sync_plex_overlays_noop_when_no_plex_servers_configured():
    with patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[
        {"id": "0", "type": "emby", "enabled": True},
    ])):
        from backend.plex_collection import sync_plex_overlays
        # Must not raise despite no Plex servers present
        await sync_plex_overlays()


async def test_sync_plex_overlays_skips_disabled_plex_servers():
    """A disabled Plex server must be excluded from the per-server loop even
    when an enabled sibling server has libraries and pending items that keep
    the sync past the early-return checks — proves the 'enabled' filter
    itself, not just an earlier no-libraries/no-servers short-circuit."""
    await _insert_library("srv1", lib_id="lib-disabled")
    await _insert_pending_item("emby-1", "rk-1", lib_id="lib-disabled")
    await _insert_library("srv2", lib_id="lib-enabled")
    await _insert_pending_item("emby-2", "rk-2", lib_id="lib-enabled")

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[
            {"id": "srv1", "type": "plex", "enabled": False},
            {"id": "srv2", "type": "plex", "enabled": True},
        ])),
        patch("backend.plex_collection.build_plex_client") as mock_build,
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()),
    ):
        mock_build.return_value = MagicMock()
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()

    called_server_ids = [call.args[0]["id"] for call in mock_build.call_args_list]
    assert called_server_ids == ["srv2"]


async def test_sync_plex_overlays_noop_when_plex_server_has_no_matching_libraries():
    """A configured Plex server with zero libraries synced must not crash and
    must not attempt any overlay work."""
    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()) as mock_apply,
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()
    mock_apply.assert_not_called()


async def test_sync_plex_overlays_skips_server_when_client_cannot_be_built():
    await _insert_library("srv1")
    await _insert_pending_item("emby-1", "rk-1")
    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=None),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()) as mock_apply,
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()
    mock_apply.assert_not_called()


async def test_sync_plex_overlays_no_pending_items_skips_apply_but_still_restores():
    """When every previously-overlaid item has left the queue, restore must
    still run even though there's nothing left to apply."""
    await _insert_library("srv1")
    # Pre-seed an overlay-tracking row with no matching pending item.
    from backend.db.engine import get_db
    from backend.db.utils import now_utc
    async with get_db() as db:
        await db.execute(
            "INSERT INTO plex_overlays (server_id, rating_key, applied_at) VALUES (?, ?, ?)",
            ("srv1", "rk-old", now_utc().isoformat()),
        )
        await db.commit()

    with (
        patch("backend.plex_collection.get_media_servers", new=AsyncMock(return_value=[_fake_plex_server()])),
        patch("backend.plex_collection.build_plex_client", return_value=MagicMock()),
        patch("backend.plex_collection._apply_plex_overlays", new=AsyncMock()) as mock_apply,
        patch("backend.plex_collection._restore_plex_posters", new=AsyncMock()) as mock_restore,
    ):
        from backend.plex_collection import sync_plex_overlays
        await sync_plex_overlays()

    mock_apply.assert_not_called()
    mock_restore.assert_awaited_once()
    assert list(mock_restore.await_args.args[1]) == ["rk-old"]


# ─── _add_overlay_keys / _remove_overlay_keys — empty-input no-ops ────────────

async def test_add_overlay_keys_noop_on_empty_set_does_not_touch_db():
    from backend.plex_collection import _add_overlay_keys, _get_overlay_keys
    await _add_overlay_keys("srv1", set())
    assert await _get_overlay_keys("srv1") == set()


async def test_remove_overlay_keys_noop_on_empty_list_does_not_touch_db():
    from backend.plex_collection import _add_overlay_keys, _remove_overlay_keys, _get_overlay_keys
    await _add_overlay_keys("srv1", {"rk-1"})
    await _remove_overlay_keys("srv1", [])
    # Nothing removed since the call was a no-op
    assert await _get_overlay_keys("srv1") == {"rk-1"}


# ─── _apply_plex_overlays (real body) ──────────────────────────────────────────

async def test_apply_plex_overlays_uploads_poster_with_computed_days_left(monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    from backend.plex_collection import _apply_plex_overlays

    delete_at = (now_utc() + timedelta(days=5)).isoformat()
    item = {
        "plex_rating_key": "rk-1", "emby_id": "e1", "delete_at": delete_at,
        "poster_url": "http://poster.example/x.jpg", "title": "Test Movie",
    }

    monkeypatch.setattr(
        "backend.plex_collection.guarded_image_get",
        AsyncMock(return_value=b"original-bytes"),
    )
    monkeypatch.setattr(
        "backend.plex_collection._overlay_poster",
        AsyncMock(return_value=b"overlaid-bytes"),
    )
    mock_plex = AsyncMock()
    mock_plex.upload_poster.return_value = True

    await _apply_plex_overlays(mock_plex, [item], "fr")

    mock_plex.upload_poster.assert_awaited_once_with("rk-1", b"overlaid-bytes")


async def test_apply_plex_overlays_falls_back_to_emby_id_when_no_rating_key(monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    from backend.plex_collection import _apply_plex_overlays

    item = {
        "plex_rating_key": "", "emby_id": "fallback-id",
        "delete_at": (now_utc() + timedelta(days=1)).isoformat(),
        "poster_url": "http://poster.example/x.jpg", "title": "T",
    }
    monkeypatch.setattr("backend.plex_collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.plex_collection._overlay_poster", AsyncMock(return_value=b"mod"))
    mock_plex = AsyncMock()
    mock_plex.upload_poster.return_value = True

    await _apply_plex_overlays(mock_plex, [item], "fr")
    mock_plex.upload_poster.assert_awaited_once_with("fallback-id", b"mod")


async def test_apply_plex_overlays_skips_item_with_unparsable_delete_at(monkeypatch):
    from backend.plex_collection import _apply_plex_overlays
    item = {"plex_rating_key": "rk-1", "emby_id": "e1", "delete_at": "not-a-date", "poster_url": "", "title": "T"}
    mock_plex = AsyncMock()
    await _apply_plex_overlays(mock_plex, [item], "fr")
    mock_plex.upload_poster.assert_not_called()


async def test_apply_plex_overlays_skips_when_poster_url_not_http(monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    from backend.plex_collection import _apply_plex_overlays
    item = {
        "plex_rating_key": "rk-1", "emby_id": "e1",
        "delete_at": (now_utc() + timedelta(days=1)).isoformat(),
        "poster_url": "", "title": "T",
    }
    mock_plex = AsyncMock()
    await _apply_plex_overlays(mock_plex, [item], "fr")
    mock_plex.upload_poster.assert_not_called()


async def test_apply_plex_overlays_skips_when_overlay_rendering_fails(monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    from backend.plex_collection import _apply_plex_overlays
    item = {
        "plex_rating_key": "rk-1", "emby_id": "e1",
        "delete_at": (now_utc() + timedelta(days=1)).isoformat(),
        "poster_url": "http://p/x.jpg", "title": "T",
    }
    monkeypatch.setattr("backend.plex_collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.plex_collection._overlay_poster", AsyncMock(return_value=None))
    mock_plex = AsyncMock()
    await _apply_plex_overlays(mock_plex, [item], "fr")
    mock_plex.upload_poster.assert_not_called()


async def test_apply_plex_overlays_swallows_exception_for_one_item_without_failing_others(monkeypatch):
    """One item's overlay error must not prevent the rest of the batch from
    being processed."""
    from datetime import timedelta
    from backend.db.utils import now_utc
    from backend.plex_collection import _apply_plex_overlays

    delete_at = (now_utc() + timedelta(days=1)).isoformat()
    good = {"plex_rating_key": "rk-good", "emby_id": "e-good", "delete_at": delete_at,
            "poster_url": "http://p/good.jpg", "title": "Good"}
    bad = {"plex_rating_key": "rk-bad", "emby_id": "e-bad", "delete_at": delete_at,
           "poster_url": "http://p/bad.jpg", "title": "Bad"}

    async def fake_guarded_get(url, timeout=20):
        if "bad" in url:
            raise RuntimeError("network blew up")
        return b"orig-bytes"

    monkeypatch.setattr("backend.plex_collection.guarded_image_get", fake_guarded_get)
    monkeypatch.setattr("backend.plex_collection._overlay_poster", AsyncMock(return_value=b"mod"))
    mock_plex = AsyncMock()
    mock_plex.upload_poster.return_value = True

    await _apply_plex_overlays(mock_plex, [good, bad], "fr")

    mock_plex.upload_poster.assert_awaited_once_with("rk-good", b"mod")


# ─── _restore_plex_posters (real body) ─────────────────────────────────────────

async def test_restore_plex_posters_calls_restore_for_each_key():
    from backend.plex_collection import _restore_plex_posters
    mock_plex = AsyncMock()
    mock_plex.restore_poster.return_value = True

    await _restore_plex_posters(mock_plex, ["rk-1", "rk-2"])

    assert mock_plex.restore_poster.await_count == 2
    mock_plex.restore_poster.assert_any_await("rk-1")
    mock_plex.restore_poster.assert_any_await("rk-2")


async def test_restore_plex_posters_continues_after_one_failure():
    from backend.plex_collection import _restore_plex_posters
    mock_plex = AsyncMock()
    mock_plex.restore_poster.side_effect = [RuntimeError("boom"), True]

    # Must not raise — one failed restore should not abort the rest.
    await _restore_plex_posters(mock_plex, ["rk-bad", "rk-good"])

    assert mock_plex.restore_poster.await_count == 2
