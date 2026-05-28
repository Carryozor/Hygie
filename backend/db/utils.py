# backend/db/utils.py
"""DB-layer constants and pure utilities — no DB access."""
import asyncio
import logging
import os
import re as _re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/app/data/hygie.db")

# ─── Status constants ─────────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_DELETED = "deleted"
STATUS_ERROR   = "error"

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


def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (with or without trailing Z) to an aware datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def http_retry(coro_factory, *, retries: int = 3, backoff: float = 1.0):
    """Execute an async callable with exponential backoff on transient errors.

    coro_factory: zero-arg async callable that performs one HTTP attempt.
    Retries on httpx.TimeoutException, httpx.ConnectError, and
    httpx.RemoteProtocolError.
    Raises on exhaustion or non-transient errors (4xx, logic errors).

    Example:
        result = await http_retry(lambda: client.get(url, headers=h))
    """
    import httpx as _httpx
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            return await coro_factory()
        except (_httpx.TimeoutException, _httpx.ConnectError, _httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc
