"""get_db() (MariaDB branch) must roll back on ANY exit, including
asyncio.CancelledError — not just Exception.

CancelledError is a BaseException, not an Exception (since Python 3.8).
run_deletion() wraps its work in asyncio.timeout(3600) and run_scan() in
asyncio.wait_for(..., 7200); either firing while a task is inside
`async with get_db(): ...` delivers CancelledError through that block.
`except Exception: rollback(); raise` let it pass straight through without
rolling back, so the pooled connection went back to the pool still holding
an open REPEATABLE-READ transaction — the v4.3.4 stale-snapshot bug
(test_db_read_snapshot_freshness.py) reachable through cancellation, with no
write involved at all.

The fix must also not blindly trust rollback() to succeed: a rollback
attempted while unwinding a cancellation can itself be interrupted or fail.
When it does, the connection must be closed rather than returned to the pool
looking clean.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest


class _FakeCursor:
    rowcount = 0
    lastrowid = 0

    async def execute(self, *_):
        return 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeRaw:
    def __init__(self):
        self.calls: list[str] = []
        self.autocommit = AsyncMock()
        self.commit = AsyncMock(side_effect=lambda: self.calls.append("commit"))
        self.rollback = AsyncMock(side_effect=lambda: self.calls.append("rollback"))
        self.close = Mock(side_effect=lambda: self.calls.append("close"))

    def cursor(self, *_):
        return _FakeCursor()


class _FakePool:
    def __init__(self, raw):
        self._raw = raw
        self.released = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._raw

            async def __aexit__(self, *_):
                pool.released += 1
                return False

        return _Ctx()


@pytest.fixture
def fake_mariadb(monkeypatch):
    import backend.db.engine as eng

    raw = _FakeRaw()
    pool = _FakePool(raw)
    monkeypatch.setattr(eng, "DIALECT", "mariadb")
    monkeypatch.setattr(eng, "_pool", pool)
    return eng, raw, pool


@pytest.mark.asyncio
async def test_cancellation_inside_get_db_still_rolls_back(fake_mariadb):
    """A task cancelled while inside `async with get_db(): ...` must still
    end the transaction before the connection is released to the pool."""
    eng, raw, pool = fake_mariadb
    entered = asyncio.Event()

    async def _worker():
        async with eng.get_db():
            entered.set()
            await asyncio.sleep(10)

    task = asyncio.ensure_future(_worker())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    raw.rollback.assert_awaited_once()
    raw.commit.assert_not_awaited()
    assert pool.released == 1
    raw.close.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_propagates_after_rollback():
    """The CancelledError itself must still propagate — get_db() rolling
    back must not accidentally swallow the cancellation."""
    import backend.db.engine as eng

    raw = _FakeRaw()
    pool = _FakePool(raw)
    import backend.db.engine as eng_mod

    orig_dialect, orig_pool = eng_mod.DIALECT, eng_mod._pool
    eng_mod.DIALECT = "mariadb"
    eng_mod._pool = pool
    try:
        with pytest.raises(asyncio.CancelledError):
            async with eng.get_db():
                raise asyncio.CancelledError()
    finally:
        eng_mod.DIALECT, eng_mod._pool = orig_dialect, orig_pool

    raw.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_failure_during_error_closes_connection_instead_of_pooling_dirty(fake_mariadb):
    """If rollback() itself fails (e.g. re-cancelled, or connection already
    broken), get_db() must close the connection rather than hand a
    connection it can no longer vouch for back to the pool."""
    eng, raw, pool = fake_mariadb
    raw.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))

    with pytest.raises(ValueError):
        async with eng.get_db():
            raise ValueError("boom")

    raw.rollback.assert_awaited_once()
    raw.close.assert_called_once()
    assert pool.released == 1  # the pool's own __aexit__ still runs — it just gets a closed conn


@pytest.mark.asyncio
async def test_rollback_failure_still_propagates_the_original_error(fake_mariadb):
    """A rollback failure must not mask the real error that triggered it."""
    eng, raw, _ = fake_mariadb
    raw.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))

    with pytest.raises(ValueError, match="boom"):
        async with eng.get_db():
            raise ValueError("boom")
