"""HYGIE_TRUSTED_PROXIES: client-IP resolution must give the SAME answer whether
Hygie resolves the IP itself (peer trusted) or uvicorn's ProxyHeadersMiddleware
already rewrote request.client (real uvicorn middleware, installed version)."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend import auth as auth_mod
from backend.startup_validator import StartupValidator

TRUSTED = "172.27.0.0/16"
STACKS = ["hygie_only", "uvicorn"]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("HYGIE_TRUST_PROXY", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    monkeypatch.delenv("HYGIE_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("HYGIE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(auth_mod, "_TRUST_PROXY", False)
    monkeypatch.setattr(auth_mod, "_warned_untrusted_headers", False, raising=False)
    monkeypatch.setattr(auth_mod, "_warned_untrusted_forwarded_for", False)
    # Never hit the real DB from the fire-and-forget journal task.
    monkeypatch.setattr("backend.db.logs.add_log", AsyncMock())


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ip")
    async def ip(request: Request):
        return {"ip": auth_mod.get_client_ip(request)}

    return app


def _stack(name: str):
    app = _app()
    if name == "uvicorn":
        return ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED)
    return app


async def _ip(stack, peer, headers):
    transport = ASGITransport(app=stack, client=(peer, 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return (await c.get("/ip", headers=headers)).json()["ip"]


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", TRUSTED)


@pytest.mark.parametrize("stack", STACKS)
async def test_untrusted_peer_headers_ignored(stack, trusted):
    ip = await _ip(_stack(stack), "203.0.113.9",
                   {"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"})
    assert ip == "203.0.113.9"


@pytest.mark.parametrize("stack", STACKS)
async def test_trusted_peer_uses_rightmost_untrusted_xff(stack, trusted):
    ip = await _ip(_stack(stack), "172.27.0.2",
                   {"X-Forwarded-For": "6.6.6.6, 1.2.3.4, 172.27.0.9"})
    assert ip == "1.2.3.4"


@pytest.mark.parametrize("stack", STACKS)
async def test_cf_header_only_without_xff(stack, trusted):
    stk = _stack(stack)
    assert await _ip(stk, "172.27.0.2", {"CF-Connecting-IP": "5.6.7.8"}) == "5.6.7.8"
    # With a usable XFF, XFF wins over a (forgeable) CF header.
    assert await _ip(stk, "172.27.0.2",
                     {"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"}) == "1.2.3.4"


@pytest.mark.parametrize("stack", STACKS)
async def test_trusted_peer_without_headers_returns_peer(stack, trusted):
    assert await _ip(_stack(stack), "172.27.0.2", {}) == "172.27.0.2"


@pytest.mark.parametrize("stack", STACKS)
async def test_ipv4_mapped_peer_matched(stack, trusted):
    ip = await _ip(_stack(stack), "::ffff:172.27.0.2", {"X-Forwarded-For": "1.2.3.4"})
    assert ip == "1.2.3.4"


@pytest.mark.parametrize("stack", STACKS)
async def test_untrusted_headers_warn_once_in_journal(stack, trusted):
    stk = _stack(stack)
    with patch("backend.db.logs.add_log", new=AsyncMock()) as add_log:
        for _ in range(2):
            await _ip(stk, "203.0.113.9", {"X-Forwarded-For": "1.2.3.4"})
        await asyncio.sleep(0.05)
    assert add_log.await_count == 1
    assert "security" in (list(add_log.await_args.args) + list(add_log.await_args.kwargs.values()))


@pytest.mark.parametrize("stack", STACKS)
async def test_no_warning_when_peer_is_in_xff(stack, trusted):
    """uvicorn already resolved the client: peer == a XFF entry -> no warning."""
    stk = _stack(stack)
    with patch("backend.db.logs.add_log", new=AsyncMock()) as add_log:
        await _ip(stk, "172.27.0.2", {"X-Forwarded-For": "1.2.3.4"})
        await asyncio.sleep(0.05)
    assert add_log.await_count == 0


async def test_invalid_cidr_is_critical(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", "172.27.0.0/16, not-a-cidr")
    assert auth_mod.trusted_networks() == ()
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert [i.level for i in issues] == ["CRITICAL"]
    assert "not-a-cidr" in issues[0].message


def test_parse_accepts_spaces_ipv4_ipv6_and_single_ips(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", " 10.0.0.0/8 , 192.168.1.5,fd00::/8 ")
    assert len(auth_mod.trusted_networks()) == 3
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", "")
    assert auth_mod.trusted_networks() == ()


async def test_legacy_mode_unchanged_and_warns_at_startup(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUST_PROXY", "1")
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert [i.level for i in issues] == ["WARN"]
    assert "HYGIE_TRUSTED_PROXIES" in issues[0].message + (issues[0].remedy or "")
    monkeypatch.setattr(auth_mod, "_TRUST_PROXY", True)
    ip = await _ip(_app(), "10.0.0.2", {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})
    assert ip == "10.0.0.1"  # rightmost entry, as before


async def test_both_variables_warns_legacy_ignored(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUST_PROXY", "1")
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", TRUSTED)
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert any("HYGIE_TRUST_PROXY" in i.message and "ignor" in i.message.lower() for i in issues)


async def test_catch_all_cidr_warns(monkeypatch):
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", "0.0.0.0/0")
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert any(i.level == "WARN" and "0.0.0.0/0" in i.message for i in issues)


async def test_forwarded_allow_ips_star_warns(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert any(i.level == "WARN" and "FORWARDED_ALLOW_IPS" in i.message for i in issues)


async def test_public_origin_warning_silent_when_trusted_proxies_set(monkeypatch):
    monkeypatch.setenv("HYGIE_ALLOWED_ORIGINS", "https://hygie.example.com")
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", TRUSTED)
    issues: list = []
    await StartupValidator()._check_proxy_trust(issues)
    assert issues == []


class _Req:
    query_params: dict = {}
    headers: dict = {}
    client = type("C", (), {"host": "10.9.9.9"})()


def test_image_proxy_budget_active_with_trusted_proxies(monkeypatch):
    from backend import proxy as proxy_mod
    proxy_mod._proxy_hits.clear()
    assert proxy_mod._proxy_rate_limited(_Req()) is False
    assert not proxy_mod._proxy_hits  # budget inactive: nothing recorded
    monkeypatch.setenv("HYGIE_TRUSTED_PROXIES", TRUSTED)
    assert proxy_mod._proxy_rate_limited(_Req()) is False
    assert proxy_mod._proxy_hits  # budget active: hit recorded
    proxy_mod._proxy_hits.clear()


def test_uvicorn_version_supports_cidr():
    """Observed: the installed uvicorn middleware trusts CIDR networks."""
    import ipaddress
    from uvicorn.middleware.proxy_headers import _TrustedHosts
    th = _TrustedHosts(TRUSTED)
    assert ipaddress.ip_network(TRUSTED) in th.trusted_networks
    assert "172.27.5.5" in th
