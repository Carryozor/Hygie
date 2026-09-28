"""GET /api/public/upcoming must never leak the INTERNAL media server URL.

Regression: routers/public.py built each safe_server's ext_url via
`_clean_url(s.get("ext_url", "") or s.get("url", ""))` — when ext_url was
unset, it fell back to the internal `url` (LAN address, often
RFC1918/loopback), handed unauthenticated to anyone who can reach the public
dashboard. Fix: return only ext_url, empty string when unset — never url.
"""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest_asyncio.fixture
async def public_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms

    import backend.auth as _auth_mod
    _auth_mod.DB_PATH = ":memory:"
    _auth_mod._rate_buckets.clear()

    db_path = str(tmp_path / "public_leak.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")

    from backend.routers import public as public_router
    app = FastAPI()
    app.include_router(public_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


@pytest.mark.asyncio
async def test_upcoming_never_leaks_internal_url_when_ext_url_unset(public_client):
    from backend.db.media_servers import save_media_servers
    await save_media_servers([{
        "id": "0", "name": "Emby", "url": "http://192.168.1.10:8096",
        "api_key": "k", "ext_url": "", "type": "emby", "enabled": True,
    }])

    resp = await public_client.get("/api/public/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["servers"]) == 1
    assert data["servers"][0]["ext_url"] == ""
    assert "192.168.1.10" not in resp.text


@pytest.mark.asyncio
async def test_upcoming_returns_ext_url_when_configured(public_client):
    from backend.db.media_servers import save_media_servers
    await save_media_servers([{
        "id": "0", "name": "Emby", "url": "http://192.168.1.10:8096",
        "api_key": "k", "ext_url": "https://emby.example.com", "type": "emby", "enabled": True,
    }])

    resp = await public_client.get("/api/public/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    assert data["servers"][0]["ext_url"] == "https://emby.example.com"
    assert "192.168.1.10" not in resp.text
