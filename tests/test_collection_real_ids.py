"""Real-Id resolution, base64 uploads and HTTP-result checks for the
'Bientôt supprimé' collection (Hygie 4.5.1).

Prod findings: Emby rejects raw JPEG bodies (expects base64) and chokes on
consolidated ids such as ``sonarr-series:312``.
"""
import base64
import logging
import re
from datetime import timedelta
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

    db_path = str(tmp_path / "collection_real.db")
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


# ─── resolver ──────────────────────────────────────────────────────────────────

async def test_resolver_returns_plain_id_without_any_lookup():
    from backend import media_item_resolver as r
    with (
        patch.object(r, "sonarr_get_series_by_id_any", new=AsyncMock()) as m_sonarr,
        patch.object(r, "find_item_by_path", new=AsyncMock()) as m_find,
    ):
        assert await r.resolve_server_item_id({"emby_id": "m3"}, "0") == "m3"
    m_sonarr.assert_not_called()
    m_find.assert_not_called()


async def test_resolver_returns_none_for_missing_emby_id():
    from backend import media_item_resolver as r
    assert await r.resolve_server_item_id({}, "0") is None


async def test_resolver_series_uses_sonarr_path_and_series_type():
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-series:7", "sonarr_series_id": 7, "arr_server_url": "http://sonarr"}
    with (
        patch.object(r, "sonarr_get_series_by_id_any",
                     new=AsyncMock(return_value={"path": "/tv/Show"})) as m_sonarr,
        patch.object(r, "find_item_by_path", new=AsyncMock(return_value={"Id": "123"})) as m_find,
    ):
        assert await r.resolve_server_item_id(row, "0") == "123"
    m_sonarr.assert_awaited_once_with(7, arr_server_url="http://sonarr")
    m_find.assert_awaited_once_with("/tv/Show", include_types="Series", server_id="0")


async def test_resolver_series_without_arr_server_url_passes_none():
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-series:7", "sonarr_series_id": "7"}
    with (
        patch.object(r, "sonarr_get_series_by_id_any",
                     new=AsyncMock(return_value={"path": "/tv/Show"})) as m_sonarr,
        patch.object(r, "find_item_by_path", new=AsyncMock(return_value={"Id": "1"})),
    ):
        await r.resolve_server_item_id(row, "0")
    m_sonarr.assert_awaited_once_with(7, arr_server_url=None)


async def test_resolver_season_uses_dirname_of_file_path_and_season_type():
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-season:7", "sonarr_series_id": 7, "season_number": 2,
           "file_path": "/tv/Show/Season 02/ep1.mkv"}
    with (
        patch.object(r, "sonarr_get_series_by_id_any",
                     new=AsyncMock(return_value={"path": "/tv/Show"})),
        patch.object(r, "find_item_by_path", new=AsyncMock(return_value={"Id": "s2"})) as m_find,
    ):
        assert await r.resolve_server_item_id(row, "9") == "s2"
    m_find.assert_awaited_once_with("/tv/Show/Season 02", include_types="Season", server_id="9")


async def test_resolver_season_without_file_path_returns_none():
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-season:7", "sonarr_series_id": 7, "season_number": 2, "file_path": ""}
    with (
        patch.object(r, "sonarr_get_series_by_id_any",
                     new=AsyncMock(return_value={"path": "/tv/Show"})),
        patch.object(r, "find_item_by_path", new=AsyncMock()) as m_find,
    ):
        assert await r.resolve_server_item_id(row, "0") is None
    m_find.assert_not_called()


@pytest.mark.parametrize("series", [None, {}, {"path": ""}])
async def test_resolver_returns_none_when_sonarr_has_no_path(series):
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-series:7", "sonarr_series_id": 7}
    with (
        patch.object(r, "sonarr_get_series_by_id_any", new=AsyncMock(return_value=series)),
        patch.object(r, "find_item_by_path", new=AsyncMock()) as m_find,
    ):
        assert await r.resolve_server_item_id(row, "0") is None
    m_find.assert_not_called()


