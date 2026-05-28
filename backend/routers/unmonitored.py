"""
Unmonitored — list Radarr/Sonarr movies/series that are unmonitored or orphaned.

Provides a view of items in your library that aren't being tracked anymore
(e.g. monitored=false but files still present). User can re-enable monitoring
or delete them outright.
"""
import logging
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_auth
from ..db.utils import DB_PATH
from ..db.settings_store import get_setting
from ..db.logs import add_log

router = APIRouter(prefix="/api/unmonitored", tags=["unmonitored"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_unmonitored(
    user: str = Depends(require_auth),
    search: Optional[str] = None,
):
    """List Radarr movies and Sonarr series that are unmonitored but have files."""
    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""

    # Get list of emby_ids already in queue or ignored (to skip)
    tracked: set = set()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT radarr_id FROM media_queue WHERE radarr_id IS NOT NULL"
        ) as cur:
            for row in await cur.fetchall():
                tracked.add(("movie", row[0]))
        async with db.execute(
            "SELECT sonarr_id FROM media_queue WHERE sonarr_id IS NOT NULL"
        ) as cur:
            for row in await cur.fetchall():
                tracked.add(("series", row[0]))

    movies = []
    series = []

    async with httpx.AsyncClient(timeout=30) as c:
        if radarr_url and radarr_key:
            try:
                r = await c.get(
                    f"{radarr_url}/api/v3/movie", params={"apikey": radarr_key}
                )
                if r.status_code == 200:
                    for m in r.json():
                        if m.get("monitored"):
                            continue
                        if not m.get("hasFile"):
                            continue
                        if ("movie", m["id"]) in tracked:
                            continue
                        title = m.get("title", "")
                        if search and search.lower() not in title.lower():
                            continue
                        poster = ""
                        for img in m.get("images", []):
                            if img.get("coverType") == "poster":
                                poster = img.get("remoteUrl") or ""
                                if poster.startswith("http"):
                                    break
                        movies.append({
                            "id": m["id"],
                            "title": title,
                            "year": m.get("year"),
                            "size_bytes": m.get("sizeOnDisk", 0),
                            "poster_url": poster,
                            "tmdb_id": m.get("tmdbId"),
                            "type": "movie",
                        })
            except Exception as e:
                logger.warning(f"Radarr unmonitored: {e}")

        if sonarr_url and sonarr_key:
            try:
                r = await c.get(
                    f"{sonarr_url}/api/v3/series", params={"apikey": sonarr_key}
                )
                if r.status_code == 200:
                    for s in r.json():
                        if s.get("monitored"):
                            continue
                        stats = s.get("statistics", {})
                        if not stats.get("episodeFileCount"):
                            continue
                        if ("series", s["id"]) in tracked:
                            continue
                        title = s.get("title", "")
                        if search and search.lower() not in title.lower():
                            continue
                        poster = ""
                        for img in s.get("images", []):
                            if img.get("coverType") == "poster":
                                poster = img.get("remoteUrl") or ""
                                if poster.startswith("http"):
                                    break
                        series.append({
                            "id": s["id"],
                            "title": title,
                            "year": s.get("year"),
                            "size_bytes": stats.get("sizeOnDisk", 0),
                            "poster_url": poster,
                            "tvdb_id": s.get("tvdbId"),
                            "episode_count": stats.get("episodeFileCount", 0),
                            "type": "series",
                        })
            except Exception as e:
                logger.warning(f"Sonarr unmonitored: {e}")

    return {"movies": movies, "series": series}


@router.post("/monitor/movie/{movie_id}")
async def monitor_movie(movie_id: int, user: str = Depends(require_auth)):
    """Re-enable monitoring on a Radarr movie."""
    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    if not radarr_url or not radarr_key:
        raise HTTPException(400, "Radarr non configuré")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{radarr_url}/api/v3/movie/{movie_id}", params={"apikey": radarr_key}
        )
        if r.status_code != 200:
            raise HTTPException(404, "Film introuvable")
        movie = r.json()
        movie["monitored"] = True
        ru = await c.put(
            f"{radarr_url}/api/v3/movie/{movie_id}",
            params={"apikey": radarr_key},
            json=movie,
        )
        if ru.status_code not in (200, 202):
            raise HTTPException(500, f"Erreur Radarr HTTP {ru.status_code}")
    await add_log("INFO", f"Monitoring activé : Radarr movie #{movie_id}", "unmonitored")
    return {"status": "ok"}


@router.post("/monitor/series/{series_id}")
async def monitor_series(series_id: int, user: str = Depends(require_auth)):
    """Re-enable monitoring on a Sonarr series."""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""
    if not sonarr_url or not sonarr_key:
        raise HTTPException(400, "Sonarr non configuré")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{sonarr_url}/api/v3/series/{series_id}", params={"apikey": sonarr_key}
        )
        if r.status_code != 200:
            raise HTTPException(404, "Série introuvable")
        series = r.json()
        series["monitored"] = True
        ru = await c.put(
            f"{sonarr_url}/api/v3/series/{series_id}",
            params={"apikey": sonarr_key},
            json=series,
        )
        if ru.status_code not in (200, 202):
            raise HTTPException(500, f"Erreur Sonarr HTTP {ru.status_code}")
    await add_log("INFO", f"Monitoring activé : Sonarr series #{series_id}", "unmonitored")
    return {"status": "ok"}


@router.delete("/movie/{movie_id}")
async def delete_movie(
    movie_id: int,
    delete_files: bool = Query(False),
    user: str = Depends(require_auth),
):
    """Delete a movie from Radarr (optionally with its files)."""
    from ..arr_clients import radarr_delete
    ok = await radarr_delete(movie_id, delete_files=delete_files)
    if not ok:
        raise HTTPException(500, "Suppression Radarr échouée")
    await add_log(
        "INFO",
        f"Radarr : film #{movie_id} supprimé (fichiers={'oui' if delete_files else 'non'})",
        "unmonitored",
    )
    return {"status": "deleted"}


@router.delete("/series/{series_id}")
async def delete_series(
    series_id: int,
    delete_files: bool = Query(False),
    user: str = Depends(require_auth),
):
    """Delete a series from Sonarr (optionally with its files)."""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""
    if not sonarr_url or not sonarr_key:
        raise HTTPException(400, "Sonarr non configuré")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(
            f"{sonarr_url}/api/v3/series/{series_id}",
            params={
                "apikey": sonarr_key,
                "deleteFiles": str(delete_files).lower(),
                "addImportListExclusion": "false",
            },
        )
        if r.status_code not in (200, 204):
            raise HTTPException(500, f"Erreur Sonarr HTTP {r.status_code}")
    await add_log(
        "INFO",
        f"Sonarr : série #{series_id} supprimée (fichiers={'oui' if delete_files else 'non'})",
        "unmonitored",
    )
    return {"status": "deleted"}
