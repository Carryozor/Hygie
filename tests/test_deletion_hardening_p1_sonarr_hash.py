"""P1 — sonarr_get_torrent_hash must only return a hash that belongs to the
episode of the targeted file, never the first downloadId of the whole series."""
import httpx
import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.sonarr as sonarr

SONARR_URL = "http://sonarr.test:8989"
SONARR_KEY = "sonarr-test-key"
HASH_A = "aa00000000000000000000000000000000aaaa"
HASH_B = "bb00000000000000000000000000000000bbbb"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    async def _config():
        return SONARR_URL, SONARR_KEY
    monkeypatch.setattr(sonarr, "_sonarr_config", _config)

    async def _sleep(_delay):
        return None
    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _sleep)


def _mock_series(httpx_mock: HTTPXMock, file_id: int):
    """Season pack A -> episodes 1,2,3 (files 11,12,13); isolated grab B -> episode 4 (file 14)."""
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/{file_id}", json={"seriesId": 5})
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5",
        json=[
            {"id": 1, "episodeFileId": 11}, {"id": 2, "episodeFileId": 12},
            {"id": 3, "episodeFileId": 13}, {"id": 4, "episodeFileId": 14},
            {"id": 5, "episodeFileId": 0},
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[
            # newest first, as Sonarr returns it: the pack A rows come BEFORE B
            {"episodeId": 1, "downloadId": HASH_A.upper(), "eventType": "grabbed"},
            {"episodeId": 2, "downloadId": HASH_A.upper(), "eventType": "grabbed"},
            {"episodeId": 3, "downloadId": HASH_A.upper(), "eventType": "grabbed"},
            {"episodeId": 4, "downloadId": HASH_B.upper(), "eventType": "grabbed"},
        ],
    )


async def test_isolated_grab_resolves_to_its_own_hash(httpx_mock: HTTPXMock):
    _mock_series(httpx_mock, 14)
    assert await sonarr.sonarr_get_torrent_hash(14) == HASH_B


async def test_pack_episode_resolves_to_pack_hash(httpx_mock: HTTPXMock):
    _mock_series(httpx_mock, 12)
    assert await sonarr.sonarr_get_torrent_hash(12) == HASH_A


async def test_episode_without_history_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/15", json={"seriesId": 5})
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId=5",
        json=[{"id": 6, "episodeFileId": 15}, {"id": 1, "episodeFileId": 11}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[{"episodeId": 1, "downloadId": HASH_A, "eventType": "grabbed"}],
    )
    assert await sonarr.sonarr_get_torrent_hash(15) is None


async def test_file_not_linked_to_any_episode_returns_none(httpx_mock: HTTPXMock):
    """Membership cannot be established -> None, never a foreign hash."""
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/99", json={"seriesId": 5})
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episode?seriesId=5", json=[{"id": 1, "episodeFileId": 11}])
    assert await sonarr.sonarr_get_torrent_hash(99) is None


async def test_prefers_imported_record_over_failed_regrab(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episodefile/14", json={"seriesId": 5})
    httpx_mock.add_response(url=f"{SONARR_URL}/api/v3/episode?seriesId=5", json=[{"id": 4, "episodeFileId": 14}])
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId=5",
        json=[
            {"episodeId": 4, "downloadId": HASH_A, "eventType": "grabbed", "date": "2026-02-01T00:00:00Z"},
            {"episodeId": 4, "downloadId": HASH_B, "eventType": "downloadFolderImported", "date": "2026-01-01T00:00:00Z"},
        ],
    )
    assert await sonarr.sonarr_get_torrent_hash(14) == HASH_B


async def test_network_failure_uses_retry_and_returns_none(httpx_mock: HTTPXMock):
    for _ in range(3):
        httpx_mock.add_exception(httpx.ConnectError("down"))
    assert await sonarr.sonarr_get_torrent_hash(14) is None
