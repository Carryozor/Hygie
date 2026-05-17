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

from .database import get_setting

logger = logging.getLogger(__name__)


async def get_client() -> Tuple[str, str]:
    """Return (url, api_key) from settings. Both stripped of trailing slashes."""
    url = (await get_setting("emby_url") or "").rstrip("/")
    key = await get_setting("emby_api_key") or ""
    return url, key


async def test_connection() -> Tuple[bool, str]:
    url, key = await get_client()
    if not url or not key:
        return False, "URL ou clé API manquante"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/System/Info", params={"api_key": key})
            if r.status_code == 200:
                info = r.json()
                return True, f"Emby {info.get('Version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def get_libraries() -> List[dict]:
    """Return all Emby libraries (folders + collection folders)."""
    url, key = await get_client()
    if not url or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{url}/Library/MediaFolders", params={"api_key": key}
            )
            if r.status_code == 200:
                return r.json().get("Items", [])
    except Exception as e:
        logger.warning(f"get_libraries error: {e}")
    return []


async def get_users() -> List[dict]:
    url, key = await get_client()
    if not url or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/Users", params={"api_key": key})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"get_users error: {e}")
    return []


async def get_items_in_library(
    library_id: str, limit: int = 500, start: int = 0
) -> Tuple[List[dict], int]:
    """Return (items, total_count) for items in a library."""
    url, key = await get_client()
    if not url or not key:
        return [], 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{url}/Items",
                params={
                    "api_key": key,
                    "ParentId": library_id,
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Episode",
                    "Fields": "Path,DateCreated,ProviderIds",
                    "Limit": limit,
                    "StartIndex": start,
                },
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("Items", []), data.get("TotalRecordCount", 0)
    except Exception as e:
        logger.warning(f"get_items_in_library error: {e}")
    return [], 0


async def get_user_data(user_id: str, item_id: str) -> Optional[dict]:
    """Get a user's UserData for a specific item (Played, PlayCount, LastPlayedDate)."""
    url, key = await get_client()
    if not url or not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{url}/Users/{user_id}/Items/{item_id}",
                params={"api_key": key, "Fields": "UserData"},
            )
            if r.status_code == 200:
                return r.json().get("UserData", {})
    except Exception:
        pass
    return None


async def delete_item(item_id: str) -> bool:
    """Delete an item from Emby. Removes the hardlink but not the physical file."""
    url, key = await get_client()
    if not url or not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.delete(
                f"{url}/Items/{item_id}", params={"api_key": key}
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_item error: {e}")
        return False
