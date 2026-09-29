"""Coverage tests for backend/emby_client.py.

Priorities per the deletion-safety contract: response parsing (missing/null
fields, pagination, unexpected types), HTTP/timeout error handling (a failed
call must never look like a successful empty result to a caller that decides
what's safe to delete), auth headers, and CircuitOpenError propagation.
"""
import time

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
from backend.arr_clients.circuit_breaker import get_breaker
from backend.exceptions import MediaServerUnreachable

FAKE_URL = "http://emby.test:8096"
FAKE_KEY = "test-api-key-12345"


@pytest.fixture(autouse=True)
async def mock_server_config(monkeypatch, tmp_path):
    """Configure database to use our fake server for every test (mirrors
    tests/test_emby_client.py's fixture)."""
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "emby_cov_test.db")
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


@pytest.fixture
def no_sleep(monkeypatch):
    """http_retry() sleeps (with real backoff) between retries on transient
    network errors. Make retries instant so exception-path tests stay fast."""
    async def _fast_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr("backend.db.utils.asyncio.sleep", _fast_sleep)


def _open_breaker(server_id: str = "0") -> None:
    """Force the emby:<server_id> circuit breaker into the OPEN state so the
    next call raises CircuitOpenError without touching the network."""
    br = get_breaker(f"emby:{server_id}", failure_threshold=5, recovery_timeout=120.0)
    br._state = br.OPEN
    br._last_failure_ts = time.monotonic()


async def _clear_servers_and_settings(monkeypatch, tmp_path, name: str) -> None:
    """Point at a fresh, empty DB (no servers, no legacy settings)."""
    db_path = str(tmp_path / f"{name}.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()


# ─── _classify_network_error ───────────────────────────────────────────────────

@pytest.mark.parametrize("message,expected", [
    ("Name or service not known", "dns_failure"),
    ("[Errno -2] Name does not resolve", "dns_failure"),
    ("[Errno 8] nodename nor servname provided", "dns_failure"),
    ("Connection refused", "connection_refused"),
    ("[Errno 111] Connection refused", "connection_refused"),
    ("No route to host", "host_unreachable"),
    ("[Errno 113] No route to host", "host_unreachable"),
    ("SSL: CERTIFICATE_VERIFY_FAILED", "ssl_error"),
    ("certificate has expired", "ssl_error"),
    ("some other unexpected transport failure", "network_error"),
])
def test_classify_network_error_maps_message_to_stable_code(message, expected):
    from backend.emby_client import _classify_network_error
    assert _classify_network_error(Exception(message)) == expected


# ─── _http_error_code ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    (401, "http_401"),
    (403, "http_403"),
    (404, "http_404"),
    (502, "http_502"),
    (503, "http_503"),
    (418, "http_418"),
])
def test_http_error_code_maps_known_codes_and_falls_back_for_unknown(code, expected):
    from backend.emby_client import _http_error_code
    assert _http_error_code(code) == expected


# ─── ensure_server_uid ──────────────────────────────────────────────────────────

async def test_ensure_server_uid_skips_http_call_when_already_set(httpx_mock: HTTPXMock):
    await _db_ms.save_media_servers([{
        "id": "0", "url": FAKE_URL, "api_key": FAKE_KEY, "server_uid": "already-set",
    }])
    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")
    assert httpx_mock.get_requests() == []


async def test_ensure_server_uid_skips_servers_that_do_not_match_id(httpx_mock: HTTPXMock):
    """With several servers configured, only the matching one is queried."""
    await _db_ms.save_media_servers([
        {"id": "other", "url": "http://other:8096", "api_key": "other-key"},
        {"id": "0", "url": FAKE_URL, "api_key": FAKE_KEY},
    ])
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Id": "srv-uuid-1"})

    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")

    assert len(httpx_mock.get_requests()) == 1
    assert str(httpx_mock.get_requests()[0].url).startswith(FAKE_URL)


