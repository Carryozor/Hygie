"""
Emby/Jellyfin 'Bientôt supprimé' collection sync.

Public API:
  sync_emby_collection  — sync collection membership + apply poster overlays
"""
import asyncio
import base64
import logging
from datetime import timedelta
from typing import Optional

import httpx

from .db.utils import now_utc, parse_iso_dt, guarded_image_get
from .db.repositories import get_poster_url_by_emby_id, get_pending_before_for_server
from .db.settings_store import get_setting, get_bool_setting, get_int_setting
from .db.media_servers import get_media_servers
from .db.logs import add_log
from .logmsg import lm
from .media_item_resolver import resolve_server_item_id

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
    _ru_users = ru.json() if ru.status_code == 200 else None
    user_id = _ru_users[0]["Id"] if isinstance(_ru_users, list) and _ru_users else ""

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
    await _warn(
        f"Création de la collection Emby '{collection_name}' échouée (HTTP {rc.status_code})"
    )
    return None


async def _warn(message: str) -> None:
    """Log a French warning both to the app logger and the UI log table."""
    logger.warning(message)
    await add_log("WARN", message, "system")


async def _sync_collection_membership(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    collection_id: str,
    wanted_ids: set,
    allow_remove: bool = True,
) -> tuple[set, set]:
    """Add/remove items to match wanted_ids. Returns (added, removed) actually applied.

    A failed add/remove call is logged and its set is returned empty, so the
    caller never reports changes the server rejected.
    """
    rc = await client.get(
        f"{emby_url}/Items",
        headers={"X-Emby-Token": emby_key},
        params={"ParentId": collection_id, "Recursive": "false", "Limit": 5000, "Fields": "Id"},
    )
    current_ids: set = set()
    if rc.status_code == 200:
        current_ids = {item["Id"] for item in rc.json().get("Items", [])}

    to_add    = wanted_ids - current_ids
    to_remove = (current_ids - wanted_ids) if allow_remove else set()

    if to_add:
        ra = await client.post(
            f"{emby_url}/Collections/{collection_id}/Items",
            headers={"X-Emby-Token": emby_key},
            params={"Ids": ",".join(to_add)},
        )
        if ra.status_code not in (200, 204):
            await _warn(f"Ajout de {len(to_add)} média(s) à la collection échoué (HTTP {ra.status_code})")
            to_add = set()
    if to_remove:
        rd = await client.delete(
            f"{emby_url}/Collections/{collection_id}/Items",
            headers={"X-Emby-Token": emby_key},
            params={"Ids": ",".join(to_remove)},
        )
        if rd.status_code not in (200, 204):
            await _warn(f"Retrait de {len(to_remove)} média(s) de la collection échoué (HTTP {rd.status_code})")
            to_remove = set()
    return to_add, to_remove


async def _refresh_primary_image(
    client: httpx.AsyncClient, emby_url: str, emby_key: str, emby_id: str
) -> bool:
    """Drop the Primary image and ask the server to re-download it (no stored poster)."""
    headers = {"X-Emby-Token": emby_key}
    rd = await client.delete(f"{emby_url}/Items/{emby_id}/Images/Primary", headers=headers)
    if rd.status_code not in (200, 204, 404):
        logger.warning(f"Restore poster: DELETE Primary HTTP {rd.status_code} for {emby_id}")
        return False
    rr = await client.post(
        f"{emby_url}/Items/{emby_id}/Refresh",
        headers=headers,
        params={
            "Recursive": "false",
            "MetadataRefreshMode": "Default",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllImages": "false",
        },
    )
    if rr.status_code not in (200, 204):
        logger.warning(f"Restore poster: Refresh HTTP {rr.status_code} for {emby_id}")
        return False
    return True


