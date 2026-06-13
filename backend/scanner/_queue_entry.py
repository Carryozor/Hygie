# backend/scanner/_queue_entry.py
"""Build and insert media_queue entries."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.repositories import insert_queue_entry, mark_notified_detected
from ..discord_client import send_notification
from ..types import QueueEntry

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
) -> QueueEntry:
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


async def _pre_mark_applicable_thresholds(emby_id: str, delete_at_str: str) -> None:
    """Pre-mark threshold notifications already satisfied at detection time.

    When an item is first added to the queue, it might already qualify for a
    configured threshold (e.g., 7-day warning). Without this guard,
    _send_pending_notifications() would send a duplicate notification right after
    the 'detected' (green) one. This records those thresholds so they're skipped.
    """
    from ..db.settings_store import get_setting
    from ..db.engine import get_db

    thresholds_raw = await get_setting("discord_notif_thresholds") or "7,1"
    try:
        threshold_days = [
            int(x.strip()) for x in thresholds_raw.split(",")
            if x.strip().isdigit()
        ]
    except Exception:
        return

    try:
        delete_at = datetime.fromisoformat(delete_at_str.replace("Z", "+00:00"))
        if delete_at.tzinfo is None:
            delete_at = delete_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        async with get_db() as db:
            row = await db.fetch_one(
                "SELECT id FROM media_queue WHERE emby_id=?", (emby_id,)
            )
            if not row:
                return
            media_id = row["id"]
            for days in threshold_days:
                cutoff = now + timedelta(days=days, hours=1)
                if delete_at <= cutoff:
                    await db.execute(
                        "INSERT OR IGNORE INTO notifications (media_id, threshold)"
                        " VALUES (?,?)",
                        (media_id, f"{days}d"),
                    )
            await db.commit()
    except Exception as e:
        logger.debug("_pre_mark_applicable_thresholds: %s", e)


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
    # Pre-mark threshold notifications that this item already qualifies for.
    # Prevents a second "7d warning" from being sent immediately after "detected"
    # if the grace period is short enough to fall within a configured threshold.
    await _pre_mark_applicable_thresholds(entry["emby_id"], entry["delete_at"])

    try:
        sent = await send_notification([item_notif], "detected", dry_run=dry_run)
        if sent:
            await mark_notified_detected(entry["emby_id"])
    except Exception as e:
        logger.warning(f"Detected notification failed for {entry['title']}: {e}")
