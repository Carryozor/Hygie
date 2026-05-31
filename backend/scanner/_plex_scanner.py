# backend/scanner/_plex_scanner.py
"""Plex library scanner — queues unwatched items past grace period."""
import logging
from datetime import timedelta

from ..plex_client import build_plex_client
from ..db.engine import get_db
from ..db.utils import now_utc, parse_iso_dt
from ..db.repositories import insert_queue_entry
from ..db.logs import add_log

logger = logging.getLogger(__name__)


async def _scan_plex_library(*, server: dict, library: dict) -> int:
    """Scan one Plex library section and queue items that meet deletion criteria.

    Uses view_count==0 as the primary condition. Items added before grace_days
    cutoff and never watched are queued as 'pending'.
    Returns count of items newly queued.
    """
    plex = build_plex_client(server)
    if plex is None:
        return 0

    section_id  = library["emby_library_id"]
    grace_days  = int(library.get("grace_days") or 7)
    lib_id      = library["id"]
    lib_name    = library["name"]
    server_name = server.get("name") or "Plex"

    items = await plex.scan_library(section_id)
    await add_log("INFO", f"Scan : {server_name} : {lib_name} — {len(items)} éléments", "scan")

    cutoff = now_utc() - timedelta(days=grace_days)
    added  = 0

    async with get_db() as db:
        queued_rows  = await db.fetch_all("SELECT emby_id FROM media_queue")
        queued_ids   = {r["emby_id"] for r in queued_rows}
        ignored_rows = await db.fetch_all("SELECT emby_id FROM ignored_media")
        ignored_ids  = {r["emby_id"] for r in ignored_rows}

    for item in items:
        plex_id = item["plex_id"]
        if not plex_id:
            continue
        if plex_id in queued_ids or plex_id in ignored_ids:
            continue
        if item["view_count"] > 0:
            continue

        added_at_str = item.get("added_at")
        if not added_at_str:
            continue
        added_at = parse_iso_dt(added_at_str)
        if added_at is None or added_at > cutoff:
            continue

        detected_at = now_utc().isoformat()
        delete_at   = (now_utc() + timedelta(days=grace_days)).isoformat()

        entry = {
            "emby_id":           plex_id,
            "title":             item["title"],
            "media_type":        item["media_type"],
            "library_id":        lib_id,
            "library_name":      lib_name,
            "file_path":         "",
            "poster_url":        item["poster_url"],
            "tmdb_id":           item["tmdb_id"],
            "seerr_id":          None,
            "seerr_user_id":     None,
            "seerr_username":    "",
            "seerr_request_url": "",
            "radarr_id":         None,
            "sonarr_id":         None,
            "sonarr_series_id":  None,
            "season_number":     item.get("season_number"),
            "detected_at":       detected_at,
            "delete_at":         delete_at,
            "added_date":        item.get("added_at"),
            "last_played":       item.get("last_viewed_at"),
        }
        await insert_queue_entry(entry)
        added += 1

    if added:
        await add_log("INFO", f"{server_name} : {lib_name} : {added} média(s) mis en file", "scan")
    return added
