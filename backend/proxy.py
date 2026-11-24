"""Image proxy endpoint with SSRF-protection whitelist."""
import asyncio
import logging
import re
import time
from collections import deque
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .db.settings_store import get_setting
from .db.media_servers import get_media_servers
from .auth import get_client_ip
from .db.utils import sanitize_url, is_loopback_or_link_local

logger = logging.getLogger(__name__)

router = APIRouter()

# Emby/Jellyfin item IDs are hex strings (32 chars) or short alphanumeric IDs
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ─── Image proxy whitelist cache (TTL: 5 min) ────────────────────────────────
# Whitelist entries are (hostname, port) tuples — matching on hostname alone
# would allow port-scanning a whitelisted internal host through the proxy.
_proxy_whitelist: set = set()
_proxy_whitelist_ts: float = 0.0
_proxy_whitelist_lock = asyncio.Lock()
_PROXY_WHITELIST_TTL = 300
_PROXY_MAX_REDIRECTS = 3


def _add_url_to_whitelist(allowed: set, url: str) -> None:
    """Add (host, port) of a configured service URL to the whitelist."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        allowed.add((host, port))
    except Exception:
        pass


def _plex_headers(url: str, servers: list) -> dict:
    """X-Plex-Token header for a URL that targets a configured Plex server, else {}.

    Matched on (host, port) of the exact URL being requested, so the token is
    never attached to a redirect hop that leaves the Plex server.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        for srv in servers:
            if (srv.get("type") or "").lower() != "plex" or not srv.get("api_key"):
                continue
            sp = urlparse(srv.get("url") or "")
            if (sp.hostname or "").lower() == host and (sp.port or (443 if sp.scheme == "https" else 80)) == port:
                return {"X-Plex-Token": srv["api_key"]}
    except Exception:
        pass
    return {}


