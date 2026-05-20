"""Storage — disk metrics from Radarr/Sonarr, matching frontend data shape exactly."""
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..database import DB_PATH, STATUS_PENDING, STATUS_DELETED, STATUS_ERROR, get_setting, TIMEOUT_MEDIUM

router = APIRouter(prefix="/api/storage", tags=["storage"])
logger = logging.getLogger(__name__)


@router.get("")
async def get_storage(user: str = Depends(require_auth)):
    """
    Shape expected by frontend:
    {
      disks: [{path, label, source, total, free, accessible}],
      movies: {total_in_library, count, monitored, unmonitored, size},
      series: {count, monitored, unmonitored, episodes, size},
      total_media_size: int,
      queue: {pending, deleted, excluded, error, reclaimable_size, reclaimable_count},
    }
    """
    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""

    disks: list = []
    movies: dict = {}
    series: dict = {}
    total_media_size: int = 0
    radarr_movies_by_id: dict = {}  # id -> movie (for reclaimable calc)

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
        # ── Radarr ────────────────────────────────────────────────────────
        if radarr_url and radarr_key:
            try:
                rd = await c.get(f"{radarr_url}/api/v3/diskspace",
                                 params={"apikey": radarr_key})
                if rd.status_code == 200:
                    for disk in rd.json():
                        disks.append({
                            "path": disk.get("path", "?"),
                            "label": disk.get("label", ""),
                            "source": "Radarr",
                            "total": disk.get("totalSpace", 0),
                            "free": disk.get("freeSpace", 0),
                            "accessible": disk.get("accessible", True),
                        })

                rm = await c.get(f"{radarr_url}/api/v3/movie",
                                 params={"apikey": radarr_key})
                if rm.status_code == 200:
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
            except Exception as e:
                logger.warning(f"Radarr storage: {e}")

        # ── Sonarr ────────────────────────────────────────────────────────
        if sonarr_url and sonarr_key:
            try:
                sd = await c.get(f"{sonarr_url}/api/v3/diskspace",
                                 params={"apikey": sonarr_key})
                if sd.status_code == 200:
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

                rs = await c.get(f"{sonarr_url}/api/v3/series",
                                 params={"apikey": sonarr_key})
                if rs.status_code == 200:
                    all_series = rs.json()
                    count = len(all_series)
                    mon = sum(1 for s in all_series if s.get("monitored"))
                    eps = sum(
                        s.get("statistics", {}).get("episodeFileCount", 0) or 0
                        for s in all_series
                    )
                    size = sum(
                        s.get("statistics", {}).get("sizeOnDisk", 0) or 0
                        for s in all_series
                    )
                    series = {
                        "count": count,
                        "monitored": mon,
                        "unmonitored": count - mon,
                        "episodes": eps,
                        "size": size,
                    }
                    total_media_size += size
            except Exception as e:
                logger.warning(f"Sonarr storage: {e}")

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
        async with aiosqlite.connect(DB_PATH) as db:
            # Status counts
            async with db.execute(
                "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
            ) as cur:
                for status, cnt in await cur.fetchall():
                    if status in queue:
                        queue[status] = cnt

            # Excluded (ignored)
            async with db.execute("SELECT COUNT(*) FROM ignored_media") as cur:
                row = await cur.fetchone()
                queue["excluded"] = row[0] if row else 0

            # Reclaimable: sum sizeOnDisk for pending movies still in Radarr
            if radarr_movies_by_id:
                async with db.execute(
                    "SELECT radarr_id FROM media_queue "
                    "WHERE status='pending' AND radarr_id IS NOT NULL"
                ) as cur:
                    pending_radarr_ids = [row[0] for row in await cur.fetchall()]

                reclaimable = 0
                count_reclaimable = 0
                for rid in pending_radarr_ids:
                    movie = radarr_movies_by_id.get(rid)
                    if movie:
                        reclaimable += movie.get("sizeOnDisk", 0) or 0
                        count_reclaimable += 1
                queue["reclaimable_size"] = reclaimable
                queue["reclaimable_count"] = count_reclaimable
            else:
                # Fallback: use pending count as reclaimable count
                queue["reclaimable_count"] = queue["pending"]

    except Exception as e:
        logger.warning(f"Queue stats: {e}")

    return {
        "disks": disks,
        "movies": movies,
        "series": series,
        "total_media_size": total_media_size,
        "queue": queue,
    }
