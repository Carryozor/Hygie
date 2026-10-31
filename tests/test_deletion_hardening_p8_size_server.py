"""P8a (size of the concerned files) and P8c (arr_server_url routing)."""
from unittest.mock import AsyncMock, patch

import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.radarr as radarr
import backend.arr_clients.sonarr as sonarr

S1 = "http://sonarr1.test:8989"
S2 = "http://sonarr2.test:8989"
R1 = "http://radarr1.test:7878"
R2 = "http://radarr2.test:7878"


@pytest.fixture(autouse=True)
def _servers(monkeypatch):
    async def _s():
        return [{"url": S1, "api_key": "k1"}, {"url": S2 + "/", "api_key": "k2"}]

    async def _r():
        return [{"url": R1, "api_key": "k1"}, {"url": R2, "api_key": "k2"}]
    monkeypatch.setattr(sonarr, "get_sonarr_servers", _s)
    monkeypatch.setattr(radarr, "get_radarr_servers", _r)

    async def _sleep(_d):
        return None
    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _sleep)


FILES = [
    {"id": 1, "seasonNumber": 1, "size": 100},
    {"id": 2, "seasonNumber": 1, "size": 200},
    {"id": 3, "seasonNumber": 2, "size": 400},
]


# ── sonarr_get_files_size ────────────────────────────────────────────────────

async def test_files_size_single_episode(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{S1}/api/v3/episodefile?seriesId=9", json=FILES)
    assert await sonarr.sonarr_get_files_size(9, episode_file_id=2, arr_server_url=S1) == 200


async def test_files_size_season(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{S1}/api/v3/episodefile?seriesId=9", json=FILES)
    assert await sonarr.sonarr_get_files_size(9, season_number=1, arr_server_url=S1) == 300


async def test_files_size_unknown_file_is_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{S1}/api/v3/episodefile?seriesId=9", json=FILES)
    assert await sonarr.sonarr_get_files_size(9, episode_file_id=99, arr_server_url=S1) is None


async def test_files_size_http_error_is_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{S1}/api/v3/episodefile?seriesId=9", status_code=500)
    assert await sonarr.sonarr_get_files_size(9, season_number=1, arr_server_url=S1) is None


# ── SizeLookupStep ───────────────────────────────────────────────────────────

def _ctx(item):
    from backend.deletion_pipeline import DeletionContext
    return DeletionContext(item=item, dry_run=False)


async def test_size_step_isolated_episode_uses_file_size_not_series():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "E", "media_type": "Episode", "sonarr_id": 2, "sonarr_series_id": 9,
                "arr_server_url": S1})
    series = AsyncMock(return_value={"statistics": {"sizeOnDisk": 999999}})
    files = AsyncMock(return_value=200)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any", new=series), \
            patch("backend.arr_clients.sonarr_get_files_size", new=files):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 200
    files.assert_awaited_once_with(9, season_number=None, episode_file_id=2, arr_server_url=S1)


async def test_size_step_season_uses_season_files():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "S", "media_type": "Episode", "sonarr_series_id": 9, "season_number": 1})
    files = AsyncMock(return_value=300)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock()), \
            patch("backend.arr_clients.sonarr_get_files_size", new=files):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 300
    assert files.await_args.kwargs["season_number"] == 1


async def test_size_step_falls_back_to_series_size_when_files_unavailable():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "S", "media_type": "Episode", "sonarr_series_id": 9, "season_number": 1})
    with patch("backend.arr_clients.sonarr_get_series_by_id_any",
               new=AsyncMock(return_value={"statistics": {"sizeOnDisk": 777}})), \
            patch("backend.arr_clients.sonarr_get_files_size", new=AsyncMock(return_value=None)):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 777


async def test_size_step_whole_series_keeps_series_size():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "S", "media_type": "Episode", "sonarr_series_id": 9})
    files = AsyncMock(return_value=1)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any",
               new=AsyncMock(return_value={"statistics": {"sizeOnDisk": 5000}})), \
            patch("backend.arr_clients.sonarr_get_files_size", new=files):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 5000
    files.assert_not_awaited()