async def test_ensure_server_uid_noop_when_no_url_or_key(httpx_mock: HTTPXMock):
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")
    assert httpx_mock.get_requests() == []


async def test_ensure_server_uid_populates_uid_on_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Id": "srv-uuid-123"})
    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")

    servers = await _db_ms.get_media_servers()
    assert servers[0]["server_uid"] == "srv-uuid-123"


async def test_ensure_server_uid_leaves_uid_unset_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", status_code=500)
    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")

    servers = await _db_ms.get_media_servers()
    assert servers[0].get("server_uid", "") == ""


async def test_ensure_server_uid_swallows_network_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    from backend.emby_client import ensure_server_uid
    # Must not raise
    await ensure_server_uid(server_id="0")
    servers = await _db_ms.get_media_servers()
    assert servers[0].get("server_uid", "") == ""


# ─── get_client / get_client_ext_url — legacy fallback ─────────────────────────

async def test_get_client_skips_non_matching_servers_in_multi_server_list():
    """With several configured servers, get_client must return the one whose
    id matches — not just the first in the list."""
    await _db_ms.save_media_servers([
        {"id": "other", "url": "http://other:8096", "api_key": "other-key"},
        {"id": "0", "url": FAKE_URL, "api_key": FAKE_KEY},
    ])
    from backend.emby_client import get_client
    url, key = await get_client(server_id="0")
    assert url == FAKE_URL
    assert key == FAKE_KEY


async def test_get_client_decrypts_encrypted_api_key():
    from backend.db.encryption import _encrypt_value
    encrypted = _encrypt_value(FAKE_KEY)
    assert encrypted.startswith("enc:")
    await _db_ms.save_media_servers([{"id": "0", "url": FAKE_URL, "api_key": encrypted}])

    from backend.emby_client import get_client
    url, key = await get_client(server_id="0")
    assert key == FAKE_KEY


async def test_get_client_ext_url_decrypts_encrypted_external_url():
    from backend.db.encryption import _encrypt_value
    encrypted = _encrypt_value("https://public.example.com")
    assert encrypted.startswith("enc:")
    await _db_ms.save_media_servers([{"id": "0", "url": FAKE_URL, "api_key": FAKE_KEY, "ext_url": encrypted}])

    from backend.emby_client import get_client_ext_url
    ext = await get_client_ext_url(server_id="0")
    assert ext == "https://public.example.com"


async def test_ensure_server_uid_decrypts_encrypted_api_key_before_request(httpx_mock: HTTPXMock):
    from backend.db.encryption import _encrypt_value
    encrypted = _encrypt_value(FAKE_KEY)
    await _db_ms.save_media_servers([{"id": "0", "url": FAKE_URL, "api_key": encrypted}])
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Id": "srv-uuid-999"})

    from backend.emby_client import ensure_server_uid
    await ensure_server_uid(server_id="0")

    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-emby-token") == FAKE_KEY
    servers = await _db_ms.get_media_servers()
    assert servers[0]["server_uid"] == "srv-uuid-999"


async def test_get_client_falls_back_to_legacy_settings_when_server_not_found():
    await _db_ss.set_setting("emby_url", "http://legacy:8096")
    await _db_ss.set_setting("emby_api_key", "legacy-key")
    from backend.emby_client import get_client
    url, key = await get_client(server_id="nonexistent")
    assert url == "http://legacy:8096"
    assert key == "legacy-key"


async def test_get_client_ext_url_returns_configured_external_url():
    await _db_ms.save_media_servers([{
        "id": "0", "url": FAKE_URL, "api_key": FAKE_KEY, "ext_url": "https://public.example.com",
    }])
    from backend.emby_client import get_client_ext_url
    ext = await get_client_ext_url(server_id="0")
    assert ext == "https://public.example.com"


