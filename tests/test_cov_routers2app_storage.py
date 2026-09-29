"""Coverage tests for backend/routers/storage.py.

`_fetch_storage_data` aggregates disk/library stats from Radarr and Sonarr —
the numbers feed straight into the dashboard, so the aggregation math itself
(counts, sizes, reclaimable size) is worth asserting precisely, not just
"the endpoint returned 200".

`get_storage()` is stale-while-revalidate: cache state (`_storage_cache`,
`_storage_refresh_task`) is module-global, reset per-test via an autouse
fixture mirroring tests/test_storage_refresh_task_lifecycle.py.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import backend.routers.storage as storage


@pytest.fixture(autouse=True)
def _clean_storage_state():
    storage._storage_refresh_task = None
    storage._storage_cache.update({"data": None, "ts": 0.0})
    yield
    storage._storage_refresh_task = None
    storage._storage_cache.update({"data": None, "ts": 0.0})


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _mock_httpx_client(get_side_effect):
    """Build a patch context for httpx.AsyncClient whose .get() delegates to
    get_side_effect(url, headers) -> _FakeResp | raises."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _get(url, headers=None, **kw):
        return get_side_effect(url, headers)

    mock_client.get = _get
    return patch("httpx.AsyncClient", return_value=mock_client)


RADARR_DISK = [{"path": "/movies", "label": "Movies", "totalSpace": 1000, "freeSpace": 400, "accessible": True}]
RADARR_MOVIES = [
    {"id": 1, "hasFile": True, "monitored": True, "sizeOnDisk": 5_000_000_000},
    {"id": 2, "hasFile": False, "monitored": True, "sizeOnDisk": 0},
    {"id": 3, "hasFile": True, "monitored": False, "sizeOnDisk": 2_000_000_000},
]
SONARR_DISK = [{"path": "/movies", "label": "dup", "totalSpace": 1, "freeSpace": 1, "accessible": True},
               {"path": "/tv", "label": "TV", "totalSpace": 2000, "freeSpace": 900, "accessible": True}]
SONARR_SERIES = [
    {"monitored": True, "statistics": {"episodeFileCount": 10, "totalEpisodeCount": 12, "episodeCount": 11, "sizeOnDisk": 3_000_000_000}},
    {"monitored": False, "statistics": {"episodeFileCount": 0, "totalEpisodeCount": 5, "episodeCount": 5, "sizeOnDisk": 0}},
]


