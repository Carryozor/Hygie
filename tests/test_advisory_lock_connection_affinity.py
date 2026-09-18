"""Regression test — 2026-09-18: the MariaDB advisory lock was never released.

MySQL/MariaDB ties GET_LOCK() to the *connection* that issued it.
MariaDBAdvisoryLockBackend ran GET_LOCK inside `async with _pool.acquire()`,
so the connection went straight back to the pool still holding the lock, and
_release_async() then ran RELEASE_LOCK on a *different* pooled connection —
which returns 0 and releases nothing.

Effect observed in production: `IS_USED_LOCK('hygie_scan_lock')` pointed at an
idle (`Sleep`) pooled connection while no scan was running. Every later
GET_LOCK(name, 0) returned 0, so run_scan()/run_deletion() raised
LockNotAvailable and returned silently (debug-level log, no job_history row).
Four consecutive scheduled scans were skipped with no trace.

The lock must therefore keep its connection for its whole lifetime.
"""
from unittest.mock import AsyncMock

import pytest

from backend._lock_backend import LockNotAvailable, MariaDBAdvisoryLockBackend


class _FakeCursor:
    def __init__(self, conn, get_lock_result):
        self._conn = conn
        self._get_lock_result = get_lock_result

    async def execute(self, sql, params=()):
        self._conn.statements.append((sql, params))
        self._last = sql

    async def fetchone(self):
        return (self._get_lock_result,)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeConn:
    def __init__(self, name, get_lock_result=1):
        self.name = name
        self.statements = []
        self._get_lock_result = get_lock_result

    def cursor(self):
        return _FakeCursor(self, self._get_lock_result)


class _FakePool:
    """Hands out a different connection on every acquire, like a real pool."""

    def __init__(self, get_lock_result=1):
        self.conns = []
        self.released = []
        self._get_lock_result = get_lock_result

    async def acquire(self):
        conn = _FakeConn(f"conn-{len(self.conns)}", self._get_lock_result)
        self.conns.append(conn)
        return conn

    async def release(self, conn):
        self.released.append(conn)


@pytest.fixture
def fake_pool(monkeypatch):
    import backend.db.engine as engine

    pool = _FakePool()
    monkeypatch.setattr(engine, "_pool", pool)
    return pool


async def test_lock_releases_on_the_connection_that_acquired_it(fake_pool):
    """RELEASE_LOCK must run on the same connection as GET_LOCK, or it is a no-op."""
    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")

    async with lock:
        assert lock.locked()

    get_lock_conns = [c for c in fake_pool.conns
                      if any("GET_LOCK" in s for s, _ in c.statements)]
    release_conns = [c for c in fake_pool.conns
                     if any("RELEASE_LOCK" in s for s, _ in c.statements)]

    assert len(get_lock_conns) == 1
    assert get_lock_conns == release_conns, (
        "RELEASE_LOCK ran on a different connection — MariaDB would return 0 "
        "and the advisory lock would stay held forever"
    )


async def test_lock_holds_its_connection_until_released(fake_pool):
    """The connection must not go back to the pool while the lock is held."""
    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")

    async with lock:
        assert fake_pool.released == [], (
            "the connection holding the lock was returned to the pool — another "
            "caller can be handed it, and the lock outlives the block"
        )

    assert len(fake_pool.released) == 1


async def test_lock_returns_its_connection_when_unavailable(monkeypatch):
    """A refused GET_LOCK must not leak the connection it borrowed."""
    import backend.db.engine as engine

    pool = _FakePool(get_lock_result=0)
    monkeypatch.setattr(engine, "_pool", pool)
    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")

    with pytest.raises(LockNotAvailable):
        await lock.acquire()

    assert not lock.locked()
    assert len(pool.released) == 1, "the borrowed connection must go back to the pool"


async def test_lock_falls_back_when_pool_not_ready(monkeypatch):
    """Startup tasks run before init_db_pool() — keep the existing fallback."""
    import backend.db.engine as engine

    monkeypatch.setattr(engine, "_pool", None)
    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")

    await lock.acquire()
    assert lock.locked()
    await lock._release_async()
    assert not lock.locked()


# ─── Starvation detection ────────────────────────────────────────────────────

async def test_starved_job_is_reported(monkeypatch):
    """A job skipped on every cycle must become visible, not stay debug-level.

    Each individual skip looks ordinary in a multi-worker deployment; only the
    age of the last completed run distinguishes "the other worker ran it" from
    "nobody has run it for a day".
    """
    from datetime import timedelta

    import backend._job_health as job_health
    from backend.db.utils import now_utc

    stale = (now_utc() - timedelta(hours=24)).isoformat()
    logs, alerts = [], []

    monkeypatch.setattr(job_health, "get_last_job_run",
                        AsyncMock(return_value={"started_at": stale}))
    monkeypatch.setattr(job_health, "get_setting", AsyncMock(return_value="360"))
    monkeypatch.setattr(job_health, "add_log",
                        AsyncMock(side_effect=lambda lvl, msg, cat: logs.append((lvl, msg))))
    monkeypatch.setattr(job_health, "send_alert",
                        AsyncMock(side_effect=lambda *a, **k: alerts.append(a)))

    age = await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque")

    assert age is not None and age > 1400
    assert logs and logs[0][0] == "ERROR"
    assert alerts, "a starved job must raise an alert"


async def test_healthy_job_is_not_reported(monkeypatch):
    """One missed cycle is normal — do not cry wolf."""
    from datetime import timedelta

    import backend._job_health as job_health
    from backend.db.utils import now_utc

    recent = (now_utc() - timedelta(minutes=370)).isoformat()
    logs = []

    monkeypatch.setattr(job_health, "get_last_job_run",
                        AsyncMock(return_value={"started_at": recent}))
    monkeypatch.setattr(job_health, "get_setting", AsyncMock(return_value="360"))
    monkeypatch.setattr(job_health, "add_log",
                        AsyncMock(side_effect=lambda lvl, msg, cat: logs.append((lvl, msg))))
    monkeypatch.setattr(job_health, "send_alert", AsyncMock())

    assert await job_health.warn_if_job_starved(
        "scan", "scan_interval_minutes", 360, "Scan de la médiathèque") is None
    assert logs == []
