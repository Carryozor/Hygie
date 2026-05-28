"""
Threshold-based Discord notifications for pending media.

Public API:
  _parse_thresholds         — parse "7,1" → [7, 1]
  _ensure_notif_columns     — safe DB migration for notification columns
  _send_pending_notifications — send per-threshold notifications
"""
import json
import logging
from datetime import timedelta

import aiosqlite

from .db.utils import DB_PATH, now_utc
from .db.settings_store import get_setting, get_bool_setting
from .db.logs import add_log
from .discord_client import send_notification

logger = logging.getLogger(__name__)


def _parse_thresholds(raw: str) -> list:
    """Parse comma-separated threshold days string into sorted list (descending)."""
    try:
        days = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
        return sorted(set(days), reverse=True)
    except Exception:
        return [7, 1]


async def _ensure_notif_columns():
    """Add new notification columns to media_queue if missing (safe migration)."""
    async with aiosqlite.connect(DB_PATH) as db:
        for col, definition in [
            ("notified_detected", "INTEGER DEFAULT 0"),
            ("notified_thresholds", "TEXT DEFAULT '[]'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE media_queue ADD COLUMN {col} {definition}")
            except Exception:
                pass
        # Migrate items already notified under old system
        await db.execute("""
            UPDATE media_queue
            SET notified_thresholds = '["migrated"]'
            WHERE (notified_30d=1 OR notified_7d=1 OR notified_1d=1)
              AND (notified_thresholds IS NULL OR notified_thresholds = '[]')
        """)
        await db.commit()


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
            cutoff = now_utc() + timedelta(days=days, hours=1)
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
                    (cutoff.isoformat(),),
                ) as cur:
                    candidates = [dict(r) for r in await cur.fetchall()]

            to_notify = [
                item for item in candidates
                if days not in json.loads(item.get("notified_thresholds") or "[]")
                and "migrated" not in json.loads(item.get("notified_thresholds") or "[]")
            ]

            if not to_notify:
                continue

            sent = await send_notification(to_notify, f"{days}d", dry_run=dry_run)
            if sent:
                async with aiosqlite.connect(DB_PATH) as db:
                    for item in to_notify:
                        notified = json.loads(item.get("notified_thresholds") or "[]")
                        if days not in notified:
                            notified.append(days)
                        await db.execute(
                            "UPDATE media_queue SET notified_thresholds=? WHERE id=?",
                            (json.dumps(notified), item["id"]),
                        )
                    await db.commit()
                await add_log(
                    "INFO",
                    f"Notification {days}j envoyée pour {len(to_notify)} média(s)",
                    "job",
                )
            else:
                logger.warning(f"Threshold notification '{days}d' failed — will retry next cycle")
    except Exception as e:
        logger.debug(f"_send_pending_notifications: {e}")
