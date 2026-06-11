"""
Emby API client.

Public API:
  get_client()                          — (url, api_key) tuple
  test_connection()                     — bool, message
  get_libraries()                       — list of {Id, Name}
  get_items_in_library(lib_id, ...)     — paginated items
  get_users()                           — list of users
  delete_item(item_id)                  — remove from Emby library
"""
import logging
from typing import Optional, Tuple, List

import httpx

from .db.settings_store import get_setting, set_setting
from .db.media_servers import get_media_servers, save_media_servers
from .db.utils import TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG, http_retry
from .db.encryption import _decrypt_value
from .arr_clients.circuit_breaker import get_breaker, CircuitOpenError

logger = logging.getLogger(__name__)


def _emby_breaker(server_id: str):
    """Return the circuit breaker for the given Emby/Jellyfin server."""
    return get_breaker(f"emby:{server_id}", failure_threshold=5, recovery_timeout=120.0)


def _classify_network_error(e: Exception) -> str:
    """Return a stable error code for a network exception."""
    s = str(e).lower()
    if "name or service not known" in s or "errno -2" in s or "errno 8" in s or "nodename nor servname" in s:
        return "dns_failure"
    if "connection refused" in s or "errno 111" in s:
        return "connection_refused"
    if "no route to host" in s or "errno 113" in s:
        return "host_unreachable"
    if "ssl" in s or "certificate" in s:
        return "ssl_error"
    return "network_error"


def _http_error_code(status_code: int) -> str:
    codes = {401: "http_401", 403: "http_403", 404: "http_404", 502: "http_502", 503: "http_503"}
    return codes.get(status_code, f"http_{status_code}")


def _auth(key: str) -> dict:
    """Return the X-Emby-Token header dict for a given API key."""
    return {"X-Emby-Token": key}


async def ensure_server_uid(server_id: str = "0") -> None:
    """Populate server_uid from /System/Info if not already stored (idempotent)."""
    servers = await get_media_servers()
    for s in servers:
        if str(s.get("id", "")) != str(server_id):
            continue
        if s.get("server_uid"):
            return  # already set
        url, key = (s.get("url") or "").rstrip("/"), s.get("api_key") or ""
        if key.startswith("enc:"):
            key = _decrypt_value(key)
        if not url or not key:
            return
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
                r = await client.get(f"{url}/System/Info", headers=_auth(key))
                if r.status_code == 200:
                    uid = r.json().get("Id", "")
                    if uid:
                        s["server_uid"] = uid
                        await save_media_servers(servers)
        except Exception:
            pass
        return


async def get_client(server_id: str = "0") -> Tuple[str, str]:
    """Return (url, api_key) for the given server_id.
    Falls back to legacy emby_url/emby_api_key if media_servers is empty.
    """
    servers = await get_media_servers()
    for s in servers:
        if str(s.get("id", "")) == str(server_id):
            url = (s.get("url") or "").rstrip("/")
            key = s.get("api_key") or ""
            if key.startswith("enc:"):
                key = _decrypt_value(key)
            return url, key
    # Fallback to legacy settings (backward compat) — get_setting decrypts automatically
    url = (await get_setting("emby_url") or "").rstrip("/")
    key = await get_setting("emby_api_key") or ""
    if url:
        logger.warning("Using legacy emby_url — reconfigure via Settings > Serveurs")
    return url, key


async def get_client_ext_url(server_id: str = "0") -> str:
    """Return the external URL for the given server."""
    servers = await get_media_servers()
    for s in servers:
        if str(s.get("id", "")) == str(server_id):
            ext = (s.get("ext_url") or "").rstrip("/")
            if ext.startswith("enc:"):
                ext = _decrypt_value(ext)
            return ext
    ext = (await get_setting("emby_external_url") or "").rstrip("/")
    if ext:
        logger.warning("Using legacy emby_external_url — reconfigure via Settings > Serveurs")
    return ext


