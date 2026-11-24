"""P3 — idempotence and resumability of the deletion flow."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests._qbit_plan_stub import echo_qbit_plan  # noqa: F401  (fixture)
import respx
from pytest_httpx import HTTPXMock

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema
import backend.deletion as _deletion_mod


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "p3.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_deletion_mod, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return db_path


async def _seed(emby_id="e1", status="pending") -> int:
    from backend.db.engine import get_db
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "T", "Movie", "lib", "Lib", "/f.mkv", past, past, status),
        )
        await db.commit()
    return new_id


async def _status(item_id) -> str:
    from backend.db.engine import get_db
    async with get_db() as db:
        return (await db.fetch_one("SELECT status FROM media_queue WHERE id=?", (item_id,)))["status"]


def _cfg(**kw):
    base = dict(dry_run=False, run_id=1, qbit_action="tag_only", qbit_tag="t",
                alert_on_error=False, mention="", msg_tpl="", alert_threshold=3)
    base.update(kw)
    return _deletion_mod._BatchConfig(**base)


# ── (a) 404 = already absent = success ───────────────────────────────────────

async def test_emby_delete_404_is_idempotent_success_when_missing_ok(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://emby.test/Items/x1", status_code=404)
    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))):
        from backend.emby_client import delete_item
        assert await delete_item("x1", missing_ok=True) is True


@pytest.mark.parametrize("code,expected", [(200, True), (204, True), (500, False), (403, False), (401, False)])
async def test_emby_delete_other_codes(httpx_mock: HTTPXMock, code, expected):
    httpx_mock.add_response(url="http://emby.test/Items/x1", status_code=code)
    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))):
        from backend.emby_client import delete_item
        assert await delete_item("x1") is expected


@respx.mock
async def test_plex_delete_404_is_idempotent_success_when_missing_ok():
    from backend.plex_client import PlexClient
    respx.delete("http://plex.local:32400/library/metadata/9").mock(return_value=httpx.Response(404))
    assert await PlexClient(url="http://plex.local:32400", token="t").delete_item("9", missing_ok=True) is True


@respx.mock
async def test_plex_delete_500_still_raises():
    from backend.plex_client import PlexClient
    respx.delete("http://plex.local:32400/library/metadata/9").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await PlexClient(url="http://plex.local:32400", token="t").delete_item("9")


# ── (b) consolidated target not found -> warning ─────────────────────────────

async def test_consolidated_target_not_found_adds_warning():
    from backend.deletion_pipeline import DeletionContext, MediaServerStep
    item = {"title": "S", "emby_id": "sonarr-series:5", "sonarr_series_id": 5}
    ctx = DeletionContext(item=item, dry_run=False)
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value={"path": "/tv/S"})),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value=None)),
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        await MediaServerStep().execute(ctx)
    assert len(ctx.step_warnings) == 1 and "introuvable" in ctx.step_warnings[0]


async def test_consolidated_series_path_unresolved_adds_warning():
    from backend.deletion_pipeline import DeletionContext, MediaServerStep
    item = {"title": "S", "emby_id": "sonarr-series:5", "sonarr_series_id": 5}
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value=None)):
        await MediaServerStep().execute(ctx)
    assert len(ctx.step_warnings) == 1


@pytest.mark.usefixtures("echo_qbit_plan")
async def test_retry_after_media_server_404_runs_remaining_steps(httpx_mock: HTTPXMock):
    """Item already gone from Emby (404) must not block Arr/Seerr/Qbit on retry."""
    from backend.deletion_pipeline import (
        ArrStep, DeletionContext, DeletionPipeline, MediaServerStep, QbitStep, SeerrStep,
    )
    httpx_mock.add_response(url="http://emby.test/Items/e1", status_code=404)
    item = {"title": "M", "emby_id": "e1", "media_type": "Movie", "seerr_id": 3, "attempts": 1}
    ctx = DeletionContext(item=item, dry_run=False, qbit_action="delete_files")
    ctx.torrent_hash = "abc"
    with (
        patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))),
        patch("backend.db.logs.add_log", new=AsyncMock()),
        patch("backend.deletion_helpers.add_log", new=AsyncMock()),
        patch("backend.deletion_helpers._delete_from_arr", new=AsyncMock(return_value=True)) as arr,
        patch("backend.deletion_helpers._delete_from_seerr", new=AsyncMock()) as seerr,
        patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as qbit_h,
    ):
        ok = await DeletionPipeline([MediaServerStep(), ArrStep(), SeerrStep(), QbitStep()]).execute(ctx)
    assert ok is True
    arr.assert_awaited_once()
    seerr.assert_awaited_once()
    qbit_h.assert_awaited_once()


# ── (c) never stay 'deleting' ────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [asyncio.CancelledError(), RuntimeError("boom"), KeyboardInterrupt()])
async def test_delete_one_never_leaves_row_deleting(dbpath, exc):
    item_id = await _seed()
    row = {"id": item_id, "title": "T", "emby_id": "e1"}
    counters = {"errors": 0}
    with patch.object(_deletion_mod, "_delete_media", new=AsyncMock(side_effect=exc)):
        try:
            await _deletion_mod._delete_one(row, _cfg(), asyncio.Semaphore(1), counters)
        except BaseException:
            pass
    assert await _status(item_id) != "deleting"


async def test_delete_one_cancel_returns_row_to_pending(dbpath):
    item_id = await _seed()
    row = {"id": item_id, "title": "T", "emby_id": "e1"}
    with patch.object(_deletion_mod, "_delete_media", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await _deletion_mod._delete_one(row, _cfg(), asyncio.Semaphore(1), {"errors": 0})
    assert await _status(item_id) == "pending"


async def test_delete_now_exception_does_not_leave_row_deleting(dbpath, test_client):
    import backend.routers.media as media_mod
    item_id = await _seed("dn1")
    with patch.object(media_mod, "_delete_media", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await media_mod.delete_now(item_id, user="u")
    assert await _status(item_id) == "pending"


async def test_reset_stale_deleting_runs_under_deletion_lock(dbpath):
    seen = {}

    async def _fake_reset():
        seen["locked"] = _deletion_mod._deletion_lock.locked()
        return 2
    with patch.object(_deletion_mod, "reset_deleting_to_pending", new=_fake_reset):
        assert await _deletion_mod.reset_stale_deleting() == 2
    assert seen["locked"] is True


async def test_reset_stale_deleting_skipped_when_another_worker_holds_lock(dbpath):
    from backend._lock_backend import LockNotAvailable

    class _Busy:
        async def __aenter__(self):
            raise LockNotAvailable("deletion")

        async def __aexit__(self, *a):
            return False
    reset = AsyncMock(return_value=5)
    with patch.object(_deletion_mod, "_deletion_lock", _Busy()), \
            patch.object(_deletion_mod, "reset_deleting_to_pending", new=reset):
        assert await _deletion_mod.reset_stale_deleting() == 0
    reset.assert_not_awaited()


# ── (d) StatsStep failure is a warning ───────────────────────────────────────

async def test_stats_step_failure_warns_and_records(caplog):
    from backend.deletion_pipeline import DeletionContext, StatsStep
    ctx = DeletionContext(item={"title": "S"}, dry_run=False)
    with patch("backend.db.engine.get_db", side_effect=RuntimeError("db down")), \
            caplog.at_level(logging.DEBUG, logger="backend.deletion_pipeline"):
        await StatsStep().execute(ctx)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert len(ctx.step_warnings) == 1 and "db down" in ctx.step_warnings[0]
