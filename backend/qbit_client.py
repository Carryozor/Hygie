"""
qBittorrent client — Gluetun compatible (persistent SID session).

Public API:
  test_qbit()                    — (bool, message)
  qbit_find_by_path(path)        — torrent hash matching a file path
  qbit_add_tag(hash, tag)        — add a tag to a torrent
  qbit_delete_torrent(hash, ...)  — remove torrent (+files optional)
"""
import asyncio
import logging
from typing import Optional

import httpx

from .database import get_setting, TIMEOUT_MEDIUM

logger = logging.getLogger(__name__)

# Persistent SID cookie — protected by a lock to prevent concurrent re-logins
_sid_cookie: Optional[str] = None
_sid_lock = asyncio.Lock()


async def _login(client: httpx.AsyncClient, url: str, user: str, password: str) -> bool:
    """Authenticate to qBittorrent and store the SID cookie."""
    global _sid_cookie
    try:
        r = await client.post(
            f"{url}/api/v2/auth/login",
            data={"username": user, "password": password},
            headers={"Referer": url},
        )
        if r.status_code == 200 and r.text.strip() == "Ok.":
            # Extract SID cookie
            cookie = r.cookies.get("SID")
            if cookie:
                _sid_cookie = cookie
                return True
    except Exception as e:
        logger.warning(f"qbit login: {e}")
    return False


async def _request(method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    """Make an authenticated qBittorrent API request.

    Uses qbit_proxy_url (QUI) when configured, falls back to qbit_url (direct).
    """
    global _sid_cookie
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    direct_url = (await get_setting("qbit_url") or "").rstrip("/")
    url = proxy_url or direct_url
    user = await get_setting("qbit_user") or ""
    password = await get_setting("qbit_password") or ""

    if not url:
        return None

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
        cookies = {"SID": _sid_cookie} if _sid_cookie else {}
        try:
            r = await client.request(method, f"{url}{path}", cookies=cookies, **kwargs)
            if r.status_code == 403:
                async with _sid_lock:
                    # Re-check inside lock — another coroutine may have renewed already
                    if user and password and await _login(client, url, user, password):
                        cookies = {"SID": _sid_cookie}
                        r = await client.request(
                            method, f"{url}{path}", cookies=cookies, **kwargs
                        )
            return r
        except Exception as e:
            logger.warning(f"qbit request {path}: {e}")
            return None


async def test_qbit() -> tuple[bool, str]:
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    direct_url = (await get_setting("qbit_url") or "").rstrip("/")
    active_url = proxy_url or direct_url
    if not active_url:
        return False, "Non configuré"
    r = await _request("GET", "/api/v2/app/version")
    if r is None:
        return False, "Connexion impossible"
    if r.status_code == 200:
        is_proxy = bool(proxy_url) or "proxy" in direct_url.lower()
        via = " (via proxy QUI)" if is_proxy else ""
        return True, f"qBittorrent {r.text.strip()}{via}"
    return False, f"HTTP {r.status_code}"


async def qbit_find_by_path(file_path: str) -> Optional[str]:
    """Find a torrent hash matching the given file path."""
    if not file_path:
        return None
    r = await _request("GET", "/api/v2/torrents/info")
    if r is None or r.status_code != 200:
        return None
    try:
        for torrent in r.json():
            save_path = torrent.get("save_path") or ""
            content_path = torrent.get("content_path") or ""
            name = torrent.get("name") or ""
            if (
                file_path == content_path
                or file_path.startswith(save_path + "/")
                or (name and name in file_path)
            ):
                return (torrent.get("hash") or "").lower()
    except Exception as e:
        logger.debug(f"qbit_find_by_path parse: {e}")
    return None


async def qbit_add_tag(torrent_hash: str, tag: str) -> bool:
    """Add a tag to a torrent."""
    if not torrent_hash or not tag:
        return False
    # Ensure the tag exists
    await _request("POST", "/api/v2/torrents/createTags", data={"tags": tag})
    r = await _request(
        "POST",
        "/api/v2/torrents/addTags",
        data={"hashes": torrent_hash, "tags": tag},
    )
    return r is not None and r.status_code in (200, 204)


async def qbit_delete_torrent(torrent_hash: str, delete_files: bool = True) -> bool:
    """Delete a torrent (and optionally its files)."""
    if not torrent_hash:
        return False
    r = await _request(
        "POST",
        "/api/v2/torrents/delete",
        data={
            "hashes": torrent_hash,
            "deleteFiles": "true" if delete_files else "false",
        },
    )
    return r is not None and r.status_code in (200, 204)
