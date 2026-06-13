# backend/_lock_backend.py
"""LockBackend abstraction for scan/deletion job serialization.

Single-worker (default): AsyncioLockBackend — asyncio.Lock in-process.
Multi-worker:            MariaDBAdvisoryLockBackend — GET_LOCK() advisory locks.
                         Requires HYGIE_LOCK_BACKEND=mariadb + DATABASE_URL.

Callers import `scan_lock` and `deletion_lock` and use them as async context
managers. In multi-worker mode, `__aenter__` raises `LockNotAvailable` if
another worker already holds the lock — callers must catch this to skip the
job for the current cycle (the worker that holds the lock will run it).

Usage:
    from ._lock_backend import scan_lock, deletion_lock, LockNotAvailable
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class LockNotAvailable(Exception):
    """Raised by MariaDBAdvisoryLockBackend when GET_LOCK() returns 0.

    Callers (run_scan, run_deletion) should catch this and return silently —
    another worker already holds the lock and is running the job.
    """


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
    containers sharing the same MariaDB).

    GET_LOCK(name, 0) is non-blocking: returns 1 if acquired, 0 if another
    connection holds it. When the lock is unavailable, acquire() raises
    LockNotAvailable — callers must catch this to skip the job for this
    cycle (the worker that holds the lock will run it instead).

    Falls back to "always acquired" if the pool is not yet initialized,
    so startup tasks (reset_stale_deleting) work before APScheduler fires.
    """

    def __init__(self, name: str) -> None:
        self._name = name
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
                    await cur.execute("SELECT GET_LOCK(%s, 0)", (self._name,))
                    row = await cur.fetchone()
                    if not row or row[0] != 1:
                        raise LockNotAvailable(self._name)
            self._held = True
        except LockNotAvailable:
            raise
        except (RuntimeError, ImportError):
            logger.debug("MariaDBAdvisoryLockBackend: pool unavailable, proceeding without lock")
            self._held = True

    def release(self) -> None:
        self._held = False

    async def _release_async(self) -> None:
        if not self._held:
            return
        self._held = False
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


# ─── Singleton lock instances ─────────────────────────────────────────────────
# HYGIE_LOCK_BACKEND=mariadb activates cross-process advisory locks.
# Required when WORKERS > 1. Default is asyncio (single-worker deployments).
_BACKEND = os.environ.get("HYGIE_LOCK_BACKEND", "asyncio").lower()

if _BACKEND == "mariadb":
    scan_lock: LockBackend = MariaDBAdvisoryLockBackend("hygie_scan_lock")
    deletion_lock: LockBackend = MariaDBAdvisoryLockBackend("hygie_deletion_lock")
else:
    scan_lock = AsyncioLockBackend("scan")
    deletion_lock = AsyncioLockBackend("deletion")
