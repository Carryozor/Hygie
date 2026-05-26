"""
Radarr, Sonarr, Seerr API clients.

Each service has:
  test_*()                  — verify connection
  *_find_by_path(path)      — find item ID matching a file path
  *_get(id)                 — get full item details (incl. poster URL from TMDB)
  *_delete(id)              — remove item (deleteFiles=false)
  *_get_torrent_hash(id)    — find qBittorrent hash via download history
"""
import logging
from typing import Optional, List

import httpx

from .database import get_setting, TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG

logger = logging.getLogger(__name__)


def _arr_auth(key: str) -> dict:
    """Return X-Api-Key header for Radarr/Sonarr."""
    return {"X-Api-Key": key}


def _path_matches(file_path: str, item_path: str, folder: str) -> bool:
    """Return True if file_path matches an arr item (exact path or inside folder)."""
    return bool(item_path and item_path == file_path) or bool(
        folder and file_path.startswith(folder.rstrip("/") + "/")
    )


# ═══ Radarr ═══════════════════════════════════════════════════════════════════
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


# ═══ Sonarr ═══════════════════════════════════════════════════════════════════
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
    """Build {episode_file_path: episode_file_id} for all Sonarr series.

    1 + n_series HTTP calls total instead of n_series per item lookup.
    Falls back to empty dict if Sonarr is unreachable.
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
                rf = await c.get(
                    f"{url}/api/v3/episodefile",
                    headers=_arr_auth(key),
                    params={"seriesId": series["id"]},
                )
                if rf.status_code == 200:
                    for ef in rf.json():
                        ep_path = ef.get("path") or ""
                        if ep_path:
                            cache[ep_path] = ef.get("id")
    except Exception as e:
        logger.debug(f"build_sonarr_path_cache: {e}")
    return cache


def sonarr_find_by_path_cached(file_path: str, cache: dict) -> Optional[int]:
    """Look up a Sonarr episode file ID from a pre-built cache (no HTTP call)."""
    return cache.get(file_path) if cache else None


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


# ═══ Seerr / Overseerr / Jellyseerr ═══════════════════════════════════════════
async def _seerr_config():
    url = (await get_setting("seerr_url") or "").rstrip("/")
    key = await get_setting("seerr_api_key") or ""
    return url, key


async def test_seerr() -> tuple[bool, str]:
    url, key = await _seerr_config()
    if not url or not key:
        return False, "Non configuré"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v1/status", headers={"X-Api-Key": key})
            if r.status_code == 200:
                return True, f"Seerr {r.json().get('version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def seerr_get_users() -> List[dict]:
    """
    List all Seerr users with their Discord IDs.
    discord_id comes from two sources (merged):
      - Seerr user notification settings (discordId field)
      - Hygie seerr_user_rules table (manually configured)
    The Hygie manual mapping takes priority if both are set.
    """
    url, key = await _seerr_config()
    if not url or not key:
        return []
    out = []
    try:
        from .database import DB_PATH
        import aiosqlite
        hygie_mappings: dict = {}
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT CAST(seerr_user_id AS TEXT), discord_id FROM seerr_user_rules "
                    "WHERE discord_id IS NOT NULL AND TRIM(discord_id) != ''"
                ) as cur:
                    async for row in cur:
                        hygie_mappings[str(row[0])] = row[1].strip()
        except Exception:
            pass

        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            skip = 0
            while True:
                r = await c.get(
                    f"{url}/api/v1/user",
                    headers={"X-Api-Key": key},
                    params={"take": 100, "skip": skip},
                )
                if r.status_code != 200:
                    break
                data = r.json()
                users = data.get("results", []) if isinstance(data, dict) else data
                total = data.get("pageInfo", {}).get("results", len(users))
                for u in users:
                    uid = u.get("id")
                    name = (
                        u.get("displayName")
                        or u.get("username")
                        or u.get("email")
                        or f"User #{uid}"
                    )
                    seerr_discord = ""
                    try:
                        rn = await c.get(
                            f"{url}/api/v1/user/{uid}/settings/notifications",
                            headers={"X-Api-Key": key},
                        )
                        if rn.status_code == 200:
                            seerr_discord = str(rn.json().get("discordId") or "").strip()
                    except Exception:
                        pass
                    hygie_discord = hygie_mappings.get(str(uid), "")
                    discord_id = hygie_discord or seerr_discord
                    out.append({
                        "id": uid,
                        "username": name,
                        "discord_id": discord_id,
                        "discord_id_seerr": seerr_discord,
                        "discord_id_hygie": hygie_discord,
                    })
                if skip + 100 >= total or not users:
                    break
                skip += 100
    except Exception as e:
        logger.debug(f"seerr_get_users: {e}")
    return out


async def build_seerr_request_cache() -> dict:
    """Build {tmdb_id: {seerr_id, user_id, username}} for all Seerr requests.

    One paginated scan instead of one per media item during scan.
    Falls back to empty dict if Seerr is unreachable.
    """
    url, key = await _seerr_config()
    if not url or not key:
        return {}
    cache: dict = {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as c:
            skip = 0
            while True:
                r = await c.get(
                    f"{url}/api/v1/request",
                    headers={"X-Api-Key": key},
                    params={"take": 100, "skip": skip, "sort": "added", "filter": "all"},
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("results", []) if isinstance(data, dict) else data
                total = data.get("pageInfo", {}).get("results", len(items))
                for req in items:
                    media = req.get("media") or {}
                    tmdb_id = str(media.get("tmdbId") or "")
                    if not tmdb_id:
                        continue
                    user = req.get("requestedBy") or {}
                    cache[tmdb_id] = {
                        "seerr_id": media.get("id"),
                        "user_id": user.get("id"),
                        "username": (
                            user.get("displayName")
                            or user.get("username")
                            or user.get("email")
                            or ""
                        ),
                    }
                if skip + 100 >= total or not items:
                    break
                skip += 100
    except Exception as e:
        logger.debug(f"build_seerr_request_cache: {e}")
    return cache


async def seerr_find_request_by_tmdb(tmdb_id: str) -> Optional[dict]:
    """Find a Seerr request by tmdbId. Returns dict with id, user_id, username."""
    url, key = await _seerr_config()
    if not url or not key or not tmdb_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
            skip = 0
            while True:
                r = await c.get(
                    f"{url}/api/v1/request",
                    headers={"X-Api-Key": key},
                    params={"take": 100, "skip": skip, "sort": "added", "filter": "all"},
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("results", []) if isinstance(data, dict) else data
                total = data.get("pageInfo", {}).get("results", len(items))
                for req in items:
                    media = req.get("media") or {}
                    if str(media.get("tmdbId") or "") == str(tmdb_id):
                        user = req.get("requestedBy") or {}
                        return {
                            "seerr_id": media.get("id"),
                            "user_id": user.get("id"),
                            "username": (
                                user.get("displayName")
                                or user.get("username")
                                or user.get("email")
                                or ""
                            ),
                        }
                if skip + 100 >= total or not items:
                    break
                skip += 100
    except Exception as e:
        logger.debug(f"seerr_find_request_by_tmdb: {e}")
    return None


async def seerr_delete_request(media_id: int) -> bool:
    """Delete a Seerr media (by media.id, not request.id)."""
    url, key = await _seerr_config()
    if not url or not key or not media_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.delete(
                f"{url}/api/v1/media/{media_id}", headers={"X-Api-Key": key}
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"seerr_delete_request: {e}")
        return False
