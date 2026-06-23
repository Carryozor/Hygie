"""Shared helpers used by Radarr, Sonarr, and Seerr clients."""
import json
import logging

from ..db.settings_store import get_setting

logger = logging.getLogger(__name__)


def _arr_auth(key: str) -> dict:
    """Return X-Api-Key header for Radarr/Sonarr."""
    return {"X-Api-Key": key}


async def _get_arr_servers(servers_setting: str, legacy_config_fn, legacy_name: str) -> list[dict]:
    """Return all enabled arr server configs (multi-server setting + legacy single-server fallback).

    Shared by get_radarr_servers/get_sonarr_servers — same lookup shape for both,
    differing only in which setting key and legacy single-server config to use.
    """
    servers = []
    raw = await get_setting(servers_setting) or "[]"
    try:
        multi = json.loads(raw) if isinstance(raw, str) else raw
        servers = [s for s in (multi or []) if s.get("enabled", True) and s.get("url") and s.get("api_key")]
    except Exception:
        pass
    if not servers:
        url, key = await legacy_config_fn()
        if url and key:
            servers = [{"id": "legacy", "name": legacy_name, "url": url, "api_key": key, "enabled": True}]
    return servers


def _path_matches(file_path: str, item_path: str, folder: str) -> bool:
    """Return True if file_path matches an arr item (exact path or inside folder)."""
    return bool(item_path and item_path == file_path) or bool(
        folder and file_path.startswith(folder.rstrip("/") + "/")
    )
