"""4.6.0 (P4-11, N2): media-server CRUD responses mask api_key; the cached list
returned by get_media_servers() is never mutated before validation/saving."""
import os

import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

REAL = "s3cr3t-api-key"


@pytest.fixture(autouse=True)
def _lan_dns(monkeypatch):
    import ipaddress
    import socket

    def _gai(host, *a, **k):
        try:
            ip = str(ipaddress.ip_address(host.strip("[]")))
        except ValueError:
            ip = "192.168.1.10"
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr("backend.db.utils.socket.getaddrinfo", _gai)


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
    app.dependency_overrides[settings_router.require_auth] = lambda: "testuser"
    app.include_router(settings_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0


async def _seed():
    from backend.db.media_servers import save_media_servers
    await save_media_servers([{
        "id": "0", "name": "Emby", "url": "http://emby.local:8096",
        "api_key": REAL, "ext_url": "", "type": "emby", "enabled": True,
    }])


@pytest.mark.asyncio
async def test_post_put_delete_responses_mask_api_keys(ms_client):
    await _seed()
    post = await ms_client.post("/api/settings/media-servers", json={
        "name": "Jelly", "url": "http://jelly.local:8096", "api_key": "second-secret", "type": "jellyfin"})
    assert post.status_code == 201
    assert "second-secret" not in post.text and REAL not in post.text
    assert all(s["api_key"] == "***" for s in post.json()["servers"])

    put = await ms_client.put("/api/settings/media-servers/0", json={"name": "Renamed"})
    assert put.status_code == 200
    assert REAL not in put.text and "second-secret" not in put.text
    assert all(s["api_key"] == "***" for s in put.json()["servers"])

    delete = await ms_client.delete("/api/settings/media-servers/1")
    assert delete.status_code == 200
    assert REAL not in delete.text
    assert [s["api_key"] for s in delete.json()["servers"]] == ["***"]

    # The stored key is untouched by masking.
    from backend.db.media_servers import get_media_servers
    assert (await get_media_servers())[0]["api_key"] == REAL


@pytest.mark.asyncio
async def test_put_refused_url_leaves_cached_list_untouched(ms_client):
    await _seed()
    from backend.db.media_servers import get_media_servers
    await get_media_servers()  # warm the cache
    r = await ms_client.put("/api/settings/media-servers/0", json={"name": "X", "url": "http://127.0.0.1:8096"})
    assert r.status_code == 422
    after = (await get_media_servers())[0]
    assert (after["name"], after["url"]) == ("Emby", "http://emby.local:8096")


@pytest.mark.asyncio
async def test_put_invalid_ext_url_does_not_leak_partial_update_into_cache(ms_client):
    await _seed()
    from backend.db.media_servers import get_media_servers
    await get_media_servers()
    r = await ms_client.put("/api/settings/media-servers/0", json={
        "name": "Changed", "url": "http://other.local:8096", "ext_url": "ftp://bad"})
    assert r.status_code == 422
    s = (await get_media_servers())[0]
    assert (s["name"], s["url"]) == ("Emby", "http://emby.local:8096")


@pytest.mark.asyncio
async def test_post_does_not_append_to_cache_when_save_fails(ms_client, monkeypatch):
    await _seed()
    from backend.db.media_servers import get_media_servers
    await get_media_servers()
    from backend.routers import settings as settings_router

    async def _boom(servers):
        raise RuntimeError("db down")

    monkeypatch.setattr(settings_router, "save_media_servers", _boom)
    with pytest.raises(RuntimeError):
        await ms_client.post("/api/settings/media-servers", json={"url": "http://new.local:8096"})
    assert len(await get_media_servers()) == 1
