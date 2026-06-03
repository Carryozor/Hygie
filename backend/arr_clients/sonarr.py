"""Sonarr API client."""
import json
import logging
from typing import Optional

import httpx

from ..db.settings_store import get_setting
from ..db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG
from .retry import with_retry
from .shared import _arr_auth

logger = logging.getLogger(__name__)


async def _sonarr_config():
    url = (await get_setting("sonarr_url") or "").rstrip("/")
    key = await get_setting("sonarr_api_key") or ""
    return url, key


async def get_sonarr_servers() -> list[dict]:
    """Return all enabled Sonarr server configs (multi + legacy single)."""
    servers = []
    raw = await get_setting("sonarr_servers") or "[]"
    try:
        multi = json.loads(raw) if isinstance(raw, str) else raw
        servers = [s for s in (multi or []) if s.get("enabled", True) and s.get("url") and s.get("api_key")]
    except Exception:
        pass
    if not servers:
        url, key = await _sonarr_config()
        if url and key:
            servers = [{"id": "legacy", "name": "Sonarr", "url": url, "api_key": key, "enabled": True}]
    return servers


async def test_sonarr() -> tuple[bool, str]:
    url, key = await _sonarr_config()
    if not url or not key:
        return False, "Non configuré"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/system/status", headers=_arr_auth(key))
            if r.status_code == 200:
                return True, f"Sonarr {r.json().get('version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def build_sonarr_path_cache() -> dict:
    """Build {episode_file_path: entry} across all enabled Sonarr servers.

    entry = {"ef_id": int, "series_id": int, "season_number": int,
             "series_title": str, "poster_url": str, "srv_url": str, "srv_key": str}
    """
    servers = await get_sonarr_servers()
    cache: dict = {}
    for srv in servers:
        url = srv["url"].rstrip("/")
        key = srv["api_key"]
        try:
            async def _fetch_series(u=url, k=key):
                async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
                    rs = await c.get(f"{u}/api/v3/series", headers=_arr_auth(k))
                    if rs.status_code != 200:
                        return []
                    return rs.json()

            all_series = await with_retry(_fetch_series, label=f"sonarr.build_cache.series[{url}]")
            for series in all_series:
                folder = (series.get("path") or "").rstrip("/")
                if not folder:
                    continue
                sid = series.get("id")
                stitle = series.get("title", "")
                poster_url = ""
                for img in series.get("images", []):
                    if img.get("coverType") == "poster":
                        remote = img.get("remoteUrl") or ""
                        if remote.startswith("http"):
                            poster_url = remote
                            break

                async def _fetch_episodes(u=url, k=key, s=sid):
                    async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
                        rf = await c.get(
                            f"{u}/api/v3/episodefile",
                            headers=_arr_auth(k),
                            params={"seriesId": s},
                        )
                        if rf.status_code == 200:
                            return rf.json()
                        return []

                episode_files = await with_retry(_fetch_episodes, label=f"sonarr.build_cache.episodes[{url}:{sid}]")
                for ef in episode_files:
                    ep_path = ef.get("path") or ""
                    if ep_path:
                        cache[ep_path] = {
                            "ef_id": ef.get("id"),
                            "series_id": sid,
                            "season_number": ef.get("seasonNumber"),
                            "series_title": stitle,
                            "poster_url": poster_url,
                            "srv_url": url,
                            "srv_key": key,
                        }
        except Exception as e:
            logger.debug(f"build_sonarr_path_cache [{url}]: {e}")
    return cache


def sonarr_find_by_path_cached(file_path: str, cache: dict) -> Optional[int]:
    """Look up a Sonarr episode file ID from a pre-built cache (no HTTP call)."""
    entry = cache.get(file_path) if cache else None
    if entry is None:
        return None
    return entry["ef_id"] if isinstance(entry, dict) else entry


def sonarr_get_cache_entry(file_path: str, cache: dict) -> Optional[dict]:
    """Return the full cache entry for an episode file path."""
    entry = cache.get(file_path) if cache else None
    if isinstance(entry, dict):
        return entry
    return None


async def sonarr_find_by_path(file_path: str) -> Optional[int]:
    """Find Sonarr episode file ID by matching the file path across all servers."""
    if not file_path:
        return None
    servers = await get_sonarr_servers()
    for srv in servers:
        url = srv["url"].rstrip("/")
        key = srv["api_key"]
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
                rs = await c.get(f"{url}/api/v3/series", headers=_arr_auth(key))
                if rs.status_code != 200:
                    continue
                for series in rs.json():
                    folder = (series.get("path") or "").rstrip("/")
                    if not folder or not file_path.startswith(folder + "/"):
                        continue
                    rf = await c.get(
                        f"{url}/api/v3/episodefile",
                        headers=_arr_auth(key),
                        params={"seriesId": series["id"]},
                    )
                    if rf.status_code == 200:
                        for ef in rf.json():
                            if ef.get("path") == file_path:
                                return ef.get("id")
        except Exception as e:
            logger.debug(f"sonarr_find_by_path [{url}]: {e}")
    return None


