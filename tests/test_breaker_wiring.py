"""Integration tests: circuit breakers wired into the HTTP retry helpers.

The breakers themselves are unit-tested in test_circuit_breaker.py — these
tests verify that with_retry / http_retry / the Seerr client actually route
their calls through the registered breakers (previously they were decorative).
"""
import os
import pytest

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.pop("DATABASE_URL", None)

import httpx

from backend.arr_clients.circuit_breaker import (
    CircuitOpenError, _registry, get_breaker,
)
from backend.arr_clients.retry import with_retry
from backend.db.utils import http_retry
from backend.exceptions import ArrClientError


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.clear()
    yield
    _registry.clear()


def _failing_fn(calls: list):
    async def fn():
        calls.append(1)
        raise httpx.ConnectError("down")
    return fn


# ─── with_retry (Radarr/Sonarr path) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_with_retry_records_failures_on_breaker():
    get_breaker("svc-a", failure_threshold=2, recovery_timeout=300)
    calls: list = []
    with pytest.raises(httpx.ConnectError):
        await with_retry(_failing_fn(calls), retries=1, service="svc-a")
    assert get_breaker("svc-a")._failure_count == 1


@pytest.mark.asyncio
async def test_with_retry_fast_fails_when_breaker_open():
    get_breaker("svc-b", failure_threshold=1, recovery_timeout=300)
    calls: list = []
    with pytest.raises(httpx.ConnectError):
        await with_retry(_failing_fn(calls), retries=1, service="svc-b")
    n_before = len(calls)

    with pytest.raises(CircuitOpenError):
        await with_retry(_failing_fn(calls), retries=1, service="svc-b")
    assert len(calls) == n_before, "open breaker must reject without calling the service"


@pytest.mark.asyncio
async def test_with_retry_without_service_bypasses_breakers():
    calls: list = []
    with pytest.raises(httpx.ConnectError):
        await with_retry(_failing_fn(calls), retries=1)
    assert _registry == {}, "no service name → no breaker registered"


# ─── http_retry (Emby path) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_retry_fast_fails_when_breaker_open():
    get_breaker("emby:0", failure_threshold=1, recovery_timeout=300)
    calls: list = []
    with pytest.raises(httpx.ConnectError):
        await http_retry(_failing_fn(calls), retries=1, service="emby:0")
    n_before = len(calls)

    with pytest.raises(CircuitOpenError):
        await http_retry(_failing_fn(calls), retries=1, service="emby:0")
    assert len(calls) == n_before


# ─── Seerr client converts CircuitOpenError → ArrClientError ─────────────────

@pytest.mark.asyncio
async def test_seerr_cache_raises_arr_error_when_breaker_open(monkeypatch):
    from backend.arr_clients import seerr as seerr_mod

    async def _fake_config():
        return "http://seerr.local:5055", "key"

    monkeypatch.setattr(seerr_mod, "_seerr_config", _fake_config)

    breaker = get_breaker("seerr", failure_threshold=1, recovery_timeout=300)
    breaker._on_failure()  # force OPEN
    assert breaker.state == breaker.OPEN

    with pytest.raises(ArrClientError):
        await seerr_mod.build_seerr_request_cache()
