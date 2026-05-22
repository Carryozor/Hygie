"""Unit tests for database.py — runs against a temporary file-based SQLite DB."""
import pytest
import aiosqlite
import backend.database as dbmod


@pytest.fixture(autouse=True)
async def fresh_db(monkeypatch, tmp_path):
    """Each test gets its own temporary DB so state never leaks between tests."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_path)
    # Clear all module-level caches
    dbmod._ms_cache = None
    dbmod._ms_cache_ts = 0.0
    dbmod._settings_cache.clear()
    dbmod._settings_cache_ts = 0.0
    await dbmod.init_db()
    yield db_path


# ─── init_db ─────────────────────────────────────────────────────────────────

async def test_init_db_creates_all_tables(fresh_db):
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {r[0] for r in await cur.fetchall()}

    expected = {
        "settings", "users", "libraries", "media_queue",
        "ignored_media", "seerr_user_rules", "logs", "job_history", "stats_history",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


async def test_init_db_seeds_default_settings(fresh_db):
    assert await dbmod.get_setting("dry_run") == "false"
    assert await dbmod.get_setting("scan_interval_minutes") == "360"
    assert await dbmod.get_setting("deletion_check_interval_minutes") == "60"
    assert await dbmod.get_setting("log_retention_days") == "14"


async def test_init_db_is_idempotent(fresh_db):
    """Calling init_db twice must not raise or corrupt existing data."""
    await dbmod.set_setting("dry_run", "true")
    await dbmod.init_db()
    assert await dbmod.get_setting("dry_run") == "true"


async def test_init_db_creates_indexes(fresh_db):
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ) as cur:
            indexes = {r[0] for r in await cur.fetchall()}
    assert "idx_logs_ts" in indexes
    assert "idx_media_status" in indexes
    assert "idx_media_delete_at" in indexes


# ─── settings ────────────────────────────────────────────────────────────────

async def test_get_set_plain_setting(fresh_db):
    await dbmod.set_setting("log_level", "DEBUG")
    assert await dbmod.get_setting("log_level") == "DEBUG"


async def test_get_missing_setting_returns_empty_string(fresh_db):
    assert await dbmod.get_setting("nonexistent_key_xyz") == ""


async def test_set_overwrites_existing_setting(fresh_db):
    await dbmod.set_setting("log_level", "INFO")
    await dbmod.set_setting("log_level", "DEBUG")
    assert await dbmod.get_setting("log_level") == "DEBUG"


async def test_sensitive_setting_encrypted_at_rest(fresh_db):
    """Sensitive values must be stored as 'enc:...' in the DB."""
    await dbmod.set_setting("emby_api_key", "secret-key-123")

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='emby_api_key'"
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row[0].startswith("enc:"), f"Expected encrypted value, got: {row[0]!r}"


async def test_sensitive_setting_decrypted_on_read(fresh_db):
    """get_setting must transparently decrypt sensitive values."""
    await dbmod.set_setting("emby_api_key", "secret-key-123")
    assert await dbmod.get_setting("emby_api_key") == "secret-key-123"


async def test_all_sensitive_keys_are_encrypted(fresh_db):
    """Every key in SENSITIVE_KEYS must be encrypted when stored."""
    for key in dbmod.SENSITIVE_KEYS:
        await dbmod.set_setting(key, f"value-for-{key}")

    async with aiosqlite.connect(fresh_db) as db:
        placeholders = ",".join("?" * len(dbmod.SENSITIVE_KEYS))
        async with db.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            list(dbmod.SENSITIVE_KEYS),
        ) as cur:
            rows = await cur.fetchall()

    for key, stored in rows:
        assert stored.startswith("enc:"), (
            f"Key '{key}' stored in plaintext: {stored[:30]!r}"
        )


async def test_get_bool_setting_true_values(fresh_db):
    for truthy in ("true", "1", "yes", "on"):
        await dbmod.set_setting("dry_run", truthy)
        assert await dbmod.get_bool_setting("dry_run") is True, f"Failed for {truthy!r}"


async def test_get_bool_setting_false_values(fresh_db):
    for falsy in ("false", "0", "no", "off", ""):
        await dbmod.set_setting("dry_run", falsy)
        assert await dbmod.get_bool_setting("dry_run") is False, f"Failed for {falsy!r}"


async def test_get_bool_setting_default(fresh_db):
    assert await dbmod.get_bool_setting("nonexistent", default=True) is True
    assert await dbmod.get_bool_setting("nonexistent", default=False) is False


async def test_get_int_setting(fresh_db):
    await dbmod.set_setting("scan_interval_minutes", "120")
    assert await dbmod.get_int_setting("scan_interval_minutes") == 120


async def test_get_int_setting_invalid_returns_default(fresh_db):
    await dbmod.set_setting("scan_interval_minutes", "not-a-number")
    assert await dbmod.get_int_setting("scan_interval_minutes", default=42) == 42


async def test_get_int_setting_missing_returns_default(fresh_db):
    assert await dbmod.get_int_setting("nonexistent_int", default=99) == 99


# ─── logs ────────────────────────────────────────────────────────────────────

async def test_add_log_persists_entry(fresh_db):
    await dbmod.add_log("INFO", "test message", "tests")

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT level, source, message FROM logs ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()

    assert row == ("INFO", "tests", "test message")


async def test_add_log_multiple_entries(fresh_db):
    await dbmod.add_log("INFO", "msg1", "src1")
    await dbmod.add_log("ERROR", "msg2", "src2")
    await dbmod.add_log("WARN", "msg3", "src3")

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute("SELECT COUNT(*) FROM logs") as cur:
            count = (await cur.fetchone())[0]

    assert count == 3


# ─── job history ─────────────────────────────────────────────────────────────

async def test_job_run_lifecycle(fresh_db):
    run_id = await dbmod.add_job_run("test_job")
    assert isinstance(run_id, int) and run_id > 0

    await dbmod.finish_job_run(run_id, "success", "all good")

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT job_type, status, message, finished_at FROM job_history WHERE id=?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row[0] == "test_job"
    assert row[1] == "success"
    assert row[2] == "all good"
    assert row[3] is not None  # finished_at was set


async def test_add_job_run_started_at_is_set(fresh_db):
    run_id = await dbmod.add_job_run("scan")

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT started_at, finished_at FROM job_history WHERE id=?", (run_id,)
        ) as cur:
            row = await cur.fetchone()

    assert row[0] is not None   # started_at set immediately
    assert row[1] is None       # finished_at not yet set


# ─── media servers ───────────────────────────────────────────────────────────

async def test_save_and_get_media_servers(fresh_db):
    servers = [{
        "id": "0", "name": "Main", "url": "http://emby:8096",
        "api_key": "abc123", "ext_url": "", "type": "emby", "enabled": True,
    }]
    await dbmod.save_media_servers(servers)

    result = await dbmod.get_media_servers()
    assert len(result) == 1
    assert result[0]["url"] == "http://emby:8096"
    assert result[0]["api_key"] == "abc123"


async def test_save_media_servers_encrypts_api_key(fresh_db):
    """media_servers JSON blob must be stored encrypted."""
    servers = [{"id": "0", "name": "Test", "url": "http://emby:8096",
                "api_key": "supersecret", "type": "emby"}]
    await dbmod.save_media_servers(servers)

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key='media_servers'"
        ) as cur:
            row = await cur.fetchone()

    assert row[0].startswith("enc:"), "media_servers must be stored encrypted"


async def test_media_servers_cache_invalidated_after_save(fresh_db):
    """After save_media_servers, subsequent get_media_servers must reflect new data."""
    await dbmod.save_media_servers([{"id": "0", "name": "Old", "url": "http://old", "api_key": "k1", "type": "emby"}])
    assert (await dbmod.get_media_servers())[0]["name"] == "Old"

    await dbmod.save_media_servers([{"id": "0", "name": "New", "url": "http://new", "api_key": "k2", "type": "emby"}])
    assert (await dbmod.get_media_servers())[0]["name"] == "New"


async def test_get_media_servers_empty_returns_empty_list(fresh_db):
    result = await dbmod.get_media_servers()
    assert result == []


# ─── parse_iso_dt ─────────────────────────────────────────────────────────────

def test_parse_iso_dt_with_z_suffix():
    from datetime import timezone
    dt = dbmod.parse_iso_dt("2026-06-20T20:20:51Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_iso_dt_with_offset():
    dt = dbmod.parse_iso_dt("2026-06-20T20:20:51+00:00")
    assert dt is not None


def test_parse_iso_dt_none_returns_none():
    assert dbmod.parse_iso_dt(None) is None
    assert dbmod.parse_iso_dt("") is None


def test_parse_iso_dt_invalid_returns_none():
    assert dbmod.parse_iso_dt("not-a-date") is None
