# backend/db/migrations.py
"""Simple versioned migration runner.

Each migration is a tuple (id, description, async_fn) run exactly once.
State tracked in the `schema_migrations` table.

Usage:
    from .migrations import run_migrations
    await run_migrations()
"""
import logging
from .engine import get_db

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id          TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    description TEXT
)
"""


async def _ensure_migrations_table() -> None:
    async with get_db() as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()


async def _is_applied(migration_id: str) -> bool:
    async with get_db() as db:
        row = await db.fetch_one(
            "SELECT id FROM schema_migrations WHERE id=?", (migration_id,)
        )
    return row is not None


async def _mark_applied(migration_id: str, description: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations (id, description) VALUES (?, ?)",
            (migration_id, description),
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
        rows = await db.fetch_all("PRAGMA table_info(logs)")
        cols = [r["name"] for r in rows]
        if "seen_status" not in cols:
            await db.execute("ALTER TABLE logs ADD COLUMN seen_status TEXT")
            await db.commit()


async def _m003_ensure_grace_days_on_expert_rules():
    """Ensure expert_rules.grace_days column exists."""
    async with get_db() as db:
        rows = await db.fetch_all("PRAGMA table_info(expert_rules)")
        cols = [r["name"] for r in rows]
        if "grace_days" not in cols:
            await db.execute("ALTER TABLE expert_rules ADD COLUMN grace_days INTEGER NOT NULL DEFAULT 7")
            await db.commit()


async def _m004_ensure_refresh_tokens_table():
    """Ensure refresh_tokens table exists (added in Plan D)."""
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT    NOT NULL UNIQUE,
                expires_at TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                revoked    INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()


_MIGRATIONS = [
    ("m001", "Establish migration tracking baseline", _m001_no_op),
    ("m002", "Ensure logs.seen_status column", _m002_ensure_seen_status_on_logs),
    ("m003", "Ensure expert_rules.grace_days column", _m003_ensure_grace_days_on_expert_rules),
    ("m004", "Ensure refresh_tokens table", _m004_ensure_refresh_tokens_table),
]
