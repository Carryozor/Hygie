"""Storage — disk metrics from Radarr/Sonarr, with stale-while-revalidate cache."""
import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..db.utils import TIMEOUT_MEDIUM
from ..db.engine import get_db
from ..db.repositories import get_status_counts, get_radarr_ids
from ..db.settings_store import get_setting
from ..arr_clients.shared import _arr_auth
from ..db.utils import STATUS_PENDING, STATUS_DELETED, STATUS_ERROR

router = APIRouter(prefix="/api/storage", tags=["storage"])
logger = logging.getLogger(__name__)

_storage_cache: dict = {"data": None, "ts": 0.0}
_storage_refresh_task: asyncio.Task | None = None
_storage_task_lock: asyncio.Lock = asyncio.Lock()
_STORAGE_TTL = 300.0  # 5 minutes — stale-while-revalidate makes this safe to extend


def invalidate_storage_cache() -> None:
    """Call after deletions or scans to force fresh data on next request."""
    _storage_cache.update({"data": None, "ts": 0.0})


async def _fetch_storage_data() -> dict:
    """Fetch fresh storage data from Radarr/Sonarr + SQLite. Updates cache in place."""

    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""

    disks: list = []
    movies: dict = {}
    series: dict = {}
    total_media_size: int = 0
    radarr_movies_by_id: dict = {}

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:

        async def _get(url: str, headers: dict):
            """Safe GET — returns None on any error."""
            try:
                r = await c.get(url, headers=headers)
                return r if r.status_code == 200 else None
            except Exception:
                return None

        async def _noop() -> None:
            return None

        # ── All 4 requests in parallel ────────────────────────────────────
        r_disk_task   = _get(f"{radarr_url}/api/v3/diskspace", _arr_auth(radarr_key)) if radarr_url and radarr_key else _noop()
        r_movie_task  = _get(f"{radarr_url}/api/v3/movie",     _arr_auth(radarr_key)) if radarr_url and radarr_key else _noop()
        s_disk_task   = _get(f"{sonarr_url}/api/v3/diskspace", _arr_auth(sonarr_key)) if sonarr_url and sonarr_key else _noop()
        s_series_task = _get(f"{sonarr_url}/api/v3/series",    _arr_auth(sonarr_key)) if sonarr_url and sonarr_key else _noop()

        rd, rm, sd, rs = await asyncio.gather(r_disk_task, r_movie_task, s_disk_task, s_series_task)

        # ── Process Radarr ────────────────────────────────────────────────
        if rd:
            for disk in rd.json():
                disks.append({
                    "path": disk.get("path", "?"),
                    "label": disk.get("label", ""),
                    "source": "Radarr",
                    "total": disk.get("totalSpace", 0),
                    "free": disk.get("freeSpace", 0),
                    "accessible": disk.get("accessible", True),
                })
        if rm:
            all_movies = rm.json()
            radarr_movies_by_id = {m["id"]: m for m in all_movies}
            total_in_lib = len(all_movies)
            with_file = sum(1 for m in all_movies if m.get("hasFile"))
            mon = sum(1 for m in all_movies if m.get("monitored"))
            size = sum(m.get("sizeOnDisk", 0) or 0 for m in all_movies)
            movies = {
                "total_in_library": total_in_lib,
                "count": with_file,
                "monitored": mon,
                "unmonitored": total_in_lib - mon,
                "size": size,
            }
            total_media_size += size

        # ── Process Sonarr ────────────────────────────────────────────────
        if sd:
            existing_paths = {d["path"] for d in disks}
            for disk in sd.json():
                if disk.get("path", "?") not in existing_paths:
                    disks.append({
                        "path": disk.get("path", "?"),
                        "label": disk.get("label", ""),
                        "source": "Sonarr",
                        "total": disk.get("totalSpace", 0),
                        "free": disk.get("freeSpace", 0),
                        "accessible": disk.get("accessible", True),
                    })
        if rs:
            all_series = rs.json()
            count = len(all_series)
            mon = sum(1 for s in all_series if s.get("monitored"))
            eps_on_disk = sum(s.get("statistics", {}).get("episodeFileCount", 0) or 0 for s in all_series)
            eps_total = sum(s.get("statistics", {}).get("totalEpisodeCount", 0) or 0 for s in all_series)
            eps_aired = sum(s.get("statistics", {}).get("episodeCount", 0) or 0 for s in all_series)
            size = sum(s.get("statistics", {}).get("sizeOnDisk", 0) or 0 for s in all_series)
            series = {
                "count": count,
                "monitored": mon,
                "unmonitored": count - mon,
                "episodes": eps_on_disk,
                "episodes_aired": eps_aired,
                "episodes_total": eps_total,
                "size": size,
            }
            total_media_size += size

    # ── Queue stats ────────────────────────────────────────────────────────
    queue: dict = {
        "pending": 0,
        "deleted": 0,
        "excluded": 0,
        "error": 0,
        "reclaimable_size": 0,
        "reclaimable_count": 0,
    }
    try:
        status_counts = await get_status_counts()
        for s in (STATUS_PENDING, STATUS_DELETED, STATUS_ERROR):
            queue[s] = status_counts.get(s, 0)

        async with get_db() as db:
            # Excluded (ignored)
            excl_row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM ignored_media")
            queue["excluded"] = excl_row["cnt"] if excl_row else 0

        # Reclaimable: sum sizeOnDisk for pending movies still in Radarr
        if radarr_movies_by_id:
            pending_radarr_ids = await get_radarr_ids(status=STATUS_PENDING)
            reclaimable = 0
            count_reclaimable = 0
            for rid in pending_radarr_ids:
                movie = radarr_movies_by_id.get(rid)
                if movie:
                    reclaimable += movie.get("sizeOnDisk", 0) or 0
                    count_reclaimable += 1
            queue["reclaimable_size"] = reclaimable
            queue["reclaimable_count"] = count_reclaimable
            if not pending_radarr_ids:
                queue["reclaimable_count"] = queue[STATUS_PENDING]

    except Exception as e:
        logger.warning(f"Queue stats: {e}")

    result = {
        "disks": disks,
        "movies": movies,
        "series": series,
        "total_media_size": total_media_size,
        "queue": queue,
    }
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