async def test_get_client_ext_url_falls_back_to_legacy_setting():
    await _db_ss.set_setting("emby_external_url", "https://legacy-public.example.com")
    from backend.emby_client import get_client_ext_url
    ext = await get_client_ext_url(server_id="nonexistent")
    assert ext == "https://legacy-public.example.com"


async def test_get_client_ext_url_empty_when_nothing_configured():
    from backend.emby_client import get_client_ext_url
    ext = await get_client_ext_url(server_id="nonexistent")
    assert ext == ""


# ─── test_connection ────────────────────────────────────────────────────────────

async def test_connection_missing_credentials_reports_message_without_crashing():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import test_connection
    result = await test_connection(server_id="0")
    # NOTE: this branch returns a 3-tuple while every other branch returns a
    # 4-tuple (ok, message, server_type, error_code) — see bug report.
    assert result[0] is False
    assert "manquante" in result[1]


async def test_connection_success_stores_server_uid_from_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_URL}/System/Info",
        json={"Version": "4.8.0.0", "ProductName": "Emby Server", "Id": "detected-uid-1"},
    )
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is True

    servers = await _db_ms.get_media_servers()
    assert servers[0]["server_uid"] == "detected-uid-1"
    assert servers[0]["type"] == "emby"


async def test_connection_detects_jellyfin_from_version_when_product_name_empty(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Version": "10.8.0", "ProductName": ""})
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is True
    assert server_type == "jellyfin"
    assert err == ""


async def test_connection_detects_emby_from_version_when_product_name_empty(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Version": "4.7.14.0", "ProductName": ""})
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is True
    assert server_type == "emby"


async def test_connection_unknown_product_name_reports_unknown_type(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Version": "1.2", "ProductName": "Weird Media Server"})
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is True
    assert server_type == "unknown"
    assert "Weird Media Server" in msg


async def test_connection_fully_unrecognized_server_reports_unknown(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", json={"Version": "2.1", "ProductName": ""})
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is True
    assert server_type == "unknown"
    assert msg == "Unknown 2.1"


async def test_connection_http_error_returns_stable_error_code(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/System/Info", status_code=502)
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is False
    assert err == "http_502"
    assert server_type == ""


async def test_connection_network_error_is_classified(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("Connection refused"))
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is False
    assert err == "connection_refused"


async def test_connection_timeout_reports_timeout_code(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is False
    assert err == "timeout"
    assert msg == "Connection timed out"


async def test_connection_unexpected_exception_reports_empty_error_code(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("weird failure"))
    from backend.emby_client import test_connection
    ok, msg, server_type, err = await test_connection(server_id="0")
    assert ok is False
    assert err == ""
    assert "weird failure" in msg


# ─── get_libraries ──────────────────────────────────────────────────────────────

async def test_get_libraries_no_credentials_returns_empty_list():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_libraries
    assert await get_libraries(server_id="0") == []


async def test_get_libraries_returns_empty_list_on_network_exception(httpx_mock: HTTPXMock, no_sleep):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_libraries
    assert await get_libraries(server_id="0") == []


# ─── get_users ──────────────────────────────────────────────────────────────────

async def test_get_users_no_credentials_returns_empty_list():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_users
    assert await get_users(server_id="0") == []


async def test_get_users_http_error_returns_empty_list_not_partial_success(httpx_mock: HTTPXMock):
    """A non-200 user list must come back empty, never look like 'zero users
    exist' — that would make every item in the library falsely appear
    unwatched to a caller checking watch state."""
    httpx_mock.add_response(url=f"{FAKE_URL}/Users", status_code=500)
    from backend.emby_client import get_users
    assert await get_users(server_id="0") == []


async def test_get_users_circuit_open_returns_empty_list_without_http_call():
    _open_breaker("0")
    from backend.emby_client import get_users
    assert await get_users(server_id="0") == []


async def test_get_users_network_exception_returns_empty_list(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_users
    assert await get_users(server_id="0") == []


# ─── get_items_in_library ────────────────────────────────────────────────────────

async def test_get_items_in_library_no_credentials_returns_empty_tuple():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_items_in_library
    items, total = await get_items_in_library("lib1", server_id="0")
    assert items == [] and total == 0


async def test_get_items_in_library_circuit_open_returns_empty_tuple():
    _open_breaker("0")
    from backend.emby_client import get_items_in_library
    items, total = await get_items_in_library("lib1", server_id="0")
    assert items == [] and total == 0


async def test_get_items_in_library_network_exception_returns_empty_tuple(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_items_in_library
    items, total = await get_items_in_library("lib1", server_id="0")
    assert items == [] and total == 0


# ─── get_series_tmdb_map ─────────────────────────────────────────────────────────

async def test_get_series_tmdb_map_no_credentials_returns_empty_dict():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_series_tmdb_map
    assert await get_series_tmdb_map("lib1", server_id="0") == {}


async def test_get_series_tmdb_map_skips_items_missing_tmdb_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={
        "Items": [
            {"Id": "s1", "ProviderIds": {"Tmdb": "111"}},
            {"Id": "s2", "ProviderIds": {}},          # no Tmdb id -> excluded
            {"Id": "", "ProviderIds": {"Tmdb": "222"}},  # no series id -> excluded
        ],
        "TotalRecordCount": 3,
    })
    from backend.emby_client import get_series_tmdb_map
    result = await get_series_tmdb_map("lib1", server_id="0")
    assert result == {"s1": "111"}


async def test_get_series_tmdb_map_paginates_across_multiple_requests(httpx_mock: HTTPXMock):
    # Page size is a fixed 500 (start += 500 each round, loop stops once
    # start >= TotalRecordCount) — TotalRecordCount must exceed 500 to force
    # a second request regardless of how many items each page returns.
    httpx_mock.add_response(
        json={"Items": [{"Id": "s1", "ProviderIds": {"Tmdb": "1"}}], "TotalRecordCount": 501},
    )
    httpx_mock.add_response(
        json={"Items": [{"Id": "s2", "ProviderIds": {"Tmdb": "2"}}], "TotalRecordCount": 501},
    )
    from backend.emby_client import get_series_tmdb_map
    result = await get_series_tmdb_map("lib1", server_id="0")
    assert result == {"s1": "1", "s2": "2"}
    assert len(httpx_mock.get_requests()) == 2


async def test_get_series_tmdb_map_stops_on_non_200_and_returns_partial(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "s1", "ProviderIds": {"Tmdb": "1"}}], "TotalRecordCount": 999},
    )
    httpx_mock.add_response(status_code=500)
    from backend.emby_client import get_series_tmdb_map
    result = await get_series_tmdb_map("lib1", server_id="0")
    assert result == {"s1": "1"}


async def test_get_series_tmdb_map_circuit_open_returns_empty_dict():
    _open_breaker("0")
    from backend.emby_client import get_series_tmdb_map
    assert await get_series_tmdb_map("lib1", server_id="0") == {}


async def test_get_series_tmdb_map_network_exception_returns_partial_results(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "s1", "ProviderIds": {"Tmdb": "1"}}], "TotalRecordCount": 999},
    )
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_series_tmdb_map
    result = await get_series_tmdb_map("lib1", server_id="0")
    assert result == {"s1": "1"}


# ─── resolve_item_tmdb ────────────────────────────────────────────────────────────

def test_resolve_item_tmdb_movie_uses_own_provider_id():
    from backend.emby_client import resolve_item_tmdb
    item = {"Type": "Movie", "ProviderIds": {"Tmdb": "555"}}
    assert resolve_item_tmdb(item, {}) == "555"


def test_resolve_item_tmdb_episode_uses_series_map():
    from backend.emby_client import resolve_item_tmdb
    item = {"Type": "Episode", "SeriesId": "s1", "ProviderIds": {"Tvdb": "999"}}
    assert resolve_item_tmdb(item, {"s1": "42"}) == "42"


def test_resolve_item_tmdb_episode_not_in_map_returns_empty_string():
    from backend.emby_client import resolve_item_tmdb
    item = {"Type": "Episode", "SeriesId": "unknown-series"}
    assert resolve_item_tmdb(item, {"s1": "42"}) == ""


def test_resolve_item_tmdb_episode_with_none_map_returns_empty_string():
    from backend.emby_client import resolve_item_tmdb
    item = {"Type": "Episode", "SeriesId": "s1"}
    assert resolve_item_tmdb(item, None) == ""


def test_resolve_item_tmdb_movie_missing_provider_ids_returns_empty_string():
    from backend.emby_client import resolve_item_tmdb
    assert resolve_item_tmdb({"Type": "Movie"}, {}) == ""


# ─── get_library_user_data ───────────────────────────────────────────────────────

async def test_get_library_user_data_no_credentials_raises_media_server_unreachable():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_library_user_data
    with pytest.raises(MediaServerUnreachable):
        await get_library_user_data("user1", "lib1", server_id="0")


async def test_get_library_user_data_paginates_and_merges_all_pages(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "i1", "UserData": {"Played": True}}], "TotalRecordCount": 2},
    )
    httpx_mock.add_response(
        json={"Items": [{"Id": "i2", "UserData": {"Played": False}}], "TotalRecordCount": 2},
    )
    from backend.emby_client import get_library_user_data
    result = await get_library_user_data("user1", "lib1", server_id="0")
    assert result == {"i1": {"Played": True}, "i2": {"Played": False}}