async def test_resolver_returns_none_without_sonarr_series_id():
    from backend import media_item_resolver as r
    with patch.object(r, "sonarr_get_series_by_id_any", new=AsyncMock()) as m_sonarr:
        assert await r.resolve_server_item_id({"emby_id": "sonarr-series:7"}, "0") is None
    m_sonarr.assert_not_called()


async def test_resolver_returns_none_when_item_not_found_on_server():
    from backend import media_item_resolver as r
    row = {"emby_id": "sonarr-series:7", "sonarr_series_id": 7}
    with (
        patch.object(r, "sonarr_get_series_by_id_any",
                     new=AsyncMock(return_value={"path": "/tv/Show"})),
        patch.object(r, "find_item_by_path", new=AsyncMock(return_value=None)),
    ):
        assert await r.resolve_server_item_id(row, "0") is None


# ─── repository ────────────────────────────────────────────────────────────────

async def test_get_pending_before_for_server_returns_resolution_columns():
    from backend.db.engine import get_db
    from backend.db.repositories import get_pending_before_for_server
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id) VALUES ('lib1', 'Series', 'x', '0')"
        )
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status,
                sonarr_series_id, season_number, arr_server_url)
               VALUES ('sonarr-season:5', 'S', 'Series', 'lib1', 'Series', '/tv/S/Season 01/a.mkv',
                       '', '2026-01-01', '2026-01-02', 'pending', 5, 1, 'http://sonarr')"""
        )
        await db.commit()
    rows = await get_pending_before_for_server("2026-12-31T00:00:00", "0")
    assert len(rows) == 1
    row = rows[0]
    assert row["sonarr_series_id"] == 5
    assert row["season_number"] == 1
    assert row["file_path"] == "/tv/S/Season 01/a.mkv"
    assert row["arr_server_url"] == "http://sonarr"


# ─── base64 uploads ────────────────────────────────────────────────────────────

async def test_overlay_upload_body_is_base64_of_jpeg(httpx_mock: HTTPXMock, monkeypatch):
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=4)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}
    monkeypatch.setattr("backend.collection.guarded_image_get", AsyncMock(return_value=b"orig"))
    monkeypatch.setattr("backend.collection._overlay_poster", AsyncMock(return_value=b"\xff\xd8jpeg\xff\xd9"))
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e1/Images/Primary", method="POST", status_code=204)

    from backend.collection import _apply_overlays
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")

    req = httpx_mock.get_requests()[0]
    assert base64.b64decode(req.content, validate=True) == b"\xff\xd8jpeg\xff\xd9"
    assert req.headers["Content-Type"] == "image/jpeg"


async def _insert_row(emby_id: str, poster_url: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
               (emby_id, title, media_type, library_id, library_name, file_path,
                poster_url, detected_at, delete_at, status)
               VALUES (?, 'T', 'Movie', 'lib1', 'Films', '', ?, '2026-01-01', '2026-01-01', 'pending')""",
            (emby_id, poster_url),
        )
        await db.commit()


async def test_restore_upload_body_is_base64_of_original_poster(httpx_mock: HTTPXMock):
    await _insert_row("e1", "http://poster.example/orig.jpg")
    httpx_mock.add_response(url="http://poster.example/orig.jpg", content=b"\xff\xd8orig",
                            headers={"Content-Type": "image/jpeg"})
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e1/Images/Primary", method="POST", status_code=204)

    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e1"})

    up = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
    assert base64.b64decode(up.content, validate=True) == b"\xff\xd8orig"
    assert up.headers["Content-Type"] == "image/jpeg"


# ─── restore without stored poster: delete Primary + refresh ───────────────────

