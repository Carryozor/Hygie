"""Async retry with exponential backoff for transient network errors.

Usage:
    from .retry import with_retry

    async def _do_call():
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            return await c.get(url, headers=headers)

    result = await with_retry(_do_call, label="radarr.build_cache")
"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Exceptions indicating transient network problems (worth retrying)
_RETRYABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def with_retry(fn, *args, retries: int = 3, base_delay: float = 1.0, label: str = "", **kwargs):
    """Call fn(*args, **kwargs) up to `retries` times on transient httpx errors.

    - Retries only on _RETRYABLE exceptions (network/timeout)
    - Non-retryable exceptions propagate immediately
    - Raises last exception if all retries exhausted
    """
    last_exc = None
    for attempt in range(retries):
        try:
            return await fn(*args, **kwargs)
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "arr retry %d/%d [%s]: %s — retrying in %.1fs",
                    attempt + 1, retries, label, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("arr retry exhausted [%s]: %s", label, exc)
        except Exception:
            raise  # non-retryable — propagate immediately
    raise last_exc
