"""Coverage gaps in backend/_lock_backend.py not hit by
tests/test_advisory_lock_connection_affinity.py:

- the LockBackend Protocol's own default __aenter__/__aexit__ mixin body
  (inherited by any concrete subclass that does not override them)
- GET_LOCK() raising mid-acquire (not just returning 0)
- the synchronous release() escape hatch
- RELEASE_LOCK() itself raising, and the pool.release() in the outer
  `finally` raising — both must degrade to a warning log, never propagate
  out of __aexit__ and crash the caller's `async with` block
- the module-level HYGIE_LOCK_BACKEND=mariadb selection branch

Getting any of these wrong reproduces the 2026-09-18 incident class: a lock
left stuck because an error mid-release was never handled.
"""
import subprocess
import sys

import pytest

from backend._lock_backend import LockBackend, MariaDBAdvisoryLockBackend


# ─── LockBackend Protocol's own default mixin body ─────────────────────────

class _MinimalLock(LockBackend):
    """A concrete subclass implementing only the required primitives, relying
    on the Protocol's own __aenter__/__aexit__ implementation (lines 48-53)
    instead of redefining them, like AsyncioLockBackend/MariaDBAdvisoryLockBackend
    do."""

    def __init__(self):
        self._locked = False

    def locked(self) -> bool:
        return self._locked

    async def acquire(self) -> None:
        self._locked = True

    def release(self) -> None:
        self._locked = False


async def test_protocol_default_context_manager_acquires_and_releases():
    lock = _MinimalLock()
    assert not lock.locked()

    async with lock:
        assert lock.locked()

    assert not lock.locked()


# ─── MariaDBAdvisoryLockBackend.acquire() — GET_LOCK raises ────────────────

class _RaisingCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        raise RuntimeError("connection reset by peer")

    async def fetchone(self):
        raise AssertionError("must not be reached")


class _ConnWithRaisingCursor:
    def cursor(self):
        return _RaisingCursor()


class _PoolForRaisingConn:
    def __init__(self):
        self.released = []

    async def acquire(self):
        return _ConnWithRaisingCursor()

    async def release(self, conn):
        self.released.append(conn)


async def test_acquire_returns_connection_to_pool_when_get_lock_raises(monkeypatch):
    """If the GET_LOCK statement itself errors (not just returns 0), the
    borrowed connection must still go back to the pool, or every later
    acquire on this backend exhausts the pool for nothing."""
    import backend.db.engine as engine
    pool = _PoolForRaisingConn()
    monkeypatch.setattr(engine, "_pool", pool)

    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")

    with pytest.raises(RuntimeError, match="connection reset"):
        await lock.acquire()

    assert not lock.locked()
    assert len(pool.released) == 1


# ─── release() — the synchronous escape hatch ───────────────────────────────

async def test_sync_release_marks_the_lock_as_not_held():
    """release() (as opposed to __aexit__/_release_async) is a lighter-weight
    marker used when callers only care about `locked()` and not about
    returning the connection — it must flip the held flag."""
    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")
    lock._held = True

    lock.release()

    assert not lock.locked()


# ─── _release_async() — RELEASE_LOCK raises ────────────────────────────────

class _CursorThatFailsOnRelease:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        self._conn.statements.append(sql)
        if "RELEASE_LOCK" in sql:
            raise RuntimeError("connection already closed")


class _ConnThatFailsOnRelease:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _CursorThatFailsOnRelease(self)


class _TrackingPool:
    def __init__(self):
        self.released = []

    async def release(self, conn):
        self.released.append(conn)


async def test_release_async_logs_and_still_returns_connection_when_release_lock_raises(monkeypatch, caplog):
    """RELEASE_LOCK() erroring (e.g. connection already dropped) must not
    leave the lock's _held flag stuck True, and must not skip returning the
    connection to the pool — both are handled by separate try/except blocks."""
    import backend.db.engine as engine
    pool = _TrackingPool()
    monkeypatch.setattr(engine, "_pool", pool)

    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")
    conn = _ConnThatFailsOnRelease()
    lock._conn = conn
    lock._held = True

    with caplog.at_level("WARNING"):
        await lock._release_async()

    assert not lock.locked()
    assert lock._conn is None
    assert pool.released == [conn], "connection must still be returned to the pool"
    assert any("release error" in r.message for r in caplog.records)


# ─── _release_async() — returning the connection to the pool raises ───────

class _CursorOk:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, params=()):
        self._conn.statements.append(sql)


class _ConnOk:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _CursorOk(self)


class _PoolThatFailsOnRelease:
    async def release(self, conn):
        raise RuntimeError("pool already closed")


async def test_release_async_logs_when_returning_connection_to_pool_fails(monkeypatch, caplog):
    """The pool itself rejecting the returned connection (e.g. shutting down)
    must be caught in the outer finally — otherwise a pool teardown race
    would raise out of __aexit__ during an unrelated shutdown sequence."""
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "_pool", _PoolThatFailsOnRelease())

    lock = MariaDBAdvisoryLockBackend("hygie_test_lock")
    lock._conn = _ConnOk()
    lock._held = True

    with caplog.at_level("WARNING"):
        await lock._release_async()  # must not raise

    assert not lock.locked()
    assert any("returning connection failed" in r.message for r in caplog.records)


# ─── Module-level backend selection ─────────────────────────────────────────
#
# Covering lines 173-174 (the `if _BACKEND == "mariadb":` branch that builds
# the scan_lock/deletion_lock singletons) requires importing this module
# fresh with HYGIE_LOCK_BACKEND=mariadb set. importlib.reload() would do that
# in-process, but reload() re-executes the module *into its existing dict*:
# classes it (re)defines (MariaDBAdvisoryLockBackend, LockNotAvailable) get
# new identities while any code that already did
# `from ._lock_backend import X` — including other test files collected in
# the same run, e.g. test_advisory_lock_connection_affinity.py — keeps the
# old class object. Since `raise LockNotAvailable(...)` resolves the name via
# the function's __globals__ (the *same*, mutated module dict) at call time,
# the old class's own methods end up raising the *new* class, so
# `pytest.raises(LockNotAvailable)` in the other file stops matching. This
# is exactly the shared-module reload trap already known for
# backend.db.engine, generalized to any module reload while other test files
# hold `from`-imported references to its classes. A subprocess sidesteps it
# entirely: the import happens in a separate process, nothing here changes.

def test_module_selects_mariadb_backend_when_env_var_is_set():
    """HYGIE_LOCK_BACKEND=mariadb must select MariaDBAdvisoryLockBackend for
    both singleton locks at import time — the switch multi-worker deployments
    rely on."""
    script = (
        "import backend._lock_backend as m;"
        "assert isinstance(m.scan_lock, m.MariaDBAdvisoryLockBackend);"
        "assert isinstance(m.deletion_lock, m.MariaDBAdvisoryLockBackend);"
        "assert m.scan_lock._name == 'hygie_scan_lock';"
        "assert m.deletion_lock._name == 'hygie_deletion_lock';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=".", capture_output=True, text=True, timeout=30,
        env={**_subprocess_env(), "HYGIE_LOCK_BACKEND": "mariadb"},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_module_defaults_to_asyncio_backend_when_env_var_is_absent():
    script = (
        "import backend._lock_backend as m;"
        "assert isinstance(m.scan_lock, m.AsyncioLockBackend);"
        "assert isinstance(m.deletion_lock, m.AsyncioLockBackend);"
        "print('OK')"
    )
    env = _subprocess_env()
    env.pop("HYGIE_LOCK_BACKEND", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=".", capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def _subprocess_env():
    import os
    return {
        **os.environ,
        "DB_PATH": ":memory:",
        "HYGIE_ENCRYPTION_KEY": "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=",
    }
