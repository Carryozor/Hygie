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


import re as _re

_SENSITIVE_PARAMS = _re.compile(r'(?i)(api[_-]?key|token|password|secret)=[^&\s]+')


def now_utc() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def sanitize_url(url: str) -> str:
    """Redact sensitive query parameters from a URL for safe logging."""
    return _SENSITIVE_PARAMS.sub(r'\1=***', url)


async def http_retry(coro_factory, *, retries: int = 3, backoff: float = 1.0):
    """Execute an async callable with exponential backoff on transient errors.

    coro_factory: zero-arg async callable that performs one HTTP attempt.
    Retries on httpx.TimeoutException and httpx.ConnectError.
    Raises on exhaustion or non-transient errors (4xx, logic errors).

    Example:
        result = await http_retry(lambda: client.get(url, headers=h))
    """
    import httpx as _httpx
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            return await coro_factory()
        except (_httpx.TimeoutException, _httpx.ConnectError, _httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc


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
    "media_servers",   # JSON array contains API keys — encrypt the whole blob
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
    "emby_leaving_soon_collection": "",
    "emby_leaving_soon_days": "30",
    "emby_leaving_soon_overlay": "false",
    "media_server_type": "",           # "" | "emby" | "jellyfin" | "unknown"
    "media_servers": "[]",             # JSON array of server configs (encrypted)
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
    "discord_notif_thresholds": "7,1",
    "discord_alert_deletion_error": "false",
    "discord_alert_scan_failure": "false",
    "discord_alert_seerr_failure": "false",
    "discord_alert_error_threshold": "3",
    "max_parallel_library_scans": "3",
    "dry_run": "false",
    "scan_interval_minutes": "360",            # 6h par défaut
    "deletion_check_interval_minutes": "60",   # 1h par défaut
    "log_level": "INFO",
    "deleted_retention_days": "90",
    "log_retention_days": "14",
    "job_history_retention_days": "90",
    "ui_language": "fr",
    "backup_path": "/app/data/backups",
    "backup_interval_hours": "24",
    "backup_retention_count": "7",
}

# ─── WebSocket broadcast ──────────────────────────────────────────────────────
_ws_clients: set = set()

def register_ws(ws):
    _ws_clients.add(ws)

def unregister_ws(ws):
    _ws_clients.discard(ws)

async def _broadcast(payload: dict):
    """Send a payload to all connected WebSocket clients.

    Each send is wrapped in a 5-second timeout so a slow or stuck client
    cannot block the broadcast for all other subscribers.
    """
    if not _ws_clients:
        return
    dead = []
    for ws in list(_ws_clients):
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=5.0)
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
]


_KNOWN_TABLES = frozenset({
    "settings", "users", "libraries", "media_queue",
    "ignored_media", "seerr_user_rules", "logs", "job_history", "stats_history",
    "rate_limit",
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


# ─── Media servers helpers ────────────────────────────────────────────────────
import time as _time

_ms_cache: Optional[list] = None
_ms_cache_ts: float = 0.0
_MS_CACHE_TTL: float = 30.0  # seconds — invalidated immediately on save


async def get_media_servers() -> list:
    """Return the parsed (decrypted) media_servers list.
    Cached for 30 seconds to avoid a DB open on every HTTP call to Emby/Jellyfin.
    Never raises.
    """
    global _ms_cache, _ms_cache_ts
    now = _time.monotonic()
    if _ms_cache is not None and now - _ms_cache_ts < _MS_CACHE_TTL:
        return _ms_cache
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM settings WHERE key='media_servers'") as cur:
                row = await cur.fetchone()
                if not row or not row[0]:
                    _ms_cache, _ms_cache_ts = [], now
                    return []
                raw = _decrypt_value(row[0])
                result = json.loads(raw) if raw else []
                _ms_cache, _ms_cache_ts = result, now
                return result
    except Exception:
        return _ms_cache if _ms_cache is not None else []


async def save_media_servers(servers: list) -> None:
    """Persist the media_servers list (encrypts if key configured). Invalidates cache."""
    global _ms_cache, _ms_cache_ts
    raw = json.dumps(servers)
    stored = _encrypt_value(raw)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("media_servers", stored)
        )
        await db.commit()
    # Invalidate both caches immediately so next reads reflect the new state
    _ms_cache, _ms_cache_ts = servers, _time.monotonic()
    _invalidate_settings_cache()


# ─── Settings cache ───────────────────────────────────────────────────────────
# Settings change rarely (user action only). Loading all of them in one query
# and caching for TTL seconds removes dozens of DB opens per scan/deletion cycle.
# Sensitive values are stored encrypted in the cache; decryption happens on read.
_settings_cache: dict[str, str] = {}
_settings_cache_ts: float = 0.0
_SETTINGS_CACHE_TTL: float = 30.0  # seconds


def _invalidate_settings_cache() -> None:
    global _settings_cache_ts
    _settings_cache_ts = 0.0


# ─── Settings ─────────────────────────────────────────────────────────────────
async def get_setting(key: str) -> str:
    global _settings_cache, _settings_cache_ts
    now = _time.monotonic()
    if not _settings_cache or now - _settings_cache_ts >= _SETTINGS_CACHE_TTL:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT key, value FROM settings") as cur:
                _settings_cache = {r[0]: r[1] for r in await cur.fetchall()}
        _settings_cache_ts = now
    raw = _settings_cache.get(key, "")
    return _decrypt_value(raw) if key in SENSITIVE_KEYS else raw


async def set_setting(key: str, value: str) -> None:
    stored = _encrypt_value(value) if key in SENSITIVE_KEYS else value
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, stored)
        )
        await db.commit()
    _invalidate_settings_cache()


async def get_bool_setting(key: str, default: bool = False) -> bool:
    """Read a setting and return it as a boolean ('true'/'1' → True)."""
    v = (await get_setting(key) or "").lower().strip()
    if not v:
        return default
    return v in ("true", "1", "yes", "on")


async def get_int_setting(key: str, default: int = 0) -> int:
    """Read a setting and return it as an integer."""
    v = (await get_setting(key) or "").strip()
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


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
