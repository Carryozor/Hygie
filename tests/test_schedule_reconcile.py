"""P3-8: a worker converges on interval settings changed by another worker."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import backend.main as main_mod


@pytest.fixture(autouse=True)
async def _fresh_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.engine as _eng
    import backend.db.settings_store as _ss

    db_path = str(tmp_path / "reconcile.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    from backend.db.schema import init_db
    await init_db()
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    yield db_path


async def _write_setting(key: str, value: str) -> None:
    """Simulate the other worker: write to the DB, bypassing this process's cache."""
    import backend.db.settings_store as _ss
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("DELETE FROM settings WHERE `key`=?", (key,))
        await db.execute("INSERT INTO settings (`key`, value) VALUES (?, ?)", (key, value))
        await db.commit()
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


async def _set_last_run(job_type: str, ran_at: datetime) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status, message) "
            "VALUES (?, ?, ?, 'success', 'test')",
            (job_type, ran_at.isoformat(), ran_at.isoformat()),
        )
        await db.commit()


async def _make_scheduler(scan_min=360, del_min=60) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.start(paused=True)
    soon = datetime.now(timezone.utc) + timedelta(hours=1)
    sched.add_job(lambda: None, "interval", minutes=scan_min, id="scan_job", next_run_time=soon)
    sched.add_job(lambda: None, "interval", minutes=del_min, id="deletion_job", next_run_time=soon)
    return sched


def _minutes(sched, job_id) -> int:
    return int(sched.get_job(job_id).trigger.interval.total_seconds() // 60)


async def test_reconcile_applies_interval_set_by_other_worker():
    sched = await _make_scheduler()
    try:
        await _write_setting("scan_interval_minutes", "120")
        await main_mod._schedule_reconcile(sched)
        assert _minutes(sched, "scan_job") == 120
        assert _minutes(sched, "deletion_job") == 60
    finally:
        sched.shutdown(wait=False)


async def test_reconcile_applies_deletion_interval():
    sched = await _make_scheduler()
    try:
        await _write_setting("deletion_check_interval_minutes", "15")
        await main_mod._schedule_reconcile(sched)
        assert _minutes(sched, "deletion_job") == 15
        assert _minutes(sched, "scan_job") == 360
    finally:
        sched.shutdown(wait=False)


async def test_reconcile_does_nothing_when_unchanged():
    sched = await _make_scheduler()
    try:
        sched.modify_job = MagicMock(wraps=sched.modify_job)
        sched.reschedule_job = MagicMock(wraps=sched.reschedule_job)
        before = sched.get_job("scan_job").next_run_time
        await main_mod._schedule_reconcile(sched)
        sched.modify_job.assert_not_called()
        sched.reschedule_job.assert_not_called()
        assert sched.get_job("scan_job").next_run_time == before
    finally:
        sched.shutdown(wait=False)


async def test_two_schedulers_converge_on_same_phase():
    ran = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _set_last_run("scan", ran)
    a, b = await _make_scheduler(), await _make_scheduler()
    try:
        await _write_setting("scan_interval_minutes", "120")
        await main_mod._schedule_reconcile(a)
        await main_mod._schedule_reconcile(b)
        na = a.get_job("scan_job").next_run_time
        nb = b.get_job("scan_job").next_run_time
        assert na == nb
        assert abs((na - (ran + timedelta(minutes=120))).total_seconds()) < 1
    finally:
        a.shutdown(wait=False)
        b.shutdown(wait=False)


async def test_reconcile_ignores_invalid_setting_and_missing_job():
    sched = AsyncIOScheduler()
    sched.start(paused=True)
    try:
        await _write_setting("scan_interval_minutes", "abc")
        await main_mod._schedule_reconcile(sched)  # no jobs, bad value: must not raise
    finally:
        sched.shutdown(wait=False)


async def test_reconcile_job_registered_every_60s():
    mock_sched = MagicMock()
    with patch.object(main_mod, "scheduler", mock_sched), \
         patch.object(main_mod, "get_bool_setting", new=AsyncMock(return_value=False)):
        await main_mod._schedule_recurring_jobs()
    calls = {c.kwargs.get("id"): c for c in mock_sched.add_job.call_args_list}
    assert "schedule_reconcile" in calls
    assert calls["schedule_reconcile"].kwargs.get("seconds") == 60
