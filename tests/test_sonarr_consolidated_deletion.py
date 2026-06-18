"""Regression tests for the consolidated season/series deletion path.

Bug: deleting a whole series (e.g. "Lupin") via the consolidated deletion_unit
feature wiped the library files via Sonarr's bulk episodefile delete, but:
  1. sonarr_get_torrent_hash() grabbed the first downloadId found anywhere in
     the series' history, regardless of which episode it belonged to — so
     qBittorrent received a delete call for an unrelated/wrong torrent hash,
     leaving the real season-pack torrent (and its files) untouched on disk.
  2. The series stayed monitored in Sonarr after its files were wiped, so
     Sonarr could re-grab the "missing" episodes on its next RSS sync.
"""
import json

import pytest
from pytest_httpx import HTTPXMock

SONARR_URL = "http://sonarr.test:8989"
SONARR_KEY = "sonarr-test-key"


@pytest.fixture(autouse=True)
async def mock_sonarr_config(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "sonarr_consolidated_test.db")
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
    await _db_ss.set_setting("sonarr_url", SONARR_URL)
    await _db_ss.set_setting("sonarr_api_key", SONARR_KEY)


# ─── sonarr_get_torrent_hashes_for_group ────────────────────────────────────

async def test_hash_lookup_filters_by_actual_episode_not_first_in_history(httpx_mock: HTTPXMock):
    """The resolver must only return hashes belonging to the episodes actually
    being deleted — not just the first downloadId encountered in the series'
    full history (which may belong to an unrelated, still-intact episode)."""
    series_id = 274

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[
            {"id": 901, "seasonNumber": 1},
            {"id": 902, "seasonNumber": 1},
            {"id": 999, "seasonNumber": 2},  # different season — excluded
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId={series_id}",
        json=[
            {"id": 11, "episodeFileId": 901},
            {"id": 12, "episodeFileId": 902},
            {"id": 13, "episodeFileId": 999},
            {"id": 14, "episodeFileId": 5000},  # unrelated episode/file entirely
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId={series_id}",
        json=[
            # Listed first, but belongs to an episode outside this group —
            # the old buggy lookup would have returned this hash.
            {"episodeId": 14, "downloadId": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "eventType": "downloadFolderImported"},
            {"episodeId": 11, "downloadId": "d889dbee954035e9abdb4ce89a14ef6102561b97", "eventType": "downloadFolderImported"},
            {"episodeId": 12, "downloadId": "d889dbee954035e9abdb4ce89a14ef6102561b97", "eventType": "downloadFolderImported"},
        ],
    )

    from backend.arr_clients.sonarr import sonarr_get_torrent_hashes_for_group
    hashes = await sonarr_get_torrent_hashes_for_group(
        series_id, season_number=1, url=SONARR_URL, key=SONARR_KEY
    )

    assert hashes == {"d889dbee954035e9abdb4ce89a14ef6102561b97"}


async def test_hash_lookup_for_whole_series_spans_multiple_torrents(httpx_mock: HTTPXMock):
    """A series-level delete can cover several seasons downloaded as separate
    torrents (e.g. a season-1 pack and a season-3 pack) — every distinct hash
    actually backing the deleted files must be returned, not just one."""
    series_id = 274

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[{"id": 901, "seasonNumber": 1}, {"id": 903, "seasonNumber": 3}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId={series_id}",
        json=[
            {"id": 11, "episodeFileId": 901},
            {"id": 31, "episodeFileId": 903},
        ],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId={series_id}",
        json=[
            {"episodeId": 11, "downloadId": "d889dbee954035e9abdb4ce89a14ef6102561b97", "eventType": "downloadFolderImported"},
            {"episodeId": 31, "downloadId": "2002516002ff4ada20d68e43e7ede20bea8de675", "eventType": "downloadFolderImported"},
        ],
    )

    from backend.arr_clients.sonarr import sonarr_get_torrent_hashes_for_group
    hashes = await sonarr_get_torrent_hashes_for_group(series_id, url=SONARR_URL, key=SONARR_KEY)

    assert hashes == {
        "d889dbee954035e9abdb4ce89a14ef6102561b97",
        "2002516002ff4ada20d68e43e7ede20bea8de675",
    }


async def test_hash_lookup_returns_empty_set_without_history(httpx_mock: HTTPXMock):
    series_id = 274
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[{"id": 901, "seasonNumber": 1}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId={series_id}",
        json=[{"id": 11, "episodeFileId": 901}],
    )
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/history/series?seriesId={series_id}",
        json=[],
    )

    from backend.arr_clients.sonarr import sonarr_get_torrent_hashes_for_group
    hashes = await sonarr_get_torrent_hashes_for_group(series_id, url=SONARR_URL, key=SONARR_KEY)

    assert hashes == set()
    # Must reach the actual history call, not return empty via an early exit —
    # an early-return bug would also produce an empty set and pass otherwise.
    assert len(httpx_mock.get_requests()) == 3


