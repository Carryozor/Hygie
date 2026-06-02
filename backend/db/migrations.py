# backend/db/migrations.py
"""Simple versioned migration runner.

Each migration is a tuple (id, description, async_fn) run exactly once.
State tracked in the `schema_migrations` table.

Usage:
    from .migrations import run_migrations
    await run_migrations()
"""
import logging
from .engine import get_db, DIALECT

logger = logging.getLogger(__name__)

# Dialect-aware DDL for the migrations tracking table
_CREATE_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id          TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT '',
    description TEXT
)
"""

_CREATE_TABLE_MARIADB = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id          VARCHAR(64)  NOT NULL,
    applied_at  VARCHAR(32)  NOT NULL DEFAULT '',
    description TEXT,
    PRIMARY KEY (id)
) ENGINE=InnoDB CHARSET=utf8mb4
"""


async def _ensure_migrations_table() -> None:
    ddl = _CREATE_TABLE_MARIADB if DIALECT == "mariadb" else _CREATE_TABLE_SQLITE
    async with get_db() as db:
        await db.execute(ddl)
        await db.commit()


async def _is_applied(migration_id: str) -> bool:
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT id FROM schema_migrations WHERE id=?", (migration_id,)
        )
    return row is not None


async def _mark_applied(migration_id: str, description: str) -> None:
    from .utils import now_utc
    ts = now_utc().isoformat()
    async with get_db() as db:
        if DIALECT == "mariadb":
            await db.execute(
                "INSERT IGNORE INTO schema_migrations (id, applied_at, description) VALUES (?, ?, ?)",
                (migration_id, ts, description),
            )
        else:
            await db.execute(
                "INSERT OR IGNORE INTO schema_migrations (id, applied_at, description) VALUES (?, ?, ?)",
                (migration_id, ts, description),
            )
        await db.commit()


async def run_migrations() -> int:
    """Run all pending migrations. Returns number of migrations applied."""
    await _ensure_migrations_table()
    applied = 0
    for migration_id, description, fn in _MIGRATIONS:
        if not await _is_applied(migration_id):
            logger.info("Applying migration %s: %s", migration_id, description)
            try:
                await fn()
                await _mark_applied(migration_id, description)
                applied += 1
                logger.info("Migration %s applied successfully", migration_id)
            except Exception as e:
                logger.error("Migration %s failed: %s", migration_id, e)
                raise
    if applied:
        logger.info("Applied %d migration(s)", applied)
    return applied


# ─── Migration registry ───────────────────────────────────────────────────────
# Each entry: (unique_id, description, async callable)
# NEVER remove or reorder — only append new entries.

async def _m001_no_op():
    """Placeholder — establishes the migration tracking baseline."""
    pass


async def _m002_ensure_seen_status_on_logs():
    """Ensure logs.seen_status column exists (was added manually in earlier versions)."""
    async with get_db() as db:
        cols = await db.table_columns("logs")
        if "seen_status" not in cols:
            await db.execute("ALTER TABLE logs ADD COLUMN seen_status TEXT")
            await db.commit()


async def _m003_ensure_grace_days_on_expert_rules():
    """Ensure expert_rules.grace_days column exists."""
    async with get_db() as db:
        cols = await db.table_columns("expert_rules")
        if "grace_days" not in cols:
            await db.execute(
                "ALTER TABLE expert_rules ADD COLUMN grace_days INTEGER NOT NULL DEFAULT 7"
            )
            await db.commit()


