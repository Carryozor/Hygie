"""Tests for arr_clients — verifies X-Api-Key header for Radarr and Sonarr."""
import pytest
from pytest_httpx import HTTPXMock

RADARR_URL = "http://radarr.test:7878"
RADARR_KEY  = "radarr-test-key"
SONARR_URL  = "http://sonarr.test:8989"
SONARR_KEY  = "sonarr-test-key"


@pytest.fixture(autouse=True)
async def mock_arr_config(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.logs as _db_logs
    db_path = str(tmp_path / "arr_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    await _db_ss.set_setting("radarr_url", RADARR_URL)
    await _db_ss.set_setting("radarr_api_key", RADARR_KEY)
    await _db_ss.set_setting("sonarr_url", SONARR_URL)
    await _db_ss.set_setting("sonarr_api_key", SONARR_KEY)


def _assert_uses_header_not_query(httpx_mock: HTTPXMock, key: str) -> None:
    for req in httpx_mock.get_requests():
        assert req.headers.get("x-api-key") == key, (
            f"Request to {req.url} missing X-Api-Key header"
        )
        assert "apikey" not in str(req.url).lower(), (
            f"Request to {req.url} leaks apikey in URL"
        )


# ─── Radarr ──────────────────────────────────────────────────────────────────

async def test_radarr_test_connection_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/system/status",
        json={"version": "5.3.0"},
    )
    from backend.arr_clients import test_radarr
    ok, msg = await test_radarr()
    assert ok is True
    _assert_uses_header_not_query(httpx_mock, RADARR_KEY)


async def test_radarr_delete_uses_header(httpx_mock: HTTPXMock):
    # DELETE includes deleteFiles and addImportExclusion query params
    httpx_mock.add_response(status_code=200)
    from backend.arr_clients import radarr_delete
    await radarr_delete(42, delete_files=False)
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == RADARR_KEY
    assert "apikey" not in str(req.url).lower()


async def test_radarr_get_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/movie/42",
        json={"id": 42, "title": "Test Movie", "images": []},
    )
    from backend.arr_clients import radarr_get
    result = await radarr_get(42)
    assert result is not None
    _assert_uses_header_not_query(httpx_mock, RADARR_KEY)


async def test_build_radarr_path_cache_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{RADARR_URL}/api/v3/movie",
        json=[{"id": 1, "path": "/data/movies/test", "movieFile": {"path": "/data/movies/test/test.mkv"}}],
    )
    from backend.arr_clients import build_radarr_path_cache
    cache = await build_radarr_path_cache()
    assert len(cache) > 0
    _assert_uses_header_not_query(httpx_mock, RADARR_KEY)


async def test_radarr_http_error_returns_gracefully(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{RADARR_URL}/api/v3/system/status", status_code=401)
    from backend.arr_clients import test_radarr
    ok, msg = await test_radarr()
    assert ok is False


# ─── Sonarr ──────────────────────────────────────────────────────────────────

async def test_sonarr_test_connection_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/system/status",
        json={"version": "4.0.0"},
    )
    from backend.arr_clients import test_sonarr
    ok, msg = await test_sonarr()
    assert ok is True
    _assert_uses_header_not_query(httpx_mock, SONARR_KEY)


async def test_sonarr_delete_episode_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/99", status_code=200)
    from backend.arr_clients import sonarr_delete_episode_file
    await sonarr_delete_episode_file(99)
    _assert_uses_header_not_query(httpx_mock, SONARR_KEY)


async def test_build_sonarr_path_cache_uses_header(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series",
        json=[{"id": 1, "path": "/data/series/test"}],
    )
    # episodefile endpoint is called with ?seriesId=1 query param
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=1",
        json=[{"id": 10, "path": "/data/series/test/S01E01.mkv"}],
    )
    from backend.arr_clients import build_sonarr_path_cache
    cache = await build_sonarr_path_cache()
    assert "/data/series/test/S01E01.mkv" in cache
    for req in httpx_mock.get_requests():
        assert req.headers.get("x-api-key") == SONARR_KEY
        assert "apikey" not in str(req.url).lower()


async def test_sonarr_http_error_returns_gracefully(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/system/status", status_code=403)
    from backend.arr_clients import test_sonarr
    ok, msg = await test_sonarr()
    assert ok is False


# ─── _arr_auth helper ─────────────────────────────────────────────────────────

def test_arr_auth_returns_correct_header():
    from backend.arr_clients import _arr_auth
    assert _arr_auth("mykey") == {"X-Api-Key": "mykey"}
    assert _arr_auth("") == {"X-Api-Key": ""}
