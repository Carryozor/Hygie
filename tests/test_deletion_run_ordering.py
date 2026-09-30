"""Characterization of the ORDER of operations inside run_deletion().

This job deletes media: the order is part of its safety contract. Every
collaborator is replaced by a recorder so that the call sequence itself is
asserted (no DB, no network).

  notifications -> library map -> pending queue -> watch-state refresh
  -> rescue guard -> settings -> claim -> delete -> status -> done log
  -> collection sync -> job-run finish
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.deletion as dmod
from backend.db.logs import _current_job_id
from backend.db.utils import STATUS_DELETED, STATUS_ERROR


class _Rec:
    def __init__(self):
        self.calls: list = []

    def wrap(self, name, ret=None, *, side=None):
        async def _fn(*a, **k):
            self.calls.append(name)
            if side is not None:
                return side(*a, **k)
            return ret
        return _fn


def _install(rec, *, dry_run=False, claim=True, media_ok=True, rows=None,
             rescued=None, sync_raises=None):
    rows = [{"id": 1, "title": "A", "library_id": "L"}] if rows is None else rows

    @asynccontextmanager
    async def _fake_db():
        db = MagicMock()
        db.fetch_all = AsyncMock(return_value=[{"id": "L", "server_id": 7}])
        rec.calls.append("library_map")
        yield db

    def _refresh(rows_, lib_map):
        rec.lib_map = lib_map
        return list(rows_)

    def _rescue(rows_):
        return list(rows_) if rescued is None else rescued

    def _log(level, msg, *a, **k):
        rec.calls.append(f"log:{level}")

    def _set_ctx(run_id):
        return _current_job_id.set(run_id)

    def _sync(*a, **k):
        if sync_raises:
            raise sync_raises

    settings = {"dry_run": dry_run, "discord_alert_deletion_error": False}
    return [
        patch.object(dmod, "add_job_run", rec.wrap("add_job_run", 42)),
        patch.object(dmod, "set_job_context", _set_ctx),
        patch.object(dmod, "get_bool_setting",
                     rec.wrap("bool_setting", side=lambda k, *a: settings[k])),
        patch.object(dmod, "add_log", rec.wrap("log_", side=_log)),
        patch.object(dmod, "_send_pending_notifications", rec.wrap("notify")),
        patch.object(dmod, "get_db", _fake_db),
        patch.object(dmod, "get_pending_queue", rec.wrap("pending_queue", rows)),
        patch.object(dmod, "_refresh_watch_state_before_deletion", rec.wrap("refresh", side=_refresh)),
        patch.object(dmod, "_rescue_watched_since_queued", rec.wrap("rescue", side=_rescue)),
        patch.object(dmod, "get_setting", rec.wrap("setting", None)),
        patch.object(dmod, "_claim_pending", rec.wrap("claim", claim)),
        patch.object(dmod, "_delete_media", rec.wrap("delete_media", media_ok)),
        patch.object(dmod, "update_queue_status", rec.wrap("status")),
        patch.object(dmod, "send_alert", rec.wrap("alert")),
        patch.object(dmod, "sync_emby_collection", rec.wrap("sync", side=_sync)),
        patch.object(dmod, "finish_job_run", rec.wrap("finish")),
    ]


async def _run(patches):
    from contextlib import ExitStack
    with ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        await dmod.run_deletion()


def _seq(rec):
    return [c for c in rec.calls if c not in ("log_", "bool_setting", "setting")]


@pytest.mark.asyncio
async def test_full_run_keeps_the_safety_ordering():
    rec = _Rec()
    await _run(_install(rec))

    seq = _seq(rec)
    assert seq == [
        "add_job_run", "log:INFO", "notify", "library_map", "pending_queue",
        "refresh", "rescue", "claim", "delete_media", "status", "log:INFO",
        "sync", "finish",
    ]


@pytest.mark.asyncio
async def test_rescue_guard_sees_the_refreshed_rows_not_the_stale_ones():
    """The rescue compares last_played with detected_at: it must run on rows
    whose last_played was just refreshed from the media server."""
    rec = _Rec()
    received = {}

    def _refresh(rows_, lib_map):
        return [{**r, "last_played": "REFRESHED"} for r in rows_]

    def _rescue(rows_):
        received["rows"] = rows_
        return rows_

    patches = _install(rec)
    patches += [
        patch.object(dmod, "_refresh_watch_state_before_deletion", rec.wrap("refresh", side=_refresh)),
        patch.object(dmod, "_rescue_watched_since_queued", rec.wrap("rescue", side=_rescue)),
    ]
    await _run(patches)

    assert received["rows"][0]["last_played"] == "REFRESHED"


@pytest.mark.asyncio
async def test_rescued_rows_are_never_claimed_or_deleted():
    rec = _Rec()
    await _run(_install(rec, rescued=[]))

    assert "claim" not in rec.calls
    assert "delete_media" not in rec.calls
    assert "sync" not in rec.calls  # nothing deleted -> no collection sync


@pytest.mark.asyncio
async def test_server_id_is_attached_from_the_library_map():
    rec = _Rec()
    seen = {}

    async def _delete_media(row, dry, **kw):
        seen["row"] = row
        return True

    patches = _install(rec) + [patch.object(dmod, "_delete_media", _delete_media)]
    await _run(patches)

    assert seen["row"]["_server_id"] == "7"
    assert rec.lib_map == {"L": "7"}


@pytest.mark.asyncio
async def test_claim_lost_skips_delete_status_and_sync():
    rec = _Rec()
    await _run(_install(rec, claim=False))

    seq = _seq(rec)
    assert "claim" in seq
    assert "delete_media" not in seq and "status" not in seq and "sync" not in seq


@pytest.mark.asyncio
async def test_dry_run_never_claims_nor_changes_status_nor_syncs():
    rec = _Rec()
    await _run(_install(rec, dry_run=True))

    seq = _seq(rec)
    assert "delete_media" in seq
    assert "claim" not in seq and "status" not in seq and "sync" not in seq


@pytest.mark.asyncio
async def test_failed_item_is_recorded_as_error_status():
    rec = _Rec()
    statuses = []
    patches = _install(rec, media_ok=False) + [
        patch.object(dmod, "update_queue_status",
                     rec.wrap("status", side=lambda i, s: statuses.append(s))),
    ]
    await _run(patches)

    assert statuses == [STATUS_ERROR]


@pytest.mark.asyncio
async def test_successful_item_is_recorded_as_deleted_status():
    rec = _Rec()
    statuses = []
    patches = _install(rec) + [
        patch.object(dmod, "update_queue_status",
                     rec.wrap("status", side=lambda i, s: statuses.append(s))),
    ]
    await _run(patches)

    assert statuses == [STATUS_DELETED]


@pytest.mark.asyncio
async def test_sync_failure_keeps_success_status_with_the_exception_message():
    """Historical behaviour: the status flips to success before the closing log
    and the collection sync, so a sync error only changes the message."""
    rec = _Rec()
    finished = {}

    def _finish(run_id, status, msg):
        finished.update(run_id=run_id, status=status, msg=msg)

    patches = _install(rec, sync_raises=RuntimeError("emby down")) + [
        patch.object(dmod, "finish_job_run", rec.wrap("finish", side=_finish)),
    ]
    await _run(patches)

    assert finished == {"run_id": 42, "status": "success", "msg": "emby down"}


@pytest.mark.asyncio
async def test_clean_run_reports_success_and_count():
    rec = _Rec()
    finished = {}

    def _finish(run_id, status, msg):
        finished.update(status=status, msg=msg)

    patches = _install(rec) + [patch.object(dmod, "finish_job_run", rec.wrap("finish", side=_finish))]
    await _run(patches)

    assert finished == {"status": "success", "msg": "1 deleted"}


@pytest.mark.asyncio
async def test_lock_is_released_and_job_context_reset_after_a_failure():
    rec = _Rec()

    def _boom(*a, **k):
        raise RuntimeError("queue unreadable")

    patches = _install(rec) + [patch.object(dmod, "get_pending_queue", rec.wrap("pending_queue", side=_boom))]
    before = _current_job_id.get()
    await _run(patches)

    assert not dmod._deletion_lock.locked()
    assert _current_job_id.get() == before
    assert rec.calls[-1] == "finish"
