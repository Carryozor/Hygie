"""GET /api/settings/media-servers must mask api_key like GET /api/settings does.

Regression: list_media_servers returned the decrypted api_key in clear,
bypassing the '***' masking applied by GET /api/settings. The frontend
ServersTab eye toggle and stores/servers.js loaded this on every boot.

Fix: mask api_key in the list response (on a copy — never mutate the cached
list get_media_servers() returns). PUT already keeps the stored key when
api_key == '***' (unchanged). A new GET /media-servers/{id}/reveal endpoint
(require_auth) returns the real key, mirroring GET /reveal/{key}.
"""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest_asyncio.fixture
async def ms_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms

    db_path = str(tmp_path / "ms.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()

    from backend.routers import settings as settings_router
    app = FastAPI()
    # Override the exact require_auth object settings.py's routes captured via
    # `from ..auth import require_auth` (settings_router.require_auth) — not
    # backend.auth.require_auth directly. Other test modules in the full suite
    # call importlib.reload(backend.auth), which rebinds backend.auth.require_auth
    # to a new function object while settings_router (imported earlier, never
    # reloaded here) keeps its own captured reference. Overriding the wrong
    # object makes the override silently not apply — a 401 in isolation-passing
    # tests that only shows up when the full suite runs in a different order.
    app.dependency_overrides[settings_router.require_auth] = lambda: "testuser"
    app.include_router(settings_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0


async def _seed_server(real_key="s3cr3t-api-key"):
    from backend.db.media_servers import save_media_servers
    await save_media_servers([{
        "id": "0", "name": "Emby", "url": "http://emby.local:8096",
        "api_key": real_key, "ext_url": "", "type": "emby", "enabled": True,
    }])


@pytest.mark.asyncio
async def test_list_media_servers_masks_api_key(ms_client):
    await _seed_server("s3cr3t-api-key")
    resp = await ms_client.get("/api/settings/media-servers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["api_key"] == "***"
    assert "s3cr3t-api-key" not in resp.text


@pytest.mark.asyncio
async def test_masking_does_not_mutate_cached_list(ms_client):
    """The masking must copy — get_media_servers() returns a cached list, and
    mutating its dicts in place would corrupt the cache for other readers
    (e.g. the actual Emby/Jellyfin/Plex client that needs the real key)."""
    await _seed_server("s3cr3t-api-key")
    await ms_client.get("/api/settings/media-servers")  # first read — masks the response only

    from backend.db.media_servers import get_media_servers
    servers = await get_media_servers()
    assert servers[0]["api_key"] == "s3cr3t-api-key"


@pytest.mark.asyncio
async def test_reveal_media_server_key_returns_real_value(ms_client):
    await _seed_server("s3cr3t-api-key")
    resp = await ms_client.get("/api/settings/media-servers/0/reveal")
    assert resp.status_code == 200
    assert resp.json()["api_key"] == "s3cr3t-api-key"


@pytest.mark.asyncio
async def test_reveal_media_server_key_requires_auth(tmp_path):
    """dependency_overrides in ms_client bypasses auth — build a client
    without the override to verify require_auth is actually wired in."""
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms

    db_path = str(tmp_path / "ms_noauth.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    await _seed_server("s3cr3t-api-key")

    from backend.routers import settings as settings_router
    app = FastAPI()
    app.include_router(settings_router.router)  # no auth override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/settings/media-servers/0/reveal")
    assert resp.status_code == 401

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0


@pytest.mark.asyncio
async def test_put_with_masked_api_key_keeps_stored_key(ms_client):
    await _seed_server("s3cr3t-api-key")
    resp = await ms_client.put(
        "/api/settings/media-servers/0",
        json={"name": "Emby renamed", "url": "http://emby.local:8096",
              "api_key": "***", "type": "emby", "enabled": True},
    )
    assert resp.status_code == 200

    from backend.db.media_servers import get_media_servers
    servers = await get_media_servers()
    assert servers[0]["name"] == "Emby renamed"
    assert servers[0]["api_key"] == "s3cr3t-api-key"
