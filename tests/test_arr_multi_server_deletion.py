"""Multi-Radarr/Sonarr deletion routing — regression tests for the
media_queue.arr_server_url column (m016).

Root cause: media_queue only stored bare radarr_id/sonarr_id/sonarr_series_id
numeric ids, never which configured server they belonged to.
radarr_delete_by_id() tried the same numeric id on EVERY configured Radarr
and stopped at the first success — with two Radarr servers both happening to
have a movie with id 42, this could delete the WRONG one. Sonarr's delete
functions were called without url/key and fell back to the single legacy
sonarr_url/sonarr_api_key setting — a different bug with the same effect
(wrong server targeted) whenever `sonarr_servers` (multi-server setting) is
used instead of the legacy single config.

Fix: arr_server_url is recorded on the queue row at scan time. Deletion
resolves the exact server by url; legacy rows (arr_server_url NULL) fall
back to the single configured server or a file-path match; if neither
resolves, deletion refuses (returns False) instead of guessing.
"""
import pytest
from pytest_httpx import HTTPXMock

from backend.arr_clients.radarr import radarr_delete_by_id
from backend.arr_clients.sonarr import (
    sonarr_delete_episode_file,
    sonarr_delete_season,
    sonarr_delete_series,
)

SERVER_A = "http://radarr-a.test:7878"
SERVER_B = "http://radarr-b.test:7878"
SONARR_A = "http://sonarr-a.test:8989"
SONARR_B = "http://sonarr-b.test:8989"


@pytest.fixture(autouse=True)
async def fresh_settings_db(monkeypatch, tmp_path):
    """Own temp SQLite DB per test, mirrors tests/test_arr_clients.py's fixture."""
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "arr_multi_test.db")
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


async def _set_radarr_servers(servers: list[dict]) -> None:
    import json
    from backend.db.settings_store import set_setting
    await set_setting("radarr_servers", json.dumps(servers))


async def _set_sonarr_servers(servers: list[dict]) -> None:
    import json
    from backend.db.settings_store import set_setting
    await set_setting("sonarr_servers", json.dumps(servers))


# ─── Radarr: two servers sharing the same numeric id ───────────────────────

