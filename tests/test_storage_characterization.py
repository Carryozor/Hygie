"""Characterization tests for the storage aggregation (written against the
pre-service-layer code, kept green across the routers/storage.py ->
services/storage_service.py extraction).

They pin the branches the coverage tests left open: pending rows absent from
Radarr, the `ignored_media` count, NULL sizes, single-source setups, URL
normalisation, client timeout and cache update.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

import backend.routers.storage as storage
from backend.db.utils import TIMEOUT_MEDIUM
from tests.test_cov_routers2app_storage import (  # noqa: F401  (autouse fixtures + helpers)
    _FakeResp,
    _clean_storage_state,
    _default_responses,
    _mock_httpx_client,
    _set_arr_settings,
    _settings_and_db,
)

pytestmark = pytest.mark.asyncio


async def test_reclaimable_skips_pending_ids_unknown_to_radarr():
    await _set_arr_settings()
    with patch("backend.routers.storage.get_radarr_ids", new=AsyncMock(return_value=[1, 999])), \
         patch("backend.routers.storage.get_status_counts",
               new=AsyncMock(return_value={"pending": 2, "deleted": 0, "error": 0})), \
         _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert result["queue"]["reclaimable_size"] == 5_000_000_000
    assert result["queue"]["reclaimable_count"] == 1


async def test_reclaimable_not_computed_without_radarr_movies():
    await _set_arr_settings()
    get_ids = AsyncMock(return_value=[1])

    def _sonarr_only(url, headers):
        if "8989" in url:
            return _default_responses(url, headers)
        return _FakeResp(500, {})

    with patch("backend.routers.storage.get_radarr_ids", new=get_ids), \
         patch("backend.routers.storage.get_status_counts",
               new=AsyncMock(return_value={"pending": 4, "deleted": 0, "error": 0})), \
         _mock_httpx_client(_sonarr_only):
        result = await storage._fetch_storage_data()
    get_ids.assert_not_awaited()
    assert result["queue"]["reclaimable_size"] == 0
    assert result["queue"]["reclaimable_count"] == 0
    assert result["movies"] == {}


async def test_queue_counts_statuses_and_excluded_rows():
    await _set_arr_settings()
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("INSERT INTO ignored_media (emby_id, title, ignored_at) VALUES ('a','A','2026-01-01')")
        await db.execute("INSERT INTO ignored_media (emby_id, title, ignored_at) VALUES ('b','B','2026-01-01')")
        await db.commit()
    with patch("backend.routers.storage.get_radarr_ids", new=AsyncMock(return_value=[])), \
         patch("backend.routers.storage.get_status_counts",
               new=AsyncMock(return_value={"pending": 3, "deleted": 2, "error": 1, "other": 9})), \
         _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    q = result["queue"]
    assert (q["pending"], q["deleted"], q["error"], q["excluded"]) == (3, 2, 1, 2)
    assert "other" not in q


async def test_missing_and_null_fields_default_safely():
    await _set_arr_settings()

    def _sparse(url, headers):
        if "7878" in url and "diskspace" in url:
            return _FakeResp(200, [{}])
        if url.endswith("/movie"):
            return _FakeResp(200, [{"id": 1, "sizeOnDisk": None, "hasFile": True}])
        if "8989" in url and "diskspace" in url:
            return _FakeResp(200, [{}, {"path": "/x"}])
        return _FakeResp(200, [{"monitored": True, "statistics": {"sizeOnDisk": None, "episodeFileCount": None}}])

    with _mock_httpx_client(_sparse):
        result = await storage._fetch_storage_data()
    assert result["disks"][0] == {
        "path": "?", "label": "", "source": "Radarr", "total": 0, "free": 0, "accessible": True,
    }
    # Sonarr's path-less disk is "?" too -> deduped against Radarr's; "/x" kept
    assert [d["path"] for d in result["disks"]] == ["?", "/x"]
    assert result["movies"] == {"total_in_library": 1, "count": 1, "monitored": 0, "unmonitored": 1, "size": 0}
    assert result["series"]["size"] == 0 and result["series"]["episodes"] == 0
    assert result["total_media_size"] == 0


async def test_only_radarr_configured_calls_only_radarr_with_trimmed_url_and_key():
    from backend.db.settings_store import set_setting
    await set_setting("radarr_url", "http://radarr.local:7878///")
    await set_setting("radarr_api_key", "rkey")
    seen = []

    def _rec(url, headers):
        seen.append((url, dict(headers)))
        return _default_responses(url, headers)

    with _mock_httpx_client(_rec):
        result = await storage._fetch_storage_data()
    assert sorted(u for u, _ in seen) == [
        "http://radarr.local:7878/api/v3/diskspace",
        "http://radarr.local:7878/api/v3/movie",
    ]
    assert all(h.get("X-Api-Key") == "rkey" for _, h in seen)
    assert result["series"] == {} and [d["source"] for d in result["disks"]] == ["Radarr"]


async def test_arr_requests_keep_medium_timeout():
    await _set_arr_settings()
    with _mock_httpx_client(_default_responses) as factory:
        await storage._fetch_storage_data()
    assert factory.call_args.kwargs == {"timeout": TIMEOUT_MEDIUM}


async def test_fetch_updates_cache_with_returned_object():
    await _set_arr_settings()
    before = time.time()
    with _mock_httpx_client(_default_responses):
        result = await storage._fetch_storage_data()
    assert storage._storage_cache["data"] is result
    assert before <= storage._storage_cache["ts"] <= time.time()
    assert set(result) == {"disks", "movies", "series", "total_media_size", "queue"}