def _is_url_allowed(url: str, allowed: set) -> bool:
    """True if url targets a whitelisted (host, port) over http(s)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return (host, port) in allowed
    except Exception:
        return False


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
        allowed: set = set()
        for cdn_host in (
            "image.tmdb.org",
            "artworks.thetvdb.com",
            "thetvdb.com",
            "fanart.tv",
            "assets.fanart.tv",
        ):
            allowed.add((cdn_host, 443))
            allowed.add((cdn_host, 80))
        # Legacy single-server URLs (emby* covered by media_servers loop below)
        for setting_key in ("radarr_url", "sonarr_url"):
            s = await get_setting(setting_key)
            if s:
                _add_url_to_whitelist(allowed, s)
        # Multi-server: add all configured server URLs and external URLs
        for srv in await get_media_servers():
            for field in ("url", "ext_url"):
                u = (srv.get(field) or "").strip()
                if u:
                    _add_url_to_whitelist(allowed, u)
        _proxy_whitelist = allowed
        _proxy_whitelist_ts = time.time()
        return allowed


# ─── Abuse limits (unauthenticated endpoints) ─────────────────────────────────
# Global cap on concurrent upstream fetches and a per-IP sliding-window budget.
# The budget is deliberately generous: a poster grid fires dozens of requests
# at once, and in deployments without HYGIE_TRUST_PROXY every visitor shares
# the proxy's IP. In-memory and per-worker by design (cheap, no DB write per
# image); it bounds abuse, it is not an auth control.
_PROXY_MAX_CONCURRENCY = 8
_PROXY_RATE_MAX = 600
_PROXY_RATE_WINDOW = 60.0
_proxy_semaphore = asyncio.Semaphore(_PROXY_MAX_CONCURRENCY)
_proxy_hits: dict = {}


def _proxy_rate_limited(request: Request) -> bool:
    """Record this request for the client IP; True when over budget.

    Inactive unless HYGIE_TRUSTED_PROXIES / HYGIE_TRUST_PROXY is set: behind a tunnel without it every
    visitor shares the tunnel's IP, so one abusive client would exhaust the
    budget for everyone (including the admin's poster grid). The concurrency
    cap still bounds upstream load in that case.
    """
    from . import auth as _auth
    if not _auth.proxy_trust_configured():
        return False
    now = time.monotonic()
    cutoff = now - _PROXY_RATE_WINDOW
    ip = get_client_ip(request)
    hits = _proxy_hits.setdefault(ip, deque())
    while hits and hits[0] <= cutoff:
        hits.popleft()
    if len(hits) >= _PROXY_RATE_MAX:
        return True
    hits.append(now)
    if len(_proxy_hits) > 1024:  # drop idle clients so the dict stays bounded
        for k in [k for k, v in _proxy_hits.items() if not v or v[-1] <= cutoff]:
            del _proxy_hits[k]
    return False


# ─── Image proxy ──────────────────────────────────────────────────────────────
@router.get("/api/proxy/image")
async def proxy_image(request: Request):
    """Rate-limited, concurrency-capped wrapper around the image fetch."""
    if _proxy_rate_limited(request):
        return Response(status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"})
    async with _proxy_semaphore:
        return await _fetch_proxied_image(request)


async def _fetch_proxied_image(request: Request):
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

        allowed = await _get_proxy_whitelist()

        if not _is_url_allowed(target_url, allowed):
            host = (parsed.hostname or "").lower()
            logger.warning(f"Proxy: host {host!r} not in whitelist")
            return Response(status_code=403, content=f"Host not allowed: {host}")

        host = (parsed.hostname or "").lower()
        if await is_loopback_or_link_local(host):
            logger.warning("Proxy: DNS rebinding attempt detected for host %r", host)
            return Response(status_code=403)

        _PROXY_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — guard against memory exhaustion
        # Redirects are followed manually so every hop is re-validated against
        # the whitelist — follow_redirects=True would let a whitelisted host
        # redirect the proxy to an internal/arbitrary target (SSRF).
        url = target_url
        servers = await get_media_servers()
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for _ in range(_PROXY_MAX_REDIRECTS + 1):
                async with client.stream("GET", url, headers=_plex_headers(url, servers)) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        from urllib.parse import urljoin
                        url = urljoin(url, r.headers.get("location", ""))
                        if not _is_url_allowed(url, allowed):
                            logger.warning(
                                f"Proxy: redirect target not in whitelist: {sanitize_url(url)[:80]}"
                            )
                            return Response(status_code=403)
                        # Re-run the same DNS-rebinding check as the initial request —
                        # a whitelisted host's DNS could be rebound between the first
                        # request and this redirect being followed.
                        redirect_host = (urlparse(url).hostname or "").lower()
                        if await is_loopback_or_link_local(redirect_host):
                            logger.warning(
                                "Proxy: DNS rebinding attempt detected on redirect for host %r",
                                redirect_host,
                            )
                            return Response(status_code=403)
                        continue
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
                                    f" for {sanitize_url(url)[:80]}"
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
                            f"Proxy: upstream HTTP {r.status_code} for {sanitize_url(url)[:80]}"
                        )
                    return Response(status_code=404)
            logger.warning(
                f"Proxy: exceeded max redirects ({_PROXY_MAX_REDIRECTS}) "
                f"for {sanitize_url(target_url)[:80]}"
            )
    except Exception as e:
        logger.error(f"Proxy error: {e}")
    return Response(status_code=404)


# ─── Poster proxy — serves Emby item images without exposing the API key ──────
@router.get("/api/proxy/poster/{server_id}/{item_id}")
async def proxy_poster(server_id: str, item_id: str, request: Request):
    """Rate-limited, concurrency-capped wrapper around the poster fetch."""
    if _proxy_rate_limited(request):
        return Response(status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"})
    async with _proxy_semaphore:
        return await _fetch_proxied_poster(server_id, item_id)


async def _fetch_proxied_poster(server_id: str, item_id: str):
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
        # No redirect following: the target is built from the configured server
        # URL — a redirect elsewhere would bypass that trust boundary.
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
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
