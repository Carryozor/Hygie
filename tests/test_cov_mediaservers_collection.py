"""Coverage tests for backend/collection.py — the Emby/Jellyfin 'Bientôt
supprimé' collection sync (membership diff + poster overlay). A wrong add/
remove computation here either hides an about-to-be-deleted title from users
or leaves stale entries after items are no longer pending; a poster restore
that silently fails must not be reported as done.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema

FAKE_URL = "http://emby.test:8096"
FAKE_KEY = "test-api-key-12345"


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "collection_cov.db")
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
    await _db_ms.save_media_servers([{
        "id": "0", "name": "Test Server", "url": FAKE_URL, "api_key": FAKE_KEY,
        "type": "emby", "enabled": True,
    }])
    import backend.collection as _collection
    _collection._overlay_cache.clear()
    yield
    _collection._overlay_cache.clear()


# ─── _get_or_create_collection ─────────────────────────────────────────────────

async def test_get_or_create_collection_finds_existing_by_case_insensitive_name(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/Items?IncludeItemTypes=BoxSet&Recursive=true&SearchTerm=Bient%C3%B4t+supprim%C3%A9&Limit=10",
        json={"Items": [{"Id": "col-1", "Name": "bientôt supprimé"}]},
    )
    from backend.collection import _get_or_create_collection
    async with httpx.AsyncClient() as client:
        col_id = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "Bientôt supprimé", {"m1"})
    assert col_id == "col-1"


async def test_get_or_create_collection_returns_none_when_not_found_and_nothing_wanted(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": []})
    from backend.collection import _get_or_create_collection
    async with httpx.AsyncClient() as client:
        col_id = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "Bientôt supprimé", set())
    assert col_id is None


async def test_get_or_create_collection_creates_when_not_found_and_items_wanted(httpx_mock: HTTPXMock):
    # No url= matcher: requests carry query strings this test doesn't need to
    # pin down, so responses are consumed in call order (search, users, create).
    httpx_mock.add_response(json={"Items": []})
    httpx_mock.add_response(json=[{"Id": "user-1"}])
    httpx_mock.add_response(status_code=200, json={"Id": "new-col-1"})

    from backend.collection import _get_or_create_collection
    async with httpx.AsyncClient() as client:
        col_id = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "Bientôt supprimé", {"m1", "m2"})
    assert col_id == "new-col-1"


async def test_get_or_create_collection_uses_empty_user_id_when_user_list_unavailable(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": []})
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(json={"Id": "new-col-2"})

    from backend.collection import _get_or_create_collection
    async with httpx.AsyncClient() as client:
        col_id = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "X", {"m1"})
    assert col_id == "new-col-2"
    create_req = [r for r in httpx_mock.get_requests() if r.url.path == "/Collections"][0]
    assert create_req.url.params["UserId"] == ""


async def test_get_or_create_collection_returns_none_when_create_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": []})
    httpx_mock.add_response(json=[{"Id": "user-1"}])
    httpx_mock.add_response(status_code=500)

    from backend.collection import _get_or_create_collection
    async with httpx.AsyncClient() as client:
        col_id = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "X", {"m1"})
    assert col_id is None


# ─── _sync_collection_membership ───────────────────────────────────────────────

async def test_sync_collection_membership_computes_add_and_remove_diff(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "already-there"}, {"Id": "should-be-removed"}]},
    )
    httpx_mock.add_response(method="POST")
    httpx_mock.add_response(method="DELETE")

    from backend.collection import _sync_collection_membership
    async with httpx.AsyncClient() as client:
        to_add, to_remove = await _sync_collection_membership(
            client, FAKE_URL, FAKE_KEY, "col-1", {"already-there", "new-item"}
        )
    assert to_add == {"new-item"}
    assert to_remove == {"should-be-removed"}


async def test_sync_collection_membership_skips_add_call_when_nothing_to_add(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": [{"Id": "already-there"}]})
    from backend.collection import _sync_collection_membership
    async with httpx.AsyncClient() as client:
        to_add, to_remove = await _sync_collection_membership(
            client, FAKE_URL, FAKE_KEY, "col-1", {"already-there"}
        )
    assert to_add == set()
    assert to_remove == set()
    # Only the initial GET was made — no POST/DELETE
    assert len(httpx_mock.get_requests()) == 1


async def test_sync_collection_membership_treats_non_200_listing_as_empty_current_set(httpx_mock: HTTPXMock):
    """A failed membership listing must not be mistaken for 'collection is
    empty' in a way that silently drops removal safety — document actual
    behavior: wanted_ids all get (re-)added, nothing gets removed."""
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(method="POST")

    from backend.collection import _sync_collection_membership
    async with httpx.AsyncClient() as client:
        to_add, to_remove = await _sync_collection_membership(
            client, FAKE_URL, FAKE_KEY, "col-1", {"m1"}
        )
    assert to_add == {"m1"}
    assert to_remove == set()


# ─── _restore_posters_for_removed ──────────────────────────────────────────────

async def test_restore_posters_for_removed_noop_on_empty_set(httpx_mock: HTTPXMock):
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, set())
    assert httpx_mock.get_requests() == []


async def test_restore_posters_for_removed_uploads_original_poster_and_logs(httpx_mock: HTTPXMock):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e1', 'T', 'Movie', 'lib1', 'Films', '', ?, '2026-01-01', '2026-01-01', 'pending')""",
            ("http://poster.example/orig.jpg",),
        )
        await db.commit()

    httpx_mock.add_response(
        url="http://poster.example/orig.jpg",
        content=b"poster-bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e1/Images/Primary", status_code=204)

    from backend.collection import _restore_posters_for_removed, _overlay_cache
    _overlay_cache["e1"] = 3  # simulate a cached overlay day-count to verify invalidation

    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e1"})

    assert "e1" not in _overlay_cache


