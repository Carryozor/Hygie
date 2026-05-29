"""Database query functions — single source of truth for media_queue and libraries SQL."""
import logging

from .engine import get_db
from .utils import now_utc

logger = logging.getLogger(__name__)


async def get_pending_queue() -> list[dict]:
    """Return pending media_queue rows whose delete_at has passed."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
            (now_utc().isoformat(),),
        )


async def get_queued_and_ignored_ids() -> tuple[set, set]:
    """Return (queued_emby_ids, ignored_emby_ids) from a single DB connection."""
    async with get_db() as db:
        queued_rows = await db.fetch_all("SELECT emby_id FROM media_queue")
        queued = {r["emby_id"] for r in queued_rows}
        ignored_rows = await db.fetch_all("SELECT emby_id FROM ignored_media")
        ignored = {r["emby_id"] for r in ignored_rows}
    return queued, ignored


async def get_enabled_libraries(server_id: str) -> list[dict]:
    """Return enabled library rows for the given server_id."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT * FROM libraries"
            " WHERE enabled=1 AND (server_id=? OR server_id IS NULL OR server_id='')",
            (server_id,),
        )


async def insert_queue_entry(entry: dict) -> None:
    """Insert one row into media_queue (status='pending')."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO media_queue
            (emby_id, title, media_type, library_id, library_name, file_path,
             poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
             seerr_request_url, radarr_id, sonarr_id, sonarr_series_id, season_number,
             detected_at, delete_at, added_date, last_played, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                entry["emby_id"], entry["title"], entry["media_type"],
                entry["library_id"], entry["library_name"], entry["file_path"],
                entry["poster_url"], entry["tmdb_id"],
                entry["seerr_id"], entry["seerr_user_id"], entry["seerr_username"],
                entry["seerr_request_url"], entry["radarr_id"], entry["sonarr_id"],
                entry.get("sonarr_series_id"), entry.get("season_number"),
                entry["detected_at"], entry["delete_at"],
                entry["added_date"], entry["last_played"],
            ),
        )
        await db.commit()


async def mark_notified_detected(emby_id: str) -> None:
    """Record a 'detected' notification for the pending queue row with this emby_id."""
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT id FROM media_queue WHERE emby_id=? AND status='pending'", (emby_id,)
        )
        if row is not None:
            await db.execute(
                "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                (row["id"], "detected"),
            )
            await db.commit()


async def update_queue_status(item_id: int, status: str) -> None:
    """Update status and record a 'now' notification only for deletion statuses."""
    async with get_db() as db:
        await db.execute(
            "UPDATE media_queue SET status=? WHERE id=?",
            (status, item_id),
        )
        if status in ("deleted", "deleting"):
            await db.execute(
                "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                (item_id, "now"),
            )
        await db.commit()


# ─── Expert rules ──────────────────────────────────────────────────────────────
async def save_expert_rule(rule: "ExpertRule") -> int:
    """Insert or update an expert rule. Returns the rule id."""
    import json
    conditions_json = json.dumps([c.model_dump() for c in rule.conditions])
    async with get_db() as db:
        if rule.id:
            rowcount = await db.execute_write(
                "UPDATE expert_rules SET name=?, library_id=?, conditions=?, operator=?, "
                "action=?, enabled=?, priority=? WHERE id=?",
                (rule.name, rule.library_id, conditions_json, rule.operator.value,
                 rule.action.value, int(rule.enabled), rule.priority, rule.id),
            )
            if rowcount == 0:
                raise ValueError(f"expert rule id={rule.id} not found")
            await db.commit()
            return rule.id
        new_id = await db.execute(
            "INSERT INTO expert_rules (name, library_id, conditions, operator, action, enabled, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule.name, rule.library_id, conditions_json, rule.operator.value,
             rule.action.value, int(rule.enabled), rule.priority),
        )
        await db.commit()
        return new_id


async def get_expert_rules(*, enabled_only: bool = False) -> list:
    """Return all expert rules ordered by priority then id."""
    import json
    from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, RuleOperator, RuleAction
    where = "WHERE enabled=1" if enabled_only else ""
    async with get_db() as db:
        rows = await db.fetch_all(
            f"SELECT * FROM expert_rules {where} ORDER BY priority ASC, id ASC"
        )
    result = []
    for d in rows:
        try:
            conditions = [_Condition(**c) for c in json.loads(d.get("conditions") or "[]")]
        except Exception as exc:
            logger.warning("Failed to deserialize conditions for rule id=%s: %s", d.get("id"), exc)
            conditions = []
        result.append(_ExpertRule(
            id=d["id"], name=d["name"], library_id=d.get("library_id"),
            conditions=conditions,
            operator=RuleOperator(d["operator"]),
            action=RuleAction(d["action"]),
            enabled=bool(d["enabled"]),
            priority=d["priority"],
            created_at=d.get("created_at"),
        ))
    return result


async def get_expert_rule_by_id(rule_id: int):
    """Return a single ExpertRule by id, or None if not found."""
    import json
    from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, RuleOperator, RuleAction
    async with get_db() as db:
        d = await db.fetch_one(
            "SELECT * FROM expert_rules WHERE id=?", (rule_id,)
        )
    if d is None:
        return None
    try:
        conditions = [_Condition(**c) for c in json.loads(d.get("conditions") or "[]")]
    except Exception as exc:
        logger.warning("Failed to deserialize conditions for rule id=%s: %s", d.get("id"), exc)
        conditions = []
    return _ExpertRule(
        id=d["id"], name=d["name"], library_id=d.get("library_id"),
        conditions=conditions,
        operator=RuleOperator(d["operator"]),
        action=RuleAction(d["action"]),
        enabled=bool(d["enabled"]),
        priority=d["priority"],
        created_at=d.get("created_at"),
    )


async def delete_expert_rule(rule_id: int) -> None:
    """Delete an expert rule by id."""
    async with get_db() as db:
        await db.execute("DELETE FROM expert_rules WHERE id=?", (rule_id,))
        await db.commit()
