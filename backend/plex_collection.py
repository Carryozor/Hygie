"""
Plex poster overlay sync.

Applies the 'Supprimé dans Xj' banner to pending Plex items on each collection sync.
When an item leaves the pending queue, Plex metadata is refreshed to restore the
original poster from agents (TMDB/etc.).

Public API:
  sync_plex_overlays  — apply/restore overlays for all enabled Plex servers
"""
import asyncio
import logging
from typing import Optional

import httpx

from .db.engine import get_db
from .db.logs import add_log
from .db.media_servers import get_media_servers
from .db.settings_store import get_bool_setting, get_setting
from .db.utils import now_utc, parse_iso_dt
from .overlay import _overlay_poster
from .plex_client import PlexClient, build_plex_client

logger = logging.getLogger(__name__)

# Per-server memory: tracks which ratingKeys had an overlay applied this session.
# Used to detect items that left the pending queue so their poster can be restored.
_overlay_applied: dict[str, set] = {}  # server_id → {rating_key, …}


async def sync_plex_overlays() -> None:
    """Apply/restore poster overlays for all enabled Plex servers."""
    overlay_enabled = await get_bool_setting("plex_overlay_enabled")
    if not overlay_enabled:
        return

    servers = await get_media_servers()
    plex_servers = [
        s for s in servers
        if s.get("type") == "plex" and s.get("enabled", True)
    ]
    if not plex_servers:
        return

    ui_lang = await get_setting("ui_language") or "fr"

    # Build server_id → library_ids mapping
    plex_server_ids = tuple(str(s["id"]) for s in plex_servers)
    async with get_db() as db:
        lib_rows = await db.fetch_all(
            "SELECT id, server_id FROM libraries WHERE server_id IN ({})".format(
                ",".join("?" * len(plex_server_ids))
            ),
            plex_server_ids,
        )
    lib_to_server: dict[str, str] = {str(r["id"]): str(r["server_id"]) for r in lib_rows}

    if not lib_to_server:
        return

    # Fetch pending Plex items
    async with get_db() as db:
        pending = await db.fetch_all(
            "SELECT emby_id, title, delete_at, poster_url, plex_rating_key, library_id "
            "FROM media_queue WHERE status='pending' "
            "AND library_id IN ({}) AND plex_rating_key != ''".format(
                ",".join("?" * len(lib_to_server))
            ),
            tuple(lib_to_server.keys()),
        )

    # Group by server
    items_by_server: dict[str, list] = {str(s["id"]): [] for s in plex_servers}
    for item in pending:
        server_id = lib_to_server.get(str(item["library_id"]))
        if server_id:
            items_by_server[server_id].append(item)

    for server in plex_servers:
        server_id = str(server["id"])
        plex = build_plex_client(server)
        if not plex:
            continue

        items = items_by_server.get(server_id, [])
        current_keys = {str(item["plex_rating_key"] or item["emby_id"]) for item in items}
        prev_keys = _overlay_applied.get(server_id, set())

        # Restore posters for items that left the pending queue since last run
        to_restore = prev_keys - current_keys
        if to_restore:
            await _restore_plex_posters(plex, list(to_restore))

        # Apply overlays to currently pending items
        if items:
            await _apply_plex_overlays(plex, items, ui_lang)

        _overlay_applied[server_id] = current_keys


async def _apply_plex_overlays(plex: PlexClient, items: list, ui_lang: str) -> None:
    """Apply 'Supprimé dans Xj' banner to each Plex item's poster."""
    sem = asyncio.Semaphore(3)

    async def _one(item: dict) -> None:
        async with sem:
            try:
                dt = parse_iso_dt(item["delete_at"])
                if not dt:
                    return
                days_left = max(0, (dt.date() - now_utc().date()).days)

                rating_key = str(item["plex_rating_key"] or item["emby_id"])
                poster_url = item.get("poster_url", "")

                original_bytes: Optional[bytes] = None
                if poster_url and poster_url.startswith("http"):
                    async with httpx.AsyncClient(timeout=20) as http:
                        try:
                            pr = await http.get(poster_url, follow_redirects=True)
                            if pr.status_code == 200 and pr.headers.get("content-type", "").startswith("image"):
                                original_bytes = pr.content
                        except Exception as e:
                            logger.debug("Plex poster fetch error: %s", e)

                if not original_bytes:
                    return

                modified = await _overlay_poster(original_bytes, days_left, ui_lang)
                if not modified:
                    return

                ok = await plex.upload_poster(rating_key, modified)
                if ok:
                    await add_log("INFO", f"Plex overlay appliqué : {item.get('title')}", "system")
            except Exception as e:
                logger.warning("Plex overlay error for %s: %s", item.get("title", "?"), e)

    await asyncio.gather(*[_one(item) for item in items])


async def _restore_plex_posters(plex: PlexClient, rating_keys: list[str]) -> None:
    """Restore original poster by triggering a Plex metadata refresh from agents."""
    for rating_key in rating_keys:
        try:
            ok = await plex.restore_poster(rating_key)
            if ok:
                await add_log("INFO", f"Plex affiche restaurée : ratingKey={rating_key}", "system")
        except Exception as e:
            logger.warning("Plex poster restore error for %s: %s", rating_key, e)