async def _restore_posters_for_removed(
    client: httpx.AsyncClient,
    emby_url: str,
    emby_key: str,
    removed_ids: set,
) -> None:
    """Restore original TMDB poster for items removed from the collection."""
    if not removed_ids:
        return
    for emby_id in removed_ids:
        try:
            poster_url = await get_poster_url_by_emby_id(emby_id) or ""
            if not poster_url:
                # Consolidated series/seasons have no stored poster: let the
                # server re-fetch its own Primary image.
                if await _refresh_primary_image(client, emby_url, emby_key, emby_id):
                    _invalidate_overlay_cache(emby_id)
                    await add_log("INFO", lm("collection.poster_restored", id=emby_id), "system")
                continue
            if not poster_url.startswith("http"):
                continue
            poster_bytes = await guarded_image_get(poster_url)
            if not poster_bytes:
                continue
            resp = await client.post(
                f"{emby_url}/Items/{emby_id}/Images/Primary",
                headers={"X-Emby-Token": emby_key, "Content-Type": "image/jpeg"},
                content=base64.b64encode(poster_bytes),
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
                    original_bytes = await guarded_image_get(poster_url)

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
                    content=base64.b64encode(modified),
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


async def _resolve_wanted(wanted: list, server_id: str) -> tuple[list, bool]:
    """Map queue rows to copies carrying the real server Id, deduplicated by Id.

    On duplicates the row with the closest ``delete_at`` wins. Returns
    (rows, has_unresolved); unresolved rows are warned about and skipped.
    """
    by_id: dict[str, dict] = {}
    has_unresolved = False
    for w in wanted:
        try:
            real_id = await resolve_server_item_id(w, server_id)
        except Exception as e:
            logger.warning(f"Résolution Id échouée pour {w.get('title', '?')}: {e}")
            real_id = None
        if not real_id:
            has_unresolved = True
            await _warn(
                f"Collection : impossible de résoudre '{w.get('title', '?')}' "
                f"sur le serveur média — aucun retrait ce cycle"
            )
            continue
        candidate = {**w, "emby_id": real_id}
        existing = by_id.get(real_id)
        if existing is None:
            by_id[real_id] = candidate
            continue
        cand_dt, cur_dt = parse_iso_dt(candidate.get("delete_at")), parse_iso_dt(existing.get("delete_at"))
        if cand_dt and (cur_dt is None or cand_dt < cur_dt):
            by_id[real_id] = candidate
    return list(by_id.values()), has_unresolved


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

    # CRITICAL: only fetch items from the Emby/Jellyfin server being synced.
    # Plex items store Plex rating keys as emby_id; if included, overlay would
    # corrupt Emby items whose numeric IDs happen to match those rating keys.
    wanted = await get_pending_before_for_server(cutoff.isoformat(), sync_server_id)

    emby_url, emby_key = await get_client(sync_server_id)
    if not emby_url or not emby_key:
        return

    wanted, has_unresolved = await _resolve_wanted(wanted, sync_server_id)
    wanted_ids = {w["emby_id"] for w in wanted}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            collection_id = await _get_or_create_collection(
                client, emby_url, emby_key, collection_name, wanted_ids
            )
            if not collection_id:
                return

            to_add, to_remove = await _sync_collection_membership(
                client, emby_url, emby_key, collection_id, wanted_ids,
                allow_remove=not has_unresolved,
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
                # Brief pause so Emby finishes writing uploaded item posters before
                # the mosaic is regenerated (Emby processes images asynchronously).
                await asyncio.sleep(1)
                del_resp = await client.delete(
                    f"{emby_url}/Items/{collection_id}/Images/Primary",
                    headers={"X-Emby-Token": emby_key},
                )
                if del_resp.status_code not in (200, 204, 404):
                    logger.warning(
                        f"Collection image delete returned HTTP {del_resp.status_code}"
                    )
                try:
                    ref_resp = await client.post(
                        f"{emby_url}/Items/{collection_id}/Refresh",
                        headers={"X-Emby-Token": emby_key},
                        params={
                            "Recursive": "false",
                            "MetadataRefreshMode": "Default",
                            "ImageRefreshMode": "FullRefresh",
                            # Do NOT replace images — Emby must regenerate the mosaic
                            # from the already-uploaded overlaid item posters, not
                            # download a new poster from the internet.
                            "ReplaceAllImages": "false",
                        },
                    )
                    if ref_resp.status_code not in (200, 204):
                        logger.warning(
                            f"Collection refresh returned HTTP {ref_resp.status_code}"
                        )
                except Exception as e:
                    logger.warning(f"Collection refresh error: {e}")

            if to_add or to_remove:
                await add_log(
                    "INFO",
                    f"Collection '{collection_name}' : {len(wanted_ids)} médias "
                    f"| +{len(to_add)} ajoutés | -{len(to_remove)} retirés",
                    "system",
                )
    except Exception as e:
        logger.warning(f"sync_emby_collection error: {e}")
