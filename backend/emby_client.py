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

from .database import get_setting, set_setting, get_media_servers, save_media_servers, TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG, http_retry

logger = logging.getLogger(__name__)


def _auth(key: str) -> dict:
    """Return the X-Emby-Token header dict for a given API key."""
    return {"X-Emby-Token": key}


async def get_client(server_id: str = "0") -> Tuple[str, str]:
    """Return (url, api_key) for the given server_id.
    Falls back to legacy emby_url/emby_api_key if media_servers is empty.
    """
    from .database import _decrypt_value
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
    from .database import _decrypt_value
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
                servers = await get_media_servers()
                updated = False
                for s in servers:
                    if str(s.get("id", "")) == str(server_id):
                        s["type"] = server_type
                        updated = True
                if updated:
                    await save_media_servers(servers)
                await set_setting("media_server_type", server_type)
                return True, label, server_type
            return False, f"HTTP {r.status_code}", ""
    except Exception as e:
        return False, str(e), ""


async def get_libraries() -> List[dict]:
    """Return all Emby libraries."""
    url, key = await get_client()
    if not url or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
            r = await client.get(f"{url}/Library/MediaFolders", headers=_auth(key))
            if r.status_code == 200:
                return r.json().get("Items", [])
    except Exception as e:
        logger.warning(f"get_libraries error: {e}")
    return []


async def get_users(server_id: str = "0") -> List[dict]:
    url, key = await get_client(server_id)
    if not url or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await client.get(f"{url}/Users", headers=_auth(key))
            if r.status_code == 200:
                return r.json()
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
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            r = await http_retry(lambda: client.get(f"{url}/Items", headers=_auth(key), params=params))
            if r.status_code == 200:
                data = r.json()
                return data.get("Items", []), data.get("TotalRecordCount", 0)
    except Exception as e:
        logger.warning(f"get_items_in_library error: {e}")
    return [], 0


async def get_library_user_data(user_id: str, library_id: str, server_id: str = "0") -> dict:
    """Return {emby_item_id: UserData} for all items in a library for one user.

    One HTTP request per user per library, replacing per-item get_user_data calls.
    """
    url, key = await get_client(server_id)
    if not url or not key:
        return {}
    params = {
        "ParentId": library_id,
        "Fields": "UserData",
        "Recursive": "true",
        "Limit": 100000,
        "IncludeItemTypes": "Movie,Episode",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
            r = await http_retry(lambda: client.get(f"{url}/Users/{user_id}/Items", headers=_auth(key), params=params))
            if r.status_code == 200:
                return {
                    item["Id"]: item.get("UserData") or {}
                    for item in r.json().get("Items", [])
                }
    except Exception as e:
        logger.warning(f"get_library_user_data error: {e}")
    return {}


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
