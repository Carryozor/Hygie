"""Tests for scheduler next_run_time persistence across restarts."""
from datetime import datetime, timezone, timedelta
import pytest
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
import backend.db.logs as _db_logs


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "sched_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


async def _set_last_run(job_type: str, ran_at: datetime):
    """Insert a fake job_history entry so _job_next_run has something to read."""
    import aiosqlite
    async with aiosqlite.connect(_db_utils.DB_PATH) as db:
        await db.execute(
            "INSERT INTO job_history (job_type, started_at, finished_at, status, message) "
            "VALUES (?, ?, ?, 'success', 'test')",
            (job_type, ran_at.isoformat(), ran_at.isoformat()),
        )
        await db.commit()


async def test_no_history_returns_soon():
    """With no job history, next run should be ~30s from now."""
    from backend.main import _job_next_run
    now = datetime.now(timezone.utc)
    next_run = await _job_next_run("scan", 360)
    delta = (next_run - now).total_seconds()
    assert 0 < delta <= 60, f"Expected ~30s, got {delta:.0f}s"


async def test_recent_run_preserves_countdown():
    """If last scan ran 2h ago with a 6h interval, next run should be ~4h from now."""
    from backend.main import _job_next_run
    interval_min = 360  # 6 hours
    ran_2h_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    await _set_last_run("scan", ran_2h_ago)

    now = datetime.now(timezone.utc)
    next_run = await _job_next_run("scan", interval_min)

    expected = ran_2h_ago + timedelta(minutes=interval_min)
    diff = abs((next_run - expected).total_seconds())
    assert diff < 5, f"next_run deviates {diff:.0f}s from expected"

    remaining_hours = (next_run - now).total_seconds() / 3600
    assert 3.9 < remaining_hours < 4.1, f"Expected ~4h remaining, got {remaining_hours:.2f}h"


async def test_overdue_run_returns_soon():
    """If last scan ran 8h ago with a 6h interval, it's overdue → run in ~30s."""
    from backend.main import _job_next_run
    ran_8h_ago = datetime.now(timezone.utc) - timedelta(hours=8)
    await _set_last_run("scan", ran_8h_ago)

    now = datetime.now(timezone.utc)
    next_run = await _job_next_run("scan", 360)
    delta = (next_run - now).total_seconds()
    assert 0 < delta <= 60, f"Expected overdue to return ~30s, got {delta:.0f}s"


async def test_deletion_job_uses_correct_type():
    """deletion_check job type must be looked up by its correct name."""
    from backend.main import _job_next_run
    ran_30min_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
    await _set_last_run("deletion_check", ran_30min_ago)

    now = datetime.now(timezone.utc)
    next_run = await _job_next_run("deletion_check", 60)  # 1h interval
    remaining_min = (next_run - now).total_seconds() / 60
    assert 25 < remaining_min < 35, f"Expected ~30min remaining, got {remaining_min:.1f}min"


async def test_different_job_types_are_independent():
    """scan and deletion_check histories must not interfere."""
    from backend.main import _job_next_run
    now = datetime.now(timezone.utc)
    await _set_last_run("scan", now - timedelta(hours=1))
    await _set_last_run("deletion_check", now - timedelta(minutes=30))

    scan_next = await _job_next_run("scan", 360)
    del_next  = await _job_next_run("deletion_check", 60)

    scan_remaining = (scan_next - now).total_seconds() / 3600
    del_remaining  = (del_next - now).total_seconds() / 60

    assert 4.9 < scan_remaining < 5.1, f"Expected ~5h for scan, got {scan_remaining:.2f}h"
    assert 25 < del_remaining < 35,    f"Expected ~30min for deletion, got {del_remaining:.1f}min"
