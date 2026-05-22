"""Tests for settings cache behaviour in database.py."""
import pytest
import aiosqlite
import backend.database as dbmod


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "cache_test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    dbmod._ms_cache = None
    dbmod._ms_cache_ts = 0.0
    # Reset settings cache
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0
    await dbmod.init_db()
    yield db_path


async def test_get_setting_returns_correct_value(fresh_db):
    await dbmod.set_setting("log_level", "DEBUG")
    assert await dbmod.get_setting("log_level") == "DEBUG"


async def test_cache_serves_value_within_ttl(fresh_db, monkeypatch):
    """Within TTL, get_setting returns cached value even if DB changes underneath."""
    await dbmod.set_setting("log_level", "INFO")
    # Prime the cache
    assert await dbmod.get_setting("log_level") == "INFO"

    # Write directly to DB, bypassing set_setting (no invalidation)
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute("UPDATE settings SET value='ERROR' WHERE key='log_level'")
        await db.commit()

    # Cache still serves old value
    assert await dbmod.get_setting("log_level") == "INFO"


async def test_set_setting_invalidates_cache(fresh_db):
    """set_setting must clear the cache so the next read gets the fresh value."""
    await dbmod.set_setting("log_level", "INFO")
    assert await dbmod.get_setting("log_level") == "INFO"

    await dbmod.set_setting("log_level", "DEBUG")
    assert await dbmod.get_setting("log_level") == "DEBUG"


async def test_cache_refreshes_after_ttl_expires(fresh_db, monkeypatch):
    """After TTL expires, next get_setting re-reads from DB."""
    await dbmod.set_setting("log_level", "INFO")
    assert await dbmod.get_setting("log_level") == "INFO"

    # Force TTL expiry
    dbmod._settings_cache_ts = 0.0

    # Write directly to DB
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute("UPDATE settings SET value='WARN' WHERE key='log_level'")
        await db.commit()

    # After TTL expiry, must read fresh value
    assert await dbmod.get_setting("log_level") == "WARN"


async def test_sensitive_setting_decrypted_through_cache(fresh_db):
    """Sensitive values must be decrypted even when served from cache."""
    await dbmod.set_setting("emby_api_key", "super-secret")
    # Force cache miss
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0
    val = await dbmod.get_setting("emby_api_key")
    assert val == "super-secret"
    # Now serve from cache
    val2 = await dbmod.get_setting("emby_api_key")
    assert val2 == "super-secret"


async def test_save_media_servers_invalidates_settings_cache(fresh_db):
    """save_media_servers modifies the 'media_servers' setting; cache must be invalidated."""
    # Prime the cache
    await dbmod.get_setting("log_level")
    ts_before = dbmod._settings_cache_ts

    await dbmod.save_media_servers([{"id": "0", "url": "http://test", "api_key": "k"}])
    ts_after = dbmod._settings_cache_ts

    # TTL timestamp must have been reset
    assert ts_after < ts_before or ts_after == 0.0


async def test_cache_populated_in_bulk(fresh_db):
    """A single DB query must populate cache for all settings."""
    await dbmod.set_setting("log_level", "DEBUG")
    await dbmod.set_setting("dry_run", "true")

    # Clear cache and force a fresh load
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0

    # First call loads all settings
    assert await dbmod.get_setting("log_level") == "DEBUG"
    # Second call uses cache (no extra DB query needed)
    assert await dbmod.get_setting("dry_run") == "true"
