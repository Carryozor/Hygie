"""Security hardening: SSRF guard (assert_public_or_lan) and Discord webhook checks."""
import os
import socket
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


def _gai(ip):
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return lambda *a, **k: [(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]


BLOCKED = [
    "http://0.0.0.0:8989",
    "http://[::]:8989",
    "http://[::ffff:127.0.0.1]:8989",
    "http://[::ffff:7f00:1]:8989",
    "http://169.254.169.254/latest/meta-data",
    "http://localhost:8989",
    "http://127.0.0.1:8989",
    "http://[::1]:8989",
    "http://224.0.0.1",
    "http://240.0.0.1",
]
ALLOWED_LITERAL = ["http://192.168.1.10:7878", "http://10.0.0.5:8989", "https://172.20.0.4"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", BLOCKED)
async def test_blocked_urls_raise(url):
    from backend.db.utils import assert_public_or_lan, UnsafeUrlError
    with pytest.raises(UnsafeUrlError):
        await assert_public_or_lan(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALLOWED_LITERAL)
async def test_lan_literals_allowed(url):
    from backend.db.utils import assert_public_or_lan
    await assert_public_or_lan(url)


@pytest.mark.asyncio
async def test_public_name_allowed_with_mocked_resolution():
    from backend.db.utils import assert_public_or_lan
    with patch("backend.db.utils.socket.getaddrinfo", _gai("93.184.216.34")):
        await assert_public_or_lan("https://seerr.example.org")


@pytest.mark.asyncio
async def test_name_resolving_to_loopback_blocked():
    from backend.db.utils import assert_public_or_lan, UnsafeUrlError
    with patch("backend.db.utils.socket.getaddrinfo", _gai("127.0.0.1")):
        with pytest.raises(UnsafeUrlError):
            await assert_public_or_lan("http://evil.example.org")


@pytest.mark.asyncio
async def test_resolution_failure_fails_closed():
    from backend.db.utils import assert_public_or_lan, UnsafeUrlError
    with patch("backend.db.utils.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
        with pytest.raises(UnsafeUrlError):
            await assert_public_or_lan("http://unresolvable.example.org")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ftp://192.168.1.10", "file:///etc/passwd", "http://", ""])
async def test_bad_scheme_or_host_rejected(url):
    from backend.db.utils import assert_public_or_lan, UnsafeUrlError
    with pytest.raises(UnsafeUrlError):
        await assert_public_or_lan(url)


@pytest.mark.asyncio
async def test_legacy_helper_blocks_unspecified_and_mapped_forms():
    from backend.db.utils import is_loopback_or_link_local
    assert await is_loopback_or_link_local("0.0.0.0") is True
    assert await is_loopback_or_link_local("::ffff:127.0.0.1") is True
    with patch("backend.db.utils.socket.getaddrinfo", side_effect=socket.gaierror("x")):
        assert await is_loopback_or_link_local("nope.example.org") is False  # legacy: fail-open
        assert await is_loopback_or_link_local("nope.example.org", fail_closed=True) is True


# ─── Discord webhook host validation ─────────────────────────────────────────
@pytest.mark.parametrize("url,ok", [
    ("https://discord.com/api/webhooks/1/abc", True),
    ("https://canary.discord.com/api/webhooks/1/abc", True),
    ("http://discord.com/api/webhooks/1/abc", True),
    ("https://chat.vidar.fr/api/webhooks/1/abc", True),      # self-hosted Discord-compatible chat
    ("http://192.168.1.20:3000/api/webhooks/1/abc", True),   # LAN stays allowed
    ("https://discord.com@evil.org/api/webhooks/1/abc", False),  # userinfo trick
    ("https://127.0.0.1/api/webhooks/1/abc", False),
    ("https://[::1]/api/webhooks/1/abc", False),
    ("https://[::ffff:127.0.0.1]/api/webhooks/1/abc", False),
    ("https://0.0.0.0/api/webhooks/1/abc", False),
    ("http://169.254.169.254/latest/meta-data", False),
    ("https://localhost/api/webhooks/1/abc", False),
    ("https://localhost./api/webhooks/1/abc", False),
    ("https://app.localhost/api/webhooks/1/abc", False),
    ("ftp://discord.com/x", False),
    ("", False),
])
def test_is_valid_discord_webhook(url, ok):
    from backend.discord_client import is_valid_discord_webhook
    assert is_valid_discord_webhook(url) is ok


@pytest.mark.asyncio
async def test_test_webhook_does_not_leak_response_body():
    import httpx
    from backend import discord_client as dc
    resp = httpx.Response(401, text="SECRET-INTERNAL-BODY")
    with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=resp)):
        ok, msg = await dc._test_webhook("https://discord.com/api/webhooks/1/x", "notifications")
    assert ok is False
    assert "SECRET-INTERNAL-BODY" not in msg
    assert "401" in msg


@pytest.mark.asyncio
async def test_test_webhook_refuses_foreign_host_without_request():
    import httpx
    from backend import discord_client as dc
    post = AsyncMock()
    with patch.object(httpx.AsyncClient, "post", post):
        ok, msg = await dc._test_webhook("http://169.254.169.254/x", "notifications")
    assert ok is False
    post.assert_not_called()


@pytest.mark.asyncio
async def test_send_alert_refuses_internal_webhook():
    import httpx
    from backend import discord_client as dc
    post = AsyncMock()
    with patch.object(dc, "_get_alert_webhook", AsyncMock(return_value="http://169.254.169.254/h")), \
         patch.object(httpx.AsyncClient, "post", post):
        assert await dc.send_alert("t", "d") is False
    post.assert_not_called()


# ─── Settings endpoints ──────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def settings_client(tmp_path):
    from fastapi import FastAPI
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms
    from backend.routers import settings as settings_router

    db_path = str(tmp_path / "s.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    from backend.db.schema import init_db
    await init_db()
    app = FastAPI()
    app.state.scheduler = None
    app.include_router(settings_router.router)
    app.dependency_overrides[settings_router.require_auth] = lambda: "testuser"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["seerr_url", "qbit_url", "radarr_url", "sonarr_url", "qbit_proxy_url"])
async def test_save_rejects_loopback_url(settings_client, key):
    r = await settings_client.post("/api/settings", json={key: "http://127.0.0.1:9999"})
    assert r.status_code == 422
    from backend.db.settings_store import get_setting
    assert not await get_setting(key)


@pytest.mark.asyncio
async def test_save_accepts_lan_url(settings_client):
    r = await settings_client.post("/api/settings", json={"seerr_url": "http://192.168.1.10:5055"})
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["discord_webhook", "discord_webhook_alerts"])
@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:8080/hook",
    "https://localhost/hook",
    "http://169.254.169.254/latest/meta-data",
    "https://discord.com@127.0.0.1/hook",
])
async def test_save_rejects_internal_webhook(settings_client, key, bad):
    r = await settings_client.post("/api/settings", json={key: bad})
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["discord_webhook", "discord_webhook_alerts"])
async def test_save_accepts_discord_and_self_hosted_webhooks(settings_client, key):
    # discord.com and a self-hosted Discord-compatible chat (prod uses chat.vidar.fr)
    for good in ("https://discord.com/api/webhooks/1/abc", "http://192.168.1.20:3000/api/webhooks/1/abc"):
        ok = await settings_client.post("/api/settings", json={key: good})
        assert ok.status_code == 200, good
    # Empty value stays allowed (disables the webhook).
    assert (await settings_client.post("/api/settings", json={key: ""})).status_code == 200


@pytest.mark.asyncio
async def test_test_service_blocks_stored_loopback_url_at_use_time(settings_client):
    from backend.db.settings_store import set_setting
    await set_setting("seerr_url", "http://127.0.0.1:5055")  # bypasses save-time check
    tester = AsyncMock(return_value=(True, "ok"))
    with patch.dict("backend.routers.settings._TESTERS", {"seerr": tester}):
        r = await settings_client.post("/api/settings/test/seerr")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    tester.assert_not_called()


@pytest.mark.asyncio
async def test_test_arr_blocks_metadata_endpoint(settings_client):
    r = await settings_client.post(
        "/api/settings/test-arr",
        json={"type": "radarr", "url": "http://169.254.169.254", "api_key": "k"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_sync_arr_blocks_unspecified_host(settings_client):
    r = await settings_client.post(
        "/api/settings/sync-arr-from-seerr",
        json={"seerr_url": "http://0.0.0.0:5055", "seerr_api_key": "k"})
    assert r.status_code == 422
