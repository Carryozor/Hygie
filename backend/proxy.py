"""Image proxy endpoint with SSRF-protection whitelist."""
import asyncio
import logging
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .db.settings_store import get_setting
from .db.media_servers import get_media_servers
from .db.utils import sanitize_url

logger = logging.getLogger(__name__)

router = APIRouter()

# Emby/Jellyfin item IDs are hex strings (32 chars) or short alphanumeric IDs
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ─── Image proxy whitelist cache (TTL: 5 min) ────────────────────────────────
_proxy_whitelist: set = set()
_proxy_whitelist_ts: float = 0.0
_proxy_whitelist_lock = asyncio.Lock()
_PROXY_WHITELIST_TTL = 300


def invalidate_proxy_whitelist() -> None:
    """Force whitelist rebuild on next request (call after service URL changes)."""
    global _proxy_whitelist_ts
    _proxy_whitelist_ts = 0.0


async def _get_proxy_whitelist() -> set:
    global _proxy_whitelist, _proxy_whitelist_ts
    if _proxy_whitelist and time.time() - _proxy_whitelist_ts < _PROXY_WHITELIST_TTL:
        return _proxy_whitelist
    async with _proxy_whitelist_lock:
        # Double-check inside lock to avoid thundering herd
        if _proxy_whitelist and time.time() - _proxy_whitelist_ts < _PROXY_WHITELIST_TTL:
            return _proxy_whitelist
        allowed = {
            "image.tmdb.org",
            "artworks.thetvdb.com",
            "thetvdb.com",
            "fanart.tv",
            "assets.fanart.tv",
        }
        # Legacy single-server URLs (emby* covered by media_servers loop below)
        for setting_key in ("radarr_url", "sonarr_url"):
            s = await get_setting(setting_key)
            if s:
                try:
                    h = (urlparse(s).hostname or "").lower()
                    if h:
                        allowed.add(h)
                except Exception:
                    pass
        # Multi-server: add all configured server URLs and external URLs
        for srv in await get_media_servers():
            for field in ("url", "ext_url"):
                u = (srv.get(field) or "").strip()
                if u:
                    try:
                        h = (urlparse(u).hostname or "").lower()
                        if h:
                            allowed.add(h)
                    except Exception:
                        pass
        _proxy_whitelist = allowed
        _proxy_whitelist_ts = time.time()
        return allowed


# ─── Image proxy ──────────────────────────────────────────────────────────────
@router.get("/api/proxy/image")
async def proxy_image(request: Request):
    """
    Proxy images from configured services (Emby/Radarr/Sonarr) and TMDB CDN.

    SSRF protection: only hosts matching configured services or known image CDNs.
    No auth requirement — img src can't send Bearer tokens.
    """
    target_url = request.query_params.get("url", "")
    if not target_url:
        return Response(status_code=400)

    try:
        parsed = urlparse(target_url)
        if parsed.scheme not in ("http", "https"):
            return Response(status_code=400)

        host = (parsed.hostname or "").lower()

        allowed = await _get_proxy_whitelist()

        if host not in allowed:
            logger.warning(f"Proxy: host {host!r} not in whitelist")
            return Response(status_code=403, content=f"Host not allowed: {host}")

        _PROXY_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — guard against memory exhaustion
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            async with client.stream("GET", target_url) as r:
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "image/jpeg")
                    if not ct.startswith("image/"):
                        return Response(status_code=415)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in r.aiter_bytes(65536):
                        total += len(chunk)
                        if total > _PROXY_MAX_BYTES:
                            logger.warning(
                                f"Proxy: response too large (>{_PROXY_MAX_BYTES // 1024 // 1024} MB)"
                                f" for {sanitize_url(target_url)[:80]}"
                            )
                            return Response(status_code=413)
                        chunks.append(chunk)
                    return Response(
                        content=b"".join(chunks),
                        media_type=ct,
                        headers={"Cache-Control": "public, max-age=3600"},
                    )
                # Don't warn on 500 (Emby returns this for items without posters)
                if r.status_code != 500:
                    logger.warning(
                        f"Proxy: upstream HTTP {r.status_code} for {sanitize_url(target_url)[:80]}"
                    )
    except Exception as e:
        logger.error(f"Proxy error: {e}")
    return Response(status_code=404)


# ─── Poster proxy — serves Emby item images without exposing the API key ──────
@router.get("/api/proxy/poster/{server_id}/{item_id}")
async def proxy_poster(server_id: str, item_id: str):
    """Serve an Emby/Jellyfin item poster image.

    The API key never appears in the client-facing URL: it is retrieved server-side
    from the encrypted settings store and injected into the upstream request.
    No auth required — img src elements cannot send Bearer tokens.
    """
    if not _ITEM_ID_RE.match(item_id):
        return Response(status_code=400)

    from .db.media_servers import get_media_servers
    servers = await get_media_servers()
    srv = next((s for s in servers if str(s.get("id")) == server_id), None)
    if not srv:
        return Response(status_code=404)

    url = (srv.get("url") or "").rstrip("/")
    key = srv.get("api_key") or ""
    if not url or not key:
        return Response(status_code=404)

    target = f"{url}/Items/{item_id}/Images/Primary?maxHeight=300"
    headers: dict = {}
    if key:
        headers["X-Emby-Authorization"] = (
            f'MediaBrowser Token="{key}", Client="Hygie", Device="Proxy"'
        )

    _PROXY_MAX_BYTES = 10 * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            async with client.stream("GET", target, headers=headers) as r:
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "image/jpeg")
                    if not ct.startswith("image/"):
                        return Response(status_code=415)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in r.aiter_bytes(65536):
                        total += len(chunk)
                        if total > _PROXY_MAX_BYTES:
                            return Response(status_code=413)
                        chunks.append(chunk)
                    return Response(
                        content=b"".join(chunks),
                        media_type=ct,
                        headers={"Cache-Control": "public, max-age=3600"},
                    )
    except Exception as e:
        logger.error("proxy_poster error: %s", e)
    return Response(status_code=404)
