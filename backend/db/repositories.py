"""Database query functions — single source of truth for media_queue and libraries SQL."""
import json
import logging
from typing import Optional

from .engine import get_db
from .utils import now_utc, STATUS_PENDING, STATUS_DELETING, STATUS_DELETED
from ..rules.models import ExpertRule as _ExpertRule, Condition as _Condition, ConditionGroup as _ConditionGroup, RuleOperator, RuleAction

logger = logging.getLogger(__name__)

# Columns allowed in update_enrichment_fields — guard against injection from caller dicts
_ENRICH_ALLOWED = frozenset({"poster_url", "seerr_id", "seerr_user_id", "seerr_username", "seerr_request_url"})


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
            except Exception as rollback_err:
                logger.warning("insert_queue_entries_batch: rollback failed: %s", rollback_err)
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


# ─── media_queue reads ────────────────────────────────────────────────────────

async def get_by_id(item_id: int) -> Optional[dict]:
    """Fetch a single media_queue row by primary key."""
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM media_queue WHERE id=?", (item_id,))


async def get_by_emby_id(emby_id: str) -> Optional[dict]:
    """Fetch a media_queue row by emby_id (any status)."""
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM media_queue WHERE emby_id=?", (emby_id,))


async def get_pending_item_by_emby_id(emby_id: str) -> Optional[dict]:
    """Fetch a pending media_queue row by emby_id."""
    async with get_db() as db:
        return await db.fetch_one(
            "SELECT id, detected_at, seerr_user_id, delete_at FROM media_queue "
            "WHERE emby_id=? AND status='pending'",
            (emby_id,),
        )


async def get_pending_by_library(library_id: str) -> list[dict]:
    """Return all pending rows for a library, regardless of delete_at."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT * FROM media_queue WHERE library_id=? AND status='pending'",
            (library_id,),
        )


async def get_all_emby_ids() -> set[str]:
    """Return emby_ids of all rows in media_queue (any status)."""
    async with get_db() as db:
        rows = await db.fetch_all("SELECT emby_id FROM media_queue")
    return {r["emby_id"] for r in rows}


async def get_queued_ids_for_server(server_id: str) -> set[str]:
    """Return queued emby_ids that belong to a specific server (via library JOIN).

    Needed for Plex scanners: Plex rating keys and Emby IDs are both sequential
    integers and collide; a global queued_ids set would incorrectly filter items.
    """
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT mq.emby_id FROM media_queue mq "
            "JOIN libraries l ON mq.library_id = l.id "
            "WHERE l.server_id = ?",
            (server_id,),
        )
    return {r["emby_id"] for r in rows}


async def get_status_counts() -> dict[str, int]:
    """Return {status: count} for all statuses present in media_queue."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT status, COUNT(*) AS cnt FROM media_queue GROUP BY status"
        )
    return {r["status"]: r["cnt"] for r in rows}


async def get_radarr_ids(*, status: str = STATUS_PENDING) -> list[int]:
    """Return non-NULL radarr_ids for the given status."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT radarr_id FROM media_queue WHERE status=? AND radarr_id IS NOT NULL",
            (status,),
        )
    return [r["radarr_id"] for r in rows]


async def get_sonarr_ids(*, status: str = STATUS_PENDING) -> list[int]:
    """Return non-NULL sonarr_ids for the given status."""
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT sonarr_id FROM media_queue WHERE status=? AND sonarr_id IS NOT NULL",
            (status,),
        )
    return [r["sonarr_id"] for r in rows]


async def get_pending_with_tmdb() -> list[dict]:
    """Return pending rows with a non-empty tmdb_id (for TMDB cross-reference)."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT tmdb_id, media_type FROM media_queue "
            "WHERE status='pending' AND tmdb_id != '' AND tmdb_id IS NOT NULL"
        )


async def get_pending_before(cutoff_iso: str) -> list[dict]:
    """Return pending rows whose delete_at <= cutoff_iso, ordered ascending."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT id, title, media_type, library_name, delete_at, poster_url, seerr_username "
            "FROM media_queue WHERE status='pending' AND delete_at <= ? "
            "ORDER BY delete_at ASC",
            (cutoff_iso,),
        )


async def get_pending_before_for_server(cutoff_iso: str, server_id: str) -> list[dict]:
    """Return pending rows for a specific server whose delete_at <= cutoff_iso."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT mq.emby_id, mq.title, mq.delete_at, mq.poster_url "
            "FROM media_queue mq JOIN libraries l ON mq.library_id = l.id "
            "WHERE mq.status='pending' AND mq.delete_at <= ? AND l.server_id = ?",
            (cutoff_iso, server_id),
        )


