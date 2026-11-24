"""K2: every worker derives the SAME next run for a changed interval.

The worker that receives the settings change (route) and the workers that
converge through the periodic reconcile share one anchored IntervalTrigger:
start_date = end of the last known run in job_history (fixed anchor if none),
no worker-specific next_run_time.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from httpx import ASGITransport, AsyncClient

import backend.main as main_mod


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import backend._scheduler_instance as _si
    import backend.db.engine as _eng
    import backend.db.media_servers as _db_ms
    import backend.db.settings_store as _ss
    import backend.db.utils as _db_utils
    from backend.db.schema import init_db
    from backend.routers import settings as settings_router

    db_path = str(tmp_path / "anchor.db")
    monkeypatch.setattr(_eng, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    await init_db()

    sched_a = await _make_scheduler()
    monkeypatch.setattr(_si, "scheduler", sched_a)

    app = FastAPI()
    app.state.scheduler = None
    app.include_router(settings_router.router)
    app.dependency_overrides[settings_router.require_auth] = lambda: "t"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.sched_a = sched_a
        yield c
    sched_a.shutdown(wait=False)
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


async def _make_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.start(paused=True)
    soon = datetime.now(timezone.utc) + timedelta(hours=1)
    sched.add_job(lambda: None, "interval", minutes=360, id="scan_job", next_run_time=soon)
    sched.add_job(lambda: None, "interval", minutes=60, id="deletion_job", next_run_time=soon)
    return sched


async def _set_last_run(job_type: str, ran_at: datetime) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status, message) "
            "VALUES (?, ?, ?, 'success', 'test')",
            (job_type, ran_at.isoformat(), ran_at.isoformat()),
        )
        await db.commit()


async def _reconciled_clone(client) -> AsyncIOScheduler:
    """An independent worker converging through the periodic reconcile."""
    import backend.db.settings_store as _ss
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    other = await _make_scheduler()
    await main_mod._schedule_reconcile(other)
    return other


async def test_route_and_reconcile_compute_the_same_next_run(client):
    ran = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _set_last_run("scan", ran)
    r = await client.post("/api/settings", json={"scan_interval_minutes": "120"})
    assert r.status_code == 200
    other = await _reconciled_clone(client)
    try:
        na = client.sched_a.get_job("scan_job").next_run_time
        nb = other.get_job("scan_job").next_run_time
        assert na == nb
        assert abs((na - (ran + timedelta(minutes=120))).total_seconds()) < 1
    finally:
        other.shutdown(wait=False)


async def test_route_and_reconcile_agree_when_anchored_due_date_is_past(client):
    ran = datetime.now(timezone.utc) - timedelta(minutes=500)
    await _set_last_run("scan", ran)
    await client.post("/api/settings", json={"scan_interval_minutes": "120"})
    other = await _reconciled_clone(client)
    try:
        na = client.sched_a.get_job("scan_job").next_run_time
        nb = other.get_job("scan_job").next_run_time
        assert na == nb
        assert na > datetime.now(timezone.utc)
        # still on the anchor's phase: a whole number of intervals after the last run
        assert (na - ran).total_seconds() % (120 * 60) == pytest.approx(0, abs=1)
    finally:
        other.shutdown(wait=False)


async def test_route_and_reconcile_agree_without_history(client):
    await client.post("/api/settings", json={"deletion_check_interval_minutes": "45"})
    other = await _reconciled_clone(client)
    try:
        assert (client.sched_a.get_job("deletion_job").next_run_time
                == other.get_job("deletion_job").next_run_time)
    finally:
        other.shutdown(wait=False)


async def test_route_does_not_reschedule_when_interval_unchanged(client):
    await client.post("/api/settings", json={"scan_interval_minutes": "120"})
    client.sched_a.modify_job = MagicMock(wraps=client.sched_a.modify_job)
    client.sched_a.reschedule_job = MagicMock(wraps=client.sched_a.reschedule_job)
    await client.post("/api/settings", json={"scan_interval_minutes": "120"})
    client.sched_a.modify_job.assert_not_called()
    client.sched_a.reschedule_job.assert_not_called()


# ─── backup job in the reconcile ────────────────────────────────────────────

async def _write(key: str, value: str) -> None:
    import backend.db.settings_store as _ss
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("DELETE FROM settings WHERE `key`=?", (key,))
        await db.execute("INSERT INTO settings (`key`, value) VALUES (?, ?)", (key, value))
        await db.commit()
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


async def test_reconcile_adds_modifies_and_removes_backup_job(client):
    sched = await _make_scheduler()
    try:
        await _write("backup_enabled", "true")
        await _write("backup_interval_hours", "12")
        await main_mod._schedule_reconcile(sched)
        job = sched.get_job("backup_job")
        assert job is not None and job.trigger.interval == timedelta(hours=12)

        await _write("backup_interval_hours", "6")
        await main_mod._schedule_reconcile(sched)
        assert sched.get_job("backup_job").trigger.interval == timedelta(hours=6)

        await _write("backup_enabled", "false")
        await main_mod._schedule_reconcile(sched)
        assert sched.get_job("backup_job") is None
    finally:
        sched.shutdown(wait=False)


async def test_two_workers_agree_on_backup_next_run(client):
    a, b = await _make_scheduler(), await _make_scheduler()
    try:
        await _write("backup_enabled", "true")
        await _write("backup_interval_hours", "12")
        await main_mod._schedule_reconcile(a)
        await main_mod._schedule_reconcile(b)
        assert a.get_job("backup_job").next_run_time == b.get_job("backup_job").next_run_time
    finally:
        a.shutdown(wait=False)
        b.shutdown(wait=False)