@pytest.fixture(autouse=True)
async def _settings_and_db(monkeypatch, tmp_path):
    """Isolated DB + radarr/sonarr settings so _fetch_storage_data has both
    a URL/key (to trigger the httpx calls) and a real media_queue table."""
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.engine as _eng

    db_path = str(tmp_path / "storage_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    yield db_path


async def _set_arr_settings():
    from backend.db.settings_store import set_setting
    await set_setting("radarr_url", "http://radarr.local:7878")
    await set_setting("radarr_api_key", "rkey")
    await set_setting("sonarr_url", "http://sonarr.local:8989")
    await set_setting("sonarr_api_key", "skey")


def _default_responses(url, headers):
    if "diskspace" in url and "7878" in url:
        return _FakeResp(200, RADARR_DISK)
    if url.endswith("/movie"):
        return _FakeResp(200, RADARR_MOVIES)
    if "diskspace" in url and "8989" in url:
        return _FakeResp(200, SONARR_DISK)
    if url.endswith("/series"):
        return _FakeResp(200, SONARR_SERIES)
    raise AssertionError(f"unexpected URL {url}")


async def test_fetch_storage_data_aggregates_radarr_movies():
    await _set_arr_settings()
    with _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert result["movies"] == {
        "total_in_library": 3, "count": 2, "monitored": 2, "unmonitored": 1,
        "size": 7_000_000_000,
    }


async def test_fetch_storage_data_aggregates_sonarr_series():
    await _set_arr_settings()
    with _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert result["series"] == {
        "count": 2, "monitored": 1, "unmonitored": 1,
        "episodes": 10, "episodes_aired": 16, "episodes_total": 17,
        "size": 3_000_000_000,
    }


async def test_fetch_storage_data_total_media_size_sums_both_sources():
    await _set_arr_settings()
    with _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert result["total_media_size"] == 7_000_000_000 + 3_000_000_000


async def test_fetch_storage_data_dedupes_disk_path_shared_by_radarr_and_sonarr():
    """Sonarr's diskspace includes /movies (same path Radarr already reported) —
    must not appear twice."""
    await _set_arr_settings()
    with _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    paths = [d["path"] for d in result["disks"]]
    assert paths.count("/movies") == 1
    assert "/tv" in paths
    assert len(result["disks"]) == 2


async def test_fetch_storage_data_empty_when_no_arr_settings_configured():
    """No radarr/sonarr URL+key configured -> no httpx calls, empty structures."""
    called = []

    def _fail(url, headers):
        called.append(url)
        raise AssertionError("must not call arr when unconfigured")

    with _mock_httpx_client(_fail):
        result = await storage._fetch_storage_data()
    assert called == []
    assert result["disks"] == []
    assert result["movies"] == {}
    assert result["series"] == {}
    assert result["total_media_size"] == 0


async def test_fetch_storage_data_treats_http_error_as_no_data():
    await _set_arr_settings()

    def _all_500(url, headers):
        return _FakeResp(500, {})

    with _mock_httpx_client(_all_500):
        result = await storage._fetch_storage_data()
    assert result["disks"] == []
    assert result["movies"] == {}


async def test_fetch_storage_data_treats_network_exception_as_no_data():
    await _set_arr_settings()

    def _raise(url, headers):
        raise ConnectionError("refused")

    with _mock_httpx_client(_raise):
        result = await storage._fetch_storage_data()
    assert result["disks"] == []
    assert result["series"] == {}


async def test_fetch_storage_data_reclaimable_size_sums_pending_radarr_movies():
    await _set_arr_settings()

    with patch(
        "backend.routers.storage.get_radarr_ids", new=AsyncMock(return_value=[1, 3])
    ), patch(
        "backend.routers.storage.get_status_counts",
        new=AsyncMock(return_value={"pending": 2, "deleted": 0, "error": 0}),
    ), _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()

    # movie ids 1 and 3 are pending and exist in radarr_movies_by_id
    assert result["queue"]["reclaimable_size"] == 5_000_000_000 + 2_000_000_000
    assert result["queue"]["reclaimable_count"] == 2


async def test_fetch_storage_data_reclaimable_count_falls_back_to_pending_total_when_no_radarr_match():
    await _set_arr_settings()
    with patch(
        "backend.routers.storage.get_radarr_ids", new=AsyncMock(return_value=[])
    ), patch(
        "backend.routers.storage.get_status_counts",
        new=AsyncMock(return_value={"pending": 7, "deleted": 0, "error": 0}),
    ), _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert result["queue"]["reclaimable_count"] == 7
    assert result["queue"]["reclaimable_size"] == 0


async def test_fetch_storage_data_queue_stats_failure_is_swallowed():
    await _set_arr_settings()
    with patch(
        "backend.routers.storage.get_status_counts", new=AsyncMock(side_effect=RuntimeError("db down"))
    ), _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    # Falls back to the zeroed default queue dict — must not raise.
    assert result["queue"]["pending"] == 0


def test_invalidate_storage_cache_clears_data_and_timestamp():
    storage._storage_cache.update({"data": {"x": 1}, "ts": 123.0})
    storage.invalidate_storage_cache()
    assert storage._storage_cache == {"data": None, "ts": 0.0}


# ─── get_storage() stale-while-revalidate ──────────────────────────────────

async def test_get_storage_cold_start_awaits_fetch_directly():
    with patch.object(storage, "_fetch_storage_data", new=AsyncMock(return_value={"disks": [], "movies": {}, "series": {}, "total_media_size": 0, "queue": {}})) as mock_fetch:
        result = await storage.get_storage(user="testuser")
    mock_fetch.assert_awaited_once()
    assert result == {"disks": [], "movies": {}, "series": {}, "total_media_size": 0, "queue": {}}


async def test_get_storage_returns_cached_data_within_ttl():
    import time
    storage._storage_cache.update({"data": {"cached": True}, "ts": time.time()})
    with patch.object(storage, "_fetch_storage_data", new=AsyncMock()) as mock_fetch:
        result = await storage.get_storage(user="testuser")
    assert result == {"cached": True}
    mock_fetch.assert_not_awaited()


async def test_get_storage_stale_data_returns_immediately_and_spawns_refresh():
    import time
    storage._storage_cache.update({"data": {"stale": True}, "ts": time.time() - 10_000})

    refresh_started = asyncio.Event()

    async def _slow_fetch():
        refresh_started.set()
        await asyncio.sleep(3600)

    with patch.object(storage, "_fetch_storage_data", new=_slow_fetch):
        result = await storage.get_storage(user="testuser")
        assert result == {"stale": True}
        await asyncio.wait_for(refresh_started.wait(), timeout=1)
        assert storage._storage_refresh_task is not None
        assert not storage._storage_refresh_task.done()

    await storage.cancel_storage_refresh()


async def test_get_storage_does_not_spawn_second_refresh_if_one_already_running():
    import time
    storage._storage_cache.update({"data": {"stale": True}, "ts": time.time() - 10_000})

    calls = []

    async def _slow_fetch():
        calls.append(1)
        await asyncio.sleep(3600)

    existing_task = asyncio.create_task(_slow_fetch())
    await asyncio.sleep(0)  # let it start
    storage._storage_refresh_task = existing_task

    with patch.object(storage, "_fetch_storage_data", new=_slow_fetch):
        await storage.get_storage(user="testuser")

    assert len(calls) == 1  # only the pre-existing task ran, no second one spawned
    await storage.cancel_storage_refresh()
