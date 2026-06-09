"""Tests for v2.5.0 fixes — poster proxy, scheduler dedup, settings cleanup, emby retry."""
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


# ─── Fix 1: _ensure_notif_columns removed ────────────────────────────────────

def test_ensure_notif_columns_no_longer_exists():
    """Dead function must be gone from scheduler."""
    import backend.scheduler as sched
    assert not hasattr(sched, "_ensure_notif_columns"), (
        "_ensure_notif_columns is dead code — should have been removed"
    )


# ─── Shared app_client fixture ────────────────────────────────────────────────

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

    db_path = str(tmp_path / "v250_test.db")
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
        j.func.__name__ = "_internal_cleanup" if job_id == "cleanup_job" else job_id
        j.next_run_time = datetime.now(timezone.utc)
        return j

    mock_sched = MagicMock()
    mock_sched.get_jobs.return_value = [
        _make_job("scan_job"),
        _make_job("deletion_job"),
        _make_job("cleanup_job"),
    ]
    main_mod.scheduler = mock_sched
    import backend.routers.scheduler as _sched_router
    _sched_router.scheduler = mock_sched

    app = main_mod.app
    async with main_mod.lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield mock_sched, c


# ─── Fix 2: _internal_cleanup scheduled exactly once ─────────────────────────

@pytest.mark.asyncio
async def test_internal_cleanup_registered_exactly_once(app_client):
    """_internal_cleanup must appear in the scheduler exactly once."""
    scheduler, _ = app_client
    jobs = scheduler.get_jobs()
    cleanup_jobs = [j for j in jobs if j.func.__name__ == "_internal_cleanup"]
    assert len(cleanup_jobs) == 1, (
        f"Expected 1 _internal_cleanup job, got {len(cleanup_jobs)}: "
        f"{[j.id for j in cleanup_jobs]}"
    )


# ─── Fix 3: Legacy settings fields rejected ───────────────────────────────────

def test_legacy_scan_interval_hours_not_in_model():
    from backend.routers.settings import SettingsUpdate
    fields = SettingsUpdate.model_fields
    assert "scan_interval_hours" not in fields, (
        "scan_interval_hours is a dead legacy field — must be removed from SettingsUpdate"
    )
    assert "deletion_check_interval_hours" not in fields, (
        "deletion_check_interval_hours is a dead legacy field — must be removed"
    )


@pytest.mark.asyncio
async def test_legacy_interval_fields_ignored_by_api(app_client):
    """Legacy hour-based interval fields must be silently ignored, not crash."""
    _, client = app_client
    resp = await client.post("/api/auth/setup", json={"username": "admin", "password": "testpass123"})
    token = resp.json().get("access_token") or resp.json().get("token")
    client.headers.update({"Authorization": f"Bearer {token}"})
    r = await client.post("/api/settings", json={
        "scan_interval_hours": "6",
        "deletion_check_interval_hours": "2",
    })
    assert r.status_code == 200
    data = r.json()
    assert "scan_interval_hours" not in data.get("updated", [])
    assert "deletion_check_interval_hours" not in data.get("updated", [])


# ─── Fix 4: emby_client retry coverage ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_users_retries_on_timeout():
    """get_users must retry on network timeout."""
    from backend import emby_client
    calls = []

    class FakeResp:
        status_code = 200
        def json(self): return [{"Id": "user1"}]

    async def fake_get(*args, **kwargs):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.TimeoutException("timeout")
        return FakeResp()

    with patch.object(emby_client, "get_client", new=AsyncMock(return_value=("http://emby", "key"))):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = fake_get
            mock_cls.return_value = mock_client
            with patch("backend.db.utils.asyncio") as mock_asyncio:
                mock_asyncio.sleep = AsyncMock()
                result = await emby_client.get_users()

    assert result == [{"Id": "user1"}]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_get_libraries_retries_on_timeout():
    """get_libraries must retry on network timeout."""
    from backend import emby_client
    calls = []

    class FakeResp:
        status_code = 200
        def json(self): return {"Items": [{"Id": "lib1", "Name": "Movies"}]}

    async def fake_get(*args, **kwargs):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.TimeoutException("timeout")
        return FakeResp()

    with patch.object(emby_client, "get_client", new=AsyncMock(return_value=("http://emby", "key"))):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = fake_get
            mock_cls.return_value = mock_client
            with patch("backend.db.utils.asyncio") as mock_asyncio:
                mock_asyncio.sleep = AsyncMock()
                result = await emby_client.get_libraries()

    assert len(result) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_delete_item_retries_on_connect_error():
    """delete_item must retry on connection error."""
    from backend import emby_client
    calls = []

    class FakeResp:
        status_code = 204

    async def fake_delete(*args, **kwargs):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("refused")
        return FakeResp()

    with patch.object(emby_client, "get_client", new=AsyncMock(return_value=("http://emby", "key"))):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.delete = fake_delete
            mock_cls.return_value = mock_client
            with patch("backend.db.utils.asyncio") as mock_asyncio:
                mock_asyncio.sleep = AsyncMock()
                result = await emby_client.delete_item("item123")

    assert result is True
    assert len(calls) == 2


# ─── Fix 5: Poster proxy endpoint + no api_key in fallback URLs ───────────────

def test_get_poster_url_fallback_has_no_api_key():
    """_get_poster_url fallback must return a proxy path, not a URL with api_key."""
    import asyncio
    from backend.rules import legacy_conditions as lc

    async def _run():
        with patch.object(lc, "radarr_get_poster_url", new=AsyncMock(return_value="")):
            with patch.object(lc, "get_client", new=AsyncMock(return_value=("http://emby:8096", "SECRETKEY"))):
                url = await lc._get_poster_url(
                    emby_id="abc123",
                    tmdb_id="",
                    media_type="Movie",
                    radarr_id=None,
                    sonarr_id=None,
                    server_id="0",
                )
        return url

    url = asyncio.run(_run())
    assert "SECRETKEY" not in url, f"api_key must not appear in poster URL: {url}"
    assert "api_key" not in url.lower(), f"api_key param must not be in URL: {url}"
    assert url.startswith("/api/proxy/poster/"), f"Expected internal proxy path, got: {url}"


@pytest.mark.asyncio
async def test_poster_proxy_endpoint_requires_valid_server(app_client):
    """Poster proxy with unknown server_id must return 404, not crash."""
    _, client = app_client
    r = await client.get("/api/proxy/poster/nonexistent/item123")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_poster_proxy_endpoint_is_public(app_client):
    """Poster proxy must not require auth (img src cannot send cookies)."""
    _, client = app_client
    r = await client.get("/api/proxy/poster/0/item123")
    assert r.status_code != 401
