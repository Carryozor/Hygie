"""Radarr API client."""
import logging
from typing import Optional

import httpx

from ..database import get_setting, TIMEOUT_SHORT, TIMEOUT_MEDIUM
from .shared import _arr_auth, _path_matches

logger = logging.getLogger(__name__)


async def _radarr_config():
    url = (await get_setting("radarr_url") or "").rstrip("/")
    key = await get_setting("radarr_api_key") or ""
    return url, key


async def test_radarr() -> tuple[bool, str]:
    url, key = await _radarr_config()
    if not url or not key:
        return False, "Non configuré"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/system/status", headers=_arr_auth(key))
            if r.status_code == 200:
                return True, f"Radarr {r.json().get('version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def build_radarr_path_cache() -> dict:
    """Build and return a path→radarr_id cache for all movies in one HTTP call.

    Returns {file_path: radarr_id} and {folder_path: radarr_id}.
    Pass the result to radarr_find_by_path_cached() to avoid per-item HTTP calls.
    """
    url, key = await _radarr_config()
    if not url or not key:
        return {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            r = await c.get(f"{url}/api/v3/movie", headers=_arr_auth(key))
            if r.status_code != 200:
                return {}
            cache: dict = {}
            for movie in r.json():
                mid = movie.get("id")
                if not mid:
                    continue
                mf_path = (movie.get("movieFile") or {}).get("path") or ""
                if mf_path:
                    cache[mf_path] = mid
                folder = (movie.get("path") or "").rstrip("/")
                if folder:
                    cache[folder] = mid
            return cache
    except Exception as e:
        logger.debug(f"build_radarr_path_cache: {e}")
    return {}


def radarr_find_by_path_cached(file_path: str, cache: dict) -> Optional[int]:
    """Look up a Radarr movie ID from a pre-built cache (no HTTP call)."""
    if not file_path or not cache:
        return None
    if file_path in cache:
        return cache[file_path]
    for path, mid in cache.items():
        if file_path.startswith(path + "/"):
            return mid
    return None


async def radarr_find_by_path(file_path: str) -> Optional[int]:
    """Find Radarr movie ID by matching the file path (single-item fallback)."""
    url, key = await _radarr_config()
    if not url or not key or not file_path:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            r = await c.get(f"{url}/api/v3/movie", headers=_arr_auth(key))
            if r.status_code != 200:
                return None
            for movie in r.json():
                mf = movie.get("movieFile") or {}
                if _path_matches(file_path, mf.get("path") or "", movie.get("path") or ""):
                    return movie.get("id")
    except Exception as e:
        logger.debug(f"radarr_find_by_path: {e}")
    return None


async def radarr_get(radarr_id: int) -> Optional[dict]:
    """Get full movie details including images."""
    url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/movie/{radarr_id}", headers=_arr_auth(key))
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug(f"radarr_get: {e}")
    return None


async def radarr_get_poster_url(radarr_id: int) -> str:
    """Return the TMDB remoteUrl for the poster (public URL)."""
    movie = await radarr_get(radarr_id)
    if not movie:
        return ""
    for img in movie.get("images", []):
        if img.get("coverType") == "poster":
            remote = img.get("remoteUrl") or ""
            if remote.startswith("http"):
                return remote
    return ""


async def radarr_delete(radarr_id: int, delete_files: bool = False) -> bool:
    """Delete a movie from Radarr (keeping files by default)."""
    url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.delete(
                f"{url}/api/v3/movie/{radarr_id}",
                headers=_arr_auth(key),
                params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"radarr_delete: {e}")
        return False


async def radarr_get_torrent_hash(radarr_id: int) -> Optional[str]:
    """Get qBittorrent hash from Radarr download history."""
    url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(
                f"{url}/api/v3/history/movie",
                headers=_arr_auth(key),
                params={"movieId": radarr_id},
            )
            if r.status_code == 200:
                records = r.json() if isinstance(r.json(), list) else r.json().get("records", [])
                for rec in records:
                    dl_id = (rec.get("downloadId") or "").lower()
                    if dl_id and len(dl_id) >= 32:
                        return dl_id
            r2 = await c.get(
                f"{url}/api/v3/history",
                headers=_arr_auth(key),
                params={"movieId": radarr_id, "pageSize": 20},
            )
            if r2.status_code == 200:
                for rec in r2.json().get("records", []):
                    dl_id = (rec.get("downloadId") or "").lower()
                    if dl_id and len(dl_id) >= 32:
                        return dl_id
    except Exception as e:
        logger.debug(f"radarr_get_torrent_hash: {e}")
    return None
