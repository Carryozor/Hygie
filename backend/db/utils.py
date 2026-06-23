# backend/db/utils.py
"""DB-layer constants and pure utilities — no DB access."""
import asyncio
import ipaddress
import os
import re as _re
import socket
from datetime import datetime, timezone
from typing import Optional

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


async def is_loopback_or_link_local(hostname: str) -> bool:
    """Return True if hostname resolves to a loopback or link-local address.

    Blocks SSRF probes against the host itself (127.0.0.1) and the cloud
    metadata service (169.254.169.254, link-local). RFC1918 LAN addresses
    (192.168.x.x, 10.x.x.x, 172.16-31.x.x) are NOT blocked — legitimate
    self-hosted Emby/Radarr/Sonarr instances commonly live there.
    """
    try:
        loop = asyncio.get_event_loop()
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, 0, socket.SOCK_STREAM),
        )
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_loopback or ip.is_link_local:
                return True
    except Exception:
        pass
    return False


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
