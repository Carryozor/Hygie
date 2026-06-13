"""Radarr API client."""
import json
import logging
from typing import Optional

import httpx

from ..db.settings_store import get_setting
from ..db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM
from .retry import with_retry
from .shared import _arr_auth, _path_matches

logger = logging.getLogger(__name__)


async def _radarr_config():
    url = (await get_setting("radarr_url") or "").rstrip("/")
    key = await get_setting("radarr_api_key") or ""
    return url, key


async def get_radarr_servers() -> list[dict]:
    """Return all enabled Radarr server configs (multi + legacy single)."""
    servers = []
    # Multi-server list (new format)
    raw = await get_setting("radarr_servers") or "[]"
    try:
        multi = json.loads(raw) if isinstance(raw, str) else raw
        servers = [s for s in (multi or []) if s.get("enabled", True) and s.get("url") and s.get("api_key")]
    except Exception:
        pass
    # Legacy single-server fallback
    if not servers:
        url, key = await _radarr_config()
        if url and key:
            servers = [{"id": "legacy", "name": "Radarr", "url": url, "api_key": key, "enabled": True}]
    return servers


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
    """Build path→(radarr_id, server_url, api_key) cache across all enabled servers."""
    servers = await get_radarr_servers()
    cache: dict = {}
    for srv in servers:
        url = srv["url"].rstrip("/")
        key = srv["api_key"]
        try:
            async def _fetch(u=url, k=key):
                async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
                    r = await c.get(f"{u}/api/v3/movie", headers=_arr_auth(k))
                    if r.status_code != 200:
                        return []
                    return r.json()
            movies = await with_retry(_fetch, label=f"radarr.build_cache[{url}]", service="radarr")
            for movie in movies:
                    mid = movie.get("id")
                    if not mid:
                        continue
                    entry = (mid, url, key)
                    mf_path = (movie.get("movieFile") or {}).get("path") or ""
                    if mf_path:
                        cache[mf_path] = entry
                    folder = (movie.get("path") or "").rstrip("/")
                    if folder:
                        cache[folder] = entry
        except Exception as e:
            logger.warning(f"build_radarr_path_cache [{url}]: {e}")
    return cache


def radarr_find_by_path_cached(file_path: str, cache: dict) -> Optional[tuple]:
    """Look up (radarr_id, url, api_key) from a pre-built cache (no HTTP call)."""
    if not file_path or not cache:
        return None
    if file_path in cache:
        return cache[file_path]
    for path, entry in cache.items():
        if file_path.startswith(path + "/"):
            return entry
    return None


async def radarr_find_by_path(file_path: str) -> Optional[tuple]:
    """Find (radarr_id, url, api_key) by matching the file path across all servers."""
    if not file_path:
        return None
    servers = await get_radarr_servers()
    for srv in servers:
        url = srv["url"].rstrip("/")
        key = srv["api_key"]
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
                r = await c.get(f"{url}/api/v3/movie", headers=_arr_auth(key))
                if r.status_code != 200:
                    continue
                for movie in r.json():
                    mf = movie.get("movieFile") or {}
                    if _path_matches(file_path, mf.get("path") or "", movie.get("path") or ""):
                        return (movie.get("id"), url, key)
        except Exception as e:
            logger.warning(f"radarr_find_by_path [{url}]: {e}")
    return None


async def radarr_get(radarr_id: int, url: str = "", key: str = "") -> Optional[dict]:
    """Get full movie details including images."""
    if not url or not key:
        url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/movie/{radarr_id}", headers=_arr_auth(key))
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"radarr_get: {e}")
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


async def radarr_delete(radarr_id: int, delete_files: bool = False, url: str = "", key: str = "") -> bool:
    """Delete a movie from Radarr (keeping files by default)."""
    if not url or not key:
        url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return False
    try:
        async def _do():
            async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
                r = await c.delete(
                    f"{url}/api/v3/movie/{radarr_id}",
                    headers=_arr_auth(key),
                    params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
                )
                return r.status_code in (200, 204)
        return await with_retry(_do, label=f"radarr.delete[{radarr_id}]", service="radarr")
    except Exception as e:
        logger.warning(f"radarr_delete: {e}")
        return False


async def radarr_get_torrent_hash(radarr_id: int, url: str = "", key: str = "") -> Optional[str]:
    """Get qBittorrent hash from Radarr download history."""
    if not url or not key:
        url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return None
    try:
        async def _do():
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
                return None
        return await with_retry(_do, label=f"radarr.torrent_hash[{radarr_id}]", service="radarr")
    except Exception as e:
        logger.warning(f"radarr_get_torrent_hash: {e}")
    return None


async def radarr_delete_by_id(radarr_id: int, delete_files: bool = False) -> bool:
    """Delete a movie from any configured Radarr server that has this ID."""
    servers = await get_radarr_servers()
    for srv in servers:
        ok = await radarr_delete(radarr_id, delete_files=delete_files,
                                 url=srv["url"].rstrip("/"), key=srv["api_key"])
        if ok:
            return True
    return False


async def radarr_get_torrent_hash_any(radarr_id: int) -> Optional[str]:
    """Get torrent hash from any configured Radarr server that has this movie."""
    servers = await get_radarr_servers()
    for srv in servers:
        h = await radarr_get_torrent_hash(radarr_id, url=srv["url"].rstrip("/"), key=srv["api_key"])
        if h:
            return h
    return None
