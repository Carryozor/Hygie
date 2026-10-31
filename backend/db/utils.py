# backend/db/utils.py
"""DB-layer constants and pure utilities — no DB access."""
import asyncio
import ipaddress
import logging
import os
import re as _re
import socket
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/app/data/hygie.db")

# ─── Status constants ─────────────────────────────────────────────────────────
STATUS_PENDING  = "pending"
STATUS_DELETING = "deleting"
STATUS_DELETED  = "deleted"
STATUS_ERROR    = "error"

# Escape character for SQL LIKE patterns. '!' is used instead of backslash
# because backslash-in-literal semantics differ between SQLite and MariaDB.
LIKE_ESCAPE_CHAR = "!"

# ─── HTTP timeout constants (seconds) ─────────────────────────────────────────
TIMEOUT_SHORT  = 10   # fast API calls (auth, status, single item)
TIMEOUT_MEDIUM = 20   # bulk listing calls (movies, series, torrents)
TIMEOUT_LONG   = 30   # paginated or library-wide calls

_SENSITIVE_PARAMS = _re.compile(r'(?i)(api[_-]?key|token|password|secret)=[^&\s]+')


def now_utc() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def sanitize_url(url: str) -> str:
    """Redact sensitive query parameters from a URL for safe logging."""
    return _SENSITIVE_PARAMS.sub(r'\1=***', url)


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied URL targets a forbidden or unresolvable host."""


def _ip_is_forbidden(ip) -> bool:
    """True for loopback, link-local (incl. cloud metadata), unspecified,
    multicast and reserved addresses. IPv4-mapped IPv6 is unwrapped first so
    ``::ffff:127.0.0.1`` cannot slip through. RFC1918 stays allowed."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_link_local or ip.is_unspecified
        or ip.is_multicast or ip.is_reserved
    )


async def _resolve_ips(hostname: str) -> list:
    """Resolve hostname to ip_address objects. Raises on any failure."""
    host = hostname.strip().strip("[]")
    # Drop an IPv6 zone id ("fe80::1%eth0") — ip_address() rejects it pre-3.9
    # and it never changes the classification.
    host = host.split("%", 1)[0]
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
    )
    if not infos:
        raise OSError(f"no address for {hostname!r}")
    return [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]


async def is_loopback_or_link_local(hostname: str, *, fail_closed: bool = False) -> bool:
    """Return True if hostname must NOT be contacted (SSRF guard).

    Blocks loopback, link-local (cloud metadata 169.254.169.254), unspecified
    (0.0.0.0 / ::), multicast, reserved and IPv4-mapped IPv6 forms. RFC1918
    LAN addresses are NOT blocked — legitimate self-hosted Emby/Radarr/Sonarr
    instances live there.

    A resolution failure means "not blocked" by default (the request itself
    will fail on the same lookup — legacy callers such as the image proxy rely
    on this); pass fail_closed=True to refuse instead. assert_public_or_lan
    always does.
    """
    try:
        return any(_ip_is_forbidden(ip) for ip in await _resolve_ips(hostname))
    except Exception as e:
        logger.debug("SSRF guard: résolution impossible pour %r (%s)", hostname, e)
        return fail_closed


async def assert_public_or_lan(url: str) -> None:
    """Raise UnsafeUrlError unless `url` is http(s) and targets a public or LAN host.

    Single entry point for user-supplied service URLs (settings, test
    endpoints, Seerr sync). Fails closed: an unresolvable host is refused.
    """
    from urllib.parse import urlparse
    try:
        parsed = urlparse((url or "").strip())
        host = parsed.hostname
    except ValueError as e:
        raise UnsafeUrlError("URL invalide") from e
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeUrlError("le schéma doit être http ou https")
    if not host:
        raise UnsafeUrlError("URL sans hôte")
    if await is_loopback_or_link_local(host, fail_closed=True):
        raise UnsafeUrlError(f"hôte non autorisé ou non résolu : {host}")


async def ssrf_guard_hook(request) -> None:
    """httpx request event hook — re-runs on every hop, redirects included.

    Attach to any AsyncClient that follows redirects to an externally-influenced
    URL (poster/image fetches): a trusted host could 30x-redirect the request to
    127.0.0.1 or the cloud metadata endpoint, and httpx would follow it silently.
    Raising here aborts the whole request. RFC1918 LAN stays allowed — see
    is_loopback_or_link_local.
    """
    import httpx
    host = (request.url.host or "").lower()
    if host and await is_loopback_or_link_local(host):
        raise httpx.HTTPError(f"SSRF guard: hôte non autorisé : {host}")


async def guarded_image_get(url: str, timeout: float = TIMEOUT_SHORT) -> Optional[bytes]:
    """Fetch image bytes from an externally-influenced URL, SSRF-safe.

    Follows redirects but re-validates every hop via ssrf_guard_hook. Returns the
    body only on a 200 image/* response, else None (SSRF block, non-image, HTTP
    error, network failure). Callers use this for poster/artwork fetches where the
    URL originates from media metadata rather than admin configuration.
    """
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"request": [ssrf_guard_hook]},
        ) as client:
            r = await client.get(url)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except Exception as e:
        logger.debug("guarded_image_get(%s): %s", sanitize_url(url), e)
    return None


def escape_like(text: str) -> str:
    """Escape LIKE wildcards in user input.

    Use with: column LIKE ? ESCAPE '!'  (see LIKE_ESCAPE_CHAR).
    Without this, a search for "100%" matches everything starting with "100".
    """
    return (
        text.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string to a timezone-aware UTC datetime.

    Handles common Emby/Jellyfin date formats:
    - "2024-01-15T10:30:00.0000000Z"  (7-decimal Z)
    - "2024-01-15T10:30:00+00:00"     (explicit offset)
    - "2024-01-15T10:30:00"           (naive — assumed UTC)
    Naive datetimes (no tz info) are treated as UTC so comparisons with
    now_utc() don't raise TypeError.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


async def http_retry(coro_factory, *, retries: int = 3, backoff: float = 1.0, service: str = ""):
    """Execute an async callable with exponential backoff on transient errors.

    coro_factory: zero-arg async callable that performs one HTTP attempt.
    Retries on httpx.TimeoutException, httpx.ConnectError, and
    httpx.RemoteProtocolError.
    Raises on exhaustion or non-transient errors (4xx, logic errors).

    When `service` is set, the whole retry sequence runs through that service's
    circuit breaker (one exhausted sequence = one breaker failure; an OPEN
    breaker raises CircuitOpenError without touching the network).

    Example:
        result = await http_retry(lambda: client.get(url, headers=h), service="emby:0")
    """
    import httpx as _httpx

    async def _run():
        last_exc: Exception = RuntimeError("no attempts")
        for attempt in range(retries):
            try:
                return await coro_factory()
            except (_httpx.TimeoutException, _httpx.ConnectError, _httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt < retries - 1:
                    await asyncio.sleep(backoff * (2 ** attempt))
        raise last_exc

    if not service:
        return await _run()
    from ..arr_clients.circuit_breaker import get_breaker
    return await get_breaker(service).call(_run)
