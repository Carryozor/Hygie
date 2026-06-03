"""
Threshold-based Discord notifications for pending media.

Public API:
  _parse_thresholds             — parse "7,1" → [7, 1]
  _send_pending_notifications   — send per-threshold notifications
"""
import logging
from datetime import timedelta

from .db.utils import now_utc
from .db.engine import get_db
from .db.settings_store import get_setting, get_bool_setting
from .db.logs import add_log
from .logmsg import lm

from .discord_client import send_notification

logger = logging.getLogger(__name__)


def _parse_thresholds(raw: str) -> list:
    """Parse comma-separated threshold days string into sorted list (descending)."""
    try:
        days = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
        return sorted(set(days), reverse=True)
    except Exception:
        return [7, 1]


async def _send_pending_notifications():
    """
    Send threshold-based Discord notifications for pending items.
    Each threshold fires independently so an item can receive multiple notifications
    (e.g. at 7 days and again at 1 day) based on discord_notif_thresholds.
    """
    dry_run = await get_bool_setting("dry_run")
    thresholds_raw = await get_setting("discord_notif_thresholds") or "7,1"
    threshold_days = _parse_thresholds(thresholds_raw)

    try:
        for days in threshold_days:
            threshold_key = f"{days}d"
            cutoff = now_utc() + timedelta(days=days, hours=1)
            async with get_db() as db:
                candidates = await db.fetch_all(
                    "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
                    (cutoff.isoformat(),),
                )

                already_notified_ids: set = set()
                if candidates:
                    placeholders = ",".join("?" * len(candidates))
                    candidate_ids = [item["id"] for item in candidates]
                    notif_rows = await db.fetch_all(
                        f"SELECT media_id FROM notifications"
                        f" WHERE threshold=? AND media_id IN ({placeholders})",
                        [threshold_key] + candidate_ids,
                    )
                    already_notified_ids = {r["media_id"] for r in notif_rows}

            to_notify = [
                item for item in candidates
                if item["id"] not in already_notified_ids
            ]

            if not to_notify:
                continue

            sent = await send_notification(to_notify, threshold_key, dry_run=dry_run)
            if sent:
                async with get_db() as db:
                    for item in to_notify:
                        await db.execute(
                            "INSERT OR IGNORE INTO notifications (media_id, threshold)"
                            " VALUES (?,?)",
                            (item["id"], threshold_key),
                        )
                    await db.commit()
                await add_log("INFO", lm("notif.sent", days=days, n=len(to_notify)), "job")
            else:
                logger.warning(
                    f"Threshold notification '{threshold_key}' failed — will retry next cycle"
                )
    except Exception as e:
        logger.warning(f"_send_pending_notifications: {e}")
