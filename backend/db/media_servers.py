# backend/db/media_servers.py
"""Media-server list persistence with a 30-second in-process TTL cache."""
import json
import logging
import time

from .utils import DB_PATH
from .engine import get_db
from .encryption import _decrypt_value, _encrypt_value
from .settings_store import _invalidate_settings_cache

logger = logging.getLogger(__name__)

# ─── Media servers helpers ────────────────────────────────────────────────────
_ms_cache = None
_ms_cache_ts: float = 0.0
_MS_CACHE_TTL: float = 30.0  # seconds — invalidated immediately on save


def _invalidate_media_servers_cache() -> None:
    global _ms_cache_ts
    _ms_cache_ts = 0.0


async def get_media_servers() -> list:
    """Return the parsed (decrypted) media_servers list.
    Cached for 30 seconds to avoid a DB open on every HTTP call to Emby/Jellyfin.
    Never raises.
    """
    global _ms_cache, _ms_cache_ts
    now = time.monotonic()
    if _ms_cache is not None and now - _ms_cache_ts < _MS_CACHE_TTL:
        return _ms_cache
    try:
        async with get_db() as db:
            row = await db.fetch_one("SELECT value FROM settings WHERE key='media_servers'")
            if not row or not row["value"]:
                _ms_cache, _ms_cache_ts = [], now
                return []
            raw = _decrypt_value(row["value"])
            result = json.loads(raw) if raw else []
            _ms_cache, _ms_cache_ts = result, now
            return result
    except Exception:
        return _ms_cache if _ms_cache is not None else []


async def save_media_servers(servers: list) -> None:
    """Persist the media_servers list (encrypts if key configured). Invalidates cache."""
    global _ms_cache, _ms_cache_ts
    raw = json.dumps(servers)
    stored = _encrypt_value(raw)
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("media_servers", stored)
        )
        await db.commit()
    # Invalidate both caches immediately so next reads reflect the new state
    _ms_cache, _ms_cache_ts = servers, time.monotonic()
    _invalidate_settings_cache()
