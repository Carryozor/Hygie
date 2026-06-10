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

from .db.settings_store import get_setting
from .db.utils import TIMEOUT_MEDIUM

logger = logging.getLogger(__name__)

# Persistent SID cookie — protected by a lock to prevent concurrent re-logins
_sid_cookie: Optional[str] = None
_sid_lock = asyncio.Lock()

# Alert cooldown: send proxy-fallback Discord alert at most once per hour
_proxy_alert_ts: float = 0.0
_PROXY_ALERT_COOLDOWN = 3600.0


def _extract_sid(cookies) -> Optional[str]:
    """Return the session cookie regardless of name (SID or QBT_SID_<PORT> in v5+)."""
    sid = cookies.get("SID")
    if sid:
        return sid
    return next((v for k, v in cookies.items() if k.startswith("QBT_SID")), None)


async def _login(client: httpx.AsyncClient, url: str, user: str, password: str) -> bool:
    """Authenticate to qBittorrent and store the SID cookie."""
    global _sid_cookie
    try:
        r = await client.post(
            f"{url}/api/v2/auth/login",
            data={"username": user, "password": password},
            headers={"Referer": url},
        )
        # qBit v5+ with bypass-auth returns 204 (empty body); older returns 200 "Ok."
        auth_ok = r.status_code == 204 or (r.status_code == 200 and r.text.strip() == "Ok.")
        if auth_ok:
            cookie = _extract_sid(r.cookies)
            if cookie:
                _sid_cookie = cookie
                return True
            # Bypass-auth may return no cookie — treat as authenticated
            if r.status_code == 204:
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
                    cookies = {"SID": _sid_cookie} if _sid_cookie else {}
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


async def _test_url_fresh(client: httpx.AsyncClient, url: str, user: str, password: str) -> tuple[bool, str]:
    """Test a qBittorrent URL with a fresh login (ignores any cached SID)."""
    try:
        r = await client.post(
            f"{url}/api/v2/auth/login",
            data={"username": user, "password": password},
            headers={"Referer": url},
        )
        # qBit v5+ with bypass-auth returns 204 (empty body); older returns 200 "Ok."
        auth_ok = r.status_code == 204 or (r.status_code == 200 and r.text.strip() == "Ok.")
        if not auth_ok:
            return False, "authentification échouée"
        sid = _extract_sid(r.cookies)
        cookies = {"SID": sid} if sid else {}
        rv = await client.get(f"{url}/api/v2/app/version", cookies=cookies)
        if rv.status_code == 200:
            version = rv.text.strip().lstrip("v")
            return True, f"v{version}"
        return False, f"HTTP {rv.status_code}"
    except Exception as e:
        return False, str(e)


async def test_qui() -> tuple[bool, str]:
    """Test only the QUI proxy URL with a fresh login."""
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    if not proxy_url:
        return False, "Proxy QUI non configuré"
    user = await get_setting("qbit_user") or ""
    password = await get_setting("qbit_password") or ""
    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
        ok, detail = await _test_url_fresh(client, proxy_url, user, password)
        if not ok:
            return False, f"Proxy QUI ❌ ({detail})"
        return True, f"Proxy QUI ✅ (qBit {detail})"


async def test_qbit() -> tuple[bool, str]:
    """Test each configured qBit URL with a fresh login and report both results."""
    proxy_url = (await get_setting("qbit_proxy_url") or "").rstrip("/")
    direct_url = (await get_setting("qbit_url") or "").rstrip("/")

    if not proxy_url and not direct_url:
        return False, "Non configuré"

    user = await get_setting("qbit_user") or ""
    password = await get_setting("qbit_password") or ""
    parts: list[str] = []
    any_ok = False

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as client:
        if proxy_url:
            ok, detail = await _test_url_fresh(client, proxy_url, user, password)
            if ok:
                parts.append(f"Proxy QUI ✅ {detail}")
                any_ok = True
            else:
                parts.append(f"Proxy QUI ❌ ({detail})")

        if direct_url and direct_url != proxy_url:
            ok, detail = await _test_url_fresh(client, direct_url, user, password)
            if ok:
                parts.append(f"Direct ✅ {detail}")
                any_ok = True
            else:
                parts.append(f"Direct ❌ ({detail})")

    msg = " | ".join(parts) if parts else "Connexion impossible"
    return any_ok, msg


async def qbit_find_by_path(file_path: str) -> Optional[str]:
    """Find a torrent hash matching the given file path."""
    if not file_path:
        return None
    r = await _request("GET", "/api/v2/torrents/info")
    if r is None or r.status_code != 200:
        return None
    try:
        for torrent in r.json():
            save_path    = (torrent.get("save_path") or "").rstrip("/")
            content_path = (torrent.get("content_path") or "").rstrip("/")
            name         = torrent.get("name") or ""
            # The torrent's content lives at content_path (file for single-file
            # torrents, folder for multi-file). Fall back to save_path/name for
            # qBittorrent versions that don't expose content_path.
            effective = content_path or (f"{save_path}/{name}" if save_path and name else "")
            if not effective:
                continue
            # Exact file match, or the arr file is inside the torrent's folder.
            # Substring matching on the torrent NAME is deliberately NOT used —
            # it could target the wrong torrent for tagging/deletion.
            if file_path == effective or file_path.startswith(effective + "/"):
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
