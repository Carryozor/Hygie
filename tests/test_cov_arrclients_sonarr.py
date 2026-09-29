"""Coverage tests for backend/arr_clients/sonarr.py.

Priority per the mission brief: delete calls (episode file / season / series)
must hit the exact target server and id, must never fall back to guessing a
server, and torrent-hash resolution must not cross episode/series boundaries.
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.sonarr as sonarr


SONARR_URL = "http://sonarr.test:8989"
SONARR_KEY = "sonarr-test-key"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    async def _config():
        return SONARR_URL, SONARR_KEY
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip real backoff delays in with_retry across every test here."""
    async def _sleep(_delay):
        return None
    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _sleep)


# ─── build_sonarr_path_cache ───────────────────────────────────────────────────

async def test_build_path_cache_maps_episode_files_with_metadata(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series",
        json=[{"id": 5, "title": "Show", "path": "/data/tv/show", "images": [
            {"coverType": "poster", "remoteUrl": "http://cdn/p.jpg"}
        ]}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[{"id": 10, "path": "/data/tv/show/S01E01.mkv", "seasonNumber": 1}],
    )

    cache = await sonarr.build_sonarr_path_cache()
    entry = cache["/data/tv/show/S01E01.mkv"]
    assert entry["ef_id"] == 10
    assert entry["series_id"] == 5
    assert entry["season_number"] == 1
    assert entry["series_title"] == "Show"
    assert entry["poster_url"] == "http://cdn/p.jpg"
    assert entry["srv_url"] == SONARR_URL
    assert entry["srv_key"] == SONARR_KEY


async def test_build_path_cache_skips_series_without_folder(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series", json=[{"id": 5, "title": "NoFolder", "path": ""}]
    )

    cache = await sonarr.build_sonarr_path_cache()
    assert cache == {}


async def test_build_path_cache_ignores_series_listing_error(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/series", status_code=500)

    cache = await sonarr.build_sonarr_path_cache()
    assert cache == {}


async def test_build_path_cache_swallows_series_listing_exception(monkeypatch, httpx_mock: HTTPXMock):
    """A genuine network exception (not just a non-200) on the /series call
    must be caught by the outer handler — one server's outage must not take
    down the whole cache build."""
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_exception(
        httpx.ConnectError("down"), url=f"{SONARR_URL}/api/v3/series", is_reusable=True
    )

    cache = await sonarr.build_sonarr_path_cache()
    assert cache == {}


async def test_build_path_cache_swallows_episodefile_error(monkeypatch, httpx_mock: HTTPXMock):
    """A failing episodefile listing for one series must not raise — the
    series just contributes no cache entries."""
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series",
        json=[{"id": 5, "title": "Show", "path": "/data/tv/show", "images": []}],
    )
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", status_code=500)

    cache = await sonarr.build_sonarr_path_cache()
    assert cache == {}


# ─── sonarr_find_by_path_cached / sonarr_get_cache_entry ──────────────────────

def test_find_by_path_cached_returns_ef_id_from_dict_entry():
    cache = {"/x.mkv": {"ef_id": 10}}
    assert sonarr.sonarr_find_by_path_cached("/x.mkv", cache) == 10


def test_find_by_path_cached_returns_none_for_missing_path():
    assert sonarr.sonarr_find_by_path_cached("/missing.mkv", {}) is None
    assert sonarr.sonarr_find_by_path_cached("/missing.mkv", None) is None


def test_get_cache_entry_returns_full_dict():
    cache = {"/x.mkv": {"ef_id": 10, "series_id": 5}}
    assert sonarr.sonarr_get_cache_entry("/x.mkv", cache) == {"ef_id": 10, "series_id": 5}


def test_get_cache_entry_returns_none_for_non_dict_or_missing():
    assert sonarr.sonarr_get_cache_entry("/x.mkv", {"/x.mkv": 10}) is None
    assert sonarr.sonarr_get_cache_entry("/missing.mkv", {}) is None


# ─── sonarr_find_by_path_full / sonarr_find_by_path ────────────────────────────

async def test_find_by_path_full_returns_none_for_empty_path():
    assert await sonarr.sonarr_find_by_path_full("") is None


async def test_find_by_path_full_matches_across_servers(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series",
        json=[{"id": 5, "path": "/data/tv/show"}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[{"id": 11, "path": "/data/tv/show/S01E02.mkv"}],
    )

    result = await sonarr.sonarr_find_by_path_full("/data/tv/show/S01E02.mkv")
    assert result == (11, SONARR_URL, SONARR_KEY)


async def test_find_by_path_full_skips_series_outside_folder(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series", json=[{"id": 5, "path": "/data/tv/other"}]
    )
    result = await sonarr.sonarr_find_by_path_full("/data/tv/show/S01E02.mkv")
    assert result is None


async def test_find_by_path_full_skips_server_on_non_200_and_checks_next(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [
            {"url": "http://a.test:1", "api_key": "ka"},
            {"url": "http://b.test:1", "api_key": "kb"},
        ]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(url="http://a.test:1/api/v3/series", status_code=500)
    httpx_mock.add_response(
        url="http://b.test:1/api/v3/series", json=[{"id": 5, "path": "/data/tv/show"}]
    )
    httpx_mock.add_response(
        url="http://b.test:1/api/v3/episodefile?seriesId=5",
        json=[{"id": 11, "path": "/data/tv/show/S01E02.mkv"}],
    )

    result = await sonarr.sonarr_find_by_path_full("/data/tv/show/S01E02.mkv")
    assert result == (11, "http://b.test:1", "kb")


async def test_find_by_path_full_swallows_exception_and_continues(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": SONARR_URL, "api_key": SONARR_KEY}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_exception(httpx.ConnectError("down"), url=f"{SONARR_URL}/api/v3/series")
    assert await sonarr.sonarr_find_by_path_full("/data/tv/show/x.mkv") is None


async def test_find_by_path_returns_only_the_id():
    async def _fake(path):
        return (11, "u", "k")
    import backend.arr_clients.sonarr as sm
    orig = sm.sonarr_find_by_path_full
    sm.sonarr_find_by_path_full = _fake
    try:
        assert await sonarr.sonarr_find_by_path("/x.mkv") == 11
    finally:
        sm.sonarr_find_by_path_full = orig


# ─── sonarr_get_series ──────────────────────────────────────────────────────────

async def test_get_series_returns_none_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)
    assert await sonarr.sonarr_get_series(1) is None


async def test_get_series_resolves_series_from_episode_file(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile/10", json={"seriesId": 5}
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series/5", json={"id": 5, "title": "Show"}
    )
    result = await sonarr.sonarr_get_series(10)
    assert result == {"id": 5, "title": "Show"}


async def test_get_series_returns_none_when_episode_file_lookup_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/10", status_code=404)
    assert await sonarr.sonarr_get_series(10) is None


async def test_get_series_returns_none_when_no_series_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/10", json={})
    assert await sonarr.sonarr_get_series(10) is None


async def test_get_series_returns_none_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await sonarr.sonarr_get_series(10) is None


# ─── sonarr_get_series_by_id / _any ────────────────────────────────────────────

async def test_get_series_by_id_returns_none_without_creds_or_id(monkeypatch):
    assert await sonarr.sonarr_get_series_by_id(0) is None
    async def _config():
        return "", ""
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)
    assert await sonarr.sonarr_get_series_by_id(5) is None


async def test_get_series_by_id_returns_series_on_200(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/series/5", json={"id": 5})
    assert await sonarr.sonarr_get_series_by_id(5) == {"id": 5}


async def test_get_series_by_id_returns_none_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await sonarr.sonarr_get_series_by_id(5) is None


async def test_get_series_by_id_any_returns_first_match(monkeypatch):
    async def _servers():
        return [{"url": "http://a:1", "api_key": "ka"}, {"url": "http://b:1", "api_key": "kb"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    async def _by_id(series_id, url="", key=""):
        return {"srv": url} if url == "http://b:1" else None
    monkeypatch.setattr(sonarr, "sonarr_get_series_by_id", _by_id)

    assert await sonarr.sonarr_get_series_by_id_any(5) == {"srv": "http://b:1"}


# ─── sonarr_get_poster_url ──────────────────────────────────────────────────────

async def test_get_poster_url_empty_when_no_series(monkeypatch):
    async def _series(ef_id):
        return None
    monkeypatch.setattr(sonarr, "sonarr_get_series", _series)
    assert await sonarr.sonarr_get_poster_url(1) == ""


async def test_get_poster_url_extracts_from_series_images(monkeypatch):
    async def _series(ef_id):
        return {"images": [{"coverType": "poster", "remoteUrl": "http://cdn/p.jpg"}]}
    monkeypatch.setattr(sonarr, "sonarr_get_series", _series)
    assert await sonarr.sonarr_get_poster_url(1) == "http://cdn/p.jpg"


# ─── sonarr_delete_episode_file — CRITICAL: exact target server/id ─────────────

async def test_delete_episode_file_uses_explicit_url_key_as_is(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://explicit:1/api/v3/episodefile/77", status_code=200)
    ok = await sonarr.sonarr_delete_episode_file(77, url="http://explicit:1", key="explicit-key")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "explicit-key"
    assert req.url.path == "/api/v3/episodefile/77"


async def test_delete_episode_file_resolves_server_via_arr_server_url(monkeypatch, httpx_mock: HTTPXMock):
    """Two servers configured; arr_server_url picks server b. Only b's DELETE
    is mocked — if the code targeted server a instead, httpx_mock would
    raise for the unexpected/unmocked request and this test would fail."""
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://b.test:1", "api_key": "kb"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile/77", status_code=200)

    ok = await sonarr.sonarr_delete_episode_file(77, arr_server_url="http://b.test:1")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_episode_file_refuses_when_server_unresolvable(monkeypatch):
    async def _servers():
        return [{"url": "http://a:1", "api_key": "ka"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    async def _with_retry(fn, *a, **kw):
        raise AssertionError("must not attempt delete when target is unresolved")
    monkeypatch.setattr(sonarr, "with_retry", _with_retry)

    ok = await sonarr.sonarr_delete_episode_file(77, arr_server_url="http://gone:1")
    assert ok is False


async def test_delete_episode_file_falls_back_to_file_path_match(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://b.test:1", "api_key": "kb"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    async def _find_full(path):
        return (77, "http://b.test:1", "kb")
    monkeypatch.setattr(sonarr, "sonarr_find_by_path_full", _find_full)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile/77", status_code=200)

    ok = await sonarr.sonarr_delete_episode_file(77, arr_server_url=None, file_path="/x.mkv")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_episode_file_returns_false_without_id(httpx_mock: HTTPXMock):
    assert await sonarr.sonarr_delete_episode_file(0, url="http://a:1", key="ka") is False


async def test_delete_episode_file_returns_false_on_exception(monkeypatch):
    async def _with_retry(fn, *a, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(sonarr, "with_retry", _with_retry)
    assert await sonarr.sonarr_delete_episode_file(1, url="http://a:1", key="ka") is False


# ─── _sonarr_unmonitor_episodes ────────────────────────────────────────────────

async def test_unmonitor_episodes_noop_for_empty_list(httpx_mock: HTTPXMock):
    async with httpx.AsyncClient() as c:
        await sonarr._sonarr_unmonitor_episodes(c, SONARR_URL, SONARR_KEY, [])
    assert httpx_mock.get_requests() == []


async def test_unmonitor_episodes_sends_put_with_ids(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode/monitor", method="PUT", status_code=200
    )
    async with httpx.AsyncClient() as c:
        await sonarr._sonarr_unmonitor_episodes(c, SONARR_URL, SONARR_KEY, [1, 2, 3])
    req = httpx_mock.get_requests()[0]
    import json
    body = json.loads(req.content)
    assert body == {"episodeIds": [1, 2, 3], "monitored": False}


async def test_unmonitor_episodes_swallows_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    async with httpx.AsyncClient() as c:
        # Must not raise even though the request fails
        await sonarr._sonarr_unmonitor_episodes(c, SONARR_URL, SONARR_KEY, [1])


# ─── _episode_ids_for_files ─────────────────────────────────────────────────────

async def test_episode_ids_for_files_filters_by_episode_file_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5",
        json=[
            {"id": 100, "episodeFileId": 10},
            {"id": 101, "episodeFileId": 11},
            {"id": 102, "episodeFileId": 999},
        ],
    )
    async with httpx.AsyncClient() as c:
        ids = await sonarr._episode_ids_for_files(c, SONARR_URL, SONARR_KEY, 5, {10, 11})
    assert sorted(ids) == [100, 101]


async def test_episode_ids_for_files_returns_empty_on_non_200(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episode?seriesId=5", status_code=500)
    async with httpx.AsyncClient() as c:
        ids = await sonarr._episode_ids_for_files(c, SONARR_URL, SONARR_KEY, 5, {10})
    assert ids == []


# ─── sonarr_delete_season — CRITICAL ───────────────────────────────────────────

async def test_delete_season_bulk_deletes_only_matching_season(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[
            {"id": 10, "seasonNumber": 1},
            {"id": 11, "seasonNumber": 2},
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile/bulk", method="DELETE", status_code=200
    )
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episode?seriesId=5", json=[])

    ok = await sonarr.sonarr_delete_season(5, 1, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True

    bulk_req = next(r for r in httpx_mock.get_requests() if r.url.path == "/api/v3/episodefile/bulk")
    import json
    body = json.loads(bulk_req.content)
    assert body == {"episodeFileIds": [10]}


async def test_delete_season_returns_true_when_season_has_no_files(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", json=[{"id": 10, "seasonNumber": 2}]
    )
    ok = await sonarr.sonarr_delete_season(5, 1, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True


async def test_delete_season_returns_false_on_listing_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", status_code=500)
    ok = await sonarr.sonarr_delete_season(5, 1, url=SONARR_URL, key=SONARR_KEY)
    assert ok is False


async def test_delete_season_resolves_server_via_arr_server_url(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://b.test:1", "api_key": "kb"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile?seriesId=5", json=[])

    ok = await sonarr.sonarr_delete_season(5, 1, arr_server_url="http://b.test:1")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_season_refuses_when_server_unresolvable(monkeypatch):
    async def _servers():
        return [{"url": "http://a:1", "api_key": "ka"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)
    ok = await sonarr.sonarr_delete_season(5, 1, arr_server_url="http://gone:1")
    assert ok is False


async def test_delete_season_falls_back_to_file_path_match(monkeypatch, httpx_mock: HTTPXMock):
    """No arr_server_url and 2+ servers configured -> ambiguous legacy
    resolution refuses, forcing the file-path-match fallback."""
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://c.test:1", "api_key": "kc"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    async def _find_full(path):
        return (11, "http://b.test:1", "kb")
    monkeypatch.setattr(sonarr, "sonarr_find_by_path_full", _find_full)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile?seriesId=5", json=[])

    ok = await sonarr.sonarr_delete_season(5, 1, arr_server_url=None, file_path="/data/x.mkv")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_season_refuses_when_resolved_server_has_blank_key(monkeypatch):
    """Defensive check: even if a target server is "resolved", a blank
    api_key must still refuse rather than send an unauthenticated delete."""
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": ""}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    ok = await sonarr.sonarr_delete_season(5, 1, arr_server_url="http://a.test:1")
    assert ok is False


async def test_delete_season_returns_false_without_creds(monkeypatch):
    async def _servers():
        return []
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)
    assert await sonarr.sonarr_delete_season(5, 1, url="", key="") is False


async def test_delete_season_returns_false_on_exception(monkeypatch):
    async def _with_retry(fn, *a, **kw):
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr(sonarr, "with_retry", _with_retry)
    assert await sonarr.sonarr_delete_season(5, 1, url=SONARR_URL, key=SONARR_KEY) is False


# ─── sonarr_delete_series — CRITICAL ───────────────────────────────────────────

async def test_delete_series_deletes_all_files_and_unmonitors(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[{"id": 10}, {"id": 11}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile/bulk", method="DELETE", status_code=200
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/series/5", method="GET",
        json={"id": 5, "monitored": True, "title": "Show"},
    )
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/series/5", method="PUT", status_code=200)

    ok = await sonarr.sonarr_delete_series(5, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True

    put_req = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    import json
    body = json.loads(put_req.content)
    assert body["monitored"] is False
    assert body["id"] == 5


async def test_delete_series_returns_true_when_no_files(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", json=[])
    ok = await sonarr.sonarr_delete_series(5, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True


async def test_delete_series_returns_false_on_listing_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", status_code=500)
    ok = await sonarr.sonarr_delete_series(5, url=SONARR_URL, key=SONARR_KEY)
    assert ok is False


async def test_delete_series_resolves_server_via_arr_server_url(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://b.test:1", "api_key": "kb"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile?seriesId=5", json=[])

    ok = await sonarr.sonarr_delete_series(5, arr_server_url="http://b.test:1")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_series_refuses_when_server_unresolvable(monkeypatch):
    async def _servers():
        return [{"url": "http://a:1", "api_key": "ka"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)
    ok = await sonarr.sonarr_delete_series(5, arr_server_url="http://gone:1")
    assert ok is False


async def test_delete_series_falls_back_to_file_path_match(monkeypatch, httpx_mock: HTTPXMock):
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": "ka"}, {"url": "http://c.test:1", "api_key": "kc"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    async def _find_full(path):
        return (11, "http://b.test:1", "kb")
    monkeypatch.setattr(sonarr, "sonarr_find_by_path_full", _find_full)

    httpx_mock.add_response(url="http://b.test:1/api/v3/episodefile?seriesId=5", json=[])

    ok = await sonarr.sonarr_delete_series(5, arr_server_url=None, file_path="/data/x.mkv")
    assert ok is True
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-api-key") == "kb"


async def test_delete_series_refuses_when_resolved_server_has_blank_key(monkeypatch):
    async def _servers():
        return [{"url": "http://a.test:1", "api_key": ""}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)

    ok = await sonarr.sonarr_delete_series(5, arr_server_url="http://a.test:1")
    assert ok is False


async def test_delete_series_returns_false_without_creds(monkeypatch):
    async def _servers():
        return []
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _servers)
    assert await sonarr.sonarr_delete_series(5, url="", key="") is False


async def test_delete_series_returns_false_on_exception(monkeypatch):
    async def _with_retry(fn, *a, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(sonarr, "with_retry", _with_retry)
    assert await sonarr.sonarr_delete_series(5, url=SONARR_URL, key=SONARR_KEY) is False


# ─── sonarr_get_torrent_hash ─────────────────────────────────────────────────────

async def test_get_torrent_hash_returns_none_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)
    assert await sonarr.sonarr_get_torrent_hash(1) is None


async def test_get_torrent_hash_resolves_via_series_history(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/10", json={"seriesId": 5})
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[{"downloadId": "AABBCCDDEEFF00112233445566778899AABBCC"}],
    )
    result = await sonarr.sonarr_get_torrent_hash(10)
    assert result == "aabbccddeeff00112233445566778899aabbcc"


async def test_get_torrent_hash_returns_none_when_episode_file_lookup_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/10", status_code=404)
    assert await sonarr.sonarr_get_torrent_hash(10) is None


async def test_get_torrent_hash_returns_none_when_no_series_id(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/10", json={})
    assert await sonarr.sonarr_get_torrent_hash(10) is None


async def test_get_torrent_hash_returns_none_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await sonarr.sonarr_get_torrent_hash(10) is None


# ─── sonarr_get_torrent_hashes_for_group ───────────────────────────────────────

async def test_get_hashes_for_group_returns_empty_without_creds(monkeypatch):
    async def _config():
        return "", ""
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)
    assert await sonarr.sonarr_get_torrent_hashes_for_group(5) == set()


async def test_get_hashes_for_group_filters_by_season(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[
            {"id": 10, "seasonNumber": 1},
            {"id": 11, "seasonNumber": 2},
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5",
        json=[
            {"id": 100, "episodeFileId": 10},
            {"id": 101, "episodeFileId": 11},
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[
            {"episodeId": 100, "downloadId": "AA00000000000000000000000000000000AAAA"},
            {"episodeId": 101, "downloadId": "BB00000000000000000000000000000000BBBB"},
        ],
    )
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, season_number=1, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == {"aa00000000000000000000000000000000aaaa"}


async def test_get_hashes_for_group_no_season_returns_all_series_hashes(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5",
        json=[{"id": 10, "seasonNumber": 1}, {"id": 11, "seasonNumber": 2}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5",
        json=[{"id": 100, "episodeFileId": 10}, {"id": 101, "episodeFileId": 11}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[
            {"episodeId": 100, "downloadId": "AA00000000000000000000000000000000AAAA"},
            {"episodeId": 101, "downloadId": "BB00000000000000000000000000000000BBBB"},
        ],
    )
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == {
        "aa00000000000000000000000000000000aaaa",
        "bb00000000000000000000000000000000bbbb",
    }


async def test_get_hashes_for_group_returns_empty_when_no_episode_files(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", json=[])
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == set()


async def test_get_hashes_for_group_returns_empty_when_episodefile_listing_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", status_code=500)
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == set()


async def test_get_hashes_for_group_returns_empty_when_no_matching_episodes(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", json=[{"id": 10, "seasonNumber": 1}]
    )
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episode?seriesId=5", json=[])
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == set()


async def test_get_hashes_for_group_returns_empty_when_history_listing_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId=5", json=[{"id": 10, "seasonNumber": 1}]
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5", json=[{"id": 100, "episodeFileId": 10}]
    )
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/history/series?seriesId=5", status_code=500)
    hashes = await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY)
    assert hashes == set()


async def test_get_hashes_for_group_returns_empty_on_exception(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await sonarr.sonarr_get_torrent_hashes_for_group(5, url=SONARR_URL, key=SONARR_KEY) == set()
