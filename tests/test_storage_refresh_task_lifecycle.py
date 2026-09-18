"""Regression test — 2026-09-18: the CI test step hung for 10 minutes after
pytest had already printed "656 passed".

`get_storage()` fires the stale-while-revalidate refresh with
`asyncio.create_task(_fetch_storage_data())` and never keeps ownership of it.
When the event loop goes away while that task is suspended inside
`async with get_db()`, the context manager's `__aexit__` never runs — and for
SQLite that context manager owns an `aiosqlite.Connection`, which *is* a
`threading.Thread` created without `daemon=True`. The thread stays blocked on
its queue forever, and `threading._shutdown()` joins it at interpreter exit.

Observed: a leak probe over the suite found 2361 aiosqlite connections created
and 2 still alive at the end, both traced to
`backend/routers/storage.py` → `backend/db/engine.py:get_db`. Non-deterministic
because it depends on where the abandoned task was suspended when the loop
closed — which is exactly how it behaved in CI.

The task must therefore be owned: cancellable, and cancelled on shutdown.
"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean_storage_state():
    import backend.routers.storage as storage

    storage._storage_refresh_task = None
    storage._storage_cache.update({"data": None, "ts": 0.0})
    yield
    storage._storage_refresh_task = None
    storage._storage_cache.update({"data": None, "ts": 0.0})


async def test_cancel_storage_refresh_awaits_the_background_task():
    """cancel_storage_refresh() must leave no pending task behind."""
    import backend.routers.storage as storage

    started = asyncio.Event()

    async def _never_finishes():
        started.set()
        await asyncio.sleep(3600)

    storage._storage_refresh_task = asyncio.create_task(_never_finishes())
    await started.wait()

    await storage.cancel_storage_refresh()

    assert storage._storage_refresh_task is None
    assert not [t for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()], \
        "an abandoned refresh task keeps its aiosqlite connection thread alive"


async def test_cancel_storage_refresh_is_safe_when_nothing_is_running():
    """Shutdown must not care whether a refresh was ever started."""
    import backend.routers.storage as storage

    storage._storage_refresh_task = None
    await storage.cancel_storage_refresh()  # must not raise

    async def _done():
        return None

    finished = asyncio.create_task(_done())
    await finished
    storage._storage_refresh_task = finished
    await storage.cancel_storage_refresh()
    assert storage._storage_refresh_task is None


async def test_shutdown_cancels_the_storage_refresh_task():
    """The app shutdown path must own the task, not leave it to the GC."""
    import backend.main as main
    import backend.routers.storage as storage

    async def _never_finishes():
        await asyncio.sleep(3600)

    storage._storage_refresh_task = asyncio.create_task(_never_finishes())

    from unittest.mock import AsyncMock, patch

    with patch.object(main, "close_db_pool", new=AsyncMock(), create=True), \
         patch.object(main, "scheduler") as sched:
        sched.shutdown.return_value = None
        await main._shutdown_lifespan(None)

    assert storage._storage_refresh_task is None
