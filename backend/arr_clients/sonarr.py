"""Sonarr API client."""
import asyncio
import logging
from typing import Optional

import httpx

from ..db.settings_store import get_setting
from ..db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG
from .retry import with_retry
from .shared import _arr_auth, _get_arr_servers

logger = logging.getLogger(__name__)


async def _sonarr_config():
    url = (await get_setting("sonarr_url") or "").rstrip("/")
    key = await get_setting("sonarr_api_key") or ""
    return url, key


async def get_sonarr_servers() -> list[dict]:
    """Return all enabled Sonarr server configs (multi + legacy single)."""
    return await _get_arr_servers("sonarr_servers", _sonarr_config, "Sonarr")


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

            all_series = await with_retry(_fetch_series, label=f"sonarr.build_cache.series[{url}]", service="sonarr")

            sem = asyncio.Semaphore(8)

            async def _process_series(series: dict) -> list:
                folder = (series.get("path") or "").rstrip("/")
                if not folder:
                    return []
                sid = series.get("id")
                stitle = series.get("title", "")
                poster_url = ""
                for img in series.get("images", []):
                    if img.get("coverType") == "poster":
                        remote = img.get("remoteUrl") or ""
                        if remote.startswith("http"):
                            poster_url = remote
                            break

                async def _fetch_eps(u=url, k=key, s=sid):
                    async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
                        rf = await c.get(
                            f"{u}/api/v3/episodefile",
                            headers=_arr_auth(k),
                            params={"seriesId": s},
                        )
                        if rf.status_code == 200:
                            return rf.json()
                        return []

                async with sem:
                    episode_files = await with_retry(_fetch_eps, label=f"sonarr.build_cache.episodes[{url}:{sid}]", service="sonarr")

                entries = []
                for ef in episode_files:
                    ep_path = ef.get("path") or ""
                    if ep_path:
                        entries.append((ep_path, {
                            "ef_id": ef.get("id"),
                            "series_id": sid,
                            "season_number": ef.get("seasonNumber"),
                            "series_title": stitle,
                            "poster_url": poster_url,
                            "srv_url": url,
                            "srv_key": key,
                        }))
                return entries

            all_entries = await asyncio.gather(*[_process_series(s) for s in all_series])
            for entries in all_entries:
                for ep_path, entry in entries:
                    cache[ep_path] = entry
        except Exception as e:
            logger.warning(f"build_sonarr_path_cache [{url}]: {e}")
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
            logger.warning(f"sonarr_find_by_path [{url}]: {e}")
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
        logger.warning(f"sonarr_get_series: {e}")
    return None


async def sonarr_get_series_by_id(series_id: int, url: str = "", key: str = "") -> Optional[dict]:
    """Get a Sonarr series by its series ID."""
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key or not series_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/series/{series_id}", headers=_arr_auth(key))
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"sonarr_get_series_by_id: {e}")
    return None


async def sonarr_get_series_by_id_any(series_id: int) -> Optional[dict]:
    """Get a series from any configured Sonarr server that has this ID.

    Unlike sonarr_get_series_by_id()'s legacy single-server fallback, this
    checks every enabled server — needed in multi-Sonarr setups where
    series_id is only meaningful on the server that issued it.
    """
    servers = await get_sonarr_servers()
    for srv in servers:
        series = await sonarr_get_series_by_id(series_id, url=srv["url"].rstrip("/"), key=srv["api_key"])
        if series:
            return series
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
        return await with_retry(_do, label=f"sonarr.delete_episode_file[{episode_file_id}]", service="sonarr")
    except Exception as e:
        logger.warning(f"sonarr_delete_episode_file: {e}")
        return False


async def _sonarr_unmonitor_episodes(c: httpx.AsyncClient, url: str, key: str, episode_ids: list[int]) -> None:
    """Bulk-unmonitor a set of episodes — best-effort, never raises.

    Called after files have already been wiped so Sonarr stops treating them
    as "missing" and re-grabbing them on its next RSS sync / automatic search.
    """
    if not episode_ids:
        return
    try:
        await c.request(
            "PUT",
            f"{url}/api/v3/episode/monitor",
            headers=_arr_auth(key),
            json={"episodeIds": episode_ids, "monitored": False},
        )
    except Exception as e:
        logger.warning(f"_sonarr_unmonitor_episodes: {e}")