async def test_size_step_passes_arr_server_url_for_movie():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "M", "media_type": "Movie", "radarr_id": 4, "arr_server_url": R2})
    get_any = AsyncMock(return_value={"movieFile": {"size": 42}})
    with patch("backend.arr_clients.radarr_get_any", new=get_any):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 42
    get_any.assert_awaited_once_with(4, arr_server_url=R2)


async def test_size_step_movie_without_server_url_keeps_old_call():
    from backend.deletion_pipeline import SizeLookupStep
    ctx = _ctx({"title": "M", "media_type": "Movie", "radarr_id": 4})
    get_any = AsyncMock(return_value={"movieFile": {"size": 42}})
    with patch("backend.arr_clients.radarr_get_any", new=get_any):
        await SizeLookupStep().execute(ctx)
    get_any.assert_awaited_once_with(4)


# ── *_any routing ────────────────────────────────────────────────────────────

async def test_radarr_hash_any_queries_only_target_server(httpx_mock: HTTPXMock):
    h = "ab" * 20
    httpx_mock.add_response(url=f"{R2}/api/v3/history/movie?movieId=4", json=[{"downloadId": h}])
    assert await radarr.radarr_get_torrent_hash_any(4, arr_server_url=R2) == h
    assert [str(r.url) for r in httpx_mock.get_requests()] == [f"{R2}/api/v3/history/movie?movieId=4"]


async def test_radarr_hash_any_unknown_server_url_queries_nobody(httpx_mock: HTTPXMock):
    assert await radarr.radarr_get_torrent_hash_any(4, arr_server_url="http://gone.test") is None
    assert httpx_mock.get_requests() == []


async def test_radarr_hash_any_without_url_tries_servers_in_order(httpx_mock: HTTPXMock):
    h = "cd" * 20
    httpx_mock.add_response(url=f"{R1}/api/v3/history/movie?movieId=4", json=[])
    httpx_mock.add_response(url=f"{R1}/api/v3/history?movieId=4&pageSize=20", json={"records": []})
    httpx_mock.add_response(url=f"{R2}/api/v3/history/movie?movieId=4", json=[{"downloadId": h}])
    assert await radarr.radarr_get_torrent_hash_any(4) == h


async def test_sonarr_series_any_targets_server(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{S2}/api/v3/series/9", json={"id": 9})
    assert await sonarr.sonarr_get_series_by_id_any(9, arr_server_url=S2) == {"id": 9}
    assert len(httpx_mock.get_requests()) == 1


async def test_sonarr_torrent_hash_resolves_server_from_arr_server_url(httpx_mock: HTTPXMock):
    h = "ef" * 20
    httpx_mock.add_response(url=f"{S2}/api/v3/episodefile/7", json={"seriesId": 3})
    httpx_mock.add_response(url=f"{S2}/api/v3/episode?seriesId=3", json=[{"id": 70, "episodeFileId": 7}])
    httpx_mock.add_response(url=f"{S2}/api/v3/history/series?seriesId=3", json=[{"episodeId": 70, "downloadId": h}])
    assert await sonarr.sonarr_get_torrent_hash(7, arr_server_url=S2) == h


async def test_find_torrent_hash_forwards_arr_server_url():
    from backend.deletion_helpers import _find_torrent_hash
    row = {"media_type": "Movie", "radarr_id": 4, "arr_server_url": R2, "file_path": "/x"}
    with patch("backend.deletion_helpers.radarr_get_torrent_hash_any", new=AsyncMock(return_value="h")) as m:
        await _find_torrent_hash(row)
    m.assert_awaited_once_with(4, arr_server_url=R2)
    row = {"media_type": "Episode", "sonarr_id": 7, "arr_server_url": S1, "file_path": "/x"}
    with patch("backend.deletion_helpers.sonarr_get_torrent_hash", new=AsyncMock(return_value="h")) as m:
        await _find_torrent_hash(row)
    m.assert_awaited_once_with(7, arr_server_url=S1)