async def test_restore_without_poster_url_deletes_primary_then_refreshes(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/real-1/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_response(method="POST", status_code=204)

    from backend.collection import _restore_posters_for_removed, _overlay_cache
    _overlay_cache["real-1"] = 3
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"real-1"})

    reqs = httpx_mock.get_requests()
    assert [(r.method, r.url.path) for r in reqs] == [
        ("DELETE", "/Items/real-1/Images/Primary"),
        ("POST", "/Items/real-1/Refresh"),
    ]
    p = reqs[1].url.params
    assert p["Recursive"] == "false"
    assert p["MetadataRefreshMode"] == "Default"
    assert p["ImageRefreshMode"] == "FullRefresh"
    assert p["ReplaceAllImages"] == "false"
    assert "real-1" not in _overlay_cache


async def test_restore_without_poster_url_accepts_404_on_delete(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="DELETE", status_code=404)
    httpx_mock.add_response(method="POST", status_code=204)
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"real-2"})
    assert [r.method for r in httpx_mock.get_requests()] == ["DELETE", "POST"]


async def test_restore_without_poster_url_skips_refresh_when_delete_fails(httpx_mock: HTTPXMock, caplog):
    httpx_mock.add_response(method="DELETE", status_code=500)
    from backend.collection import _restore_posters_for_removed, _overlay_cache
    _overlay_cache["real-3"] = 2
    with caplog.at_level(logging.WARNING, logger="backend.collection"):
        async with httpx.AsyncClient() as client:
            await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"real-3"})
    assert [r.method for r in httpx_mock.get_requests()] == ["DELETE"]
    assert _overlay_cache["real-3"] == 2
    assert "real-3" in caplog.text


async def test_restore_without_poster_url_warns_when_refresh_fails(httpx_mock: HTTPXMock, caplog):
    httpx_mock.add_response(method="DELETE", status_code=204)
    httpx_mock.add_response(method="POST", status_code=500)
    from backend.collection import _restore_posters_for_removed, _overlay_cache
    _overlay_cache["real-4"] = 2
    with caplog.at_level(logging.WARNING, logger="backend.collection"):
        async with httpx.AsyncClient() as client:
            await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"real-4"})
    assert _overlay_cache["real-4"] == 2
    assert "real-4" in caplog.text


# ─── membership HTTP checks ────────────────────────────────────────────────────

async def test_membership_add_failure_warns_and_returns_empty_added(httpx_mock: HTTPXMock, caplog):
    httpx_mock.add_response(json={"Items": []})
    httpx_mock.add_response(method="POST", status_code=500)
    from backend.collection import _sync_collection_membership
    with (
        patch("backend.collection.add_log", new=AsyncMock()) as m_log,
        caplog.at_level(logging.WARNING, logger="backend.collection"),
    ):
        async with httpx.AsyncClient() as client:
            added, removed, _members = await _sync_collection_membership(
                client, FAKE_URL, FAKE_KEY, "col-1", {"a"})
    assert added == set() and removed == set()
    assert "500" in caplog.text
    assert any(c.args[0] == "WARN" for c in m_log.await_args_list)


async def test_membership_remove_failure_warns_and_returns_empty_removed(httpx_mock: HTTPXMock, caplog):
    httpx_mock.add_response(json={"Items": [{"Id": "old"}]})
    httpx_mock.add_response(method="DELETE", status_code=500)
    from backend.collection import _sync_collection_membership
    with (
        patch("backend.collection.add_log", new=AsyncMock()) as m_log,
        caplog.at_level(logging.WARNING, logger="backend.collection"),
    ):
        async with httpx.AsyncClient() as client:
            added, removed, _members = await _sync_collection_membership(
                client, FAKE_URL, FAKE_KEY, "col-1", set())
    assert added == set() and removed == set()
    assert any(c.args[0] == "WARN" for c in m_log.await_args_list)


