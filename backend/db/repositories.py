"""Database query functions — single source of truth for media_queue and libraries SQL."""
import json
import logging

from .engine import get_db
from .utils import now_utc
from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, ConditionGroup as _ConditionGroup, RuleOperator, RuleAction

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
    """Return enabled library rows for the given server_id.

    m005 migration ensures all libraries have an explicit server_id ('0' for legacy),
    so we can match exactly without the IS NULL / = '' fallback.
    """
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT * FROM libraries WHERE enabled=1 AND server_id=?",
            (server_id,),
        )


_INSERT_SQL = """INSERT INTO media_queue
    (emby_id, title, media_type, library_id, library_name, file_path,
     poster_url, tmdb_id, seerr_id, seerr_user_id, seerr_username,
     seerr_request_url, radarr_id, sonarr_id, sonarr_series_id, season_number,
     detected_at, delete_at, added_date, last_played, view_count, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')"""

# Single-row inserts use OR IGNORE so a duplicate emby_id during a scan does
# not abort the entire library loop — the scanner's pre-filter (queued_ids) is
# the primary guard; this is a fail-safe for races.
_INSERT_OR_IGNORE_SQL = _INSERT_SQL.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)


def _entry_params(entry: dict) -> tuple:
    return (
        entry["emby_id"], entry["title"], entry["media_type"],
        entry["library_id"], entry["library_name"], entry["file_path"],
        entry["poster_url"], entry["tmdb_id"],
        entry["seerr_id"], entry["seerr_user_id"], entry["seerr_username"],
        entry["seerr_request_url"], entry["radarr_id"], entry["sonarr_id"],
        entry.get("sonarr_series_id"), entry.get("season_number"),
        entry["detected_at"], entry["delete_at"],
        entry["added_date"], entry["last_played"],
        entry.get("view_count", 0),
    )


async def insert_queue_entry(entry: dict) -> None:
    """Insert one row into media_queue (status='pending'). Duplicate emby_id is silently ignored."""
    async with get_db() as db:
        await db.execute(_INSERT_OR_IGNORE_SQL, _entry_params(entry))
        await db.commit()


async def insert_queue_entries_batch(entries: list) -> None:
    """Insert multiple media_queue rows atomically (all-or-nothing).

    If any entry fails (e.g. duplicate emby_id UNIQUE constraint), the entire
    batch is explicitly rolled back and the exception propagated to the caller.
    Explicit rollback makes the atomicity guarantee portable across SQLite and
    MariaDB without relying on implicit connection-close behavior.
    """
    if not entries:
        return
    params_seq = [_entry_params(e) for e in entries]
    async with get_db() as db:
        try:
            await db.executemany(_INSERT_SQL, params_seq)
            await db.commit()
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise


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

def _parse_condition_groups(raw_json: str, legacy_operator: str = "AND") -> list[_ConditionGroup]:
    """Deserialize conditions JSON, auto-upgrading old flat format to groups."""
    try:
        raw = json.loads(raw_json or "[]")
    except Exception:
        return []
    if not raw:
        return []
    # Old flat format: list of condition dicts with 'field' key
    if isinstance(raw[0], dict) and "field" in raw[0]:
        try:
            return [_ConditionGroup(
                conditions=[_Condition(**c) for c in raw],
                operator=RuleOperator(legacy_operator),
            )]
        except Exception as exc:
            logger.warning("Failed to upgrade flat conditions: %s", exc)
            return []
    # New groups format: list of group dicts with 'conditions' key
    groups = []
    for g in raw:
        try:
            groups.append(_ConditionGroup(
                conditions=[_Condition(**c) for c in g.get("conditions", [])],
                operator=RuleOperator(g.get("operator", "AND")),
            ))
        except Exception as exc:
            logger.warning("Failed to parse condition group: %s", exc)
    return groups


async def save_expert_rule(rule: _ExpertRule) -> int:
    """Insert or update an expert rule. Returns the rule id."""
    conditions_json = json.dumps([g.model_dump() for g in rule.condition_groups])
    library_ids_json = json.dumps(rule.library_ids) if rule.library_ids is not None else None
    async with get_db() as db:
        if rule.id:
            rowcount = await db.execute_write(
                "UPDATE expert_rules SET name=?, library_id=?, library_ids=?, conditions=?, operator=?, "
                "action=?, grace_days=?, enabled=?, priority=? WHERE id=?",
                (rule.name, rule.library_id, library_ids_json, conditions_json, rule.operator.value,
                 rule.action.value, rule.grace_days, int(rule.enabled), rule.priority, rule.id),
            )
            if rowcount == 0:
                raise ValueError(f"expert rule id={rule.id} not found")
            await db.commit()
            return rule.id
        new_id = await db.execute(
            "INSERT INTO expert_rules (name, library_id, library_ids, conditions, operator, action, grace_days, enabled, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule.name, rule.library_id, library_ids_json, conditions_json, rule.operator.value,
             rule.action.value, rule.grace_days, int(rule.enabled), rule.priority),
        )
        await db.commit()
        return new_id


async def get_expert_rules(*, enabled_only: bool = False) -> list:
    """Return all expert rules ordered by priority then id."""
    where = "WHERE enabled=1" if enabled_only else ""
    async with get_db() as db:
        rows = await db.fetch_all(
            f"SELECT * FROM expert_rules {where} ORDER BY priority ASC, id ASC"
        )
    result = []
    for d in rows:
        condition_groups = _parse_condition_groups(
            d.get("conditions") or "[]", d.get("operator", "AND")
        )
        if not condition_groups:
            logger.warning("Rule id=%s has no valid conditions, skipping", d.get("id"))
            continue
        library_ids = None
        raw_lids = d.get("library_ids")
        if raw_lids:
            try:
                library_ids = json.loads(raw_lids)
            except Exception:
                pass
        result.append(_ExpertRule(
            id=d["id"], name=d["name"], library_id=d.get("library_id"),
            library_ids=library_ids,
            condition_groups=condition_groups,
            operator=RuleOperator(d["operator"]),
            action=RuleAction(d["action"]),
            grace_days=d.get("grace_days", 7),
            enabled=bool(d["enabled"]),
            priority=d["priority"],
            created_at=d.get("created_at"),
        ))
    return result


async def get_expert_rule_by_id(rule_id: int):
    """Return a single ExpertRule by id, or None if not found."""
    async with get_db() as db:
        d = await db.fetch_one(
            "SELECT * FROM expert_rules WHERE id=?", (rule_id,)
        )
    if d is None:
        return None
    condition_groups = _parse_condition_groups(
        d.get("conditions") or "[]", d.get("operator", "AND")
    )
    if not condition_groups:
        logger.warning("Rule id=%s has no valid conditions", d.get("id"))
        return None
    library_ids = None
    raw_lids = d.get("library_ids")
    if raw_lids:
        try:
            library_ids = json.loads(raw_lids)
        except Exception:
            pass
    return _ExpertRule(
        id=d["id"], name=d["name"], library_id=d.get("library_id"),
        library_ids=library_ids,
        condition_groups=condition_groups,
        operator=RuleOperator(d["operator"]),
        action=RuleAction(d["action"]),
        grace_days=d.get("grace_days", 7),
        enabled=bool(d["enabled"]),
        priority=d["priority"],
        created_at=d.get("created_at"),
    )


async def delete_expert_rule(rule_id: int) -> None:
    """Delete an expert rule by id."""
    async with get_db() as db:
        await db.execute("DELETE FROM expert_rules WHERE id=?", (rule_id,))
        await db.commit()
