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
import time
from typing import Optional

import httpx

from .database import get_setting, TIMEOUT_MEDIUM

logger = logging.getLogger(__name__)

# Persistent SID cookie — protected by a lock to prevent concurrent re-logins
_sid_cookie: Optional[str] = None
_sid_lock = asyncio.Lock()

# Alert cooldown: send proxy-fallback Discord alert at most once per hour
_proxy_alert_ts: float = 0.0
_PROXY_ALERT_COOLDOWN = 3600.0


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
            cookie = r.cookies.get("SID")
            if cookie:
                _sid_cookie = cookie
                return True
    except Exception as e:
        logger.warning(f"qbit login: {e}")
    return False


async def _try_url(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    path: str,
    user: str,
    password: str,
    **kwargs,
) -> Optional[httpx.Response]:
    """Attempt a single authenticated request against one qBit URL."""
    global _sid_cookie
    cookies = {"SID": _sid_cookie} if _sid_cookie else {}
    try:
        r = await client.request(method, f"{url}{path}", cookies=cookies, **kwargs)
        if r.status_code == 403:
            async with _sid_lock:
                if user and password and await _login(client, url, user, password):
                    cookies = {"SID": _sid_cookie}
                    r = await client.request(method, f"{url}{path}", cookies=cookies, **kwargs)
        return r
    except Exception as e:
        logger.warning(f"qbit {method} {path} @ {url}: {e}")
        return None


async def _alert_proxy_fallback() -> None:
    """Send a Discord alert when the QUI proxy is unreachable and direct mode is used."""
    global _proxy_alert_ts
    now = time.monotonic()
    if now - _proxy_alert_ts < _PROXY_ALERT_COOLDOWN:
        return
    _proxy_alert_ts = now
    try:
        from .discord_client import send_alert
        await send_alert(
            "qBittorrent : proxy QUI injoignable",
            "Le proxy QUI n'a pas répondu. Hygie utilise la connexion directe en fallback.\n"
            "Vérifiez l'état du proxy.",
            level="warning",
        )
    except Exception as e:
        logger.debug(f"proxy alert send error: {e}")


async def _request(method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    """Make an authenticated qBittorrent API request.

    Tries the proxy (QUI) first if configured; falls back to direct URL + credentials
    and sends a Discord alert when the proxy is unreachable.
    """
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    direct_url = (await get_setting("qbit_url") or "").rstrip("/")
    user = await get_setting("qbit_user") or ""
    password = await get_setting("qbit_password") or ""

    if not proxy_url and not direct_url:
        return None

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
        # Try proxy first
        if proxy_url:
            r = await _try_url(client, proxy_url, method, path, user, password, **kwargs)
            if r is not None:
                return r
            # Proxy failed — fall back to direct if available
            if direct_url and direct_url != proxy_url:
                logger.warning("qbit proxy unreachable, falling back to direct URL")
                r = await _try_url(client, direct_url, method, path, user, password, **kwargs)
                if r is not None:
                    await _alert_proxy_fallback()
                    return r
            return None

        # No proxy configured — direct only
        return await _try_url(client, direct_url, method, path, user, password, **kwargs)


async def test_qbit() -> tuple[bool, str]:
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    direct_url = (await get_setting("qbit_url") or "").rstrip("/")

    if not proxy_url and not direct_url:
        return False, "Non configuré"

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
        user = await get_setting("qbit_user") or ""
        password = await get_setting("qbit_password") or ""

        # Test proxy
        if proxy_url:
            r = await _try_url(client, proxy_url, "GET", "/api/v2/app/version", user, password)
            if r is not None and r.status_code == 200:
                return True, f"qBittorrent {r.text.strip()} (via proxy QUI)"
            proxy_status = f"proxy injoignable" if r is None else f"proxy HTTP {r.status_code}"
        else:
            proxy_status = None

        # Test direct
        if direct_url and direct_url != proxy_url:
            r = await _try_url(client, direct_url, "GET", "/api/v2/app/version", user, password)
            if r is not None and r.status_code == 200:
                via = f" (fallback direct — {proxy_status})" if proxy_status else " (direct)"
                return True, f"qBittorrent {r.text.strip()}{via}"
            direct_status = "direct injoignable" if r is None else f"direct HTTP {r.status_code}"
            if proxy_status:
                return False, f"{proxy_status} | {direct_status}"
            return False, direct_status

        if proxy_status:
            return False, proxy_status
        return False, "Connexion impossible"


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