async def test_connection(server_id: str = "0") -> Tuple[bool, str, str]:
    """Test connection and auto-detect server type (emby|jellyfin|unknown).
    Returns (ok, message, server_type). Updates server type in media_servers on success.
    """
    url, key = await get_client(server_id)
    if not url or not key:
        return False, "URL ou clé API manquante", ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await client.get(f"{url}/System/Info", headers=_auth(key))
            if r.status_code == 200:
                info = r.json()
                version = info.get("Version", "?")
                product = (info.get("ProductName") or "").lower()
                v_parts = version.split(".")
                v_major = v_parts[0] if v_parts else ""
                if "jellyfin" in product or (not product and v_major == "10"):
                    server_type, label = "jellyfin", f"Jellyfin {version}"
                elif "emby" in product or (not product and (v_major == "4" or len(v_parts) >= 4)):
                    server_type, label = "emby", f"Emby {version}"
                elif product:
                    server_type = "unknown"
                    label = (info.get("ProductName") or "?") + f" {version}"
                else:
                    server_type = "unknown"
                    label = f"Unknown {version}"
                server_uid = info.get("Id", "")
                servers = await get_media_servers()
                updated = False
                for s in servers:
                    if str(s.get("id", "")) == str(server_id):
                        s["type"] = server_type
                        if server_uid:
                            s["server_uid"] = server_uid
                        updated = True
                if updated:
                    await save_media_servers(servers)
                await set_setting("media_server_type", server_type)
                return True, label, server_type, ""
            code = _http_error_code(r.status_code)
            return False, f"HTTP {r.status_code}", "", code
    except (httpx.ConnectError, httpx.NetworkError) as e:
        return False, str(e), "", _classify_network_error(e)
    except httpx.TimeoutException:
        return False, "Connection timed out", "", "timeout"
    except Exception as e:
        return False, str(e), "", ""


async def get_libraries(server_id: str = "0") -> List[dict]:
    """Return all Emby libraries for the given server."""
    url, key = await get_client(server_id)
    if not url or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            r = await http_retry(lambda: client.get(f"{url}/Library/MediaFolders", headers=_auth(key)))
            if r.status_code == 200:
                return r.json().get("Items", [])
    except Exception as e:
        logger.warning(f"get_libraries error: {e}")
    return []


