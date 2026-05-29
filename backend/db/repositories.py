"""Database query functions — single source of truth for media_queue and libraries SQL."""
import logging

import aiosqlite

from .utils import now_utc

logger = logging.getLogger(__name__)


async def get_pending_queue(*, db_path: str) -> list[dict]:
    """Return pending media_queue rows whose delete_at has passed."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM media_queue WHERE status='pending' AND delete_at <= ?",
            (now_utc().isoformat(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_queued_and_ignored_ids(*, db_path: str) -> tuple[set, set]:
    """Return (queued_emby_ids, ignored_emby_ids) from a single DB connection."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT emby_id FROM media_queue") as cur:
            queued = {r[0] async for r in cur}
        async with db.execute("SELECT emby_id FROM ignored_media") as cur:
            ignored = {r[0] async for r in cur}
    return queued, ignored


async def get_enabled_libraries(server_id: str, *, db_path: str) -> list[dict]:
    """Return enabled library rows for the given server_id."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM libraries"
            " WHERE enabled=1 AND (server_id=? OR server_id IS NULL OR server_id='')",
            (server_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def insert_queue_entry(entry: dict, *, db_path: str) -> None:
    """Insert one row into media_queue (status='pending')."""
    async with aiosqlite.connect(db_path) as db:
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


async def mark_notified_detected(emby_id: str, *, db_path: str) -> None:
    """Record a 'detected' notification for the pending queue row with this emby_id."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM media_queue WHERE emby_id=? AND status='pending'", (emby_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            await db.execute(
                "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                (row[0], "detected"),
            )
            await db.commit()


async def update_queue_status(item_id: int, status: str, *, db_path: str) -> None:
    """Update status and record a 'now' notification only for deletion statuses."""
    async with aiosqlite.connect(db_path) as db:
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
async def save_expert_rule(rule: "ExpertRule", *, db_path: str) -> int:
    """Insert or update an expert rule. Returns the rule id."""
    import json
    conditions_json = json.dumps([c.model_dump() for c in rule.conditions])
    async with aiosqlite.connect(db_path) as db:
        if rule.id:
            cursor = await db.execute(
                "UPDATE expert_rules SET name=?, library_id=?, conditions=?, operator=?, "
                "action=?, enabled=?, priority=? WHERE id=?",
                (rule.name, rule.library_id, conditions_json, rule.operator.value,
                 rule.action.value, int(rule.enabled), rule.priority, rule.id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"expert rule id={rule.id} not found")
            await db.commit()
            return rule.id
        cursor = await db.execute(
            "INSERT INTO expert_rules (name, library_id, conditions, operator, action, enabled, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule.name, rule.library_id, conditions_json, rule.operator.value,
             rule.action.value, int(rule.enabled), rule.priority),
        )
        await db.commit()
        return cursor.lastrowid


async def get_expert_rules(*, db_path: str, enabled_only: bool = False) -> list:
    """Return all expert rules ordered by priority then id."""
    import json
    from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, RuleOperator, RuleAction
    where = "WHERE enabled=1" if enabled_only else ""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM expert_rules {where} ORDER BY priority ASC, id ASC"
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
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


async def get_expert_rule_by_id(rule_id: int, *, db_path: str):
    """Return a single ExpertRule by id, or None if not found."""
    import json
    from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, RuleOperator, RuleAction
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM expert_rules WHERE id=?", (rule_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
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


async def delete_expert_rule(rule_id: int, *, db_path: str) -> None:
    """Delete an expert rule by id."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM expert_rules WHERE id=?", (rule_id,))
        await db.commit()
