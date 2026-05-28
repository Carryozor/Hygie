"""Tests for settings cache behaviour in database.py."""
import pytest
import aiosqlite
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
import backend.db.logs as _db_logs


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "cache_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
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


# ─── reschedule_jobs tests ────────────────────────────────────────────────────

def test_reschedule_jobs_calls_scheduler_reschedule_job(monkeypatch):
    """reschedule_jobs() should forward interval changes to the scheduler."""
    import backend.main as _main  # lazy import — avoids module-level DB_PATH binding side effects
    calls = []

    class _FakeScheduler:
        def reschedule_job(self, job_id, trigger, minutes):
            calls.append((job_id, trigger, minutes))

    monkeypatch.setattr(_main, "scheduler", _FakeScheduler())
    _main.reschedule_jobs(scan_minutes=45, deletion_minutes=30)

    assert ("scan_job", "interval", 45) in calls
    assert ("deletion_job", "interval", 30) in calls


def test_reschedule_jobs_skips_none_args(monkeypatch):
    """Passing None for an argument must not attempt to reschedule that job."""
    import backend.main as _main
    calls = []

    class _FakeScheduler:
        def reschedule_job(self, job_id, trigger, minutes):
            calls.append(job_id)

    monkeypatch.setattr(_main, "scheduler", _FakeScheduler())
    _main.reschedule_jobs(scan_minutes=10, deletion_minutes=None)

    assert "scan_job" in calls
    assert "deletion_job" not in calls


def test_reschedule_jobs_clamps_to_minimum_one(monkeypatch):
    """Minutes below 1 must be clamped to 1."""
    import backend.main as _main
    calls = []

    class _FakeScheduler:
        def reschedule_job(self, job_id, trigger, minutes):
            calls.append((job_id, minutes))

    monkeypatch.setattr(_main, "scheduler", _FakeScheduler())
    _main.reschedule_jobs(scan_minutes=0, deletion_minutes=-5)

    assert ("scan_job", 1) in calls
    assert ("deletion_job", 1) in calls


def test_reschedule_jobs_swallows_scheduler_errors(monkeypatch):
    """Exceptions from the scheduler must be caught so the caller is not disrupted."""
    import backend.main as _main

    class _BrokenScheduler:
        def reschedule_job(self, *args, **kwargs):
            raise RuntimeError("scheduler not running")

    monkeypatch.setattr(_main, "scheduler", _BrokenScheduler())
    # Must not raise
    _main.reschedule_jobs(scan_minutes=60, deletion_minutes=60)
