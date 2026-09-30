"""Storage service — Radarr/Sonarr disk + library aggregation and queue stats.

Extracted from routers/storage.py: no FastAPI/Request dependency, no cache
(the router owns the stale-while-revalidate cache and the HTTP concerns).
"""
import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from ..arr_clients.shared import _arr_auth
from ..db.engine import get_db
from ..db.repositories import get_radarr_ids as _default_get_radarr_ids
from ..db.repositories import get_status_counts as _default_get_status_counts
from ..db.settings_store import get_setting
from ..db.utils import STATUS_DELETED, STATUS_ERROR, STATUS_PENDING, TIMEOUT_MEDIUM

logger = logging.getLogger(__name__)


def _empty_queue() -> dict:
    return {
        "pending": 0,
        "deleted": 0,
        "excluded": 0,
        "error": 0,
        "reclaimable_size": 0,
        "reclaimable_count": 0,
    }


def _disk_entry(disk: dict, source: str) -> dict:
    return {
        "path": disk.get("path", "?"),
        "label": disk.get("label", ""),
        "source": source,
        "total": disk.get("totalSpace", 0),
        "free": disk.get("freeSpace", 0),
        "accessible": disk.get("accessible", True),
    }


def summarize_movies(all_movies: list) -> dict:
    """Aggregate a Radarr /movie payload."""
    total_in_lib = len(all_movies)
    mon = sum(1 for m in all_movies if m.get("monitored"))
    return {
        "total_in_library": total_in_lib,
        "count": sum(1 for m in all_movies if m.get("hasFile")),
        "monitored": mon,
        "unmonitored": total_in_lib - mon,
        "size": sum(m.get("sizeOnDisk", 0) or 0 for m in all_movies),
    }


def summarize_series(all_series: list) -> dict:
    """Aggregate a Sonarr /series payload."""
    count = len(all_series)
    mon = sum(1 for s in all_series if s.get("monitored"))

    def _stat(key: str) -> int:
        return sum(s.get("statistics", {}).get(key, 0) or 0 for s in all_series)

    return {
        "count": count,
        "monitored": mon,
        "unmonitored": count - mon,
        "episodes": _stat("episodeFileCount"),
        "episodes_aired": _stat("episodeCount"),
        "episodes_total": _stat("totalEpisodeCount"),
        "size": _stat("sizeOnDisk"),
    }


def merge_disks(radarr_disks: list, sonarr_disks: list) -> list:
    """Radarr disks first; Sonarr disks only when their path is not already listed."""
    disks = [_disk_entry(d, "Radarr") for d in radarr_disks]
    existing_paths = {d["path"] for d in disks}
    for disk in sonarr_disks:
        if disk.get("path", "?") not in existing_paths:
            disks.append(_disk_entry(disk, "Sonarr"))
    return disks


async def _load_arr_config() -> tuple[tuple[str, str], tuple[str, str]]:
    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""
    return (radarr_url, radarr_key), (sonarr_url, sonarr_key)


async def _fetch_arr_payloads(radarr: tuple[str, str], sonarr: tuple[str, str]) -> tuple:
    """GET diskspace + library from each configured *arr, in parallel.

    Each slot is the httpx response on HTTP 200, else None (unconfigured,
    non-200 or any error).
    """
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

        def _req(cfg: tuple[str, str], path: str):
            url, key = cfg
            return _get(f"{url}{path}", _arr_auth(key)) if url and key else _noop()

        return await asyncio.gather(
            _req(radarr, "/api/v3/diskspace"),
            _req(radarr, "/api/v3/movie"),
            _req(sonarr, "/api/v3/diskspace"),
            _req(sonarr, "/api/v3/series"),
        )


async def _radarr_reclaimable(
    movies_by_id: dict, pending_total: int, get_radarr_ids: Callable[..., Awaitable]
) -> tuple[int, int]:
    """(size, count) of pending movies still present in Radarr."""
    pending_ids = await get_radarr_ids(status=STATUS_PENDING)
    matched = [m for rid in pending_ids if (m := movies_by_id.get(rid))]
    size = sum(m.get("sizeOnDisk", 0) or 0 for m in matched)
    # No pending id at all: fall back to the pending total (original behaviour).
    count = len(matched) if pending_ids else pending_total
    return size, count


async def build_queue_stats(
    radarr_movies_by_id: dict,
    *,
    get_status_counts: Callable[..., Awaitable] = _default_get_status_counts,
    get_radarr_ids: Callable[..., Awaitable] = _default_get_radarr_ids,
) -> dict:
    """Queue counters + reclaimable size. Never raises: a DB failure is logged
    and the counters gathered so far are returned (as before the extraction).

    `get_status_counts` / `get_radarr_ids` are injectable so the router can
    hand over its own module-level names (existing tests patch them there).
    """
    queue = _empty_queue()
    try:
        status_counts = await get_status_counts()
        for s in (STATUS_PENDING, STATUS_DELETED, STATUS_ERROR):
            queue[s] = status_counts.get(s, 0)

        async with get_db() as db:
            excl_row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM ignored_media")
            queue["excluded"] = excl_row["cnt"] if excl_row else 0

        if radarr_movies_by_id:
            size, count = await _radarr_reclaimable(
                radarr_movies_by_id, queue[STATUS_PENDING], get_radarr_ids
            )
            queue["reclaimable_size"] = size
            queue["reclaimable_count"] = count
    except Exception as e:
        logger.warning(f"Queue stats: {e}")
    return queue


async def collect_storage_data(
    *,
    get_status_counts: Callable[..., Awaitable] = _default_get_status_counts,
    get_radarr_ids: Callable[..., Awaitable] = _default_get_radarr_ids,
) -> dict:
    """Fetch fresh storage data from Radarr/Sonarr + the queue tables."""
    radarr, sonarr = await _load_arr_config()
    rd, rm, sd, rs = await _fetch_arr_payloads(radarr, sonarr)

    disks = merge_disks(rd.json() if rd else [], sd.json() if sd else [])
    movies: dict = {}
    series: dict = {}
    movies_by_id: dict = {}
    if rm:
        all_movies = rm.json()
        movies_by_id = {m["id"]: m for m in all_movies}
        movies = summarize_movies(all_movies)
    if rs:
        series = summarize_series(rs.json())

    queue = await build_queue_stats(
        movies_by_id, get_status_counts=get_status_counts, get_radarr_ids=get_radarr_ids
    )
    return {
        "disks": disks,
        "movies": movies,
        "series": series,
        "total_media_size": movies.get("size", 0) + series.get("size", 0),
        "queue": queue,
    }
