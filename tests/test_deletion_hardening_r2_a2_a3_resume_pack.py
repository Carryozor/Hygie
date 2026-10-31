"""R2/A2 (resume after interruption: Arr 404 tolerance, persisted torrent hash)
and A3 (season pack shared with episodes still in the library)."""
from unittest.mock import AsyncMock, patch

import pytest
from pytest_httpx import HTTPXMock

import backend.arr_clients.sonarr as sonarr
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema

SURL, SKEY = "http://sonarr.test:8989", "k"
RURL, RKEY = "http://radarr.test:7878", "k"
HASH = "aa00000000000000000000000000000000aaaa"
HASH2 = "bb00000000000000000000000000000000bbbb"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    async def _sleep(_d):
        return None
    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _sleep)


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.deletion as _d
    path = str(tmp_path / "a2.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_d, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


async def _seed(status="deleting", attempts=0, torrent_hash="", media_type="Movie") -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        nid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, attempts, torrent_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("e1", "T", media_type, "l", "L", "/f.mkv", "2026-01-01", "2026-01-01",
             status, attempts, torrent_hash),
        )
        await db.commit()
    return nid


async def _stored_hash(nid) -> str:
    from backend.db.engine import get_db
    async with get_db() as db:
        return (await db.fetch_one("SELECT torrent_hash FROM media_queue WHERE id=?", (nid,)))["torrent_hash"]


# ═══ A2a — 404 tolerance in arr clients ═════════════════════════════════════

async def test_radarr_delete_404_fails_by_default(httpx_mock: HTTPXMock):
    from backend.arr_clients.radarr import radarr_delete
    httpx_mock.add_response(method="DELETE", status_code=404)
    assert await radarr_delete(7, url=RURL, key=RKEY) is False


async def test_radarr_delete_404_ok_with_missing_ok(httpx_mock: HTTPXMock):
    from backend.arr_clients.radarr import radarr_delete
    httpx_mock.add_response(method="DELETE", status_code=404)
    assert await radarr_delete(7, url=RURL, key=RKEY, missing_ok=True) is True


async def test_radarr_delete_500_still_fails_with_missing_ok(httpx_mock: HTTPXMock):
    from backend.arr_clients.radarr import radarr_delete
    httpx_mock.add_response(method="DELETE", status_code=500)
    assert await radarr_delete(7, url=RURL, key=RKEY, missing_ok=True) is False


async def test_sonarr_episode_file_404_default_vs_missing_ok(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="DELETE", status_code=404)
    assert await sonarr.sonarr_delete_episode_file(5, url=SURL, key=SKEY) is False
    httpx_mock.add_response(method="DELETE", status_code=404)
    assert await sonarr.sonarr_delete_episode_file(5, url=SURL, key=SKEY, missing_ok=True) is True


async def test_sonarr_season_404_on_listing_default_vs_missing_ok(httpx_mock: HTTPXMock):
    httpx_mock.add_response(method="GET", status_code=404)
    assert await sonarr.sonarr_delete_season(1, 2, url=SURL, key=SKEY) is False
    httpx_mock.add_response(method="GET", status_code=404)
    assert await sonarr.sonarr_delete_season(1, 2, url=SURL, key=SKEY, missing_ok=True) is True


async def test_sonarr_series_404_on_bulk_delete_default_vs_missing_ok(httpx_mock: HTTPXMock):
    for _ in range(2):
        httpx_mock.add_response(method="GET", json=[{"id": 1, "seasonNumber": 1}])
        httpx_mock.add_response(method="DELETE", status_code=404)
    assert await sonarr.sonarr_delete_series(1, url=SURL, key=SKEY) is False
    assert await sonarr.sonarr_delete_series(1, url=SURL, key=SKEY, missing_ok=True) is True


async def test_arr_step_passes_missing_ok_only_on_retry():
    from backend.deletion_pipeline import ArrStep, DeletionContext
    for attempts, expected in ((0, None), (2, True)):
        ctx = DeletionContext(item={"title": "M", "attempts": attempts}, dry_run=False)
        with patch("backend.deletion_helpers._delete_from_arr", new=AsyncMock(return_value=True)) as m:
            await ArrStep().execute(ctx)
        assert m.await_args.kwargs.get("missing_ok") is expected


async def test_delete_from_arr_forwards_missing_ok_to_radarr():
    from backend.deletion_helpers import _delete_from_arr
    row = {"media_type": "Movie", "radarr_id": 3, "title": "M", "file_path": "/f"}
    with (
        patch("backend.deletion_helpers.radarr_delete_by_id", new=AsyncMock(return_value=True)) as m,
        patch("backend.deletion_helpers.add_log", new=AsyncMock()),
    ):
        await _delete_from_arr(row, missing_ok=True)
    assert m.await_args.kwargs["missing_ok"] is True