async def test_get_library_user_data_defaults_missing_userdata_to_empty_dict(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={"Items": [{"Id": "i1"}], "TotalRecordCount": 1},
    )
    from backend.emby_client import get_library_user_data
    result = await get_library_user_data("user1", "lib1", server_id="0")
    assert result == {"i1": {}}


async def test_get_library_user_data_raises_on_http_error_instead_of_returning_partial(httpx_mock: HTTPXMock):
    """A truncated/failed fetch must fail loudly — a missing item here means
    'never watched' to callers, so a partial page is worse than an exception."""
    httpx_mock.add_response(status_code=500)
    from backend.emby_client import get_library_user_data
    with pytest.raises(MediaServerUnreachable):
        await get_library_user_data("user1", "lib1", server_id="0")


async def test_get_library_user_data_raises_media_server_unreachable_on_circuit_open():
    _open_breaker("0")
    from backend.emby_client import get_library_user_data
    with pytest.raises(MediaServerUnreachable):
        await get_library_user_data("user1", "lib1", server_id="0")


async def test_get_library_user_data_raises_media_server_unreachable_on_network_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_library_user_data
    with pytest.raises(MediaServerUnreachable):
        await get_library_user_data("user1", "lib1", server_id="0")


# ─── get_user_data ────────────────────────────────────────────────────────────────