async def test_radarr_delete_by_id_routes_only_to_the_bound_server(httpx_mock: HTTPXMock):
    """Two Radarr servers both have a movie with id=42. arr_server_url must
    route the DELETE to server B only — server A must receive nothing."""
    await _set_radarr_servers([
        {"id": "a", "name": "A", "url": SERVER_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SERVER_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SERVER_B}/api/v3/movie/42?deleteFiles=false&addImportExclusion=false", status_code=200)

    ok = await radarr_delete_by_id(42, delete_files=False, arr_server_url=SERVER_B)

    assert ok is True
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].url.host == "radarr-b.test"
    assert requests[0].headers.get("x-api-key") == "key-b"


async def test_radarr_delete_by_id_refuses_when_bound_server_no_longer_configured(httpx_mock: HTTPXMock):
    """arr_server_url points at a server that's no longer in the configured
    list (removed/reconfigured) — must refuse rather than guess another one."""
    await _set_radarr_servers([
        {"id": "a", "name": "A", "url": SERVER_A, "api_key": "key-a", "enabled": True},
    ])

    ok = await radarr_delete_by_id(42, delete_files=False, arr_server_url=SERVER_B)

    assert ok is False
    assert len(httpx_mock.get_requests()) == 0


async def test_radarr_delete_by_id_legacy_row_single_server_still_works(httpx_mock: HTTPXMock):
    """arr_server_url is None (legacy row) but only one server is configured
    — unambiguous, must still delete."""
    await _set_radarr_servers([
        {"id": "a", "name": "A", "url": SERVER_A, "api_key": "key-a", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SERVER_A}/api/v3/movie/42?deleteFiles=false&addImportExclusion=false", status_code=200)

    ok = await radarr_delete_by_id(42, delete_files=False, arr_server_url=None)

    assert ok is True
    assert len(httpx_mock.get_requests()) == 1


async def test_radarr_delete_by_id_legacy_row_multi_server_no_path_match_fails_closed(httpx_mock: HTTPXMock):
    """arr_server_url is None, 2 servers configured, and the file path
    matches neither server's library — must NOT delete anything anywhere."""
    await _set_radarr_servers([
        {"id": "a", "name": "A", "url": SERVER_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SERVER_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SERVER_A}/api/v3/movie", json=[])
    httpx_mock.add_response(url=f"{SERVER_B}/api/v3/movie", json=[])

    ok = await radarr_delete_by_id(
        42, delete_files=False, arr_server_url=None, file_path="/movies/unmatched.mkv"
    )

    assert ok is False
    delete_requests = [r for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert delete_requests == []


async def test_radarr_delete_by_id_legacy_row_multi_server_path_match_resolves(httpx_mock: HTTPXMock):
    """arr_server_url is None, 2 servers, but the file path matches server B's
    library — must delete on server B only."""
    await _set_radarr_servers([
        {"id": "a", "name": "A", "url": SERVER_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SERVER_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SERVER_A}/api/v3/movie", json=[])
    httpx_mock.add_response(
        url=f"{SERVER_B}/api/v3/movie",
        json=[{"id": 42, "path": "/movies/dune", "movieFile": {"path": "/movies/dune/dune.mkv"}}],
    )
    httpx_mock.add_response(url=f"{SERVER_B}/api/v3/movie/42?deleteFiles=false&addImportExclusion=false", status_code=200)

    ok = await radarr_delete_by_id(
        42, delete_files=False, arr_server_url=None, file_path="/movies/dune/dune.mkv"
    )

    assert ok is True
    delete_requests = [r for r in httpx_mock.get_requests() if r.method == "DELETE"]
    assert len(delete_requests) == 1
    assert delete_requests[0].url.host == "radarr-b.test"


# ─── Sonarr: episode file delete ───────────────────────────────────────────

async def test_sonarr_delete_episode_file_routes_only_to_the_bound_server(httpx_mock: HTTPXMock):
    await _set_sonarr_servers([
        {"id": "a", "name": "A", "url": SONARR_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SONARR_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SONARR_B}/api/v3/episodefile/901", status_code=200)

    ok = await sonarr_delete_episode_file(901, arr_server_url=SONARR_B)

    assert ok is True
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].url.host == "sonarr-b.test"


async def test_sonarr_delete_episode_file_legacy_multi_server_no_path_match_fails_closed(httpx_mock: HTTPXMock):
    await _set_sonarr_servers([
        {"id": "a", "name": "A", "url": SONARR_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SONARR_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SONARR_A}/api/v3/series", json=[])
    httpx_mock.add_response(url=f"{SONARR_B}/api/v3/series", json=[])

    ok = await sonarr_delete_episode_file(
        901, arr_server_url=None, file_path="/series/unmatched/ep.mkv"
    )

    assert ok is False
    assert [r for r in httpx_mock.get_requests() if r.method == "DELETE"] == []


async def test_sonarr_delete_episode_file_legacy_single_server_still_works(httpx_mock: HTTPXMock):
    await _set_sonarr_servers([
        {"id": "a", "name": "A", "url": SONARR_A, "api_key": "key-a", "enabled": True},
    ])
    httpx_mock.add_response(url=f"{SONARR_A}/api/v3/episodefile/901", status_code=200)

    ok = await sonarr_delete_episode_file(901, arr_server_url=None)

    assert ok is True


# ─── Sonarr: season / series consolidated delete ───────────────────────────

async def test_sonarr_delete_season_routes_only_to_the_bound_server(httpx_mock: HTTPXMock):
    await _set_sonarr_servers([
        {"id": "a", "name": "A", "url": SONARR_A, "api_key": "key-a", "enabled": True},
        {"id": "b", "name": "B", "url": SONARR_B, "api_key": "key-b", "enabled": True},
    ])
    httpx_mock.add_response(
        url=f"{SONARR_B}/api/v3/episodefile?seriesId=274",
        json=[{"id": 1, "seasonNumber": 1}],
    )
    httpx_mock.add_response(url=f"{SONARR_B}/api/v3/episodefile/bulk", method="DELETE", status_code=200)
    httpx_mock.add_response(url=f"{SONARR_B}/api/v3/episode?seriesId=274", json=[])

    ok = await sonarr_delete_season(274, 1, arr_server_url=SONARR_B)

    assert ok is True
    hosts = {r.url.host for r in httpx_mock.get_requests()}
    assert hosts == {"sonarr-b.test"}


async def test_sonarr_delete_series_refuses_when_bound_server_not_configured(httpx_mock: HTTPXMock):
    await _set_sonarr_servers([
        {"id": "a", "name": "A", "url": SONARR_A, "api_key": "key-a", "enabled": True},
    ])

    ok = await sonarr_delete_series(274, arr_server_url=SONARR_B)

    assert ok is False
    assert httpx_mock.get_requests() == []
