"""
Database — SQLite, schema, settings, logs, job history.

Public API:
  init_db()                 — create tables, run migrations, seed defaults
  get_setting(key)          — read setting
  set_setting(key, value)   — write setting
  add_log(level, msg, src)  — write log + broadcast via WebSocket
  add_job_run(job_type)     — start job, returns run_id
  finish_job_run(id, ...)   — end job
"""
import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "/app/data/hygie.db")

# ─── Status constants ─────────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_DELETED = "deleted"
STATUS_ERROR   = "error"

# ─── HTTP timeout constants (seconds) ─────────────────────────────────────────
TIMEOUT_SHORT  = 10   # fast API calls (auth, status, single item)
TIMEOUT_MEDIUM = 20   # bulk listing calls (movies, series, torrents)
TIMEOUT_LONG   = 30   # paginated or library-wide calls


def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (with or without trailing Z) to an aware datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


logger = logging.getLogger(__name__)

# ─── Encryption (optional) ────────────────────────────────────────────────────
# Sensitive settings are encrypted at rest when HYGIE_ENCRYPTION_KEY is set.
# If the env var is absent, values are stored in plaintext (backward-compatible).

_ENC_PREFIX = "enc:"

SENSITIVE_KEYS = frozenset({
    "emby_api_key",
    "radarr_api_key",
    "sonarr_api_key",
    "seerr_api_key",
    "qbit_password",
    "qbit_proxy_url",
    "discord_webhook",
})

_fernet_instance = None
_fernet_loaded   = False


def _get_fernet():
    """Return a Fernet instance if HYGIE_ENCRYPTION_KEY is configured, else None."""
    global _fernet_instance, _fernet_loaded
    if _fernet_loaded:
        return _fernet_instance
    _fernet_loaded = True
    raw_key = os.environ.get("HYGIE_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet_instance = Fernet(raw_key.encode())
        logger.info("HYGIE_ENCRYPTION_KEY loaded — sensitive settings encrypted at rest")
    except Exception as e:
        logger.warning(f"Invalid HYGIE_ENCRYPTION_KEY ({e}) — storing settings in plaintext")
    return _fernet_instance


def _encrypt_value(value: str) -> str:
    """Encrypt value if Fernet is available and value is non-empty."""
    f = _get_fernet()
    if not f or not value:
        return value
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt_value(value: str) -> str:
    """Decrypt value if it carries the enc: prefix; return as-is otherwise."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    f = _get_fernet()
    if not f:
        logger.warning("Encrypted value found but HYGIE_ENCRYPTION_KEY is not set — cannot decrypt")
        return value
    try:
        return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt setting: {e}")
        return value


async def _migrate_encrypt_settings(db) -> None:
    """Encrypt any plaintext sensitive settings when the encryption key is available."""
    if not _get_fernet():
        return
    placeholders = ",".join("?" * len(SENSITIVE_KEYS))
    async with db.execute(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        list(SENSITIVE_KEYS),
    ) as cur:
        rows = await cur.fetchall()
    migrated = 0
    for key, value in rows:
        if value and not value.startswith(_ENC_PREFIX):
            await db.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (_encrypt_value(value), key),
            )
            migrated += 1
    if migrated:
        await db.commit()
        logger.info(f"Encrypted {migrated} sensitive setting(s) in database")

# Default settings — written ONCE at first init (INSERT OR IGNORE)
DEFAULT_SETTINGS = {
    "emby_url": "",
    "emby_api_key": "",
    "emby_external_url": "",
    "emby_leaving_soon_collection": "",
    "emby_leaving_soon_days": "30",
    "emby_leaving_soon_overlay": "false",
    "radarr_url": "",
    "radarr_api_key": "",
    "sonarr_url": "",
    "sonarr_api_key": "",
    "seerr_url": "",
    "seerr_api_key": "",
    "seerr_external_url": "",
    "qbit_url": "",
    "qbit_proxy_url": "",
    "qbit_user": "",
    "qbit_password": "",
    "qbit_action": "tag_only",  # tag_only | delete_torrent
    "qbit_tag": "Supprimé-Hygie",
    "discord_webhook": "",
    "dry_run": "false",
    "scan_interval_hours": "6",
    "deletion_check_interval_hours": "1",
    "log_level": "INFO",
    "deleted_retention_days": "90",
    "log_retention_days": "14",
    "job_history_retention_days": "90",
    "ui_language": "fr",
}

# ─── WebSocket broadcast ──────────────────────────────────────────────────────
_ws_clients: set = set()

def register_ws(ws):
    _ws_clients.add(ws)

def unregister_ws(ws):
    _ws_clients.discard(ws)

async def _broadcast(payload: dict):
    """Send a payload to all connected WebSocket clients."""
    if not _ws_clients:
        return
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


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
            created_at TEXT
        )""",
        [
            ("seerr_conditions", "TEXT NOT NULL DEFAULT '[]'"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT"),
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
            notified_now INTEGER DEFAULT 0
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
]


_KNOWN_TABLES = frozenset({
    "settings", "users", "libraries", "media_queue",
    "ignored_media", "seerr_user_rules", "logs", "job_history", "stats_history",
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
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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

        # 5. Seed defaults (INSERT OR IGNORE preserves user values)
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )

        await db.commit()

        # 6. Encrypt any plaintext sensitive settings (no-op if key not configured)
        await _migrate_encrypt_settings(db)

    logger.info(f"Database initialized: {DB_PATH}")


# ─── Settings ─────────────────────────────────────────────────────────────────
async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            if not row:
                return ""
            return _decrypt_value(row[0]) if key in SENSITIVE_KEYS else row[0]


async def set_setting(key: str, value: str):
    stored = _encrypt_value(value) if key in SENSITIVE_KEYS else value
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, stored)
        )
        await db.commit()


async def get_all_settings() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            result = {}
            for k, v in await cur.fetchall():
                result[k] = _decrypt_value(v) if k in SENSITIVE_KEYS else v
            return result


# ─── Logs ─────────────────────────────────────────────────────────────────────
async def add_log(level: str, message: str, source: str = "system"):
    """Insert a log entry and broadcast it via WebSocket."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO logs (ts, level, source, message) VALUES (?, ?, ?, ?)",
                (ts, level, source, message),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to write log: {e}")

    # Broadcast to WebSocket subscribers
    try:
        await _broadcast({
            "type": "log",
            "ts": ts,
            "level": level,
            "source": source,
            "message": message,
        })
    except Exception:
        pass


# ─── Job history ──────────────────────────────────────────────────────────────
async def add_job_run(job_type: str) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO job_history (job_type, started_at) VALUES (?, ?)",
            (job_type, ts),
        )
        await db.commit()
        return cur.lastrowid


async def finish_job_run(run_id: int, status: str, message: str = ""):
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE job_history SET finished_at=?, status=?, message=? WHERE id=?",
            (ts, status, message, run_id),
        )
        await db.commit()