async def sonarr_get_series(episode_file_id: int) -> Optional[dict]:
    """Get the series details that owns the given episode file."""
    url, key = await _sonarr_config()
    if not url or not key or not episode_file_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            ref = await c.get(
                f"{url}/api/v3/episodefile/{episode_file_id}",
                headers=_arr_auth(key),
            )
            if ref.status_code != 200:
                return None
            series_id = ref.json().get("seriesId")
            if not series_id:
                return None
            rs = await c.get(f"{url}/api/v3/series/{series_id}", headers=_arr_auth(key))
            if rs.status_code == 200:
                return rs.json()
    except Exception as e:
        logger.debug(f"sonarr_get_series: {e}")
    return None


async def sonarr_get_series_by_id(series_id: int) -> Optional[dict]:
    """Get a Sonarr series by its series ID."""
    url, key = await _sonarr_config()
    if not url or not key or not series_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/series/{series_id}", headers=_arr_auth(key))
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug(f"sonarr_get_series_by_id: {e}")
    return None


async def sonarr_get_poster_url(episode_file_id: int) -> str:
    series = await sonarr_get_series(episode_file_id)
    if not series:
        return ""
    for img in series.get("images", []):
        if img.get("coverType") == "poster":
            remote = img.get("remoteUrl") or ""
            if remote.startswith("http"):
                return remote
    return ""


async def sonarr_delete_episode_file(episode_file_id: int, url: str = "", key: str = "") -> bool:
    """Delete an episode file from Sonarr."""
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key or not episode_file_id:
        return False
    try:
        async def _do():
            async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
                r = await c.delete(
                    f"{url}/api/v3/episodefile/{episode_file_id}",
                    headers=_arr_auth(key),
                )
                return r.status_code in (200, 204)
        return await with_retry(_do, label=f"sonarr.delete_episode_file[{episode_file_id}]")
    except Exception as e:
        logger.warning(f"sonarr_delete_episode_file: {e}")
        return False


async def sonarr_delete_season(series_id: int, season_number: int, url: str = "", key: str = "") -> bool:
    """Delete all episode files for a given season."""
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key:
        return False
    try:
        async def _do():
            async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
                rf = await c.get(
                    f"{url}/api/v3/episodefile",
                    headers=_arr_auth(key),
                    params={"seriesId": series_id},
                )
                if rf.status_code != 200:
                    return False
                ef_ids = [ef["id"] for ef in rf.json() if ef.get("seasonNumber") == season_number]
                if not ef_ids:
                    return True
                dr = await c.request(
                    "DELETE",
                    f"{url}/api/v3/episodefile/bulk",
                    headers=_arr_auth(key),
                    json={"episodeFileIds": ef_ids},
                )
                return dr.status_code in (200, 204)
        return await with_retry(_do, label=f"sonarr.delete_season[{series_id}:{season_number}]")
    except Exception as e:
        logger.warning(f"sonarr_delete_season: {e}")
        return False


async def sonarr_delete_series(series_id: int, url: str = "", key: str = "") -> bool:
    """Delete an entire series from Sonarr (keeps the series entry, deletes all files)."""
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key:
        return False
    try:
        async def _do():
            async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
                # Delete all episode files in bulk
                rf = await c.get(
                    f"{url}/api/v3/episodefile",
                    headers=_arr_auth(key),
                    params={"seriesId": series_id},
                )
                if rf.status_code != 200:
                    return False
                ef_ids = [ef["id"] for ef in rf.json()]
                if not ef_ids:
                    return True
                dr = await c.request(
                    "DELETE",
                    f"{url}/api/v3/episodefile/bulk",
                    headers=_arr_auth(key),
                    json={"episodeFileIds": ef_ids},
                )
                return dr.status_code in (200, 204)
        return await with_retry(_do, label=f"sonarr.delete_series[{series_id}]")
    except Exception as e:
        logger.warning(f"sonarr_delete_series: {e}")
        return False


async def sonarr_get_torrent_hash(episode_file_id: int, url: str = "", key: str = "") -> Optional[str]:
    """Get qBittorrent hash from Sonarr download history."""
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key or not episode_file_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            ref = await c.get(
                f"{url}/api/v3/episodefile/{episode_file_id}",
                headers=_arr_auth(key),
            )
            if ref.status_code != 200:
                return None
            series_id = ref.json().get("seriesId")
            if not series_id:
                return None
            r = await c.get(
                f"{url}/api/v3/history/series",
                headers=_arr_auth(key),
                params={"seriesId": series_id},
            )
            if r.status_code == 200:
                records = r.json() if isinstance(r.json(), list) else r.json().get("records", [])
                for rec in records:
                    dl_id = (rec.get("downloadId") or "").lower()
                    if dl_id and len(dl_id) >= 32:
                        return dl_id
    except Exception as e:
        logger.debug(f"sonarr_get_torrent_hash: {e}")
    return None
