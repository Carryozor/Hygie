# backend/scanner/_queue_entry.py
"""Build and insert media_queue entries."""
import logging
from typing import Optional

from ..db.repositories import insert_queue_entry, mark_notified_detected
from ..notifications import _send_pending_notifications
from ..discord_client import send_notification

logger = logging.getLogger(__name__)


def _build_queue_entry(
    item: dict,
    lib: dict,
    *,
    detect_at,
    delete_at,
    added_date,
    last_played,
    poster_url: str = "",
    tmdb_id: str = "",
    seerr_id=None,
    seerr_user_id=None,
    seerr_username: str = "",
    seerr_request_url: str = "",
    radarr_id=None,
    sonarr_id=None,
    sonarr_series_id=None,
    season_number=None,
) -> dict:
    """Build a media_queue entry dict from an Emby/Plex item and enrichment data."""
    return {
        "emby_id":           item.get("Id", ""),
        "title":             item.get("Name") or "?",
        "media_type":        item.get("Type") or "Movie",
        "library_id":        lib["id"],
        "library_name":      lib["name"],
        "file_path":         item.get("Path") or "",
        "poster_url":        poster_url,
        "tmdb_id":           tmdb_id,
        "seerr_id":          seerr_id,
        "seerr_user_id":     seerr_user_id,
        "seerr_username":    seerr_username,
        "seerr_request_url": seerr_request_url,
        "radarr_id":         radarr_id,
        "sonarr_id":         sonarr_id,
        "sonarr_series_id":  sonarr_series_id,
        "season_number":     season_number,
        "detected_at":       detect_at.isoformat(),
        "delete_at":         delete_at.isoformat(),
        "added_date":        added_date.isoformat() if added_date else detect_at.isoformat(),
        "last_played":       last_played.isoformat() if last_played else None,
    }


async def _insert_queue_entry(
    entry: dict,
    queued_ids: Optional[set],
    dry_run: bool,
) -> None:
    """Insert one eligible item into media_queue and send detected notification."""
    await insert_queue_entry(entry)
    if queued_ids is not None:
        queued_ids.add(entry["emby_id"])
    item_notif = {k: entry[k] for k in (
        "title", "media_type", "library_name",
        "seerr_user_id", "seerr_username",
        "poster_url", "delete_at",
    )}
    try:
        sent = await send_notification([item_notif], "detected", dry_run=dry_run)
        if sent:
            await mark_notified_detected(entry["emby_id"])
    except Exception as e:
        logger.warning(f"Detected notification failed for {entry['title']}: {e}")