async def _episode_ids_for_files(
    c: httpx.AsyncClient, url: str, key: str, series_id: int, ef_ids: set[int]
) -> list[int]:
    """Map episode file IDs to the episode IDs that reference them."""
    re_ = await c.get(
        f"{url}/api/v3/episode", headers=_arr_auth(key), params={"seriesId": series_id}
    )
    if re_.status_code != 200:
        return []
    return [ep["id"] for ep in re_.json() if ep.get("episodeFileId") in ef_ids]


async def sonarr_delete_season(series_id: int, season_number: int, url: str = "", key: str = "") -> bool:
    """Delete all episode files for a given season, then unmonitor those episodes."""
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
                ok = dr.status_code in (200, 204)
                if ok:
                    episode_ids = await _episode_ids_for_files(c, url, key, series_id, set(ef_ids))
                    await _sonarr_unmonitor_episodes(c, url, key, episode_ids)
                return ok
        return await with_retry(_do, label=f"sonarr.delete_season[{series_id}:{season_number}]", service="sonarr")
    except Exception as e:
        logger.warning(f"sonarr_delete_season: {e}")
        return False


async def sonarr_delete_series(series_id: int, url: str = "", key: str = "") -> bool:
    """Delete all files of an entire series, then unmonitor the series.

    Keeps the series entry itself (matching Hygie's per-episode behavior of
    never touching arr bookkeeping beyond what's needed), but disables
    monitoring — otherwise Sonarr keeps treating the wiped episodes as
    "missing" and can re-grab them on its next RSS sync.
    """
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
                ok = dr.status_code in (200, 204)
                if ok:
                    rs = await c.get(f"{url}/api/v3/series/{series_id}", headers=_arr_auth(key))
                    if rs.status_code == 200:
                        series = rs.json()
                        series["monitored"] = False
                        await c.request(
                            "PUT",
                            f"{url}/api/v3/series/{series_id}",
                            headers=_arr_auth(key),
                            json=series,
                        )
                return ok
        return await with_retry(_do, label=f"sonarr.delete_series[{series_id}]", service="sonarr")
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
        logger.warning(f"sonarr_get_torrent_hash: {e}")
    return None


async def sonarr_get_torrent_hashes_for_group(
    series_id: int, season_number: Optional[int] = None, url: str = "", key: str = ""
) -> set[str]:
    """Resolve every distinct qBittorrent hash backing a season's or series'
    episode files, by matching history records to the actual episodes involved.

    Replaces the old single-hash sonarr_get_torrent_hash() for consolidated
    deletes: that function returned the first downloadId found anywhere in the
    series' history regardless of which episode it belonged to, so a
    season/series delete could resolve (and "delete") a torrent unrelated to
    any of the files actually being removed — while the real torrent(s)
    backing those files were silently left untouched (qBittorrent's delete
    endpoint returns 200 even for a hash that matches no torrent).

    Must run BEFORE the bulk episodefile delete — Sonarr's episode/history
    records are looked up by the (still-existing) episode file IDs.
    """
    if not url or not key:
        url, key = await _sonarr_config()
    if not url or not key or not series_id:
        return set()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            rf = await c.get(
                f"{url}/api/v3/episodefile", headers=_arr_auth(key), params={"seriesId": series_id}
            )
            if rf.status_code != 200:
                return set()
            ef_list = rf.json()
            if season_number is not None:
                ef_list = [ef for ef in ef_list if ef.get("seasonNumber") == season_number]
            ef_ids = {ef["id"] for ef in ef_list}
            if not ef_ids:
                return set()

            episode_ids = set(await _episode_ids_for_files(c, url, key, series_id, ef_ids))
            if not episode_ids:
                return set()

            rh = await c.get(
                f"{url}/api/v3/history/series", headers=_arr_auth(key), params={"seriesId": series_id}
            )
            if rh.status_code != 200:
                return set()
            records = rh.json() if isinstance(rh.json(), list) else rh.json().get("records", [])
            hashes: set[str] = set()
            for rec in records:
                if rec.get("episodeId") not in episode_ids:
                    continue
                dl_id = (rec.get("downloadId") or "").lower()
                if dl_id and len(dl_id) >= 32:
                    hashes.add(dl_id)
            return hashes
    except Exception as e:
        logger.warning(f"sonarr_get_torrent_hashes_for_group: {e}")
        return set()
