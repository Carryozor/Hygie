# backend/db/schema.py
"""SQLite schema definitions, migrations, and init_db."""
import os
import json
import logging
from datetime import datetime, timezone

import aiosqlite

from .utils import DB_PATH
from .encryption import _migrate_encrypt_settings, _decrypt_value, _encrypt_value
from .settings_store import DEFAULT_SETTINGS

logger = logging.getLogger(__name__)


# ─── Schema definition ───────────────────────────────────────────────────────
# (table_name, create_sql, list of (col_name, sql_type_with_default) for migrations)
_TABLES = [
    (
        "settings",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        [],
    ),
    (
        "users",
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        [],
    ),
    (
        "libraries",
        """CREATE TABLE IF NOT EXISTS libraries (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            emby_library_id TEXT NOT NULL,
            conditions TEXT NOT NULL DEFAULT '[]',
            logic TEXT NOT NULL DEFAULT 'AND',
            grace_days INTEGER NOT NULL DEFAULT 7,
            seerr_conditions TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            server_id TEXT DEFAULT '0',
            deletion_unit TEXT NOT NULL DEFAULT 'episode'
        )""",
        [
            ("seerr_conditions", "TEXT NOT NULL DEFAULT '[]'"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT"),
            ("server_id", "TEXT DEFAULT '0'"),
            ("deletion_unit", "TEXT NOT NULL DEFAULT 'episode'"),
        ],
    ),
    (
        "media_queue",
        """CREATE TABLE IF NOT EXISTS media_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emby_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            library_id TEXT NOT NULL,
            library_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            poster_url TEXT DEFAULT '',
            tmdb_id TEXT DEFAULT '',
            seerr_id INTEGER,
            seerr_user_id INTEGER,
            seerr_username TEXT DEFAULT '',
            seerr_request_url TEXT DEFAULT '',
            radarr_id INTEGER,
            sonarr_id INTEGER,
            detected_at TEXT NOT NULL,
            delete_at TEXT NOT NULL,
            added_date TEXT,
            last_played TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            notified_30d INTEGER DEFAULT 0,
            notified_7d INTEGER DEFAULT 0,
            notified_1d INTEGER DEFAULT 0,
            notified_now INTEGER DEFAULT 0,
            notified_detected INTEGER DEFAULT 0,
            notified_thresholds TEXT DEFAULT '[]'
        )""",
        [
            ("poster_url", "TEXT DEFAULT ''"),
            ("tmdb_id", "TEXT DEFAULT ''"),
            ("seerr_id", "INTEGER"),
            ("seerr_user_id", "INTEGER"),
            ("seerr_username", "TEXT DEFAULT ''"),
            ("seerr_request_url", "TEXT DEFAULT ''"),
            ("radarr_id", "INTEGER"),
            ("sonarr_id", "INTEGER"),
            ("added_date", "TEXT"),
            ("last_played", "TEXT"),
            ("notified_30d", "INTEGER DEFAULT 0"),
            ("notified_7d", "INTEGER DEFAULT 0"),
            ("notified_1d", "INTEGER DEFAULT 0"),
            ("notified_now", "INTEGER DEFAULT 0"),
            ("notified_detected", "INTEGER DEFAULT 0"),
            ("notified_thresholds", "TEXT DEFAULT '[]'"),
            ("sonarr_series_id", "INTEGER"),
            ("season_number", "INTEGER"),
        ],
    ),
    (
        "ignored_media",
        """CREATE TABLE IF NOT EXISTS ignored_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emby_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            media_type TEXT DEFAULT 'Movie',
            library_id TEXT DEFAULT '',
            library_name TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            poster_url TEXT DEFAULT '',
            tmdb_id TEXT DEFAULT '',
            seerr_id INTEGER,
            seerr_user_id INTEGER,
            seerr_username TEXT DEFAULT '',
            seerr_request_url TEXT DEFAULT '',
            radarr_id INTEGER,
            sonarr_id INTEGER,
            added_date TEXT,
            last_played TEXT,
            reason TEXT DEFAULT '',
            ignored_at TEXT NOT NULL,
            expire_at TEXT
        )""",
        [
            ("media_type", "TEXT DEFAULT 'Movie'"),
            ("library_id", "TEXT DEFAULT ''"),
            ("library_name", "TEXT DEFAULT ''"),
            ("file_path", "TEXT DEFAULT ''"),
            ("poster_url", "TEXT DEFAULT ''"),
            ("tmdb_id", "TEXT DEFAULT ''"),
            ("seerr_id", "INTEGER"),
            ("seerr_user_id", "INTEGER"),
            ("seerr_username", "TEXT DEFAULT ''"),
            ("seerr_request_url", "TEXT DEFAULT ''"),
            ("radarr_id", "INTEGER"),
            ("sonarr_id", "INTEGER"),
            ("added_date", "TEXT"),
            ("last_played", "TEXT"),
            ("expire_at", "TEXT"),
        ],
    ),
    (
        "seerr_user_rules",
        """CREATE TABLE IF NOT EXISTS seerr_user_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seerr_user_id INTEGER NOT NULL,
            seerr_username TEXT NOT NULL,
            library_id TEXT NOT NULL,
            grace_days INTEGER NOT NULL DEFAULT 30,
            enabled INTEGER NOT NULL DEFAULT 1,
            discord_id TEXT DEFAULT '',
            created_at TEXT
        )""",
        [
            ("discord_id", "TEXT DEFAULT ''"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT"),
        ],
    ),
    (
        "logs",
        """CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL
        )""",
        [],
    ),
    (
        "job_history",
        """CREATE TABLE IF NOT EXISTS job_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT,
            message TEXT
        )""",
        [],
    ),
    (
        "stats_history",
        """CREATE TABLE IF NOT EXISTS stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            total_deleted INTEGER DEFAULT 0,
            total_scanned INTEGER DEFAULT 0,
            space_freed_bytes INTEGER DEFAULT 0,
            month TEXT NOT NULL
        )""",
        [],
    ),
    (
        "rate_limit",
        """CREATE TABLE IF NOT EXISTS rate_limit (
            key TEXT NOT NULL,
            ts REAL NOT NULL
        )""",
        [],
    ),
    (
        "expert_rules",
        """CREATE TABLE IF NOT EXISTS expert_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            library_id  INTEGER,
            conditions  TEXT    NOT NULL DEFAULT '[]',
            operator    TEXT    NOT NULL DEFAULT 'AND',
            action      TEXT    NOT NULL DEFAULT 'queue',
            enabled     INTEGER NOT NULL DEFAULT 1,
            priority    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )""",
        [],
    ),
    (
        "notifications",
        """CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id    INTEGER NOT NULL REFERENCES media_queue(id) ON DELETE CASCADE,
            threshold   TEXT    NOT NULL,
            sent_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            UNIQUE (media_id, threshold)
        )""",
        [],
    ),
]


_KNOWN_TABLES = frozenset({
    "settings", "users", "libraries", "media_queue",
    "ignored_media", "seerr_user_rules", "logs", "job_history", "stats_history",
    "rate_limit", "expert_rules", "notifications",
    # Legacy names used during migration
    "logs_legacy", "job_history_legacy",
})

async def _table_columns(db, table: str) -> set:
    """Return the set of column names for a given table."""
    if table not in _KNOWN_TABLES:
        raise ValueError(f"Unknown table: {table!r}")
    cols = set()
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        async for row in cur:
            cols.add(row[1])
    return cols


async def _table_exists(db, table: str) -> bool:
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cur:
        return await cur.fetchone() is not None


async def _migrate_logs_table(db):
    """Migrate legacy logs table: 'timestamp' -> 'ts', add 'source' if missing."""
    if not await _table_exists(db, "logs"):
        return
    cols = await _table_columns(db, "logs")
    if "ts" in cols:
        return
    logger.info("Migrating logs table to new schema (ts column)")
    ts_col = "timestamp" if "timestamp" in cols else None
    level_col = "level" if "level" in cols else None
    source_col = "source" if "source" in cols else None
    message_col = "message" if "message" in cols else None
    await db.execute("ALTER TABLE logs RENAME TO logs_legacy")
    await db.execute("""
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL
        )
    """)
    if ts_col and level_col and message_col:
        src = (
            f"{ts_col}, {level_col}, "
            + (source_col if source_col else "'system'")
            + f", {message_col}"
        )
        await db.execute(
            f"INSERT INTO logs (ts, level, source, message) SELECT {src} FROM logs_legacy"
        )
    await db.execute("DROP TABLE logs_legacy")
    logger.info("Logs table migrated successfully")


async def _migrate_job_history_table(db):
    """Migrate legacy job_history: may have 'type' instead of 'job_type', etc."""
    if not await _table_exists(db, "job_history"):
        return
    cols = await _table_columns(db, "job_history")
    if {"job_type", "started_at"}.issubset(cols):
        return
    logger.info("Migrating job_history table to new schema")
    type_col = "job_type" if "job_type" in cols else ("type" if "type" in cols else None)
    start_col = "started_at" if "started_at" in cols else ("started" if "started" in cols else None)
    finish_col = "finished_at" if "finished_at" in cols else ("finished" if "finished" in cols else None)
    status_col = "status" if "status" in cols else None
    message_col = "message" if "message" in cols else None
    await db.execute("ALTER TABLE job_history RENAME TO job_history_legacy")
    await db.execute("""
        CREATE TABLE job_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT,
            message TEXT
        )
    """)
    if type_col and start_col:
        cols_in = [type_col, start_col]
        cols_out = ["job_type", "started_at"]
        for src, dst in [(finish_col, "finished_at"), (status_col, "status"), (message_col, "message")]:
            if src:
                cols_in.append(src); cols_out.append(dst)
        await db.execute(
            f"INSERT INTO job_history ({', '.join(cols_out)}) "
            f"SELECT {', '.join(cols_in)} FROM job_history_legacy"
        )
    await db.execute("DROP TABLE job_history_legacy")
    logger.info("job_history table migrated successfully")


async def _ensure_columns(db, table: str, expected: list):
    """Add missing columns to a table (ALTER TABLE ADD COLUMN, idempotent)."""
    if not expected or not await _table_exists(db, table):
        return
    existing = await _table_columns(db, table)
    for col_name, col_def in expected:
        if col_name not in existing:
            try:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                )
                logger.info(f"Migration: added {table}.{col_name}")
            except Exception as e:
                logger.warning(f"Could not add column {table}.{col_name}: {e}")


# ─── Schema & migrations ──────────────────────────────────────────────────────
async def init_db():
    """Create tables, run migrations, seed defaults."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:  # skip for in-memory (:memory:) or bare filenames
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # 1. Migrate legacy tables if needed (BEFORE CREATE TABLE)
        await _migrate_logs_table(db)
        await _migrate_job_history_table(db)

        # 2. Create tables if missing (no-op if they exist)
        for table_name, create_sql, _ in _TABLES:
            await db.execute(create_sql)

        # 3. Add missing columns to existing tables
        for table_name, _, expected_cols in _TABLES:
            await _ensure_columns(db, table_name, expected_cols)

        # 4. Indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_status ON media_queue(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_delete_at ON media_queue(delete_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_emby_id ON media_queue(emby_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_library_id ON media_queue(library_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ignored_emby_id ON ignored_media(emby_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_key ON rate_limit(key, ts)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_media ON notifications(media_id)"
        )

        # 5. Seed defaults (INSERT OR IGNORE preserves user values)
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )

        await db.commit()

        # 6. Encrypt any plaintext sensitive settings (no-op if key not configured)
        await _migrate_encrypt_settings(db)

        # 7. Migrate legacy emby_url/key to media_servers[0] (idempotent)
        async with db.execute("SELECT value FROM settings WHERE key='media_servers'") as cur:
            ms_row = await cur.fetchone()
        current_ms_raw = ms_row[0] if ms_row else "[]"
        current_ms = _decrypt_value(current_ms_raw) if current_ms_raw else "[]"
        if current_ms in ("[]", "", None, "null"):
            async with db.execute("SELECT value FROM settings WHERE key='emby_url'") as cur:
                url_row = await cur.fetchone()
            emby_url = url_row[0] if url_row else ""
            if emby_url and not emby_url.startswith("enc:"):
                # Use _decrypt_value so encrypted keys are read as plaintext
                async with db.execute("SELECT value FROM settings WHERE key='emby_api_key'") as cur:
                    key_row = await cur.fetchone()
                async with db.execute("SELECT value FROM settings WHERE key='emby_external_url'") as cur:
                    ext_row = await cur.fetchone()
                async with db.execute("SELECT value FROM settings WHERE key='media_server_type'") as cur:
                    type_row = await cur.fetchone()
                raw_key = (key_row[0] if key_row else "") or ""
                raw_ext = (ext_row[0] if ext_row else "") or ""
                server0 = {
                    "id": "0", "name": "Serveur Principal",
                    "url": emby_url,
                    "api_key": _decrypt_value(raw_key),   # decrypt if stored encrypted
                    "ext_url": _decrypt_value(raw_ext),
                    "type": (type_row[0] if type_row else "") or "",
                    "enabled": True,
                }
                # Encrypt immediately so API keys never rest in plaintext in DB
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("media_servers", _encrypt_value(json.dumps([server0])))
                )
                await db.commit()

        # 8. Migrate interval settings: hours → minutes (idempotent)
        async with db.execute("SELECT value FROM settings WHERE key='scan_interval_hours'") as cur:
            row = await cur.fetchone()
        if row and row[0] and row[0].strip().isdigit():
            async with db.execute("SELECT value FROM settings WHERE key='scan_interval_minutes'") as cur2:
                existing = await cur2.fetchone()
            if not existing or existing[0] == "360":
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("scan_interval_minutes", str(int(row[0]) * 60))
                )
        async with db.execute("SELECT value FROM settings WHERE key='deletion_check_interval_hours'") as cur:
            row = await cur.fetchone()
        if row and row[0] and row[0].strip().isdigit():
            async with db.execute("SELECT value FROM settings WHERE key='deletion_check_interval_minutes'") as cur2:
                existing = await cur2.fetchone()
            if not existing or existing[0] == "60":
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("deletion_check_interval_minutes", str(int(row[0]) * 60))
                )
        await db.commit()

        # 9a. One-time: purge per-item "Ignoré (non demandé sur Seerr)" log spam
        #     These were logged at INFO before being moved to DEBUG.
        async with db.execute(
            "DELETE FROM logs WHERE message LIKE 'Ignoré (non demandé sur Seerr)%'"
            " OR message LIKE 'Ignoré (utilisateur Seerr%'"
        ) as cur:
            purged = cur.rowcount
        if purged:
            logger.info(f"Purged {purged} verbose per-item scan log entries")
            await db.commit()

        # 9. Mark orphaned job_history entries (process killed mid-job) as interrupted
        ts_now = datetime.now(timezone.utc).isoformat()
        async with db.execute(
            "UPDATE job_history SET finished_at=?, status='interrupted', message='Interrompu (redémarrage)' "
            "WHERE finished_at IS NULL OR status IS NULL",
            (ts_now,)
        ) as cur:
            if cur.rowcount:
                logger.info(f"Marked {cur.rowcount} orphaned job(s) as interrupted")
        await db.commit()

    logger.info(f"Database initialized: {DB_PATH}")
