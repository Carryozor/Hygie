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


from .db.engine import get_db
from .db.logs import add_log
from .logmsg import lm

from .db.media_servers import get_media_servers
from .db.settings_store import get_bool_setting, get_setting
from .db.utils import now_utc, parse_iso_dt, guarded_image_get
from .overlay import _overlay_poster
from .plex_client import PlexClient, build_plex_client

logger = logging.getLogger(__name__)


async def _get_overlay_keys(server_id: str) -> set:
    """Return the ratingKeys currently tracked as having an overlay applied
    for this server, per the plex_overlays table.

    Persisted in the DB (not a module-level dict) so overlay tracking
    survives a process restart and is shared across WORKERS>1 — a bare
    in-memory dict is lost on restart and not visible to the other worker,
    so a poster's "deleted in Xj" overlay could never be restored once the
    item left the pending queue on a different worker/run than the one that
    applied it.
    """
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT rating_key FROM plex_overlays WHERE server_id=?", (server_id,)
        )
    return {r["rating_key"] for r in rows}


async def _add_overlay_keys(server_id: str, rating_keys: set) -> None:
    """Record ratingKeys as having an overlay applied (idempotent)."""
    if not rating_keys:
        return
    applied_at = now_utc().isoformat()
    async with get_db() as db:
        for rating_key in rating_keys:
            await db.execute(
                "INSERT OR IGNORE INTO plex_overlays (server_id, rating_key, applied_at) "
                "VALUES (?, ?, ?)",
                (server_id, rating_key, applied_at),
            )
        await db.commit()


async def _remove_overlay_keys(server_id: str, rating_keys: list) -> None:
    """Forget ratingKeys once their overlay has been restored."""
    if not rating_keys:
        return
    async with get_db() as db:
        for rating_key in rating_keys:
            await db.execute(
                "DELETE FROM plex_overlays WHERE server_id=? AND rating_key=?",
                (server_id, rating_key),
            )
        await db.commit()


async def sync_plex_overlays() -> None:
    """Apply/restore poster overlays for all enabled Plex servers.

    Requires Plex Pass (upload poster API) and plex_overlay_enabled=true.
    Disabled by default — enable in Settings → Serveurs → section Plex.
    """
    overlay_enabled = await get_bool_setting("plex_overlay_enabled")
    if not overlay_enabled:
        return  # Off by default — requires Plex Pass to work correctly

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
            "SELECT id, server_id FROM libraries WHERE server_id IN ({})".format(  # nosec B608 - placeholders count from len(plex_server_ids), values bound
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
            "SELECT emby_id, title, delete_at, poster_url, plex_rating_key, library_id "  # nosec B608 - placeholders count from len(lib_to_server), values bound
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
        prev_keys = await _get_overlay_keys(server_id)

        # Restore posters for items that left the pending queue since last run
        to_restore = prev_keys - current_keys
        if to_restore:
            await _restore_plex_posters(plex, list(to_restore))
            await _remove_overlay_keys(server_id, list(to_restore))

        # Apply overlays to currently pending items
        if items:
            await _apply_plex_overlays(plex, items, ui_lang)
            await _add_overlay_keys(server_id, current_keys)


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
                    original_bytes = await guarded_image_get(poster_url, timeout=20)

                if not original_bytes:
                    return

                modified = await _overlay_poster(original_bytes, days_left, ui_lang)
                if not modified:
                    return

                ok = await plex.upload_poster(rating_key, modified)
                if ok:
                    await add_log("INFO", lm("collection.plex_overlay", title=item.get('title')), "system")
            except Exception as e:
                logger.warning("Plex overlay error for %s: %s", item.get("title", "?"), e)

    await asyncio.gather(*[_one(item) for item in items])


async def _restore_plex_posters(plex: PlexClient, rating_keys: list[str]) -> None:
    """Restore original poster by triggering a Plex metadata refresh from agents."""
    for rating_key in rating_keys:
        try:
            ok = await plex.restore_poster(rating_key)
            if ok:
                await add_log("INFO", lm("collection.plex_restored", key=rating_key), "system")
        except Exception as e:
            logger.warning("Plex poster restore error for %s: %s", rating_key, e)
