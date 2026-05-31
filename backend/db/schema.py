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
        "refresh_tokens",
        """CREATE TABLE IF NOT EXISTS refresh_tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT    NOT NULL UNIQUE,
            expires_at TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            revoked    INTEGER NOT NULL DEFAULT 0
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
            torrent_hash TEXT DEFAULT '',
            poster_url TEXT DEFAULT '',
            tmdb_id TEXT DEFAULT '',
            seerr_id INTEGER,
            seerr_user_id INTEGER,
            seerr_username TEXT DEFAULT '',
            seerr_discord_id TEXT DEFAULT '',
            seerr_request_url TEXT DEFAULT '',
            radarr_id INTEGER,
            sonarr_id INTEGER,
            detected_at TEXT NOT NULL,
            delete_at TEXT NOT NULL,
            added_date TEXT,
            last_played TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            ignored INTEGER DEFAULT 0,
            notified_30d INTEGER DEFAULT 0,
            notified_7d INTEGER DEFAULT 0,
            notified_1d INTEGER DEFAULT 0,
            notified_now INTEGER DEFAULT 0,
            notified_detected INTEGER DEFAULT 0,
            notified_thresholds TEXT DEFAULT '[]',
            sonarr_series_id INTEGER,
            season_number INTEGER,
            plex_rating_key TEXT DEFAULT '',
            view_count INTEGER DEFAULT 0
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
            ("plex_rating_key", "TEXT DEFAULT ''"),
            ("view_count", "INTEGER DEFAULT 0"),
            ("torrent_hash", "TEXT DEFAULT ''"),
            ("seerr_discord_id", "TEXT DEFAULT ''"),
            ("ignored", "INTEGER DEFAULT 0"),
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
            name TEXT NOT NULL DEFAULT '',
            seerr_user_id INTEGER NOT NULL,
            seerr_username TEXT NOT NULL,
            library_id TEXT NOT NULL,
            grace_days INTEGER NOT NULL DEFAULT 30,
            enabled INTEGER NOT NULL DEFAULT 1,
            discord_id TEXT DEFAULT '',
            created_at TEXT
        )""",
        [
            ("name", "TEXT NOT NULL DEFAULT ''"),
            ("discord_id", "TEXT DEFAULT ''"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT"),
            ("library_ids", "TEXT"),
        ],
    ),
    (
        "logs",
        """CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            category TEXT DEFAULT '',
            seen_status TEXT
        )""",
        [
            ("category", "TEXT DEFAULT ''"),
            ("seen_status", "TEXT"),
        ],
    ),
    (
        "job_history",
        """CREATE TABLE IF NOT EXISTS job_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT,
            message TEXT,
            result TEXT
        )""",
        [
            ("result", "TEXT"),
        ],
    ),
    (
        "stats_history",
        """CREATE TABLE IF NOT EXISTS stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            total_deleted INTEGER DEFAULT 0,
            total_scanned INTEGER DEFAULT 0,
            space_freed_bytes INTEGER DEFAULT 0,
            month TEXT NOT NULL,
            library_id TEXT
        )""",
        [
            ("library_id", "TEXT"),
        ],
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
            library_ids TEXT,
            conditions  TEXT    NOT NULL DEFAULT '[]',
            operator    TEXT    NOT NULL DEFAULT 'AND',
            action      TEXT    NOT NULL DEFAULT 'queue',
            grace_days  INTEGER NOT NULL DEFAULT 7,
            enabled     INTEGER NOT NULL DEFAULT 1,
            priority    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )""",
        [("library_ids", "TEXT"), ("grace_days", "INTEGER NOT NULL DEFAULT 7")],
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
async def _init_db_sqlite():
    """SQLite schema init — original implementation."""
    from .engine import SQLITE_PATH as _SQLITE_PATH
    db_dir = os.path.dirname(_SQLITE_PATH)
    if db_dir:  # skip for in-memory (:memory:) or bare filenames
        os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(_SQLITE_PATH) as db:
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

        # 7b. v2 → v3 data migration (idempotent, runs once)
        await _migrate_v2_to_v3(db)

        # 7c. Libraries → expert rules migration (idempotent)
        n = await _migrate_libraries_to_expert_rules(db)
        if n:
            logger.info(f"Migration automatique : {n} règle(s) experte(s) créée(s) depuis les bibliothèques")

        # 8. Migrate interval settings: hours → minutes — runs once, then deletes the old key.
        # The old condition `existing[0] == "60"` was a bug: it matched the default value and
        # overwrote a user-set "60" (1h) with hours*60 on every restart.
        async with db.execute("SELECT value FROM settings WHERE key='scan_interval_hours'") as cur:
            row = await cur.fetchone()
        if row and row[0] and row[0].strip().isdigit():
            async with db.execute("SELECT value FROM settings WHERE key='scan_interval_minutes'") as cur2:
                existing = await cur2.fetchone()
            if not existing:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("scan_interval_minutes", str(int(row[0]) * 60))
                )
            await db.execute("DELETE FROM settings WHERE key='scan_interval_hours'")
        async with db.execute("SELECT value FROM settings WHERE key='deletion_check_interval_hours'") as cur:
            row = await cur.fetchone()
        if row and row[0] and row[0].strip().isdigit():
            async with db.execute("SELECT value FROM settings WHERE key='deletion_check_interval_minutes'") as cur2:
                existing = await cur2.fetchone()
            if not existing:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("deletion_check_interval_minutes", str(int(row[0]) * 60))
                )
            await db.execute("DELETE FROM settings WHERE key='deletion_check_interval_hours'")
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

    logger.info(f"Database initialized: {_SQLITE_PATH}")


_EXPERT_RULE_FIELD_MAP = {
    "days_not_watched": "days_not_watched",
    "days_since_added": "added_days_ago",
    "added_days_ago":   "added_days_ago",
    "play_count":       "play_count",
}


def _build_expert_conditions(old_conds: list, seerr_conds: list) -> list:
    """Map old library conditions + seerr filters to ExpertRule condition dicts."""
    new_conds = []
    for c in old_conds:
        field = c.get("field")
        op    = c.get("op", "gt")
        value = c.get("value", 0)
        if field == "never_watched":
            # never_watched=true → play_count eq 0 ; false → play_count gte 1
            new_conds.append({
                "field": "play_count",
                "op":    "eq"  if bool(value) else "gte",
                "value": 0     if bool(value) else 1,
            })
        elif field in _EXPERT_RULE_FIELD_MAP:
            new_conds.append({"field": _EXPERT_RULE_FIELD_MAP[field], "op": op, "value": value})

    includes = [c["user_id"] for c in seerr_conds if c.get("type") == "user_include"]
    excludes = [c["user_id"] for c in seerr_conds if c.get("type") == "user_exclude"]
    if includes:
        new_conds.append({"field": "seerr_user_id", "op": "in",     "value": includes})
    if excludes:
        new_conds.append({"field": "seerr_user_id", "op": "not_in", "value": excludes})
    return new_conds


async def _migrate_libraries_to_expert_rules(db) -> int:
    """Convert library conditions → expert_rules rows (raw aiosqlite connection).

    Idempotent: skips rules where (name, library_id) already exists.
    Field mapping:
      days_since_added  → added_days_ago
      days_not_watched  → days_not_watched
      play_count        → play_count
      never_watched=1   → play_count eq 0
      never_watched=0   → play_count gte 1
    seerr_conditions:
      user_include ids  → seerr_user_id in [ids]
      user_exclude ids  → seerr_user_id not_in [ids]
    """
    if not await _table_exists(db, "libraries") or not await _table_exists(db, "expert_rules"):
        return 0

    _orig_factory = db.row_factory
    db.row_factory = lambda cur, row: {col[0]: row[i] for i, col in enumerate(cur.description)}
    try:
        async with db.execute("SELECT id, name, conditions, logic, seerr_conditions, enabled FROM libraries") as cur:
            rows = await cur.fetchall()
    finally:
        db.row_factory = _orig_factory  # restore BEFORE any further queries

    if not rows:
        return 0

    created = 0
    ts = datetime.now(timezone.utc).isoformat()
    for row in rows:
        lib_id    = row["id"]
        name      = row["name"]
        conds_raw = row["conditions"] or "[]"
        logic     = row["logic"] or "AND"
        seerr_raw = row["seerr_conditions"] or "[]"
        enabled   = row["enabled"]

        try:
            old_conds   = json.loads(conds_raw)
            seerr_conds = json.loads(seerr_raw)
        except Exception:
            continue

        if not old_conds:
            continue

        new_conds = _build_expert_conditions(old_conds, seerr_conds)
        if not new_conds:
            continue

        rule_name = f"{name} (migré)"
        async with db.execute(
            "SELECT id FROM expert_rules WHERE name=? AND CAST(library_id AS TEXT)=CAST(? AS TEXT)",
            (rule_name, lib_id),
        ) as cur2:
            if await cur2.fetchone():
                continue

        await db.execute(
            "INSERT INTO expert_rules (name, library_id, conditions, operator, action, enabled, priority, created_at) "
            "VALUES (?, ?, ?, ?, 'queue', ?, 0, ?)",
            (rule_name, lib_id, json.dumps(new_conds), logic, int(enabled), ts),
        )
        created += 1
        logger.info(f"Migration: bibliothèque → règle experte : {rule_name} ({len(new_conds)} conditions)")

    if created:
        await db.commit()
    return created


async def _migrate_v2_to_v3(db) -> None:
    """One-time data migrations when upgrading from v2.x to v3.x.

    Idempotent — safe to call on every startup; each step is guarded.
    """
    # Guard: skip if already migrated
    async with db.execute("SELECT value FROM settings WHERE key='v3_migration_done'") as cur:
        if await cur.fetchone():
            return

    logger.info("Running v2 → v3 data migration…")

    # 1. Backfill server_id='0' on libraries that predate multi-server support.
    if await _table_exists(db, "libraries"):
        cols = await _table_columns(db, "libraries")
        if "server_id" in cols:
            async with db.execute(
                "UPDATE libraries SET server_id='0' WHERE server_id IS NULL OR server_id=''"
            ) as cur:
                if cur.rowcount:
                    logger.info(f"v2→v3: backfilled server_id='0' on {cur.rowcount} library row(s)")

    # 2. Backfill deletion_unit='episode' on libraries missing it.
    if await _table_exists(db, "libraries"):
        cols = await _table_columns(db, "libraries")
        if "deletion_unit" in cols:
            async with db.execute(
                "UPDATE libraries SET deletion_unit='episode' WHERE deletion_unit IS NULL OR deletion_unit=''"
            ) as cur:
                if cur.rowcount:
                    logger.info(f"v2→v3: backfilled deletion_unit on {cur.rowcount} library row(s)")

    # 3. Remove standalone emby_url / emby_api_key / emby_external_url settings once
    #    media_servers has been populated (step 7 above does the conversion).
    async with db.execute("SELECT value FROM settings WHERE key='media_servers'") as cur:
        ms_row = await cur.fetchone()
    if ms_row and ms_row[0] not in (None, "", "[]", "null"):
        for old_key in ("emby_url", "emby_api_key", "emby_external_url", "media_server_type"):
            async with db.execute("DELETE FROM settings WHERE key=?", (old_key,)) as cur:
                if cur.rowcount:
                    logger.info(f"v2→v3: removed legacy setting '{old_key}'")

    # Mark migration complete
    await db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('v3_migration_done', '1')"
    )
    await db.commit()
    logger.info("v2 → v3 migration complete")


async def _init_db_mariadb() -> None:
    """MariaDB schema init: create tables + indexes + seed defaults."""
    from .engine import get_db
    from .schema_mariadb import MARIADB_TABLES, MARIADB_INDEXES
    async with get_db() as db:
        for _table_name, ddl in MARIADB_TABLES:
            await db.execute(ddl)
        for idx_sql in MARIADB_INDEXES:
            await db.execute(idx_sql)
        for k, v in DEFAULT_SETTINGS.items():
            existing = await db.fetch_one("SELECT 1 FROM settings WHERE `key`=?", (k,))
            if not existing:
                await db.execute("INSERT INTO settings (`key`, value) VALUES (?, ?)", (k, v))
        await db.commit()

    n = await _migrate_libraries_to_expert_rules_dbconn()
    if n:
        logger.info(f"Migration MariaDB : {n} règle(s) experte(s) créée(s) depuis les bibliothèques")
    logger.info("MariaDB schema initialized")


async def _migrate_libraries_to_expert_rules_dbconn() -> int:
    """DbConn-compatible variant of the libraries → expert_rules migration.

    Used by the MariaDB path. The SQLite path uses the raw-connection variant
    above since it runs before the DbConn pool is available.
    """
    from .engine import get_db
    try:
        async with get_db() as db:
            rows = await db.fetch_all(
                "SELECT id, name, conditions, logic, seerr_conditions, enabled FROM libraries"
            )
    except Exception:
        return 0

    if not rows:
        return 0

    created = 0
    ts = datetime.now(timezone.utc).isoformat()
    for row in rows:
        lib_id    = row["id"]
        name      = row["name"]
        conds_raw = row.get("conditions") or "[]"
        logic     = row.get("logic") or "AND"
        seerr_raw = row.get("seerr_conditions") or "[]"
        enabled   = row.get("enabled", 1)

        try:
            old_conds   = json.loads(conds_raw)
            seerr_conds = json.loads(seerr_raw)
        except Exception:
            continue

        if not old_conds:
            continue

        new_conds = _build_expert_conditions(old_conds, seerr_conds)
        if not new_conds:
            continue

        rule_name = f"{name} (migré)"
        async with get_db() as db:
            existing = await db.fetch_one(
                "SELECT id FROM expert_rules WHERE name=? AND CAST(library_id AS CHAR)=CAST(? AS CHAR)",
                (rule_name, lib_id),
            )
        if existing:
            continue

        async with get_db() as db:
            await db.execute(
                "INSERT INTO expert_rules "
                "(name, library_id, conditions, operator, action, enabled, priority, created_at) "
                "VALUES (?, ?, ?, ?, 'queue', ?, 0, ?)",
                (rule_name, lib_id, json.dumps(new_conds), logic, int(enabled), ts),
            )
            await db.commit()
        created += 1
        logger.info(f"Migration MariaDB: bibliothèque → règle experte : {rule_name}")

    return created


async def init_db() -> None:
    """Initialize database — SQLite (default) or MariaDB based on DATABASE_URL."""
    from .engine import DIALECT
    if DIALECT == "mariadb":
        await _init_db_mariadb()
    else:
        await _init_db_sqlite()
