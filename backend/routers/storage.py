"""Storage — disk metrics from Radarr/Sonarr, with stale-while-revalidate cache."""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..db.repositories import get_status_counts, get_radarr_ids
from ..services.storage_service import collect_storage_data

router = APIRouter(prefix="/api/storage", tags=["storage"])
logger = logging.getLogger(__name__)

_storage_cache: dict = {"data": None, "ts": 0.0}
_storage_refresh_task: asyncio.Task | None = None
_storage_task_lock: asyncio.Lock = asyncio.Lock()
_STORAGE_TTL = 300.0  # 5 minutes — stale-while-revalidate makes this safe to extend


def invalidate_storage_cache() -> None:
    """Call after deletions or scans to force fresh data on next request."""
    _storage_cache.update({"data": None, "ts": 0.0})


async def cancel_storage_refresh() -> None:
    """Cancel and await the background refresh task, if one is running.

    The stale-while-revalidate refresh is spawned with create_task() and
    returns immediately, so nothing else owns it. If the event loop goes away
    while it is suspended inside `async with get_db()`, that context manager
    never runs its __aexit__ — and under SQLite it owns an aiosqlite.Connection,
    which is a non-daemon thread. The thread then blocks interpreter exit
    forever (observed 2026-09-18: a CI test step hung for 10 minutes after
    pytest had already reported success). Whoever tears the loop down must call
    this first.
    """
    global _storage_refresh_task
    task, _storage_refresh_task = _storage_refresh_task, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def _fetch_storage_data() -> dict:
    """Fetch fresh storage data via the service and update the cache in place.

    get_status_counts / get_radarr_ids are passed explicitly from this module's
    globals so `patch("backend.routers.storage.<name>")` stays effective.
    """
    result = await collect_storage_data(
        get_status_counts=get_status_counts, get_radarr_ids=get_radarr_ids
    )
    _storage_cache.update({"data": result, "ts": time.time()})
    return result


@router.get("")
async def get_storage(user: str = Depends(require_auth)):
    """
    Stale-while-revalidate: if cache exists (even expired) return it immediately
    and trigger a background refresh. Only blocks on the very first cold request.
    Cache TTL is 5 minutes; invalidated by invalidate_storage_cache() after mutations.
    """
    global _storage_refresh_task
    now = time.time()
    fresh = _storage_cache["data"] is not None and now - _storage_cache["ts"] < _STORAGE_TTL
    if fresh:
        return _storage_cache["data"]

    if _storage_cache["data"] is not None:
        # Stale data available — return it instantly and refresh in background
        async with _storage_task_lock:
            if _storage_refresh_task is None or _storage_refresh_task.done():
                _storage_refresh_task = asyncio.create_task(_fetch_storage_data())
                _storage_refresh_task.add_done_callback(
                    lambda t: logger.warning("storage cache refresh failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None else None
                )
        return _storage_cache["data"]

    # Cold start — no data at all, must wait
    return await _fetch_storage_data()
