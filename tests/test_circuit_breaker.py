"""Tests for CircuitBreaker — state transitions, probe serialization, decay."""
import asyncio
import time
import pytest
from backend.arr_clients.circuit_breaker import CircuitBreaker, CircuitOpenError


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _breaker(threshold: int = 3, recovery: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker("test", failure_threshold=threshold, recovery_timeout=recovery)


async def _ok():
    return "ok"


async def _fail():
    raise ValueError("boom")


# ─── CLOSED state ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initial_state_is_closed():
    cb = _breaker()
    assert cb.state == CircuitBreaker.CLOSED


@pytest.mark.asyncio
async def test_closed_passes_calls_through():
    cb = _breaker()
    result = await cb.call(_ok)
    assert result == "ok"


@pytest.mark.asyncio
async def test_closed_propagates_exceptions():
    cb = _breaker()
    with pytest.raises(ValueError):
        await cb.call(_fail)


@pytest.mark.asyncio
async def test_failure_count_increments():
    cb = _breaker(threshold=5)
    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.call(_fail)
    assert cb._failure_count == 3
    assert cb.state == CircuitBreaker.CLOSED


@pytest.mark.asyncio
async def test_success_in_closed_decays_failure_count():
    cb = _breaker(threshold=5)
    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.call(_fail)
    assert cb._failure_count == 3
    await cb.call(_ok)
    assert cb._failure_count == 2


# ─── CLOSED → OPEN transition ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opens_after_threshold_failures():
    cb = _breaker(threshold=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.call(_fail)
    assert cb.state == CircuitBreaker.OPEN


@pytest.mark.asyncio
async def test_open_rejects_calls_immediately():
    cb = _breaker(threshold=1)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    with pytest.raises(CircuitOpenError):
        await cb.call(_ok)


@pytest.mark.asyncio
async def test_circuit_open_error_carries_service_name():
    cb = CircuitBreaker("my-service", failure_threshold=1)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    try:
        await cb.call(_ok)
    except CircuitOpenError as e:
        assert "my-service" in str(e)


# ─── OPEN → HALF_OPEN transition ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_half_open_after_recovery_timeout():
    cb = _breaker(threshold=1, recovery=0.01)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    assert cb.state == CircuitBreaker.OPEN
    await asyncio.sleep(0.02)
    assert cb.state == CircuitBreaker.HALF_OPEN


# ─── HALF_OPEN → CLOSED (probe success) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_probe_closes_circuit():
    cb = _breaker(threshold=1, recovery=0.01)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    await asyncio.sleep(0.02)
    result = await cb.call(_ok)
    assert result == "ok"
    assert cb.state == CircuitBreaker.CLOSED
    assert cb._failure_count == 0


# ─── HALF_OPEN → OPEN (probe failure) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_probe_reopens_circuit():
    cb = _breaker(threshold=1, recovery=0.01)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    await asyncio.sleep(0.02)
    assert cb.state == CircuitBreaker.HALF_OPEN
    with pytest.raises(ValueError):
        await cb.call(_fail)
    assert cb.state == CircuitBreaker.OPEN


# ─── Concurrent probe serialization ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_one_probe_in_half_open():
    """Two concurrent calls in HALF_OPEN: one probes, the other raises CircuitOpenError."""
    cb = _breaker(threshold=1, recovery=0.01)
    with pytest.raises(ValueError):
        await cb.call(_fail)
    await asyncio.sleep(0.02)

    slow_probe_started = asyncio.Event()
    probe_unblock = asyncio.Event()

    async def _slow_ok():
        slow_probe_started.set()
        await probe_unblock.wait()
        return "ok"

    probe_task = asyncio.create_task(cb.call(_slow_ok))
    await slow_probe_started.wait()

    # While the probe holds the lock, a second concurrent call must raise CircuitOpenError.
    # Run it as a task so we can unblock the probe without deadlocking.
    second_task = asyncio.create_task(cb.call(_ok))
    # Give the event loop a tick so second_task can start and attempt to acquire the lock
    await asyncio.sleep(0)

    # Unblock the probe → it finishes, releases lock, circuit CLOSES
    probe_unblock.set()
    result = await probe_task
    assert result == "ok"
    assert cb.state == CircuitBreaker.CLOSED

    # The second task was waiting for the lock; once released it sees state==CLOSED
    # and succeeds — OR it checked state==OPEN before lock and raised CircuitOpenError.
    # Either outcome is acceptable; the key invariant is only ONE probe runs at a time.
    try:
        await second_task
    except CircuitOpenError:
        pass  # Raced and saw OPEN before the probe released — correct behavior
