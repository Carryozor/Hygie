"""Sonarr API client."""
import logging
from typing import Optional

import httpx

from ..database import get_setting, TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG
from .shared import _arr_auth

logger = logging.getLogger(__name__)


async def _sonarr_config():
    url = (await get_setting("sonarr_url") or "").rstrip("/")
    key = await get_setting("sonarr_api_key") or ""
    return url, key


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
    """Build {episode_file_path: entry} for all Sonarr series.

    entry = {"ef_id": int, "series_id": int, "season_number": int,
             "series_title": str, "poster_url": str}
    1 + n_series HTTP calls total. Falls back to empty dict if Sonarr is unreachable.
    """
    url, key = await _sonarr_config()
    if not url or not key:
        return {}
    cache: dict = {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
            rs = await c.get(f"{url}/api/v3/series", headers=_arr_auth(key))
            if rs.status_code != 200:
                return {}
            for series in rs.json():
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
                rf = await c.get(
                    f"{url}/api/v3/episodefile",
                    headers=_arr_auth(key),
                    params={"seriesId": sid},
                )
                if rf.status_code == 200:
                    for ef in rf.json():
                        ep_path = ef.get("path") or ""
                        if ep_path:
                            cache[ep_path] = {
                                "ef_id": ef.get("id"),
                                "series_id": sid,
                                "season_number": ef.get("seasonNumber"),
                                "series_title": stitle,
                                "poster_url": poster_url,
                            }
    except Exception as e:
        logger.debug(f"build_sonarr_path_cache: {e}")
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
    """Find Sonarr episode file ID by matching the file path."""
    url, key = await _sonarr_config()
    if not url or not key or not file_path:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            rs = await c.get(f"{url}/api/v3/series", headers=_arr_auth(key))
            if rs.status_code != 200:
                return None
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
        logger.debug(f"sonarr_find_by_path: {e}")
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


async def sonarr_delete_episode_file(episode_file_id: int) -> bool:
    """Delete an episode file from Sonarr."""
    url, key = await _sonarr_config()
    if not url or not key or not episode_file_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.delete(
                f"{url}/api/v3/episodefile/{episode_file_id}",
                headers=_arr_auth(key),
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"sonarr_delete_episode_file: {e}")
        return False


async def sonarr_delete_season(series_id: int, season_number: int) -> bool:
    """Delete all episode files for a given season."""
    url, key = await _sonarr_config()
    if not url or not key:
        return False
    try:
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
    except Exception as e:
        logger.warning(f"sonarr_delete_season: {e}")
        return False


async def sonarr_delete_series(series_id: int) -> bool:
    """Delete an entire series from Sonarr (keeps the series entry, deletes all files)."""
    url, key = await _sonarr_config()
    if not url or not key:
        return False
    try:
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
    except Exception as e:
        logger.warning(f"sonarr_delete_series: {e}")
        return False


async def sonarr_get_torrent_hash(episode_file_id: int) -> Optional[str]:
    """Get qBittorrent hash from Sonarr download history."""
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