# ─── sonarr_delete_series / sonarr_delete_season — unmonitor after wipe ─────

async def test_sonarr_delete_series_unmonitors_after_deleting_files(httpx_mock: HTTPXMock):
    series_id = 274

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[{"id": 901}, {"id": 902}],
    )
    httpx_mock.add_response(method="DELETE", url=f"{SONARR_URL}/api/v3/episodefile/bulk", status_code=200)
    httpx_mock.add_response(
        method="GET",
        url=f"{SONARR_URL}/api/v3/series/{series_id}",
        json={"id": series_id, "title": "Lupin", "monitored": True, "path": "/data/media/series/Lupin"},
    )
    httpx_mock.add_response(method="PUT", url=f"{SONARR_URL}/api/v3/series/{series_id}", status_code=202)

    from backend.arr_clients.sonarr import sonarr_delete_series
    ok = await sonarr_delete_series(series_id, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True

    put_requests = [r for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert len(put_requests) == 1, "series must be unmonitored via a single PUT"
    body = json.loads(put_requests[0].content)
    assert body["monitored"] is False


async def test_sonarr_delete_series_skips_unmonitor_when_no_files(httpx_mock: HTTPXMock):
    """If there were no episode files to begin with, there's nothing to unmonitor for."""
    series_id = 274
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[],
    )

    from backend.arr_clients.sonarr import sonarr_delete_series
    ok = await sonarr_delete_series(series_id, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True
    assert not any(r.method in ("PUT", "DELETE") for r in httpx_mock.get_requests())


async def test_sonarr_delete_season_unmonitors_only_that_seasons_episodes(httpx_mock: HTTPXMock):
    series_id = 274
    season = 1

    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episodefile?seriesId={series_id}",
        json=[
            {"id": 901, "seasonNumber": 1},
            {"id": 902, "seasonNumber": 1},
            {"id": 999, "seasonNumber": 2},
        ],
    )
    httpx_mock.add_response(method="DELETE", url=f"{SONARR_URL}/api/v3/episodefile/bulk", status_code=200)
    httpx_mock.add_response(
        url=f"{SONARR_URL}/api/v3/episode?seriesId={series_id}",
        json=[
            {"id": 11, "episodeFileId": 901, "seasonNumber": 1},
            {"id": 12, "episodeFileId": 902, "seasonNumber": 1},
            {"id": 13, "episodeFileId": 999, "seasonNumber": 2},
        ],
    )
    httpx_mock.add_response(method="PUT", url=f"{SONARR_URL}/api/v3/episode/monitor", status_code=202)

    from backend.arr_clients.sonarr import sonarr_delete_season
    ok = await sonarr_delete_season(series_id, season, url=SONARR_URL, key=SONARR_KEY)
    assert ok is True

    put_requests = [r for r in httpx_mock.get_requests() if r.method == "PUT"]
    assert len(put_requests) == 1
    body = json.loads(put_requests[0].content)
    assert body["monitored"] is False
    assert sorted(body["episodeIds"]) == [11, 12]
