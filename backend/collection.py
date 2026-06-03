"""
Emby/Jellyfin 'Bientôt supprimé' collection sync.

Public API:
  sync_emby_collection  — sync collection membership + apply poster overlays
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

import httpx

from .db.utils import now_utc, parse_iso_dt
from .db.engine import get_db
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.media_servers import get_media_servers
from .db.logs import add_log
from .logmsg import lm

from .emby_client import get_client
from .overlay import _overlay_poster

logger = logging.getLogger(__name__)

# In-memory cache: emby_id -> days_left when overlay was last applied.
# Prevents re-uploading the same poster on every sync cycle when the day count
# hasn't changed. Ephemeral — reset on restart, which is acceptable.
_overlay_cache: dict[str, int] = {}


def _invalidate_overlay_cache(emby_id: str) -> None:
    _overlay_cache.pop(emby_id, None)


async def _get_or_create_collection(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    collection_name: str,
    wanted_ids: set,
) -> Optional[str]:
    """Find existing collection by name or create it. Returns collection_id or None."""
    r = await client.get(
        f"{emby_url}/Items",
        headers={"X-Emby-Token": emby_key},
        params={
            "IncludeItemTypes": "BoxSet",
            "Recursive": "true",
            "SearchTerm": collection_name,
            "Limit": 10,
        },
    )
    if r.status_code == 200:
        for item in r.json().get("Items", []):
            if item.get("Name", "").lower() == collection_name.lower():
                return item["Id"]

    if not wanted_ids:
        return None

    ru = await client.get(f"{emby_url}/Users", headers={"X-Emby-Token": emby_key})
    user_id = ru.json()[0]["Id"] if ru.status_code == 200 and ru.json() else ""

    rc = await client.post(
        f"{emby_url}/Collections",
        headers={"X-Emby-Token": emby_key},
        params={"Name": collection_name, "Ids": ",".join(wanted_ids), "UserId": user_id},
    )
    if rc.status_code in (200, 204):
        await add_log(
            "INFO",
            f"Collection Emby '{collection_name}' créée ({len(wanted_ids)} médias)",
            "system",
        )
        return rc.json().get("Id")
    return None


async def _sync_collection_membership(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    collection_id: str,
    wanted_ids: set,
) -> tuple[set, set]:
    """Add/remove items to match wanted_ids. Returns (added, removed) for logging."""
    rc = await client.get(
        f"{emby_url}/Items",
        headers={"X-Emby-Token": emby_key},
        params={"ParentId": collection_id, "Recursive": "false", "Limit": 5000, "Fields": "Id"},
    )
    current_ids: set = set()
    if rc.status_code == 200:
        current_ids = {item["Id"] for item in rc.json().get("Items", [])}

    to_add    = wanted_ids - current_ids
    to_remove = current_ids - wanted_ids

    if to_add:
        await client.post(
            f"{emby_url}/Collections/{collection_id}/Items",
            headers={"X-Emby-Token": emby_key},
            params={"Ids": ",".join(to_add)},
        )
    if to_remove:
        await client.delete(
            f"{emby_url}/Collections/{collection_id}/Items",
            headers={"X-Emby-Token": emby_key},
            params={"Ids": ",".join(to_remove)},
        )
    return to_add, to_remove


async def _restore_posters_for_removed(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    removed_ids: set,
) -> None:
    """Restore original TMDB poster for items removed from the collection."""
    if not removed_ids:
        return
    async with get_db() as db:
        for emby_id in removed_ids:
            try:
                row = await db.fetch_one(
                    "SELECT poster_url FROM media_queue WHERE emby_id=?", (emby_id,)
                )
                poster_url = row["poster_url"] if row else ""
                if not poster_url or not poster_url.startswith("http"):
                    continue
                pr = await client.get(poster_url, follow_redirects=True)
                if pr.status_code != 200 or not pr.headers.get("content-type", "").startswith("image"):
                    continue
                resp = await client.post(
                    f"{emby_url}/Items/{emby_id}/Images/Primary",
                    headers={"X-Emby-Token": emby_key, "Content-Type": "image/jpeg"},
                    content=pr.content,
                )
                if resp.status_code in (200, 204):
                    _invalidate_overlay_cache(emby_id)
                    await add_log("INFO", lm("collection.poster_restored", id=emby_id), "system")
                else:
                    logger.warning(f"Restore poster HTTP {resp.status_code} for {emby_id}")
            except Exception as e:
                logger.warning(f"Restore poster error for {emby_id}: {e}")


async def _apply_overlays(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    wanted: list,
    ui_lang: str,
) -> None:
    """Apply 'Supprimé dans Xj' banner to all wanted items — max 5 concurrent."""
    sem = asyncio.Semaphore(5)

    async def _apply_one(w: dict) -> None:
        async with sem:
            try:
                dt = parse_iso_dt(w["delete_at"])
                if not dt:
                    return
                days_left = max(0, (dt.date() - now_utc().date()).days)
                emby_id = w["emby_id"]

                # Skip if the overlay for this day count was already applied this session
                if _overlay_cache.get(emby_id) == days_left:
                    return

                original_bytes: Optional[bytes] = None
                poster_url = w.get("poster_url", "")
                if poster_url and poster_url.startswith("http"):
                    try:
                        pr = await client.get(poster_url, follow_redirects=True)
                        if pr.status_code == 200 and pr.headers.get("content-type", "").startswith("image"):
                            original_bytes = pr.content
                    except Exception as e:
                        logger.debug(f"Poster fetch failed: {e}")

                if not original_bytes:
                    pr = await client.get(
                        f"{emby_url}/Items/{emby_id}/Images/Primary",
                        headers={"X-Emby-Token": emby_key},
                        params={"maxHeight": 600},
                    )
                    if pr.status_code == 200:
                        original_bytes = pr.content

                if not original_bytes:
                    return

                modified = await _overlay_poster(original_bytes, days_left, ui_lang)
                if not modified:
                    return

                resp = await client.post(
                    f"{emby_url}/Items/{emby_id}/Images/Primary",
                    headers={"X-Emby-Token": emby_key, "Content-Type": "image/jpeg"},
                    content=modified,
                )
                if resp.status_code in (200, 204):
                    _overlay_cache[emby_id] = days_left
                    await add_log("INFO", lm("collection.overlay_applied", title=w.get('title')), "system")
                else:
                    logger.warning(
                        f"Overlay upload HTTP {resp.status_code} for {w.get('title')}: "
                        f"{resp.text[:100]}"
                    )
            except Exception as e:
                logger.warning(f"Overlay error for {w.get('title', '?')}: {e}")

    await asyncio.gather(*[_apply_one(w) for w in wanted])


async def sync_emby_collection():
    """Sync the 'Bientôt supprimé' collection — compatible with both Emby and Jellyfin.
    Uses the first enabled server from media_servers; falls back to legacy server "0".
    """
    sync_server_id = "0"
    servers = await get_media_servers()
    enabled = [
        s for s in servers
        if s.get("enabled", True) and s.get("type") in ("emby", "jellyfin", "")
    ]
    if enabled:
        sync_server_id = str(enabled[0].get("id", "0"))
    elif servers:
        first_type = servers[0].get("type", "")
        if first_type not in ("emby", "jellyfin", ""):
            return
    if not servers:
        server_type = await get_setting("media_server_type")
        if server_type not in ("emby", "jellyfin", ""):
            return

    collection_name = await get_setting("emby_leaving_soon_collection")
    if not collection_name:
        return

    try:
        days = await get_int_setting("emby_leaving_soon_days", 30)
    except ValueError:
        days = 30

    cutoff = now_utc() + timedelta(days=days)

    async with get_db() as db:
        # CRITICAL: only fetch items from the Emby/Jellyfin server being synced.
        # Plex items store Plex rating keys as emby_id; if included, overlay would
        # corrupt Emby items whose numeric IDs happen to match those rating keys.
        wanted = await db.fetch_all(
            """SELECT mq.emby_id, mq.title, mq.delete_at, mq.poster_url
               FROM media_queue mq
               JOIN libraries l ON mq.library_id = l.id
               WHERE mq.status='pending' AND mq.delete_at <= ?
               AND l.server_id = ?""",
            (cutoff.isoformat(), sync_server_id),
        )

    wanted_ids = {w["emby_id"] for w in wanted}

    emby_url, emby_key = await get_client(sync_server_id)
    if not emby_url or not emby_key:
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            collection_id = await _get_or_create_collection(
                client, emby_url, emby_key, collection_name, wanted_ids
            )
            if not collection_id:
                return

            to_add, to_remove = await _sync_collection_membership(
                client, emby_url, emby_key, collection_id, wanted_ids
            )

            overlay_enabled = await get_bool_setting("emby_leaving_soon_overlay")
            if overlay_enabled:
                ui_lang = await get_setting("ui_language") or "fr"
                if to_remove:
                    await _restore_posters_for_removed(client, emby_url, emby_key, to_remove)
                if wanted:
                    await _apply_overlays(client, emby_url, emby_key, wanted, ui_lang)
                # Delete the collection's cached primary image so Emby rebuilds the
                # mosaic from the freshly-overlaid item posters (fixes stale day counts).
                try:
                    await client.delete(
                        f"{emby_url}/Items/{collection_id}/Images/Primary",
                        headers={"X-Emby-Token": emby_key},
                    )
                except Exception:
                    pass
                try:
                    await client.post(
                        f"{emby_url}/Items/{collection_id}/Refresh",
                        headers={"X-Emby-Token": emby_key},
                        params={
                            "Recursive": "false",
                            "MetadataRefreshMode": "None",
                            "ImageRefreshMode": "FullRefresh",
                            "ReplaceAllImages": "true",
                        },
                    )
                except Exception as e:
                    logger.debug(f"Collection refresh error: {e}")

            if to_add or to_remove:
                await add_log(
                    "INFO",
                    f"Collection '{collection_name}' : {len(wanted_ids)} médias "
                    f"| +{len(to_add)} ajoutés | -{len(to_remove)} retirés",
                    "system",
                )
    except Exception as e:
        logger.warning(f"sync_emby_collection error: {e}")