# ═══ A2b — persisted torrent hash ═══════════════════════════════════════════

async def test_first_attempt_persists_resolved_single_hash(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed()
    ctx = DeletionContext(item={"id": nid, "title": "M", "media_type": "Movie", "attempts": 0}, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=HASH)):
        await TorrentHashStep().execute(ctx)
    assert ctx.torrent_hash == HASH
    assert await _stored_hash(nid) == HASH


async def test_first_attempt_persists_hash_set_joined(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed(media_type="Episode")
    item = {"id": nid, "title": "S", "media_type": "Episode", "sonarr_series_id": 5, "season_number": 1}
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hashes_consolidated",
               new=AsyncMock(return_value={HASH, HASH2})):
        await TorrentHashStep().execute(ctx)
    assert set((await _stored_hash(nid)).split(",")) == {HASH, HASH2}


async def test_unresolved_hash_writes_nothing(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed()
    ctx = DeletionContext(item={"id": nid, "title": "M", "media_type": "Movie"}, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=None)):
        await TorrentHashStep().execute(ctx)
    assert await _stored_hash(nid) == ""


async def test_persisted_hash_used_when_fresh_lookup_finds_nothing(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed(attempts=1, torrent_hash=HASH)
    ctx = DeletionContext(item={"id": nid, "title": "M", "media_type": "Movie", "attempts": 1}, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=None)) as find:
        await TorrentHashStep().execute(ctx)
    find.assert_awaited_once()  # R3/F1: fresh lookup first, persisted = fallback
    assert ctx.torrent_hash == HASH


async def test_persisted_hash_set_used_for_consolidated(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed(attempts=1, torrent_hash=f"{HASH},{HASH2}", media_type="Episode")
    item = {"id": nid, "title": "S", "media_type": "Episode", "sonarr_series_id": 5, "season_number": 1,
            "attempts": 1}
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hashes_consolidated",
               new=AsyncMock(return_value=set())) as find:
        await TorrentHashStep().execute(ctx)
    find.assert_awaited_once()  # R3/F1: fresh lookup first, persisted = fallback
    assert ctx.torrent_hashes == {HASH, HASH2}


async def test_dry_run_persists_nothing(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed()
    ctx = DeletionContext(item={"id": nid, "title": "M", "media_type": "Movie"}, dry_run=True)
    with patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=HASH)):
        await TorrentHashStep().execute(ctx)
    assert await _stored_hash(nid) == ""


async def test_resume_after_interruption_runs_arr_seerr_qbit_with_persisted_hash(dbpath):
    """Retry (attempts=1): hash read from DB, ArrStep tolerant, Seerr+Qbit still run."""
    from backend.deletion_pipeline import (
        ArrStep, DeletionContext, DeletionPipeline, QbitStep, SeerrStep, TorrentHashStep,
    )
    nid = await _seed(attempts=1, torrent_hash=HASH)
    item = {"id": nid, "title": "M", "media_type": "Movie", "attempts": 1, "seerr_id": 4}
    ctx = DeletionContext(item=item, dry_run=False, qbit_action="delete_files")
    with (
        patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=None)) as find,
        patch("backend.deletion_helpers._delete_from_arr", new=AsyncMock(return_value=True)) as arr,
        patch("backend.deletion_helpers._delete_from_seerr", new=AsyncMock()) as seerr,
        patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as qb,
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        ok = await DeletionPipeline([TorrentHashStep(), ArrStep(), SeerrStep(), QbitStep()]).execute(ctx)
    assert ok is True
    find.assert_awaited_once()
    assert arr.await_args.kwargs["missing_ok"] is True
    seerr.assert_awaited_once()
    assert qb.await_args.args[0] == HASH


# ═══ A3 — shared season pack ════════════════════════════════════════════════

def _history(*pairs):
    return [{"episodeId": e, "downloadId": h.upper(), "eventType": "grabbed"} for e, h in pairs]


async def test_sonarr_remaining_counts_files_still_linked(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SURL}/api/v3/history/series?seriesId=5",
                            json=_history((1, HASH), (2, HASH), (3, HASH), (9, HASH2)))
    httpx_mock.add_response(url=f"{SURL}/api/v3/episodefile?seriesId=5", json=[
        {"id": 12, "episodeIds": [2]}, {"id": 13, "episodeIds": [3]}, {"id": 19, "episodeIds": [9]},
    ])
    assert await sonarr.sonarr_remaining_files_for_hash(5, HASH, url=SURL, key=SKEY) == 2