async def test_membership_allow_remove_false_never_deletes(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": [{"Id": "old"}]})
    httpx_mock.add_response(method="POST", status_code=204)
    from backend.collection import _sync_collection_membership
    async with httpx.AsyncClient() as client:
        added, removed, _members = await _sync_collection_membership(
            client, FAKE_URL, FAKE_KEY, "col-1", {"new"}, allow_remove=False)
    assert added == {"new"} and removed == set()
    assert "DELETE" not in [r.method for r in httpx_mock.get_requests()]


# ─── collection creation HTTP check ────────────────────────────────────────────

async def test_create_collection_http_500_logs_warning_and_returns_none(httpx_mock: HTTPXMock, caplog):
    httpx_mock.add_response(json={"Items": []})
    httpx_mock.add_response(json=[{"Id": "u1"}])
    httpx_mock.add_response(method="POST", status_code=500, text="Unrecognized Guid format")
    from backend.collection import _get_or_create_collection
    with (
        patch("backend.collection.add_log", new=AsyncMock()) as m_log,
        caplog.at_level(logging.WARNING, logger="backend.collection"),
    ):
        async with httpx.AsyncClient() as client:
            col = await _get_or_create_collection(client, FAKE_URL, FAKE_KEY, "X", {"1"})
    assert col is None
    assert "500" in caplog.text
    assert any(c.args[0] == "WARN" for c in m_log.await_args_list)


# ─── sync_emby_collection end-to-end with consolidated rows ────────────────────

def _row(emby_id, title="Show", days=5, **extra):
    from backend.db.utils import now_utc
    return {"emby_id": emby_id, "title": title,
            "delete_at": (now_utc() + timedelta(days=days)).isoformat(),
            "poster_url": "", **extra}


async def _configure(overlay="false"):
    await _db_ss.set_setting("emby_leaving_soon_collection", "Soon")
    await _db_ss.set_setting("emby_leaving_soon_overlay", overlay)


async def test_sync_adds_real_id_never_sonarr_string(httpx_mock: HTTPXMock):
    await _configure()
    rows = [_row("sonarr-series:7", sonarr_series_id=7)]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": []})
    httpx_mock.add_response(method="POST", status_code=204)
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value="123")) as m_res,
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    m_res.assert_awaited_once()
    posts = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    assert posts[0].url.path == "/Collections/col-1/Items"
    assert posts[0].url.params["Ids"] == "123"
    assert all("sonarr-" not in str(r.url) for r in httpx_mock.get_requests())


async def test_sync_creates_collection_with_real_ids_only(httpx_mock: HTTPXMock):
    await _configure()
    rows = [_row("sonarr-series:7", sonarr_series_id=7)]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": []})
    httpx_mock.add_response(url=re.compile(r".*/Users$"), json=[{"Id": "u1"}])
    httpx_mock.add_response(method="POST", url=re.compile(r".*/Collections\?.*"), json={"Id": "new-col"})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": [{"Id": "123"}]})
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value="123")),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    create = [r for r in httpx_mock.get_requests() if r.method == "POST"][0]
    assert create.url.params["Ids"] == "123"
    assert all("sonarr-" not in str(r.url) for r in httpx_mock.get_requests())


async def test_sync_unresolved_row_warns_and_never_removes_members(httpx_mock: HTTPXMock, caplog):
    await _configure()
    rows = [_row("sonarr-series:7", title="Ghost Show", sonarr_series_id=7)]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": [{"Id": "kept"}]})
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value=None)),
        patch("backend.collection.add_log", new=AsyncMock()) as m_log,
        caplog.at_level(logging.WARNING, logger="backend.collection"),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    assert "DELETE" not in [r.method for r in httpx_mock.get_requests()]
    assert "Ghost Show" in caplog.text
    warns = [c for c in m_log.await_args_list if c.args[0] == "WARN"]
    assert any("Ghost Show" in c.args[1] for c in warns)


async def test_sync_dedups_by_real_id_and_keeps_earliest_delete_at(httpx_mock: HTTPXMock, monkeypatch):
    await _configure(overlay="true")

    async def fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.collection.asyncio.sleep", fast_sleep)
    soon, later = _row("sonarr-season:7", days=3), _row("sonarr-season:8", days=20)
    rows = [later, soon]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": [{"Id": "55"}]})
    httpx_mock.add_response(method="DELETE", status_code=204)
    httpx_mock.add_response(method="POST", status_code=204)
    apply_mock = AsyncMock()
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value="55")),
        patch("backend.collection._apply_overlays", new=apply_mock),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    apply_mock.assert_awaited_once()
    applied = apply_mock.await_args.args[3]
    assert len(applied) == 1
    assert applied[0]["emby_id"] == "55"
    assert applied[0]["delete_at"] == soon["delete_at"]
    # inputs not mutated
    assert rows[0]["emby_id"] == "sonarr-season:8" and rows[1]["emby_id"] == "sonarr-season:7"


