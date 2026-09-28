"""Shared helpers used by Radarr, Sonarr, and Seerr clients."""
import json
import logging
from typing import Awaitable, Callable, Optional, TypeVar

import httpx

from ..db.settings_store import get_setting
from ..db.utils import TIMEOUT_SHORT

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _arr_auth(key: str) -> dict:
    """Return X-Api-Key header for Radarr/Sonarr."""
    return {"X-Api-Key": key}


async def _resolve_arr_creds(url: str, key: str, config_fn: Callable[[], Awaitable[tuple[str, str]]]) -> tuple[str, str]:
    """Fall back to the module's default (url, key) config when either is missing."""
    if not url or not key:
        return await config_fn()
    return url, key


def _extract_poster_url(images: list[dict]) -> str:
    """Return the first public poster remoteUrl from an arr images list."""
    for img in images:
        if img.get("coverType") == "poster":
            remote = img.get("remoteUrl") or ""
            if remote.startswith("http"):
                return remote
    return ""


async def _test_arr_connection(url: str, key: str, service_name: str) -> tuple[bool, str]:
    """Shared body for test_radarr()/test_sonarr(): ping /system/status."""
    if not url or not key:
        return False, "Non configuré"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.get(f"{url}/api/v3/system/status", headers=_arr_auth(key))
            if r.status_code == 200:
                return True, f"{service_name} {r.json().get('version', '?')}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _normalize_arr_url(url: str) -> str:
    """Normalize a Radarr/Sonarr base URL for equality comparison."""
    return (url or "").strip().rstrip("/").lower()


async def _resolve_arr_server(
    servers: list[dict], arr_server_url: Optional[str]
) -> Optional[tuple[str, str]]:
    """Resolve which configured server (url, api_key) a stored arr id belongs to.

    - arr_server_url set (recorded on the queue row at scan time): must match
      a currently configured server's url exactly (normalized). No match
      (server removed/reconfigured since) means unresolved — never falls
      back to guessing a different server.
    - arr_server_url is None (legacy row, predates this column): unambiguous
      only when exactly one server is configured. With zero or 2+ candidate
      servers this returns None; the caller may attempt a file-path-based
      fallback, and must otherwise refuse to delete rather than guess.

    This never returns a server chosen by trying the same numeric id across
    every configured server — that brute-force pattern is exactly the bug
    being fixed (see CHANGELOG / arr_server_url migration comment).
    """
    if arr_server_url:
        target = _normalize_arr_url(arr_server_url)
        for srv in servers:
            if _normalize_arr_url(srv.get("url", "")) == target:
                return srv["url"].rstrip("/"), srv["api_key"]
        return None
    if len(servers) == 1:
        return servers[0]["url"].rstrip("/"), servers[0]["api_key"]
    return None


async def _first_from_servers(
    servers: list[dict], fetch_fn: Callable[[str, str], Awaitable[Optional[T]]]
) -> Optional[T]:
    """Call fetch_fn(url, key) against each enabled server, return the first truthy result."""
    for srv in servers:
        result = await fetch_fn(srv["url"].rstrip("/"), srv["api_key"])
        if result:
            return result
    return None


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
