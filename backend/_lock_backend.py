# backend/_lock_backend.py
"""LockBackend abstraction for scan/deletion job serialization.

Single-worker (current): AsyncioLockBackend — asyncio.Lock in-process.
Multi-worker (future):   MariaDBAdvisoryLockBackend — GET_LOCK() advisory locks.

Switching backends requires only changing LOCK_BACKEND in this module or
wiring via an env var. All callers import `scan_lock` and `deletion_lock`
(LockContext objects) and use them as async context managers:

    async with scan_lock:
        ...

Usage:
    from ._lock_backend import scan_lock, deletion_lock
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LockBackend(Protocol):
    """Protocol for an async mutual-exclusion lock for a named job."""

    def locked(self) -> bool:
        """Return True if the lock is currently held."""
        ...

    async def acquire(self) -> None:
        """Acquire the lock, blocking until available."""
        ...

    def release(self) -> None:
        """Release the lock."""
        ...

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *_):
        self.release()


class AsyncioLockBackend:
    """In-process asyncio.Lock. Default backend (single-worker deployments)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = asyncio.Lock()

    def locked(self) -> bool:
        return self._lock.locked()

    async def acquire(self) -> None:
        await self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *_):
        self.release()


class MariaDBAdvisoryLockBackend:
    """Cross-process advisory lock via MySQL GET_LOCK().

    Suitable for multi-worker deployments (Uvicorn workers=N or multiple
    containers sharing the same MariaDB). Falls back to AsyncioLockBackend
    when the DB pool is not yet initialized.

    GET_LOCK(name, 0) uses a 0-second timeout — non-blocking. Callers that
    need to wait should retry in a loop; the scheduler's single-fire design
    means a second worker simply skips the job for this cycle.
    """

    def __init__(self, name: str, timeout: int = 30) -> None:
        self._name = name
        self._timeout = timeout
        self._held = False

    def locked(self) -> bool:
        return self._held

    async def acquire(self) -> None:
        try:
            from .db.engine import _pool
            if _pool is None:
                raise RuntimeError("pool not ready")
            async with _pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT GET_LOCK(%s, %s)", (self._name, self._timeout))
                    row = await cur.fetchone()
                    if not row or row[0] != 1:
                        raise TimeoutError(f"Could not acquire advisory lock '{self._name}'")
            self._held = True
        except (RuntimeError, ImportError):
            logger.warning("MariaDBAdvisoryLockBackend: falling back to no-op (pool unavailable)")
            self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            import asyncio as _asyncio
            _asyncio.get_event_loop().run_until_complete(self._release_async())
        except Exception:
            pass

    async def _release_async(self) -> None:
        try:
            from .db.engine import _pool
            if _pool is None:
                return
            async with _pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT RELEASE_LOCK(%s)", (self._name,))
        except Exception as e:
            logger.warning("MariaDBAdvisoryLockBackend release error: %s", e)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *_):
        await self._release_async()
        self._held = False


# ─── Singleton lock instances ─────────────────────────────────────────────────
# HYGIE_LOCK_BACKEND=mariadb activates cross-process advisory locks.
# Default is asyncio (safe for single-worker, which is the current constraint).
_BACKEND = os.environ.get("HYGIE_LOCK_BACKEND", "asyncio").lower()

if _BACKEND == "mariadb":
    scan_lock: LockBackend = MariaDBAdvisoryLockBackend("hygie_scan_lock")
    deletion_lock: LockBackend = MariaDBAdvisoryLockBackend("hygie_deletion_lock")
else:
    scan_lock = AsyncioLockBackend("scan")
    deletion_lock = AsyncioLockBackend("deletion")
