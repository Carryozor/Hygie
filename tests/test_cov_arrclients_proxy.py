"""Coverage tests for backend/proxy.py — SSRF-guarded image proxy.

Security-critical: verifies the whitelist build, per-hop redirect
re-validation, DNS-rebinding guard, content-type/size enforcement, and the
poster proxy's item-id validation. No real network or DNS access — the
`is_loopback_or_link_local` DNS lookup is monkeypatched everywhere so tests
never depend on real resolution.
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import pytest

import backend.proxy as proxy_mod
from backend.proxy import (
    _add_url_to_whitelist,
    _get_proxy_whitelist,
    _is_url_allowed,
    invalidate_proxy_whitelist,
)


@pytest.fixture(autouse=True)
def _reset_whitelist_cache():
    proxy_mod._proxy_whitelist = set()
    proxy_mod._proxy_whitelist_ts = 0.0
    yield
    proxy_mod._proxy_whitelist = set()
    proxy_mod._proxy_whitelist_ts = 0.0


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """Default: nothing is loopback/link-local unless a test overrides this."""
    async def _fake(host):
        return False
    monkeypatch.setattr(proxy_mod, "is_loopback_or_link_local", _fake)


# ─── _add_url_to_whitelist ──────────────────────────────────────────────────────

def test_add_url_to_whitelist_explicit_port():
    allowed = set()
    _add_url_to_whitelist(allowed, "http://radarr.local:7878")
    assert ("radarr.local", 7878) in allowed


def test_add_url_to_whitelist_default_https_port():
    allowed = set()
    _add_url_to_whitelist(allowed, "https://emby.example.com")
    assert ("emby.example.com", 443) in allowed


def test_add_url_to_whitelist_default_http_port():
    allowed = set()
    _add_url_to_whitelist(allowed, "http://emby.example.com")
    assert ("emby.example.com", 80) in allowed


def test_add_url_to_whitelist_skips_url_without_host():
    allowed = set()
    _add_url_to_whitelist(allowed, "not-a-url")
    assert allowed == set()


def test_add_url_to_whitelist_swallows_exception_on_malformed_input():
    """urlparse(None) does NOT raise (returns empty parts) — a non-str,
    non-None type is needed to actually exercise the except branch."""
    allowed = set()
    _add_url_to_whitelist(allowed, 12345)
    assert allowed == set()


# ─── _is_url_allowed ────────────────────────────────────────────────────────────

def test_is_url_allowed_swallows_exception_on_malformed_input():
    assert _is_url_allowed(12345, {("x", 80)}) is False


# ─── invalidate_proxy_whitelist ─────────────────────────────────────────────────

def test_invalidate_proxy_whitelist_resets_ts():
    proxy_mod._proxy_whitelist_ts = 999999999.0
    invalidate_proxy_whitelist()
    assert proxy_mod._proxy_whitelist_ts == 0.0


# ─── _get_proxy_whitelist ───────────────────────────────────────────────────────

async def test_get_proxy_whitelist_includes_known_cdns(monkeypatch):
    async def _get_setting(key):
        return None
    async def _servers():
        return []
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)
    monkeypatch.setattr(proxy_mod, "get_media_servers", _servers)

    allowed = await _get_proxy_whitelist()
    assert ("image.tmdb.org", 443) in allowed
    assert ("fanart.tv", 443) in allowed


async def test_get_proxy_whitelist_includes_configured_radarr_sonarr(monkeypatch):
    async def _get_setting(key):
        return {"radarr_url": "http://radarr:7878", "sonarr_url": "http://sonarr:8989"}.get(key)
    async def _servers():
        return []
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)
    monkeypatch.setattr(proxy_mod, "get_media_servers", _servers)

    allowed = await _get_proxy_whitelist()
    assert ("radarr", 7878) in allowed
    assert ("sonarr", 8989) in allowed


async def test_get_proxy_whitelist_includes_media_server_url_and_ext_url(monkeypatch):
    async def _get_setting(key):
        return None
    async def _servers():
        return [{"url": "http://emby.local:8096", "ext_url": "https://emby.public.example.com"}]
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)
    monkeypatch.setattr(proxy_mod, "get_media_servers", _servers)

    allowed = await _get_proxy_whitelist()
    assert ("emby.local", 8096) in allowed
    assert ("emby.public.example.com", 443) in allowed


async def test_get_proxy_whitelist_uses_cache_within_ttl(monkeypatch):
    call_count = {"n": 0}
    async def _get_setting(key):
        call_count["n"] += 1
        return None
    async def _servers():
        return []
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)
    monkeypatch.setattr(proxy_mod, "get_media_servers", _servers)

    first = await _get_proxy_whitelist()
    second = await _get_proxy_whitelist()
    assert first is second
    # get_setting called twice per build (radarr_url, sonarr_url) -> only 1 build happened
    assert call_count["n"] == 2


async def test_get_proxy_whitelist_double_check_inside_lock_avoids_rebuild(monkeypatch):
    """Thundering-herd guard: if another coroutine refreshed the whitelist
    while this one was waiting on the lock, it must return that fresh
    result instead of rebuilding again."""
    class _FakeLock:
        async def __aenter__(self):
            # Simulate a concurrent refresh completing right as we acquire the lock.
            proxy_mod._proxy_whitelist = {("winner", 1)}
            proxy_mod._proxy_whitelist_ts = time_mod.time()
            return self

        async def __aexit__(self, *a):
            return False

    import time as time_mod
    monkeypatch.setattr(proxy_mod, "_proxy_whitelist_lock", _FakeLock())

    async def _get_setting(key):
        raise AssertionError("must not rebuild — should return the double-checked cache")
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)

    result = await _get_proxy_whitelist()
    assert result == {("winner", 1)}


async def test_get_proxy_whitelist_rebuilds_after_invalidate(monkeypatch):
    async def _get_setting(key):
        return None
    async def _servers():
        return []
    monkeypatch.setattr(proxy_mod, "get_setting", _get_setting)
    monkeypatch.setattr(proxy_mod, "get_media_servers", _servers)

    first = await _get_proxy_whitelist()
    invalidate_proxy_whitelist()
    second = await _get_proxy_whitelist()
    assert first is not second


# ─── proxy_image ────────────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self, url: str):
        self.query_params = {"url": url} if url is not None else {}


class _FakeStream:
    def __init__(self, status_code, headers, chunks=(b"\xff\xd8img",)):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, _size):
        for c in self._chunks:
            yield c


class _FakeAsyncClient:
    """Configurable fake httpx.AsyncClient. `responses` maps url -> _FakeStream
    (or a list of them consumed one per call, for multi-hop redirects)."""
    def __init__(self, responses, *a, **kw):
        self._responses = responses
        self.requested: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        self.requested.append(url)
        resp = self._responses[url]
        if isinstance(resp, list):
            return resp.pop(0)
        return resp


def _install_fake_client(monkeypatch, responses):
    client_holder = {}

    def _make(*a, **kw):
        c = _FakeAsyncClient(responses, *a, **kw)
        client_holder["client"] = c
        return c

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _make)
    return client_holder


async def _whitelist(monkeypatch, allowed):
    async def _fn():
        return allowed
    monkeypatch.setattr(proxy_mod, "_get_proxy_whitelist", _fn)


async def test_proxy_image_missing_url_returns_400():
    resp = await proxy_mod.proxy_image(_FakeRequest(None))
    assert resp.status_code == 400


async def test_proxy_image_rejects_bad_scheme():
    resp = await proxy_mod.proxy_image(_FakeRequest("ftp://host/img.jpg"))
    assert resp.status_code == 400


async def test_proxy_image_rejects_host_not_in_whitelist(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    resp = await proxy_mod.proxy_image(_FakeRequest("http://evil.example.com/img.jpg"))
    assert resp.status_code == 403


async def test_proxy_image_blocks_dns_rebinding_on_initial_host(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    async def _rebind(host):
        return True
    monkeypatch.setattr(proxy_mod, "is_loopback_or_link_local", _rebind)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/img.jpg"))
    assert resp.status_code == 403


async def test_proxy_image_returns_image_bytes_on_success(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/img.jpg": _FakeStream(200, {"content-type": "image/jpeg"}),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/img.jpg"))
    assert resp.status_code == 200
    assert resp.body == b"\xff\xd8img"
    assert resp.media_type == "image/jpeg"


async def test_proxy_image_rejects_non_image_content_type(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/page.html": _FakeStream(200, {"content-type": "text/html"}),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/page.html"))
    assert resp.status_code == 415


async def test_proxy_image_follows_redirect_within_whitelist(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/redirect.jpg": _FakeStream(
            302, {"location": "http://good.example.com/final.jpg"}
        ),
        "http://good.example.com/final.jpg": _FakeStream(200, {"content-type": "image/png"}),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/redirect.jpg"))
    assert resp.status_code == 200
    assert resp.media_type == "image/png"


async def test_proxy_image_blocks_dns_rebinding_on_redirect_hop(monkeypatch):
    """Whitelisted host's DNS could be rebound between the first request and
    the redirect being followed — must re-check on every hop."""
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/redirect.jpg": _FakeStream(
            302, {"location": "http://good.example.com/final.jpg"}
        ),
    }
    _install_fake_client(monkeypatch, responses)

    calls = {"n": 0}
    async def _rebind(host):
        calls["n"] += 1
        return calls["n"] > 1  # first check (initial host) passes, redirect hop fails
    monkeypatch.setattr(proxy_mod, "is_loopback_or_link_local", _rebind)

    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/redirect.jpg"))
    assert resp.status_code == 403


async def test_proxy_image_exceeds_max_redirects_returns_404(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/r0.jpg": _FakeStream(302, {"location": "http://good.example.com/r1.jpg"}),
        "http://good.example.com/r1.jpg": _FakeStream(302, {"location": "http://good.example.com/r2.jpg"}),
        "http://good.example.com/r2.jpg": _FakeStream(302, {"location": "http://good.example.com/r3.jpg"}),
        "http://good.example.com/r3.jpg": _FakeStream(302, {"location": "http://good.example.com/r4.jpg"}),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/r0.jpg"))
    assert resp.status_code == 404


async def test_proxy_image_returns_404_on_upstream_error(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    responses = {
        "http://good.example.com/missing.jpg": _FakeStream(404, {}),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/missing.jpg"))
    assert resp.status_code == 404


async def test_proxy_image_enforces_max_bytes(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})
    big_chunk = b"x" * (2 * 1024 * 1024)
    chunks = tuple(big_chunk for _ in range(6))  # 12 MB > 10 MB cap
    responses = {
        "http://good.example.com/huge.jpg": _FakeStream(200, {"content-type": "image/jpeg"}, chunks=chunks),
    }
    _install_fake_client(monkeypatch, responses)
    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/huge.jpg"))
    assert resp.status_code == 413


async def test_proxy_image_swallows_exception_and_returns_404(monkeypatch):
    await _whitelist(monkeypatch, {("good.example.com", 80)})

    def _raise(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _raise)

    resp = await proxy_mod.proxy_image(_FakeRequest("http://good.example.com/x.jpg"))
    assert resp.status_code == 404


# ─── proxy_poster ───────────────────────────────────────────────────────────────

async def test_proxy_poster_rejects_invalid_item_id():
    resp = await proxy_mod.proxy_poster("srv1", "not valid!!")
    assert resp.status_code == 400


async def test_proxy_poster_returns_404_when_server_not_found(monkeypatch):
    async def _servers():
        return [{"id": "other", "url": "http://emby:8096", "api_key": "k"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)
    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 404


async def test_proxy_poster_returns_404_when_server_missing_url_or_key(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "", "api_key": ""}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)
    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 404


async def test_proxy_poster_returns_image_and_injects_api_key_header(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "http://emby.local:8096", "api_key": "secret-key"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)

    captured = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url, headers=None, **kw):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeStream(200, {"content-type": "image/jpeg"})

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _Client)

    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 200
    assert resp.body == b"\xff\xd8img"
    assert "abc123" in captured["url"]
    assert "secret-key" in captured["headers"]["X-Emby-Authorization"]


async def test_proxy_poster_rejects_non_image_content_type(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "http://emby.local:8096", "api_key": "k"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)

    class _Client:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url, headers=None, **kw):
            return _FakeStream(200, {"content-type": "text/html"})

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _Client)
    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 415


async def test_proxy_poster_enforces_max_bytes(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "http://emby.local:8096", "api_key": "k"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)

    big_chunk = b"x" * (2 * 1024 * 1024)
    chunks = tuple(big_chunk for _ in range(6))

    class _Client:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url, headers=None, **kw):
            return _FakeStream(200, {"content-type": "image/jpeg"}, chunks=chunks)

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _Client)
    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 413


async def test_proxy_poster_returns_404_on_non_200(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "http://emby.local:8096", "api_key": "k"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)

    class _Client:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url, headers=None, **kw):
            return _FakeStream(404, {})

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _Client)
    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 404


async def test_proxy_poster_swallows_exception_and_returns_404(monkeypatch):
    async def _servers():
        return [{"id": "srv1", "url": "http://emby.local:8096", "api_key": "k"}]
    monkeypatch.setattr("backend.db.media_servers.get_media_servers", _servers)

    def _raise(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", _raise)

    resp = await proxy_mod.proxy_poster("srv1", "abc123")
    assert resp.status_code == 404
