"""R2/B2 (manual delete paths record failures like the job) and
B3 (the job only claims 'error' rows that are due and under the attempt cap)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.deletion as _d
    path = str(tmp_path / "b2.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_d, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


def _iso(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


async def _seed(emby_id="e1", status="deleting", **cols) -> int:
    from backend.db.engine import get_db
    past = _iso(hours=-1)
    async with get_db() as db:
        nid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "T", "Movie", "lib", "Lib", "/f.mkv", past, past, status),
        )
        for k, v in cols.items():
            await db.execute(f"UPDATE media_queue SET {k}=? WHERE id=?", (v, nid))
        await db.commit()
    return nid


async def _row(nid) -> dict:
    from backend.db.engine import get_db
    async with get_db() as db:
        return dict(await db.fetch_one("SELECT * FROM media_queue WHERE id=?", (nid,)))


# ═══ B3 ═════════════════════════════════════════════════════════════════════

async def test_b3_scheduled_claim_rules(dbpath):
    from backend.db.repositories import claim_for_deletion
    pend = await _seed("p", "pending")
    due = await _seed("d", "error", attempts=2, next_retry_at=_iso(minutes=-5))
    future = await _seed("f", "error", attempts=1, next_retry_at=_iso(hours=2))
    capped = await _seed("c", "error", attempts=5, next_retry_at=_iso(minutes=-5))
    final = await _seed("n", "error", attempts=5, next_retry_at=None)
    legacy = await _seed("l", "error")
    assert await claim_for_deletion(pend, scheduled=True)
    assert await claim_for_deletion(due, scheduled=True)
    for nid in (future, capped, final, legacy):
        assert not await claim_for_deletion(nid, scheduled=True)
        assert (await _row(nid))["status"] == "error"


async def test_b3_manual_claim_can_still_target_error_rows(dbpath):
    from backend.db.repositories import claim_for_deletion
    future = await _seed("f", "error", attempts=1, next_retry_at=_iso(hours=2))
    final = await _seed("n", "error", attempts=5, next_retry_at=None)
    assert await claim_for_deletion(future) and await claim_for_deletion(final)


async def test_b3_job_claim_uses_scheduled_mode(dbpath):
    import backend.deletion as d
    future = await _seed("f", "error", attempts=1, next_retry_at=_iso(hours=2))
    assert await d._claim_pending(future) is False
    assert (await _row(future))["status"] == "error"


# ═══ B2 ═════════════════════════════════════════════════════════════════════

async def _alerts_on():
    from backend.db.settings_store import set_setting
    await set_setting("discord_alert_deletion_error", "true")


async def test_b2_delete_claimed_failure_records_error_with_backoff(dbpath):
    import backend.routers.media as m
    nid = await _seed()
    with (patch.object(m, "_delete_media", new=AsyncMock(return_value=False)),
          patch("backend.deletion.send_alert", new=AsyncMock())):
        assert await m._delete_claimed(nid, {"id": nid, "title": "T"}) is False
    row = await _row(nid)
    assert (row["status"], row["attempts"]) == ("error", 1)
    assert row["next_retry_at"]


async def test_b2_exception_recorded_as_failure_with_message(dbpath):
    import backend.routers.media as m
    nid = await _seed()
    with (patch.object(m, "_delete_media", new=AsyncMock(side_effect=RuntimeError("boom"))),
          patch("backend.deletion.send_alert", new=AsyncMock())):
        assert await m._delete_claimed(nid, {"id": nid, "title": "T"}) is False
    row = await _row(nid)
    assert row["status"] == "error" and "boom" in row["last_error"]


async def test_b2_cancellation_releases_claim_and_counts_attempt(dbpath):
    import asyncio
    import backend.routers.media as m
    nid = await _seed()
    with patch.object(m, "_delete_media", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await m._delete_claimed(nid, {"id": nid, "title": "T"})
    row = await _row(nid)
    assert (row["status"], row["attempts"]) == ("pending", 1)


async def test_b2_alert_only_on_first_and_final_attempt(dbpath):
    import backend.routers.media as m
    await _alerts_on()
    alert = AsyncMock()
    with (patch.object(m, "_delete_media", new=AsyncMock(return_value=False)),
          patch("backend.deletion.send_alert", new=alert)):
        nid = await _seed(attempts=0)
        await m._delete_claimed(nid, {"id": nid, "title": "T"})
        assert alert.await_count == 1                      # 1st attempt -> alert
        for expected in (1, 1, 1):                         # attempts 2..4 -> silent
            await _force_deleting(nid)
            await m._delete_claimed(nid, {"id": nid, "title": "T"})
            assert alert.await_count == expected
        await _force_deleting(nid)
        await m._delete_claimed(nid, {"id": nid, "title": "T"})  # 5th -> final
    assert alert.await_count == 2
    assert (await _row(nid))["next_retry_at"] is None


async def _force_deleting(nid):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("UPDATE media_queue SET status='deleting' WHERE id=?", (nid,))
        await db.commit()


async def test_b2_success_marks_deleted(dbpath):
    import backend.routers.media as m
    nid = await _seed()
    with patch.object(m, "_delete_media", new=AsyncMock(return_value=True)):
        assert await m._delete_claimed(nid, {"id": nid, "title": "T"}) is True
    assert (await _row(nid))["status"] == "deleted"


async def test_b2_alert_failure_does_not_break_the_route(dbpath):
    import backend.routers.media as m
    await _alerts_on()
    nid = await _seed()
    with (patch.object(m, "_delete_media", new=AsyncMock(return_value=False)),
          patch("backend.deletion.send_alert", new=AsyncMock(side_effect=RuntimeError("discord down")))):
        assert await m._delete_claimed(nid, {"id": nid, "title": "T"}) is False
    assert (await _row(nid))["status"] == "error"


async def test_b2_bulk_failure_records_error_and_can_be_retried_manually(dbpath):
    import backend.routers.media as m
    nid = await _seed(status="pending")
    with (patch.object(m, "_delete_media", new=AsyncMock(return_value=False)),
          patch("backend.deletion.send_alert", new=AsyncMock())):
        res = await m._bulk_delete([nid], "?")
        assert res == {"affected": 0, "skipped": 1}
        row = await _row(nid)
        assert (row["status"], row["attempts"]) == ("error", 1)
    with patch.object(m, "_delete_media", new=AsyncMock(return_value=True)):
        res = await m._bulk_delete([nid], "?")           # explicit manual retry of an error row
    assert res == {"affected": 1, "skipped": 0}
    assert (await _row(nid))["status"] == "deleted"


async def test_b2_dry_run_changes_nothing(dbpath):
    import backend.routers.media as m
    from backend.db.settings_store import set_setting
    await set_setting("dry_run", "true")
    nid = await _seed(status="pending")
    with patch.object(m, "_delete_media", new=AsyncMock(return_value=False)):
        await m._bulk_delete([nid], "?")
    row = await _row(nid)
    assert (row["status"], row["attempts"]) == ("pending", 0)
