# backend/media_server_factory.py
"""Unified media server abstraction — eliminates scattered is_plex() dispatch.

Centralizes the Emby/Plex branching so callers never import both clients
and branch on server type themselves.

Public API:
  get_server_item_id(server, item)  — the server-local ID for an item
  delete_server_item(server, item)  — delete via the correct client
"""
import logging
from typing import Optional

from .db.media_servers import is_plex

logger = logging.getLogger(__name__)


def get_server_item_id(server: dict, item: dict) -> str:
    """Return the server-local identifier for an item.

    Plex items use plex_rating_key (stored in media_queue.plex_rating_key);
    Emby/Jellyfin items use emby_id.
    Falls back to emby_id for Plex when plex_rating_key is absent (legacy rows).
    """
    if is_plex(server):
        return item.get("plex_rating_key") or item.get("emby_id", "")
    return item.get("emby_id", "")


async def delete_server_item(
    server: dict,
    item: dict,
    *,
    server_id: Optional[str] = None,
    missing_ok: bool = False,
) -> bool:
    """Delete an item on the appropriate media server.

    Returns True on success, False on failure (client unavailable or error).
    ``missing_ok`` makes a 404 ("already absent") a success; callers set it
    only on a retry. Does NOT handle dry_run — callers guard that themselves.
    """
    extra = {"missing_ok": True} if missing_ok else {}
    if is_plex(server):
        from .plex_client import build_plex_client
        plex = build_plex_client(server)
        if plex is None:
            logger.warning("delete_server_item: could not build Plex client for server %s", server.get("id"))
            return False
        rating_key = get_server_item_id(server, item)
        return await plex.delete_item(rating_key, **extra)
    else:
        from .emby_client import delete_item
        emby_id = item.get("emby_id", "")
        sid = server_id or str(server.get("id", "0"))
        return await delete_item(emby_id, server_id=sid, **extra)
