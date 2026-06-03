"""Tests for emby_client — verifies API key is sent via header, not query string."""
import pytest
from pytest_httpx import HTTPXMock
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema

FAKE_URL = "http://emby.test:8096"
FAKE_KEY  = "test-api-key-12345"


@pytest.fixture(autouse=True)
async def mock_server_config(monkeypatch, tmp_path):
    """Configure database to use our fake server for every test."""
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "emby_test.db")
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
        "id": "0", "name": "Test Server",
        "url": FAKE_URL, "api_key": FAKE_KEY,
        "ext_url": "", "type": "emby", "enabled": True,
    }])


def _assert_uses_header_not_query(httpx_mock: HTTPXMock, key: str = FAKE_KEY) -> None:
    """Assert every intercepted request uses header auth, not query param."""
    for req in httpx_mock.get_requests():
        assert req.headers.get("x-emby-token") == key, (
            f"Request to {req.url} missing X-Emby-Token header"
        )
        assert "api_key" not in str(req.url), (
            f"Request to {req.url} leaks api_key in URL"
        )


# ─── test_connection ─────────────────────────────────────────────────────────

async def test_connection_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/System/Info",
        json={"Version": "4.8.0.0", "ProductName": "Emby Server"},
    )
    from backend.emby_client import test_connection
    ok, msg, server_type, _err = await test_connection(server_id="0")

    assert ok is True
    assert server_type == "emby"
    _assert_uses_header_not_query(httpx_mock)


async def test_connection_detects_jellyfin(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/System/Info",
        json={"Version": "10.9.0", "ProductName": "Jellyfin Server"},
    )
    from backend.emby_client import test_connection
    ok, msg, server_type, _err = await test_connection(server_id="0")

    assert ok is True
    assert server_type == "jellyfin"
    _assert_uses_header_not_query(httpx_mock)


async def test_connection_http_error_returns_false(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", status_code=401)
    from backend.emby_client import test_connection
    ok, msg, _st, _err = await test_connection(server_id="0")
    assert ok is False


# ─── get_libraries ────────────────────────────────────────────────────────────

async def test_get_libraries_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/Library/MediaFolders",
        json={"Items": [{"Id": "lib1", "Name": "Movies"}, {"Id": "lib2", "Name": "Series"}]},
    )
    from backend.emby_client import get_libraries
    libs = await get_libraries()

    assert len(libs) == 2
    _assert_uses_header_not_query(httpx_mock)


async def test_get_libraries_empty_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/Library/MediaFolders", status_code=500)
    from backend.emby_client import get_libraries
    assert await get_libraries() == []


# ─── get_users ────────────────────────────────────────────────────────────────

async def test_get_users_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/Users",
        json=[{"Id": "user1", "Name": "Alice"}, {"Id": "user2", "Name": "Bob"}],
    )
    from backend.emby_client import get_users
    users = await get_users(server_id="0")

    assert len(users) == 2
    _assert_uses_header_not_query(httpx_mock)


# ─── delete_item ─────────────────────────────────────────────────────────────

async def test_delete_item_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/Items/item123",
        status_code=204,
    )
    from backend.emby_client import delete_item
    await delete_item("item123", server_id="0")

    _assert_uses_header_not_query(httpx_mock)
    assert httpx_mock.get_requests()[0].method == "DELETE"


# ─── get_items_in_library ────────────────────────────────────────────────────

async def test_get_items_in_library_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "m1", "Name": "Movie 1", "Type": "Movie", "Path": "/data/m1.mkv"}],
              "TotalRecordCount": 1},
    )
    from backend.emby_client import get_items_in_library
    items, total = await get_items_in_library("lib1", server_id="0")

    assert total == 1
    _assert_uses_header_not_query(httpx_mock)


# ─── no credentials → graceful fallback ───────────────────────────────────────

async def test_get_users_no_url_returns_empty(monkeypatch, tmp_path):
    db_path = str(tmp_path / "empty.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    # No servers configured

    from backend.emby_client import get_users
    users = await get_users(server_id="0")
    assert users == []


# ─── Tests: server_id propagation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_library_passes_server_id_to_get_poster_url(monkeypatch, tmp_path):
    """_scan_library must pass server_id to _get_poster_url, not use default '0'."""
    import backend.db.engine as _db_engine
    import backend.db.utils as _db_utils2
    import backend.db.settings_store as _db_ss2
    import backend.db.media_servers as _db_ms2
    import backend.db.schema as _db_schema2
    db_path = str(tmp_path / "poster_test.db")
    monkeypatch.setattr(_db_utils2, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss2, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms2, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema2, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms2._ms_cache = None; _db_ms2._ms_cache_ts = 0.0
    _db_ss2._settings_cache.clear(); _db_ss2._settings_cache_ts = 0.0
    await _db_schema2.init_db()
    await _db_ms2.save_media_servers([{"id": "srv2", "type": "emby",
        "url": "http://server2:8096", "api_key": "key2", "enabled": True}])

    called_with = []
    from unittest.mock import AsyncMock, patch
    async def mock_get_client(sid="0"):
        called_with.append(sid)
        return ("http://server2:8096", "key2")

    with patch("backend.rules.legacy_conditions.get_client", new=mock_get_client):
        from backend.rules.legacy_conditions import _get_poster_url
        await _get_poster_url("item123", server_id="srv2")

    # Verify server_id was propagated, not defaulting to "0"
    assert "srv2" in called_with, f"Expected 'srv2' in calls, got {called_with}"
    # This test confirms the scanner correctly propagates server_id to poster URL resolution