async def _m004_ensure_refresh_tokens_table():
    """Ensure refresh_tokens table exists (added in Plan D)."""
    async with get_db() as db:
        if not await db.table_exists("refresh_tokens"):
            if DIALECT == "mariadb":
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS refresh_tokens (
                        id         INT          NOT NULL AUTO_INCREMENT,
                        user_id    INT          NOT NULL,
                        token_hash VARCHAR(255) NOT NULL,
                        expires_at VARCHAR(32)  NOT NULL,
                        created_at VARCHAR(32)  NOT NULL DEFAULT '',
                        revoked    TINYINT      NOT NULL DEFAULT 0,
                        PRIMARY KEY (id),
                        UNIQUE KEY uq_rt_token (token_hash),
                        CONSTRAINT fk_rt_user FOREIGN KEY (user_id)
                            REFERENCES users(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB CHARSET=utf8mb4
                """)
            else:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS refresh_tokens (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        token_hash TEXT    NOT NULL UNIQUE,
                        expires_at TEXT    NOT NULL,
                        created_at TEXT    NOT NULL DEFAULT '',
                        revoked    INTEGER NOT NULL DEFAULT 0
                    )
                """)
            await db.commit()


async def _m005_normalize_library_server_id():
    """Set server_id='0' for libraries where it is NULL or empty.

    Libraries created before multi-server support had no server_id. They must
    be explicitly assigned to the legacy server '0' so they don't accidentally
    match all server queries.
    """
    async with get_db() as db:
        await db.execute_write(
            "UPDATE libraries SET server_id='0' WHERE server_id IS NULL OR server_id=''",
            (),
        )
        await db.commit()


async def _m006_fix_mariadb_expert_rules_schema():
    """Ensure expert_rules has library_ids and grace_days columns (MariaDB fix).

    The original MariaDB schema was missing these columns and had the wrong type
    for library_id (INT instead of VARCHAR). This migration adds the missing
    columns; the type fix applies to new tables only (existing data unaffected
    since the column was unused on MariaDB).
    """
    async with get_db() as db:
        cols = await db.table_columns("expert_rules")
        if "library_ids" not in cols:
            await db.execute(
                "ALTER TABLE expert_rules ADD COLUMN library_ids LONGTEXT DEFAULT NULL"
                if DIALECT == "mariadb" else
                "ALTER TABLE expert_rules ADD COLUMN library_ids TEXT DEFAULT NULL"
            )
        if "grace_days" not in cols:
            await db.execute(
                "ALTER TABLE expert_rules ADD COLUMN grace_days INT NOT NULL DEFAULT 7"
                if DIALECT == "mariadb" else
                "ALTER TABLE expert_rules ADD COLUMN grace_days INTEGER NOT NULL DEFAULT 7"
            )
        await db.commit()


async def _m007_migrate_notification_columns():
    """One-time migration: copy legacy notified_* flags into the notifications table.

    Older versions tracked notification state via boolean columns on media_queue
    (notified_30d, notified_7d, etc.). The current schema uses a separate
    notifications table. This migration runs once at startup instead of on every
    deletion cycle.
    """
    async with get_db() as db:
        for col, threshold in [
            ("notified_30d", "30d"),
            ("notified_7d",  "7d"),
            ("notified_1d",  "1d"),
            ("notified_now", "now"),
            ("notified_detected", "detected"),
        ]:
            try:
                if DIALECT == "mariadb":
                    await db.execute(
                        f"INSERT IGNORE INTO notifications (media_id, threshold) "
                        f"SELECT id, %s FROM media_queue WHERE {col}=1",
                        (threshold,),
                    )
                else:
                    await db.execute(
                        f"INSERT OR IGNORE INTO notifications (media_id, threshold) "
                        f"SELECT id, ? FROM media_queue WHERE {col}=1",
                        (threshold,),
                    )
            except Exception:
                pass  # column may not exist on very old DBs — safe to skip

        # Also migrate intermediate notified_thresholds JSON column if it exists
        import json as _json
        try:
            rows = await db.fetch_all(
                "SELECT id, notified_thresholds FROM media_queue"
                " WHERE notified_thresholds IS NOT NULL AND notified_thresholds != '[]'"
            )
            for row in rows:
                media_id, raw = row["id"], row["notified_thresholds"]
                for entry in _json.loads(raw or "[]"):
                    if entry == "migrated":
                        continue
                    threshold = f"{entry}d" if isinstance(entry, int) else str(entry)
                    if DIALECT == "mariadb":
                        await db.execute(
                            "INSERT IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                            (media_id, threshold),
                        )
                    else:
                        await db.execute(
                            "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                            (media_id, threshold),
                        )
        except Exception:
            pass

        await db.commit()


_MIGRATIONS = [
    ("m001", "Establish migration tracking baseline",              _m001_no_op),
    ("m002", "Ensure logs.seen_status column",                     _m002_ensure_seen_status_on_logs),
    ("m003", "Ensure expert_rules.grace_days column",              _m003_ensure_grace_days_on_expert_rules),
    ("m004", "Ensure refresh_tokens table",                        _m004_ensure_refresh_tokens_table),
    ("m005", "Normalize library server_id: NULL/empty → '0'",      _m005_normalize_library_server_id),
    ("m006", "Fix MariaDB expert_rules: add library_ids, grace_days", _m006_fix_mariadb_expert_rules_schema),
    ("m007", "Migrate legacy notified_* columns to notifications table", _m007_migrate_notification_columns),
]
