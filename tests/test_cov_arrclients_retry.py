"""Coverage tests for backend/arr_clients/retry.py — with_retry() backoff,
exhaustion, non-retryable propagation, and circuit-breaker wiring.
"""
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")

import httpx
import pytest

from backend.arr_clients.retry import with_retry
from backend.arr_clients import circuit_breaker as cb_mod


@pytest.fixture(autouse=True)
def _reset_breaker_registry():
    cb_mod._registry.clear()
    yield
    cb_mod._registry.clear()


async def test_retries_on_transient_error_then_succeeds(monkeypatch):
    """A transient network error on attempt 1 is retried (with a sleep) and
    the eventual successful result is returned."""
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _fake_sleep)

    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return "ok"

    result = await with_retry(_flaky, retries=3, base_delay=1.0, label="t")

    assert result == "ok"
    assert calls["n"] == 2
    # backoff for the first (and only) retried attempt: base_delay * 2**0
    assert sleeps == [1.0]


async def test_retry_backoff_delay_doubles_each_attempt(monkeypatch):
    """Backoff delay must follow base_delay * 2**attempt, not a fixed delay."""
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _fake_sleep)

    async def _always_fails():
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        await with_retry(_always_fails, retries=3, base_delay=0.5, label="t")

    # 3 attempts total -> 2 retried sleeps (after attempt 1 and attempt 2), none after the last
    assert sleeps == [0.5, 1.0]


async def test_raises_last_exception_after_retries_exhausted(monkeypatch):
    """When every attempt fails with a retryable error, with_retry re-raises
    the last exception instead of swallowing it."""
    async def _fake_sleep(_delay):
        return None

    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _fake_sleep)

    calls = {"n": 0}

    async def _always_fails():
        calls["n"] += 1
        raise httpx.ReadTimeout("still timing out")

    with pytest.raises(httpx.ReadTimeout):
        await with_retry(_always_fails, retries=3, label="t")

    assert calls["n"] == 3


async def test_non_retryable_exception_propagates_immediately(monkeypatch):
    """A non-network exception (e.g. a bug in the caller) must propagate on
    the first attempt — no retry, no swallowed traceback."""
    sleep_calls = {"n": 0}

    async def _fake_sleep(_delay):
        sleep_calls["n"] += 1

    monkeypatch.setattr("backend.arr_clients.retry.asyncio.sleep", _fake_sleep)

    calls = {"n": 0}

    async def _buggy():
        calls["n"] += 1
        raise ValueError("not a network error")

    with pytest.raises(ValueError):
        await with_retry(_buggy, retries=3, label="t")

    assert calls["n"] == 1
    assert sleep_calls["n"] == 0


async def test_with_retry_routes_through_named_circuit_breaker():
    """service= must route the whole retry sequence through that breaker —
    a failure there counts as one breaker failure, not one per attempt."""
    async def _always_fails():
        raise httpx.ConnectError("down")

    async def _fake_sleep(_d):
        return None

    breaker = cb_mod.get_breaker("radarr", failure_threshold=1)
    assert breaker._failure_count == 0

    import backend.arr_clients.retry as retry_mod
    orig_sleep = retry_mod.asyncio.sleep
    retry_mod.asyncio.sleep = _fake_sleep
    try:
        with pytest.raises(httpx.ConnectError):
            await with_retry(_always_fails, retries=1, service="radarr", label="t")
    finally:
        retry_mod.asyncio.sleep = orig_sleep

    # One exhausted retry sequence -> exactly one recorded breaker failure
    assert breaker._failure_count == 1


async def test_with_retry_without_service_does_not_touch_breaker_registry():
    """No service= given -> with_retry must not create/register any breaker."""
    async def _ok():
        return 42

    assert await with_retry(_ok, label="t") == 42
    assert cb_mod._registry == {}


async def test_open_breaker_short_circuits_without_calling_fn():
    """An already-OPEN breaker must reject the call before fn() ever runs —
    with_retry(service=...) must not hit the network when the circuit is open."""
    breaker = cb_mod.get_breaker("sonarr", failure_threshold=1)
    breaker._state = cb_mod.CircuitBreaker.OPEN
    breaker._last_failure_ts = __import__("time").monotonic()

    calls = {"n": 0}

    async def _fn():
        calls["n"] += 1
        return "should not run"

    with pytest.raises(cb_mod.CircuitOpenError):
        await with_retry(_fn, service="sonarr", label="t")

    assert calls["n"] == 0
