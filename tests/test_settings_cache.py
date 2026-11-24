"""Tests for settings cache behaviour in database.py."""
import pytest
import aiosqlite
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "cache_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    # Reset settings cache
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


async def test_get_setting_returns_correct_value(fresh_db):
    await _db_ss.set_setting("log_level", "DEBUG")
    assert await _db_ss.get_setting("log_level") == "DEBUG"


async def test_cache_serves_value_within_ttl(fresh_db, monkeypatch):
    """Within TTL, get_setting returns cached value even if DB changes underneath."""
    await _db_ss.set_setting("log_level", "INFO")
    # Prime the cache
    assert await _db_ss.get_setting("log_level") == "INFO"

    # Write directly to DB, bypassing set_setting (no invalidation)
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute("UPDATE settings SET value='ERROR' WHERE key='log_level'")
        await db.commit()

    # Cache still serves old value
    assert await _db_ss.get_setting("log_level") == "INFO"


async def test_set_setting_invalidates_cache(fresh_db):
    """set_setting must clear the cache so the next read gets the fresh value."""
    await _db_ss.set_setting("log_level", "INFO")
    assert await _db_ss.get_setting("log_level") == "INFO"

    await _db_ss.set_setting("log_level", "DEBUG")
    assert await _db_ss.get_setting("log_level") == "DEBUG"


async def test_cache_refreshes_after_ttl_expires(fresh_db, monkeypatch):
    """After TTL expires, next get_setting re-reads from DB."""
    await _db_ss.set_setting("log_level", "INFO")
    assert await _db_ss.get_setting("log_level") == "INFO"

    # Force TTL expiry
    _db_ss._settings_cache_ts = 0.0

    # Write directly to DB
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute("UPDATE settings SET value='WARN' WHERE key='log_level'")
        await db.commit()

    # After TTL expiry, must read fresh value
    assert await _db_ss.get_setting("log_level") == "WARN"


async def test_sensitive_setting_decrypted_through_cache(fresh_db):
    """Sensitive values must be decrypted even when served from cache."""
    await _db_ss.set_setting("emby_api_key", "super-secret")
    # Force cache miss
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    val = await _db_ss.get_setting("emby_api_key")
    assert val == "super-secret"
    # Now serve from cache
    val2 = await _db_ss.get_setting("emby_api_key")
    assert val2 == "super-secret"


async def test_save_media_servers_invalidates_settings_cache(fresh_db):
    """save_media_servers modifies the 'media_servers' setting; cache must be invalidated."""
    # Prime the cache
    await _db_ss.get_setting("log_level")
    ts_before = _db_ss._settings_cache_ts

    await _db_ms.save_media_servers([{"id": "0", "url": "http://test", "api_key": "k"}])
    ts_after = _db_ss._settings_cache_ts

    # TTL timestamp must have been reset
    assert ts_after < ts_before or ts_after == 0.0


async def test_cache_populated_in_bulk(fresh_db):
    """A single DB query must populate cache for all settings."""
    await _db_ss.set_setting("log_level", "DEBUG")
    await _db_ss.set_setting("dry_run", "true")

    # Clear cache and force a fresh load
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0

    # First call loads all settings
    assert await _db_ss.get_setting("log_level") == "DEBUG"
    # Second call uses cache (no extra DB query needed)
    assert await _db_ss.get_setting("dry_run") == "true"


# ─── apply_interval_changes tests (replaces reschedule_jobs) ──────────────────

class _FakeJob:
    def __init__(self, minutes):
        from datetime import timedelta
        self.trigger = type("T", (), {"interval": timedelta(minutes=minutes)})()


class _FakeScheduler:
    timezone = None

    def __init__(self, broken=False):
        self.calls = []
        self.broken = broken

    def get_job(self, job_id):
        if self.broken:
            raise RuntimeError("scheduler not running")
        return _FakeJob(999)

    def reschedule_job(self, job_id, trigger):
        self.calls.append((job_id, int(trigger.interval.total_seconds() // 60)))


async def test_apply_interval_changes_forwards_both_intervals(monkeypatch):
    import backend._scheduler_instance as _si
    from backend._schedule_reconcile import apply_interval_changes
    sched = _FakeScheduler()
    monkeypatch.setattr(_si, "scheduler", sched)
    await apply_interval_changes(scan_minutes=45, deletion_minutes=30)
    assert ("scan_job", 45) in sched.calls
    assert ("deletion_job", 30) in sched.calls


async def test_apply_interval_changes_skips_none_args(monkeypatch):
    import backend._scheduler_instance as _si
    from backend._schedule_reconcile import apply_interval_changes
    sched = _FakeScheduler()
    monkeypatch.setattr(_si, "scheduler", sched)
    await apply_interval_changes(scan_minutes=10, deletion_minutes=None)
    assert [c[0] for c in sched.calls] == ["scan_job"]


async def test_apply_interval_changes_clamps_to_minimum_one(monkeypatch):
    import backend._scheduler_instance as _si
    from backend._schedule_reconcile import apply_interval_changes
    sched = _FakeScheduler()
    monkeypatch.setattr(_si, "scheduler", sched)
    await apply_interval_changes(scan_minutes=0, deletion_minutes=-5)
    assert ("scan_job", 1) in sched.calls
    assert ("deletion_job", 1) in sched.calls


async def test_apply_interval_changes_swallows_scheduler_errors(monkeypatch):
    import backend._scheduler_instance as _si
    from backend._schedule_reconcile import apply_interval_changes
    monkeypatch.setattr(_si, "scheduler", _FakeScheduler(broken=True))
    await apply_interval_changes(scan_minutes=60, deletion_minutes=60)  # must not raise