async def test_sync_dedup_keeps_first_when_later_row_is_not_earlier(httpx_mock: HTTPXMock):
    await _configure()
    first, second = _row("sonarr-season:7", days=3), _row("sonarr-season:8", days=20)
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=[first, second])),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value="55")),
        patch("backend.collection._sync_collection_membership",
              new=AsyncMock(return_value=(set(), set(), set()))) as m_mem,
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    assert m_mem.await_args.args[4] == {"55"}


async def test_sync_does_not_log_added_count_when_add_fails(httpx_mock: HTTPXMock):
    await _configure()
    rows = [_row("m1")]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": []})
    httpx_mock.add_response(method="POST", status_code=500)
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.add_log", new=AsyncMock()) as m_log,
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    msgs = [c.args[1] for c in m_log.await_args_list]
    assert any(c.args[0] == "WARN" for c in m_log.await_args_list)
    assert not any("+1 ajoutés" in m for m in msgs)


async def test_sync_resolver_exception_is_treated_as_unresolved(httpx_mock: HTTPXMock, caplog):
    await _configure()
    rows = [_row("sonarr-series:7", title="Boom Show", sonarr_series_id=7)]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": [{"Id": "kept"}]})
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.resolve_server_item_id", new=AsyncMock(side_effect=RuntimeError("sonarr down"))),
        caplog.at_level(logging.WARNING, logger="backend.collection"),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    assert "sonarr down" in caplog.text
    assert "DELETE" not in [r.method for r in httpx_mock.get_requests()]


async def test_resolve_wanted_dedup_prefers_parsable_delete_at():
    from backend.collection import _resolve_wanted
    bad = {"emby_id": "a", "title": "A", "delete_at": "not-a-date"}
    good = _row("b", title="B", days=9)
    with patch("backend.collection.resolve_server_item_id", new=AsyncMock(return_value="55")):
        rows, unresolved = await _resolve_wanted([bad, good], "0")
    assert unresolved is False
    assert len(rows) == 1 and rows[0]["delete_at"] == good["delete_at"]


# ─── M1: no stacked banners ────────────────────────────────────────────────────

async def test_apply_overlays_http_poster_fetch_failure_does_not_fall_back_to_emby_primary(
    httpx_mock: HTTPXMock, monkeypatch,
):
    from backend.db.utils import now_utc
    w = {"emby_id": "e1", "title": "T", "delete_at": (now_utc() + timedelta(days=4)).isoformat(),
         "poster_url": "http://poster.example/x.jpg"}
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.collection.guarded_image_get", fetch)
    render = AsyncMock(return_value=b"modified")
    monkeypatch.setattr("backend.collection._overlay_poster", render)

    from backend.collection import _apply_overlays, _overlay_cache
    async with httpx.AsyncClient() as client:
        await _apply_overlays(client, FAKE_URL, FAKE_KEY, [w], "fr")

    fetch.assert_awaited_once_with("http://poster.example/x.jpg")
    assert httpx_mock.get_requests() == []  # no Primary GET, no upload
    render.assert_not_awaited()
    assert "e1" not in _overlay_cache


# ─── M2: orphan banners are restored / never applied ───────────────────────────

async def test_restore_non_http_poster_refreshes_primary_instead_of_skipping(httpx_mock: HTTPXMock):
    await _insert_row("e2", "/api/proxy/poster/0/e2")
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e2/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_response(url=re.compile(r".*/Items/e2/Refresh.*"), method="POST", status_code=204)
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e2"})
    assert [(r.method, r.url.path) for r in httpx_mock.get_requests()] == [
        ("DELETE", "/Items/e2/Images/Primary"),
        ("POST", "/Items/e2/Refresh"),
    ]