async def get_users(server_id: str = "0") -> List[dict]:
    url, key = await get_client(server_id)
    if not url or not key:
        return []
    breaker = _emby_breaker(server_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await breaker.call(
                lambda: http_retry(lambda: client.get(f"{url}/Users", headers=_auth(key)))
            )
            if r.status_code == 200:
                return r.json()
    except CircuitOpenError:
        logger.warning("Circuit breaker OPEN for emby:%s — get_users skipped", server_id)
    except Exception as e:
        logger.warning(f"get_users error: {e}")
    return []


async def get_items_in_library(
    library_id: str, limit: int = 500, start: int = 0, server_id: str = "0"
) -> Tuple[List[dict], int]:
    """Return (items, total_count) for items in a library."""
    url, key = await get_client(server_id)
    if not url or not key:
        return [], 0
    params = {
        "ParentId": library_id,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Episode",
        "Fields": "Path,DateCreated,ProviderIds",
        "Limit": limit,
        "StartIndex": start,
    }
    breaker = _emby_breaker(server_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            r = await breaker.call(
                lambda: http_retry(lambda: client.get(f"{url}/Items", headers=_auth(key), params=params))
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("Items", []), data.get("TotalRecordCount", 0)
    except CircuitOpenError:
        logger.warning("Circuit breaker OPEN for emby:%s — get_items_in_library skipped (lib=%s)", server_id, library_id)
    except Exception as e:
        logger.warning(f"get_items_in_library error: {e}")
    return [], 0


async def get_series_tmdb_map(library_id: str, server_id: str = "0") -> dict:
    """Return {series_emby_id: series_tmdb_id_str} for all series in a library.

    Emby Episode items carry no series-level Tmdb provider id (only episode
    Tvdb/Imdb ids), while the Seerr request cache is keyed by the series
    tmdbId — this parent-level map bridges the two.
    """
    url, key = await get_client(server_id)
    if not url or not key:
        return {}
    out: dict = {}
    start = 0
    breaker = _emby_breaker(server_id)
    while True:
        params = {
            "ParentId": library_id,
            "Recursive": "true",
            "IncludeItemTypes": "Series",
            "Fields": "ProviderIds",
            "Limit": 500,
            "StartIndex": start,
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
                r = await breaker.call(
                    lambda: http_retry(lambda: client.get(f"{url}/Items", headers=_auth(key), params=params))
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("Items", [])
                total = data.get("TotalRecordCount", 0)
        except CircuitOpenError:
            logger.warning("Circuit breaker OPEN for emby:%s — get_series_tmdb_map skipped (lib=%s)", server_id, library_id)
            break
        except Exception as e:
            logger.warning(f"get_series_tmdb_map error: {e}")
            break
        for it in items:
            sid  = it.get("Id")
            tmdb = str((it.get("ProviderIds") or {}).get("Tmdb") or "")
            if sid and tmdb:
                out[sid] = tmdb
        start += 500
        if start >= total:
            break
    return out


def resolve_item_tmdb(item: dict, series_tmdb_map: Optional[dict]) -> str:
    """Return the Seerr-matchable TMDB id for an Emby item.

    Movies carry their own Tmdb provider id; episodes must use the parent
    series' Tmdb id (their own ProviderIds never contain it).
    """
    if (item.get("Type") or "") == "Episode":
        series_id = item.get("SeriesId") or ""
        return (series_tmdb_map or {}).get(series_id, "")
    return str((item.get("ProviderIds") or {}).get("Tmdb") or "")


async def get_library_user_data(user_id: str, library_id: str, server_id: str = "0") -> dict:
    """Return {emby_item_id: UserData} for all items in a library for one user.

    Paginates in batches of 500 to avoid truncation on large libraries.
    """
    url, key = await get_client(server_id)
    if not url or not key:
        return {}

    result: dict = {}
    start_index = 0
    page_size = 500

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            while True:
                params = {
                    "ParentId": library_id,
                    "Fields": "UserData",
                    "Recursive": "true",
                    "StartIndex": start_index,
                    "Limit": page_size,
                    "IncludeItemTypes": "Movie,Episode",
                }
                r = await http_retry(
                    lambda: client.get(f"{url}/Users/{user_id}/Items", headers=_auth(key), params=params)
                )
                if r.status_code != 200:
                    break
                body = r.json()
                items = body.get("Items", [])
                for item in items:
                    result[item["Id"]] = item.get("UserData") or {}
                total = body.get("TotalRecordCount", 0)
                start_index += len(items)
                if not items or start_index >= total:
                    break
    except Exception as e:
        logger.warning(f"get_library_user_data error: {e}")
    return result


async def get_user_data(user_id: str, item_id: str, server_id: str = "0") -> Optional[dict]:
    """Get a user's UserData for a specific item (Played, PlayCount, LastPlayedDate)."""
    url, key = await get_client(server_id)
    if not url or not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await client.get(
                f"{url}/Users/{user_id}/Items/{item_id}",
                headers=_auth(key),
                params={"Fields": "UserData"},
            )
            if r.status_code == 200:
                return r.json().get("UserData", {})
    except Exception:
        pass
    return None


async def get_play_activity(server_id: str = "0", days: int = 365) -> dict:
    """Fetch Emby activity log and return {item_id: most_recent_stop_date_iso}.

    Used as a fallback when UserData.LastPlayedDate is null despite Played=True —
    which happens when items are marked as played via Seerr or manually in the UI
    without going through the Emby player.

    NOTE: the activity log UserId is a short numeric string (e.g. "3"), NOT the
    full UUID from /Users. We therefore store only the most-recent date across
    ALL users per item so callers don't need to match user ID formats.
    """
    from datetime import datetime, timedelta, timezone
    url, key = await get_client(server_id)
    if not url or not key:
        return {}

    min_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result: dict = {}   # {item_id: most_recent_stop_date_iso}
    start  = 0
    limit  = 500

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            while True:
                r = await client.get(
                    f"{url}/System/ActivityLog/Entries",
                    headers=_auth(key),
                    params={
                        "minDate":    min_date,
                        "hasUserId":  "true",
                        "limit":      limit,
                        "startIndex": start,
                    },
                )
                if r.status_code != 200:
                    break
                body  = r.json()
                items = body.get("Items", [])
                for entry in items:
                    if entry.get("Type") not in ("playback.stop",):
                        continue
                    item_id = str(entry.get("ItemId") or "")
                    date    = entry.get("Date") or ""
                    if not item_id or not date:
                        continue
                    # Keep the most recent stop date across ALL users for this item
                    if item_id not in result or date > result[item_id]:
                        result[item_id] = date
                total = body.get("TotalRecordCount", 0)
                start += len(items)
                if not items or start >= total:
                    break
    except Exception as e:
        logger.warning("get_play_activity error: %s", e)

    return result


async def delete_item(item_id: str, server_id: str = "0") -> bool:
    """Delete an item from the media server. Removes the hardlink but not the physical file."""
    url, key = await get_client(server_id)
    if not url or not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await http_retry(lambda: client.delete(f"{url}/Items/{item_id}", headers=_auth(key)))
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_item error: {e}")
        return False