async def get_all_for_enrichment() -> list[dict]:
    """Return columns needed for the Seerr/poster enrichment background task."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT id, emby_id, tmdb_id, media_type, radarr_id, sonarr_id, seerr_username "
            "FROM media_queue"
        )


async def get_pending_for_poster_regen() -> list[dict]:
    """Return columns needed for poster URL regeneration (pending items only)."""
    async with get_db() as db:
        return await db.fetch_all(
            "SELECT id, emby_id, tmdb_id, media_type, radarr_id, sonarr_id "
            "FROM media_queue WHERE status='pending'"
        )


async def get_poster_url_by_emby_id(emby_id: str) -> Optional[str]:
    """Return the poster_url for a queued item, or None if not found."""
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT poster_url FROM media_queue WHERE emby_id=?", (emby_id,)
        )
    return row["poster_url"] if row else None


# ─── media_queue updates ──────────────────────────────────────────────────────

async def reset_deleting_to_pending() -> int:
    """Recover items stuck in 'deleting' after a crash. Returns the row count recovered."""
    async with get_db() as db:
        n = await db.execute_write(
            "UPDATE media_queue SET status=? WHERE status=?",
            (STATUS_PENDING, STATUS_DELETING),
        )
        await db.commit()
    return n


async def claim_for_deletion(item_id: int) -> bool:
    """Atomically transition a pending item to 'deleting'. Returns False if already claimed."""
    async with get_db() as db:
        claimed = await db.execute_write(
            "UPDATE media_queue SET status=? WHERE id=? AND status=?",
            (STATUS_DELETING, item_id, STATUS_PENDING),
        )
        await db.commit()
    return bool(claimed)


async def update_last_played_scrobble(emby_id: str, last_played_iso: str) -> None:
    """Update last_played and reset status to 'pending' (Plex scrobble webhook)."""
    async with get_db() as db:
        await db.execute_write(
            "UPDATE media_queue SET last_played=?, status='pending' WHERE emby_id=?",
            (last_played_iso, emby_id),
        )
        await db.commit()


async def update_queue_item_dates(
    item_id: int,
    delete_at: str,
    last_played_iso: Optional[str],
    view_count: int,
) -> None:
    """Update delete_at, last_played, and view_count for a queued item."""
    async with get_db() as db:
        await db.execute(
            "UPDATE media_queue SET delete_at=?, last_played=?, view_count=? WHERE id=?",
            (delete_at, last_played_iso, view_count, item_id),
        )
        await db.commit()


async def update_activity_log_batch(params: list[tuple]) -> None:
    """Batch-update last_played/view_count from scan activity log via executemany.

    Each param tuple is (last_played_iso, emby_id, last_played_iso) — the third
    value is used in the WHERE clause to only advance the timestamp, never regress.
    """
    if not params:
        return
    async with get_db() as db:
        await db.executemany(
            "UPDATE media_queue SET last_played=?, view_count=MAX(COALESCE(view_count,0),1) "
            "WHERE emby_id=? AND status='pending' "
            "AND (last_played IS NULL OR last_played='' OR last_played < ?)",
            params,
        )
        await db.commit()


async def update_poster(item_id: int, poster_url: str) -> None:
    """Update the poster_url for a single item."""
    async with get_db() as db:
        await db.execute(
            "UPDATE media_queue SET poster_url=? WHERE id=?", (poster_url, item_id)
        )
        await db.commit()


async def update_enrichment_fields(item_id: int, fields: dict) -> None:
    """Update a safe subset of enrichment fields (poster_url, seerr_*) for one item."""
    safe = {k: v for k, v in fields.items() if k in _ENRICH_ALLOWED}
    if not safe:
        return
    set_clause = ", ".join(f"{k}=?" for k in safe)
    async with get_db() as db:
        await db.execute(
            f"UPDATE media_queue SET {set_clause} WHERE id=?",
            list(safe.values()) + [item_id],
        )
        await db.commit()


# ─── media_queue deletes ──────────────────────────────────────────────────────

async def delete_by_id(item_id: int) -> None:
    """Delete a single media_queue row by primary key."""
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue WHERE id=?", (item_id,))
        await db.commit()


async def delete_by_emby_id(emby_id: str) -> None:
    """Delete all media_queue rows with the given emby_id."""
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue WHERE emby_id=?", (emby_id,))
        await db.commit()


async def delete_by_ids(item_ids: list[int]) -> None:
    """Delete multiple media_queue rows by primary key in one statement."""
    if not item_ids:
        return
    placeholders = ",".join("?" * len(item_ids))
    async with get_db() as db:
        await db.execute(f"DELETE FROM media_queue WHERE id IN ({placeholders})", item_ids)
        await db.commit()


async def purge_by_status(status: str) -> int:
    """Delete all rows with the given status. Returns the count deleted."""
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM media_queue WHERE status=?", (status,)
        )
        count = row["cnt"] if row else 0
        if count:
            await db.execute("DELETE FROM media_queue WHERE status=?", (status,))
            await db.commit()
    return count


async def delete_stale_deleted(before_iso: str) -> int:
    """Delete rows with status='deleted' where detected_at < before_iso.

    Returns the count of rows purged (0 if none qualify).
    """
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM media_queue WHERE status='deleted' AND detected_at < ?",
            (before_iso,),
        )
        count = row["cnt"] if row else 0
        if count:
            await db.execute(
                "DELETE FROM media_queue WHERE status='deleted' AND detected_at < ?",
                (before_iso,),
            )
            await db.commit()
    return count


async def delete_stale_pending_no_seerr(library_ids: list[str]) -> int:
    """Remove pending items with no seerr_user_id in the specified library IDs.

    Used by the scanner orchestrator to clean up items from libraries that
    require a Seerr requester but were queued before that requirement was set.
    Returns the count deleted.
    """
    if not library_ids:
        return 0
    placeholders = ",".join("?" * len(library_ids))
    async with get_db() as db:
        n = await db.execute_write(
            f"DELETE FROM media_queue WHERE status='pending' "
            f"AND (seerr_user_id IS NULL OR seerr_user_id = 0) "
            f"AND library_id IN ({placeholders})",
            list(library_ids),
        )
        await db.commit()
    return n


async def delete_pending_by_server(server_id: str) -> int:
    """Remove all pending queue entries from libraries belonging to server_id.

    Called when a media server is disabled or removed. Returns the count deleted.
    """
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT mq.id FROM media_queue mq "
            "JOIN libraries l ON mq.library_id = l.id "
            "WHERE l.server_id = ? AND mq.status = 'pending'",
            (server_id,),
        )
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await db.execute_write(
            f"DELETE FROM media_queue WHERE id IN ({placeholders})", tuple(ids)
        )
        await db.commit()
    return len(ids)