async def test_sonarr_remaining_zero_when_all_files_gone(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SURL}/api/v3/history/series?seriesId=5",
                            json=_history((1, HASH), (2, HASH)))
    httpx_mock.add_response(url=f"{SURL}/api/v3/episodefile?seriesId=5",
                            json=[{"id": 19, "episodeIds": [9]}])
    assert await sonarr.sonarr_remaining_files_for_hash(5, HASH, url=SURL, key=SKEY) == 0


async def test_sonarr_remaining_none_when_unverifiable(httpx_mock: HTTPXMock):
    # no history record for the hash -> cannot establish which episodes it covers
    httpx_mock.add_response(url=f"{SURL}/api/v3/history/series?seriesId=5", json=_history((9, HASH2)))
    assert await sonarr.sonarr_remaining_files_for_hash(5, HASH, url=SURL, key=SKEY) is None


async def test_sonarr_remaining_none_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{SURL}/api/v3/history/series?seriesId=5", status_code=500)
    assert await sonarr.sonarr_remaining_files_for_hash(5, HASH, url=SURL, key=SKEY) is None


def _qbit_ctx(action="delete_files", media_type="Episode", **item):
    from backend.deletion_pipeline import DeletionContext
    base = {"title": "S01E02", "media_type": media_type, "sonarr_series_id": 5, "sonarr_id": 12}
    base.update(item)
    ctx = DeletionContext(item=base, dry_run=False, qbit_action=action)
    ctx.torrent_hash = HASH
    return ctx


async def _run_qbit(ctx, remaining):
    from backend.deletion_pipeline import QbitStep
    with (
        patch("backend.arr_clients.sonarr_remaining_files_for_hash",
              new=AsyncMock(side_effect=remaining if isinstance(remaining, Exception) else None,
                            return_value=None if isinstance(remaining, Exception) else remaining)) as chk,
        patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as qb,
        patch("backend.db.logs.add_log", new=AsyncMock()) as log,
    ):
        await QbitStep().execute(ctx)
    return chk, qb, log


async def test_shared_pack_with_remaining_episode_is_kept_and_warned():
    ctx = _qbit_ctx()
    chk, qb, log = await _run_qbit(ctx, 2)
    qb.assert_not_awaited()
    assert any("torrent partagé avec 2 autre(s) épisode(s) encore présent(s) — conservé" in w
               for w in ctx.step_warnings)
    assert any(c.args[0] == "WARN" for c in log.await_args_list)


async def test_pack_deleted_when_no_file_remains():
    ctx = _qbit_ctx()
    chk, qb, _ = await _run_qbit(ctx, 0)
    qb.assert_awaited_once()
    assert ctx.step_warnings == []


async def test_sonarr_unreachable_means_no_delete():
    ctx = _qbit_ctx()
    chk, qb, _ = await _run_qbit(ctx, None)
    qb.assert_not_awaited()
    assert ctx.step_warnings


async def test_check_exception_means_no_delete():
    ctx = _qbit_ctx()
    chk, qb, _ = await _run_qbit(ctx, RuntimeError("down"))
    qb.assert_not_awaited()
    assert ctx.step_warnings


async def test_movie_is_unchanged_no_check():
    ctx = _qbit_ctx(media_type="Movie")
    chk, qb, _ = await _run_qbit(ctx, 5)
    chk.assert_not_awaited()
    qb.assert_awaited_once()


@pytest.mark.parametrize("action", ["delete_torrent", "tag_only", "pause"])
async def test_non_file_actions_never_blocked(action):
    ctx = _qbit_ctx(action=action)
    chk, qb, _ = await _run_qbit(ctx, 5)
    chk.assert_not_awaited()
    qb.assert_awaited_once()


async def test_consolidated_checks_every_hash_and_keeps_only_shared_ones():
    from backend.deletion_pipeline import DeletionContext, QbitStep
    ctx = DeletionContext(
        item={"title": "S", "media_type": "Episode", "sonarr_series_id": 5, "season_number": 1},
        dry_run=False, qbit_action="delete_files")
    ctx.torrent_hashes = {HASH, HASH2}

    async def _remaining(series_id, h, **kw):
        return 1 if h == HASH else 0
    with (
        patch("backend.arr_clients.sonarr_remaining_files_for_hash", new=_remaining),
        patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as qb,
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        await QbitStep().execute(ctx)
    assert [c.args[0] for c in qb.await_args_list] == [HASH2]
    assert len(ctx.step_warnings) == 1


async def test_missing_series_id_on_episode_row_is_not_deleted():
    ctx = _qbit_ctx(sonarr_series_id=None)
    chk, qb, _ = await _run_qbit(ctx, 0)
    qb.assert_not_awaited()
    assert ctx.step_warnings