async def test_restore_failed_http_fetch_refreshes_primary_instead_of_skipping(httpx_mock: HTTPXMock, monkeypatch):
    await _insert_row("e4", "http://poster.example/x.jpg")
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.collection.guarded_image_get", fetch)
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/e4/Images/Primary", method="DELETE", status_code=204)
    httpx_mock.add_response(url=re.compile(r".*/Items/e4/Refresh.*"), method="POST", status_code=204)
    from backend.collection import _restore_posters_for_removed
    async with httpx.AsyncClient() as client:
        await _restore_posters_for_removed(client, FAKE_URL, FAKE_KEY, {"e4"})
    fetch.assert_awaited_once()
    assert [(r.method, r.url.path) for r in httpx_mock.get_requests()] == [
        ("DELETE", "/Items/e4/Images/Primary"),
        ("POST", "/Items/e4/Refresh"),
    ]


async def test_membership_returns_effective_members(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": [{"Id": "keep"}, {"Id": "old"}]})
    httpx_mock.add_response(method="POST", status_code=204)
    httpx_mock.add_response(method="DELETE", status_code=204)
    from backend.collection import _sync_collection_membership
    async with httpx.AsyncClient() as client:
        added, removed, members = await _sync_collection_membership(
            client, FAKE_URL, FAKE_KEY, "col-1", {"keep", "new"})
    assert (added, removed, members) == ({"new"}, {"old"}, {"keep", "new"})


async def test_membership_failed_add_excluded_from_effective_members(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": [{"Id": "keep"}]})
    httpx_mock.add_response(method="POST", status_code=500)
    from backend.collection import _sync_collection_membership
    with patch("backend.collection.add_log", new=AsyncMock()):
        async with httpx.AsyncClient() as client:
            _, _, members = await _sync_collection_membership(
                client, FAKE_URL, FAKE_KEY, "col-1", {"keep", "new"})
    assert members == {"keep"}


async def test_membership_failed_remove_keeps_item_as_member(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"Items": [{"Id": "old"}]})
    httpx_mock.add_response(method="DELETE", status_code=500)
    from backend.collection import _sync_collection_membership
    with patch("backend.collection.add_log", new=AsyncMock()):
        async with httpx.AsyncClient() as client:
            _, removed, members = await _sync_collection_membership(
                client, FAKE_URL, FAKE_KEY, "col-1", set())
    assert removed == set() and members == {"old"}


async def test_sync_does_not_overlay_items_whose_collection_add_failed(httpx_mock: HTTPXMock, monkeypatch):
    await _configure(overlay="true")

    async def fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.collection.asyncio.sleep", fast_sleep)
    rows = [_row("m1")]
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*SearchTerm.*"), json={"Items": [{"Id": "col-1", "Name": "Soon"}]})
    httpx_mock.add_response(url=re.compile(r".*/Items\?.*ParentId.*"), json={"Items": []})
    httpx_mock.add_response(method="POST", url=re.compile(r".*/Collections/col-1/Items.*"), status_code=500)
    httpx_mock.add_response(method="DELETE", url=f"{FAKE_URL}/Items/col-1/Images/Primary", status_code=204)
    httpx_mock.add_response(method="POST", url=re.compile(r".*/Items/col-1/Refresh.*"), status_code=204)
    apply_mock = AsyncMock()
    with (
        patch("backend.collection.get_pending_before_for_server", new=AsyncMock(return_value=rows)),
        patch("backend.collection.add_log", new=AsyncMock()),
        patch("backend.collection._apply_overlays", new=apply_mock),
    ):
        from backend.collection import sync_emby_collection
        await sync_emby_collection()
    applied = apply_mock.await_args.args[3] if apply_mock.await_args else []
    assert applied == []
    assert not any(r.url.path == "/Items/m1/Images/Primary" for r in httpx_mock.get_requests())