async def test_get_user_data_no_credentials_returns_none():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_user_data
    assert await get_user_data("user1", "item1", server_id="0") is None


async def test_get_user_data_success_returns_userdata_dict(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"UserData": {"Played": True, "PlayCount": 3}})
    from backend.emby_client import get_user_data
    result = await get_user_data("user1", "item1", server_id="0")
    assert result == {"Played": True, "PlayCount": 3}


async def test_get_user_data_http_error_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=404)
    from backend.emby_client import get_user_data
    assert await get_user_data("user1", "item1", server_id="0") is None


async def test_get_user_data_network_exception_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_user_data
    assert await get_user_data("user1", "item1", server_id="0") is None


# ─── get_play_activity ────────────────────────────────────────────────────────────

async def test_get_play_activity_no_credentials_returns_empty_dict():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import get_play_activity
    assert await get_play_activity(server_id="0") == {}


async def test_get_play_activity_filters_non_stop_entries_and_keeps_latest_date(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={
        "Items": [
            {"Type": "playback.stop", "ItemId": "i1", "Date": "2026-09-01T10:00:00Z"},
            {"Type": "playback.start", "ItemId": "i1", "Date": "2026-09-05T10:00:00Z"},  # excluded: not a stop
            {"Type": "playback.stop", "ItemId": "i1", "Date": "2026-09-03T10:00:00Z"},   # older than first
            {"Type": "playback.stop", "ItemId": "i2", "Date": "2026-09-02T10:00:00Z"},
        ],
        "TotalRecordCount": 4,
    })
    from backend.emby_client import get_play_activity
    result = await get_play_activity(server_id="0", days=30)
    # i1: most recent *stop* date kept, the later playback.start entry must not win
    assert result["i1"] == "2026-09-03T10:00:00Z"
    assert result["i2"] == "2026-09-02T10:00:00Z"


