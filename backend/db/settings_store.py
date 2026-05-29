# backend/db/settings_store.py
"""Settings cache and CRUD — all reads are served from an in-process TTL cache."""
import time as _time

from .utils import DB_PATH
from .engine import get_db
from .encryption import SENSITIVE_KEYS, _decrypt_value, _encrypt_value

# Default settings — written ONCE at first init (INSERT OR IGNORE)
DEFAULT_SETTINGS = {
    "emby_leaving_soon_collection": "",
    "emby_leaving_soon_days": "30",
    "emby_leaving_soon_overlay": "false",
    "media_server_type": "",           # "" | "emby" | "jellyfin" | "unknown"
    "media_servers": "[]",             # JSON array of server configs (encrypted)
    "radarr_url": "",
    "radarr_api_key": "",
    "sonarr_url": "",
    "sonarr_api_key": "",
    "seerr_url": "",
    "seerr_api_key": "",
    "seerr_external_url": "",
    "qbit_url": "",
    "qbit_proxy_url": "",
    "qbit_user": "",
    "qbit_password": "",
    "qbit_action": "tag_only",  # tag_only | delete_torrent
    "qbit_tag": "Supprimé-Hygie",
    "discord_webhook": "",
    "discord_webhook_alerts": "",
    "discord_notif_thresholds": "7,1",
    "discord_alert_deletion_error": "false",
    "discord_alert_deletion_error_mention": "",
    "discord_alert_deletion_error_msg": "",
    "discord_alert_scan_failure": "false",
    "discord_alert_scan_failure_mention": "",
    "discord_alert_scan_failure_msg": "",
    "discord_alert_seerr_failure": "false",
    "discord_alert_seerr_failure_mention": "",
    "discord_alert_seerr_failure_msg": "",
    "discord_alert_error_threshold": "3",
    "max_parallel_library_scans": "3",
    "dry_run": "false",
    "scan_interval_minutes": "360",            # 6h par défaut
    "deletion_check_interval_minutes": "60",   # 1h par défaut
    "log_level": "INFO",
    "deleted_retention_days": "90",
    "log_retention_days": "14",
    "job_history_retention_days": "90",
    "ui_language": "fr",
    "backup_path": "/app/data/backups",
    "backup_interval_hours": "24",
    "backup_retention_count": "7",
    "backup_enabled": "true",
    "plex_webhook_secret": "",
    "plex_tv_token": "",
    "plex_overlay_enabled": "false",
}

# ─── Settings cache ───────────────────────────────────────────────────────────
# Settings change rarely (user action only). Loading all of them in one query
# and caching for TTL seconds removes dozens of DB opens per scan/deletion cycle.
# Sensitive values are stored encrypted in the cache; decryption happens on read.
_settings_cache: dict[str, str] = {}
_settings_cache_ts: float = 0.0
_SETTINGS_CACHE_TTL: float = 30.0  # seconds


def _invalidate_settings_cache() -> None:
    global _settings_cache_ts
    _settings_cache_ts = 0.0


# ─── Settings ─────────────────────────────────────────────────────────────────
async def get_setting(key: str) -> str:
    global _settings_cache, _settings_cache_ts
    now = _time.monotonic()
    if not _settings_cache or now - _settings_cache_ts >= _SETTINGS_CACHE_TTL:
        async with get_db() as db:
            rows = await db.fetch_all("SELECT key, value FROM settings")
            _settings_cache = {r["key"]: r["value"] for r in rows}
        _settings_cache_ts = now
    raw = _settings_cache.get(key, "")
    return _decrypt_value(raw) if key in SENSITIVE_KEYS else raw


async def set_setting(key: str, value: str) -> None:
    stored = _encrypt_value(value) if key in SENSITIVE_KEYS else value
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, stored)
        )
        await db.commit()
    _invalidate_settings_cache()


async def get_bool_setting(key: str, default: bool = False) -> bool:
    """Read a setting and return it as a boolean ('true'/'1' → True)."""
    v = (await get_setting(key) or "").lower().strip()
    if not v:
        return default
    return v in ("true", "1", "yes", "on")


async def get_int_setting(key: str, default: int = 0) -> int:
    """Read a setting and return it as an integer."""
    v = (await get_setting(key) or "").strip()
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


async def get_all_settings() -> dict:
    async with get_db() as db:
        rows = await db.fetch_all("SELECT key, value FROM settings")
        return {r["key"]: (_decrypt_value(r["value"]) if r["key"] in SENSITIVE_KEYS else r["value"]) for r in rows}
