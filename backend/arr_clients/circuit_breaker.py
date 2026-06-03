# backend/arr_clients/circuit_breaker.py
"""Simple three-state circuit breaker for external service calls.

States:
  CLOSED    — normal operation, all calls pass through
  OPEN      — failure threshold exceeded, calls rejected immediately (fast-fail)
  HALF_OPEN — recovery timeout elapsed, one probe call allowed;
               success → CLOSED, failure → OPEN (reset timer)

Usage:
    breaker = get_breaker("emby")
    try:
        result = await breaker.call(lambda: client.get_items(...))
    except CircuitOpenError:
        # Service known-bad — skip this cycle
        ...
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is OPEN and the call is rejected."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Circuit breaker OPEN for: {service}")


class CircuitBreaker:
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self._state            = self.CLOSED
        self._failure_count    = 0
        self._last_failure_ts  = 0.0
        self._probe_lock       = asyncio.Lock()

    @property
    def state(self) -> str:
        if self._state == self.OPEN:
            if time.monotonic() - self._last_failure_ts >= self.recovery_timeout:
                return self.HALF_OPEN
        return self._state

    def _on_success(self) -> None:
        self._state         = self.CLOSED
        self._failure_count = 0

    def _on_failure(self) -> None:
        self._failure_count  += 1
        self._last_failure_ts = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self._state != self.OPEN:
                logger.warning(
                    "Circuit breaker OPEN for '%s' after %d consecutive failures",
                    self.name, self._failure_count,
                )
            self._state = self.OPEN

    async def call(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        """Execute coro_factory() through the breaker.

        Raises CircuitOpenError immediately when the circuit is OPEN.
        All other exceptions from coro_factory propagate normally (after recording failure).
        """
        current = self.state

        if current == self.OPEN:
            raise CircuitOpenError(self.name)

        if current == self.HALF_OPEN:
            # Only one probe at a time — serialize under lock
            async with self._probe_lock:
                if self.state == self.OPEN:
                    raise CircuitOpenError(self.name)
                try:
                    result = await coro_factory()
                    self._on_success()
                    logger.info("Circuit breaker CLOSED for '%s' (probe succeeded)", self.name)
                    return result
                except Exception:
                    self._on_failure()
                    raise

        # CLOSED — normal path
        try:
            result = await coro_factory()
            # Decay failure count on success so transient errors don't accumulate
            if self._failure_count > 0:
                self._failure_count = max(0, self._failure_count - 1)
            return result
        except Exception:
            self._on_failure()
            raise


# ─── Global registry ──────────────────────────────────────────────────────────
_registry: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """Return the circuit breaker for a named service, creating it if needed."""
    if name not in _registry:
        _registry[name] = CircuitBreaker(name, failure_threshold, recovery_timeout)
    return _registry[name]


def all_breaker_states() -> dict[str, dict]:
    """Return status snapshot of all registered circuit breakers (for health endpoint)."""
    return {
        name: {"state": cb.state, "failure_count": cb._failure_count}
        for name, cb in _registry.items()
    }
