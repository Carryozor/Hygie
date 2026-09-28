"""Radarr API client."""
import logging
from typing import Optional

import httpx

from ..db.settings_store import get_setting
from ..db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM
from .retry import with_retry
from .shared import (
    _arr_auth,
    _extract_poster_url,
    _first_from_servers,
    _get_arr_servers,
    _path_matches,
    _resolve_arr_creds,
    _resolve_arr_server,
    _test_arr_connection,
)

logger = logging.getLogger(__name__)


async def _radarr_config():
    url = (await get_setting("radarr_url") or "").rstrip("/")
    key = await get_setting("radarr_api_key") or ""
    return url, key


async def get_radarr_servers() -> list[dict]:
    """Return all enabled Radarr server configs (multi + legacy single)."""
    return await _get_arr_servers("radarr_servers", _radarr_config, "Radarr")


async def test_radarr() -> tuple[bool, str]:
    url, key = await _radarr_config()
    return await _test_arr_connection(url, key, "Radarr")


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
    url, key = await _resolve_arr_creds(url, key, _radarr_config)
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
    return _extract_poster_url(movie.get("images", []))


async def radarr_delete(radarr_id: int, delete_files: bool = False, url: str = "", key: str = "") -> bool:
    """Delete a movie from Radarr (keeping files by default)."""
    url, key = await _resolve_arr_creds(url, key, _radarr_config)
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
    url, key = await _resolve_arr_creds(url, key, _radarr_config)
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


async def radarr_delete_by_id(
    radarr_id: int,
    delete_files: bool = False,
    arr_server_url: Optional[str] = None,
    file_path: str = "",
) -> bool:
    """Delete a movie from the single Radarr server that owns this id.

    Resolves the target server from arr_server_url (recorded on the queue
    row at scan time). Legacy rows (arr_server_url is None, predates that
    column) resolve via the single configured server, or — with more than
    one server configured — a file-path match against each server's library.

    Deliberately never tries the same numeric id across every configured
    server: a previous version looped every Radarr and stopped at the first
    success, which in a multi-Radarr setup could delete an unrelated movie
    that happened to reuse the same id on a different instance. If the
    target server can't be resolved, this refuses to delete and returns
    False rather than guess.
    """
    servers = await get_radarr_servers()
    target = await _resolve_arr_server(servers, arr_server_url)
    if not target and file_path:
        found = await radarr_find_by_path(file_path)
        if found:
            target = (found[1], found[2])
    if not target:
        logger.warning(
            "radarr_delete_by_id: cannot resolve target server for id=%s "
            "(arr_server_url=%r, %d server(s) configured) — refusing to delete",
            radarr_id, arr_server_url, len(servers),
        )
        return False
    url, key = target
    return await radarr_delete(radarr_id, delete_files=delete_files, url=url, key=key)


async def radarr_get_any(radarr_id: int) -> Optional[dict]:
    """Get full movie details from any configured Radarr server that has this ID.

    Unlike radarr_get()'s legacy single-server fallback, this checks every
    enabled server — needed in multi-Radarr setups where radarr_id is only
    meaningful on the server that issued it.
    """
    servers = await get_radarr_servers()
    return await _first_from_servers(servers, lambda url, key: radarr_get(radarr_id, url=url, key=key))


async def radarr_get_torrent_hash_any(radarr_id: int) -> Optional[str]:
    """Get torrent hash from any configured Radarr server that has this movie."""
    servers = await get_radarr_servers()
    return await _first_from_servers(
        servers, lambda url, key: radarr_get_torrent_hash(radarr_id, url=url, key=key)
    )