async def test_get_play_activity_skips_entries_missing_item_id_or_date(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={
        "Items": [
            {"Type": "playback.stop", "ItemId": "", "Date": "2026-09-01T10:00:00Z"},
            {"Type": "playback.stop", "ItemId": "i1", "Date": ""},
            {"Type": "playback.stop", "ItemId": "i2", "Date": "2026-09-01T10:00:00Z"},
        ],
        "TotalRecordCount": 3,
    })
    from backend.emby_client import get_play_activity
    result = await get_play_activity(server_id="0")
    assert result == {"i2": "2026-09-01T10:00:00Z"}


async def test_get_play_activity_raises_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=500)
    from backend.emby_client import get_play_activity
    with pytest.raises(MediaServerUnreachable):
        await get_play_activity(server_id="0")


async def test_get_play_activity_raises_on_circuit_open():
    _open_breaker("0")
    from backend.emby_client import get_play_activity
    with pytest.raises(MediaServerUnreachable):
        await get_play_activity(server_id="0")


async def test_get_play_activity_raises_on_network_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import get_play_activity
    with pytest.raises(MediaServerUnreachable):
        await get_play_activity(server_id="0")


# ─── find_item_by_path ────────────────────────────────────────────────────────────

async def test_find_item_by_path_empty_path_returns_none_without_http_call(httpx_mock: HTTPXMock):
    from backend.emby_client import find_item_by_path
    assert await find_item_by_path("", server_id="0") is None
    assert httpx_mock.get_requests() == []


async def test_find_item_by_path_no_credentials_returns_none():
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import find_item_by_path
    assert await find_item_by_path("/data/x", server_id="0") is None


async def test_find_item_by_path_circuit_open_returns_none():
    _open_breaker("0")
    from backend.emby_client import find_item_by_path
    assert await find_item_by_path("/data/x", server_id="0") is None


async def test_find_item_by_path_network_exception_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(RuntimeError("boom"))
    from backend.emby_client import find_item_by_path
    assert await find_item_by_path("/data/x", server_id="0") is None


# ─── delete_item ────────────────────────────────────────────────────────────────

async def test_delete_item_no_credentials_returns_false_without_http_call(httpx_mock: HTTPXMock):
    await _db_ms.save_media_servers([{"id": "0", "url": "", "api_key": ""}])
    from backend.emby_client import delete_item
    result = await delete_item("item1", server_id="0")
    assert result is False
    assert httpx_mock.get_requests() == []


async def test_delete_item_network_exception_returns_false_not_true(httpx_mock: HTTPXMock, no_sleep):
    """A delete call that never reached the server must never be reported as
    a successful deletion."""
    httpx_mock.add_exception(httpx.ConnectError("down"), is_reusable=True)
    from backend.emby_client import delete_item
    result = await delete_item("item1", server_id="0")
    assert result is False


async def test_delete_item_http_error_status_returns_false(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{FAKE_URL}/Items/item1", status_code=500)
    from backend.emby_client import delete_item
    result = await delete_item("item1", server_id="0")
    assert result is False