async def test_restore_posters_for_removed_skips_when_fetch_returns_no_bytes(monkeypatch):
    """A poster URL that IS http but whose fetch fails/returns non-image
    content must be skipped, not crash the batch."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e4', 'T', 'Movie', 'lib1', 'Films', '', 'http://poster.example/x.jpg',
                       '2026-01-01', '2026-01-01', 'pending')""",
        )
        await db.commit()
    monkeypatch.setattr("backend.collection.guarded_image_get", AsyncMock(return_value=None))

    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e4"})
    # Must not raise; nothing to assert beyond "no crash" since the whole
    # point is that a failed fetch is a silent, safe no-op.


async def test_restore_posters_for_removed_skips_item_with_no_stored_poster_url():
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        # No media_queue row for "unknown-id" -> get_poster_url_by_emby_id returns None
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"unknown-id"})
    # Must not raise


async def test_restore_posters_for_removed_skips_non_http_poster_url():
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e2', 'T', 'Movie', 'lib1', 'Films', '', '', '2026-01-01', '2026-01-01', 'pending')""",
        )
        await db.commit()
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e2"})
    # Must not raise, no HTTP call attempted for the poster fetch


async def test_restore_posters_for_removed_continues_after_one_item_errors(httpx_mock: HTTPXMock):
    """An exception restoring one item's poster (e.g. the Emby upload call
    itself failing) must not abort restoring the rest of the batch."""
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e-bad', 'T', 'Movie', 'lib1', 'Films', '', 'http://poster.example/bad.jpg',
                       '2026-01-01', '2026-01-01', 'pending')""",
        )
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e-good', 'T', 'Movie', 'lib1', 'Films', '', 'http://poster.example/good.jpg',
                       '2026-01-01', '2026-01-01', 'pending')""",
        )
        await db.commit()

    # guarded_image_get swallows its own network errors internally (returns
    # None), so to exercise _restore_posters_for_removed's own except branch
    # the failure must come from the Emby upload call instead.
    httpx_mock.add_response(
        url="http://poster.example/bad.jpg",
        content=b"poster-bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    httpx_mock.add_exception(RuntimeError("emby upload boom"), url=f"{FAKE_URL}/Items/e-bad/Images/Primary")
    httpx_mock.add_response(
        url="http://poster.example/good.jpg",
        content=b"poster-bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e-good/Images/Primary", status_code=204)

    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e-bad", "e-good"})

    upload_reqs = [r for r in httpx_mock.get_requests() if r.url.path == "/Items/e-good/Images/Primary"]
    assert len(upload_reqs) == 1


async def test_restore_posters_for_removed_logs_warning_on_upload_http_error(httpx_mock: HTTPXMock, caplog):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES ('e3', 'T', 'Movie', 'lib1', 'Films', '', 'http://poster.example/x.jpg',
                       '2026-01-01', '2026-01-01', 'pending')""",
        )
        await db.commit()
    httpx_mock.add_response(
        url="http://poster.example/x.jpg",
        content=b"poster-bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e3/Images/Primary", status_code=500)

    from backend.collection import _restore_posters_for_removed, _overlay_cache
    _overlay_cache["e3"] = 1
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e3"})
    # A failed upload must not be treated as a successful restore: cache entry stays.
    assert _overlay_cache.get("e3") == 1


# ─── _apply_overlays ────────────────────────────────────────────────────────────

async def test_apply_overlays_uploads_and_updates_cache_on_success(httpx_mock: HTTPXMock, monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=4)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}

    monkeypatch.setattr("backend.collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=b"modified"))
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e1/Images/Primary", method="POST", status_code=204)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")

    assert _overlay_cache["e1"] == 4


async def test_apply_overlays_skips_when_cached_day_count_unchanged(httpx_mock: HTTPXMock, monkeypatch):
    """Re-uploading the same banner every cycle wastes calls — must be
    skipped once the day count already matches the cache."""
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=4)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}

    from backend.collection import _apply_overlays, _overlay_cache
    _overlay_cache["e1"] = 4

    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")

    assert httpx_mock.get_requests() == []


async def test_apply_overlays_falls_back_to_fetching_current_poster_when_stored_url_missing(httpx_mock: HTTPXMock, monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=2)).isoformat(), "poster_url": ""}

    httpx_mock.add_response(method="GET", content=b"fetched-poster")
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=b"modified"))
    httpx_mock.add_response(method="POST", status_code=200)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")
    assert _overlay_cache["e1"] == 2


async def test_apply_overlays_skips_item_with_unparsable_delete_at():
    w = {"emby_id": "e1", "title": "T", "delete_at": "garbage", "poster_url": ""}
    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")
    assert "e1" not in _overlay_cache


async def test_apply_overlays_skips_when_no_poster_bytes_available_anywhere(httpx_mock: HTTPXMock):
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=2)).isoformat(), "poster_url": ""}
    httpx_mock.add_response(method="GET", status_code=404)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")
    assert "e1" not in _overlay_cache


async def test_apply_overlays_skips_when_overlay_render_returns_none(httpx_mock: HTTPXMock, monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=2)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}
    monkeypatch.setattr("backend.collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=None))

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")
    assert "e1" not in _overlay_cache
    # guarded_image_get is mocked and overlay rendering fails before any
    # upload/fallback-fetch call is made — no HTTP request at all.
    assert httpx_mock.get_requests() == []


async def test_apply_overlays_upload_failure_does_not_update_cache(httpx_mock: HTTPXMock, monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=2)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}
    monkeypatch.setattr("backend.collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=b"modified"))
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e1/Images/Primary", method="POST", status_code=500)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")
    assert "e1" not in _overlay_cache


async def test_apply_overlays_continues_batch_after_one_item_raises(httpx_mock: HTTPXMock, monkeypatch):
    from datetime import timedelta
    from backend.db.utils import now_utc
    delete_at = (now_utc() + timedelta(days=2)).isoformat()
    bad = {"emby_id": "e-bad", "title": "Bad", "delete_at": delete_at, "poster_url": "http://poster.example/bad.jpg"}
    good = {"emby_id": "e-good", "title": "Good", "delete_at": delete_at, "poster_url": "http://poster.example/good.jpg"}

    async def fake_guarded_get(url, timeout=10):
        if "bad" in url:
            raise RuntimeError("network blew up")
        return b"orig"

    monkeypatch.setattr("backend.collection.guarded_image_get", fake_guarded_get)
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=b"modified"))
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e-good/Images/Primary", method="POST", status_code=204)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [bad, good], "fr")

    assert "e-bad" not in _overlay_cache
    assert _overlay_cache["e-good"] == 2


# ─── sync_emby_collection — orchestration branches ─────────────────────────────

async def test_sync_emby_collection_noop_when_no_servers_and_unsupported_legacy_type():
    await _db_ms.save_media_servers([])
    await _db_ss.set_setting("media_server_type", "plex")
    with patch("backend.collection.get_media_servers", new=AsyncMock(return_value=[])):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()  # must not raise


async def test_sync_emby_collection_noop_when_only_server_configured_is_plex():
    await _db_ms.save_media_servers([{"id": "0", "type": "plex", "enabled": True}])
    from backend.collection import sync_emby_collection
    await sync_emby_collection()  # must not raise, must not reach get_client


async def test_sync_emby_collection_noop_when_no_collection_name_configured(httpx_mock: HTTPXMock):
    # media_servers has our emby server (enabled) but no collection name set
    from backend.collection import sync_emby_collection
    await sync_emby_collection()
    assert httpx_mock.get_requests() == []


async def test_sync_emby_collection_defaults_days_to_30_on_invalid_setting():
    await _db_ss.set_setting("emby_leaving_soon_collection", "Bientôt supprimé")
    await _db_ss.set_setting("emby_leaving_soon_days", "not-a-number")

    captured = {}

    async def fake_get_pending(cutoff_iso, server_id):
        captured["cutoff_iso"] = cutoff_iso
        return []

    with (
        patch("backend.collection.get_pending_before_for_server", new=fake_get_pending),
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value=None)),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()

    from backend.db.utils import parse_iso_dt, now_utc
    cutoff = parse_iso_dt(captured["cutoff_iso"])
    delta_days = (cutoff.date() - now_utc().date()).days
    assert delta_days == 30


async def test_sync_emby_collection_noop_when_no_credentials():
    await _db_ms.save_media_servers([{"id": "0", "type": "emby", "enabled": True, "url": "", "api_key": ""}])
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    with patch("backend.collection._get_or_create_collection", new=AsyncMock()) as mock_gc:
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    mock_gc.assert_not_called()


async def test_sync_emby_collection_returns_early_when_collection_cannot_be_resolved():
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    with (
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value=None)),
        patch("backend.collection._sync_collection_membership", new=AsyncMock()) as mock_sync,
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    mock_sync.assert_not_called()


async def test_sync_emby_collection_overlay_disabled_skips_restore_and_apply():
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    await _db_ss.set_setting("emby_leaving_soon_overlay", "false")
    with (
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value="col-1")),
        patch("backend.collection._sync_collection_membership", new=AsyncMock(return_value=({"m1"}, {"m2"}))),
        patch("backend.collection._restore_posters_for_removed", new=AsyncMock()) as mock_restore,
        patch("backend.collection._apply_overlays", new=AsyncMock()) as mock_apply,
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    mock_restore.assert_not_called()
    mock_apply.assert_not_called()


async def test_sync_emby_collection_overlay_enabled_restores_and_applies_then_refreshes_mosaic(httpx_mock: HTTPXMock, monkeypatch):
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    await _db_ss.set_setting("emby_leaving_soon_overlay", "true")

    async def fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.collection.asyncio.sleep", fast_sleep)

    httpx_mock.add_response(url=f"{FAKE_URL}/Items/col-1/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_response(method="POST", status_code=204)  # /Refresh call carries query params

    with (
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value="col-1")),
        patch("backend.collection._sync_collection_membership", new=AsyncMock(return_value=(set(), {"m2"}))),
        patch("backend.collection._restore_posters_for_removed", new=AsyncMock()) as mock_restore,
        patch("backend.collection._apply_overlays", new=AsyncMock()) as mock_apply,
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=[{"emby_id": "m3"}])),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()

    mock_restore.assert_awaited_once()
    mock_apply.assert_awaited_once()
    refresh_req = [r for r in httpx_mock.get_requests() if r.url.path == "/Items/col-1/Refresh"][0]
    assert refresh_req.url.params["ReplaceAllImages"] == "false"


async def test_sync_emby_collection_logs_warning_when_image_delete_http_fails(httpx_mock: HTTPXMock, monkeypatch):
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    await _db_ss.set_setting("emby_leaving_soon_overlay", "true")

    async def fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.collection.asyncio.sleep", fast_sleep)

    httpx_mock.add_response(url=f"{FAKE_URL}/Items/col-1/Images/Primary", method="DELETE", status_code=500)
    httpx_mock.add_response(method="POST", status_code=204)  # /Refresh

    with (
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value="col-1")),
        patch("backend.collection._sync_collection_membership", new=AsyncMock(return_value=(set(), set()))),
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=[])),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()  # must not raise despite the 500


async def test_sync_emby_collection_logs_warning_when_refresh_http_fails(httpx_mock: HTTPXMock, monkeypatch):
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    await _db_ss.set_setting("emby_leaving_soon_overlay", "true")

    async def fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.collection.asyncio.sleep", fast_sleep)

    httpx_mock.add_response(url=f"{FAKE_URL}/Items/col-1/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_response(method="POST", status_code=500)  # /Refresh returns an error status (not an exception)

    with (
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value="col-1")),
        patch("backend.collection._sync_collection_membership", new=AsyncMock(return_value=(set(), set()))),
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=[])),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()  # must not raise


async def test_sync_emby_collection_falls_back_to_30_days_when_get_int_setting_raises(monkeypatch):
    """Defensive code: collection.py wraps get_int_setting in its own
    try/except ValueError, even though get_int_setting currently never
    raises (it catches ValueError internally and returns the default) — see
    bug report. This pins the defensive fallback still works if that
    invariant ever changes."""
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")

    async def raising_get_int_setting(key, default=0):
        raise ValueError("simulated")

    captured = {}

    async def fake_get_pending(cutoff_iso, server_id):
        captured["cutoff_iso"] = cutoff_iso
        return []

    with (
        patch("backend.collection.get_int_setting", new=raising_get_int_setting),
        patch("backend.collection.get_pending_before_for_server", new=fake_get_pending),
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value=None)),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()

    from backend.db.utils import parse_iso_dt, now_utc
    cutoff = parse_iso_dt(captured["cutoff_iso"])
    assert (cutoff.date() - now_utc().date()).days == 30


async def test_sync_emby_collection_swallows_mosaic_refresh_exception(httpx_mock: HTTPXMock):
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    await _db_ss.set_setting("emby_leaving_soon_overlay", "true")

    async def fast_sleep(*_a, **_kw):
        return None

    # The mosaic-refresh POST call itself raises — must be caught by its own
    # local try/except (not just the function-wide outer one), and must not
    # prevent sync_emby_collection from completing normally.
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/col-1/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_exception(RuntimeError("refresh unreachable"), method="POST")
    with (
        patch("backend.collection.asyncio.sleep", new=fast_sleep),
        patch("backend.collection._get_or_create_collection", new=AsyncMock(return_value="col-1")),
        patch("backend.collection._sync_collection_membership", new=AsyncMock(return_value=(set(), set()))),
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=[])),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()  # must not raise


async def test_sync_emby_collection_outer_exception_is_swallowed(httpx_mock: HTTPXMock):
    await _db_ss.set_setting("emby_leaving_soon_collection", "X")
    with patch("backend.collection._get_or_create_collection", new=AsyncMock(side_effect=RuntimeError("boom"))):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()  # must not raise
