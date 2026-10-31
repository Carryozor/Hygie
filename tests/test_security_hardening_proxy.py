"""Security hardening: image proxy rate limit and concurrency cap."""
import asyncio

import pytest

from backend import proxy as proxy_mod


class _Req:
    def __init__(self, ip="10.1.1.1"):
        self.query_params = {}
        self.headers = {}
        self.client = type("C", (), {"host": ip})()


@pytest.fixture(autouse=True)
def _reset():
    proxy_mod._proxy_hits.clear()
    yield
    proxy_mod._proxy_hits.clear()


@pytest.fixture
def trusted_proxy(monkeypatch):
    """The per-IP budget only applies when real client IPs are visible."""
    monkeypatch.setattr("backend.auth._TRUST_PROXY", True)


@pytest.mark.asyncio
async def test_image_proxy_rate_limited_per_ip(monkeypatch, trusted_proxy):
    monkeypatch.setattr(proxy_mod, "_PROXY_RATE_MAX", 5)
    codes = [(await proxy_mod.proxy_image(_Req())).status_code for _ in range(7)]
    assert codes[:5] == [400] * 5  # missing url -> 400, budget still counted
    assert codes[5:] == [429, 429]
    # Another IP keeps its own budget.
    assert (await proxy_mod.proxy_image(_Req("10.2.2.2"))).status_code == 400


@pytest.mark.asyncio
async def test_poster_proxy_shares_rate_limit(monkeypatch, trusted_proxy):
    monkeypatch.setattr(proxy_mod, "_PROXY_RATE_MAX", 2)
    for _ in range(2):
        assert (await proxy_mod.proxy_poster("0", "bad id!", _Req())).status_code == 400
    assert (await proxy_mod.proxy_poster("0", "abc", _Req())).status_code == 429


@pytest.mark.asyncio
async def test_concurrency_is_capped(monkeypatch):
    monkeypatch.setattr(proxy_mod, "_proxy_semaphore", asyncio.Semaphore(3))
    state = {"now": 0, "peak": 0}

    async def slow(request):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return None

    monkeypatch.setattr(proxy_mod, "_fetch_proxied_image", slow)
    await asyncio.gather(*(proxy_mod.proxy_image(_Req(f"10.3.0.{i}")) for i in range(12)))
    assert state["peak"] == 3
