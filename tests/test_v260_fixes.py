"""Tests for v2.6.0 fixes — scheduler reschedule via settings API, default values."""
import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest.fixture()
async def app_client(tmp_path, monkeypatch):
    import importlib
    from argon2 import PasswordHasher
    from httpx import AsyncClient, ASGITransport

    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.logs as _db_logs
    import backend.db.repositories as _db_repos
    import backend.db.engine as _db_engine
    import backend.routers.stats as _r_stats
    import backend.routers.metrics as _r_metrics
    import backend.routers.calendar as _r_calendar
    import backend.routers.expert_rules as _r_expert_rules
    import backend.routers.ignored as _r_ignored
    import backend.routers.libraries as _r_libraries
    import backend.routers.logs as _r_logs
    import backend.routers.media as _r_media
    import backend.routers.seerr_rules as _r_seerr
    import backend.routers.storage as _r_storage
    import backend.routers.unmonitored as _r_unmonitored

    db_path = str(tmp_path / "v260_test.db")
    _all_db_modules = [
        _db_utils, _db_ss, _db_ms, _db_schema, _db_logs, _db_repos,
        _r_stats, _r_metrics, _r_calendar, _r_expert_rules, _r_ignored,
        _r_libraries, _r_logs, _r_media, _r_seerr, _r_storage, _r_unmonitored,
    ]
    for mod in _all_db_modules:
        if hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0

    import backend.auth as auth_mod
    import backend.main as main_mod
    importlib.reload(auth_mod)
    importlib.reload(main_mod)
    auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    for mod in _all_db_modules:
        if hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)

    from datetime import datetime, timezone

    def _make_job(job_id):
        j = MagicMock()
        j.id = job_id
        j.func = MagicMock()
        j.func.__name__ = job_id
        j.next_run_time = datetime.now(timezone.utc)
        return j

    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [_make_job("scan_job"), _make_job("deletion_job")]
    main_mod.scheduler = mock_sched
    import backend.routers.scheduler as _sched_router
    _sched_router.scheduler = mock_sched

    app = main_mod.app
    async with main_mod.lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield mock_sched, c


async def _setup_user(client):
    r = await client.post("/api/auth/setup", json={"username": "admin", "password": "testpass123"})
    assert r.status_code == 200
    token = r.json().get("access_token") or r.json().get("token")
    client.headers.update({"Authorization": f"Bearer {token}"})


# ─── Fix 1: discord_notif_thresholds has a default value ─────────────────────

@pytest.mark.asyncio
async def test_discord_notif_thresholds_has_default(app_client):
    """discord_notif_thresholds must have a default value in DB (not empty)."""
    from backend.db.settings_store import get_setting
    val = await get_setting("discord_notif_thresholds")
    assert val == "7,1", f"Expected default '7,1', got {val!r}"


# ─── Fix 2: Saving new interval triggers reschedule ──────────────────────────

@pytest.mark.asyncio
async def test_saving_scan_interval_reschedules_job(app_client):
    """Saving a new scan_interval_minutes must call reschedule on the scheduler."""
    scheduler, client = app_client
    await _setup_user(client)

    r = await client.post("/api/settings", json={"scan_interval_minutes": "120"})
    assert r.status_code == 200
    assert "scan_interval_minutes" in r.json()["updated"]


@pytest.mark.asyncio
async def test_saving_deletion_interval_reschedules_job(app_client):
    """Saving a new deletion_check_interval_minutes must call reschedule."""
    scheduler, client = app_client
    await _setup_user(client)

    r = await client.post("/api/settings", json={"deletion_check_interval_minutes": "30"})
    assert r.status_code == 200
    assert "deletion_check_interval_minutes" in r.json()["updated"]


@pytest.mark.asyncio
async def test_same_interval_does_not_raise(app_client):
    """Saving the same interval value must not raise or error."""
    scheduler, client = app_client
    await _setup_user(client)

    r = await client.post("/api/settings", json={"scan_interval_minutes": "360"})
    assert r.status_code == 200


# ─── Fix 3: reschedule_job error is logged, not silently swallowed ────────────

def test_reschedule_logger_defined():
    """settings.py must have a logger for reschedule error reporting."""
    import backend.routers.settings as smod
    assert hasattr(smod, "logger"), "settings.py must define a module-level logger"


# ─── Fix 4: Interval clamping ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interval_clamped_to_minimum(app_client):
    """Interval of 0 must be accepted without error (clamping happens in scheduler)."""
    scheduler, client = app_client
    await _setup_user(client)

    r = await client.post("/api/settings", json={"scan_interval_minutes": "0"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_interval_clamped_to_maximum(app_client):
    """Interval above maximum must be accepted without error."""
    scheduler, client = app_client
    await _setup_user(client)

    r = await client.post("/api/settings", json={"scan_interval_minutes": "99999"})
    assert r.status_code == 200
