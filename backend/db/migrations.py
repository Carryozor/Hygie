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
                # Single dialect-agnostic form: DbConn._q rewrites
                # INSERT OR IGNORE → INSERT IGNORE and ? → %s for MariaDB.
                # (The old MariaDB branch hard-coded %s, which the %→%% escaping
                # in _q would corrupt into %%s and break the parameter binding.)
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


async def _m008_add_job_id_to_logs():
    """Add job_id column to logs table for correlating log entries with job runs.

    Once set, add_log() automatically picks up the current job_id from the
    async context variable set at the start of each scan/deletion job.
    This allows filtering all logs for a specific job run without scanning
    the full log table by timestamp.
    """
    async with get_db() as db:
        cols = await db.table_columns("logs")
        if "job_id" not in cols:
            await db.execute(
                "ALTER TABLE logs ADD COLUMN job_id INT DEFAULT NULL"
                if DIALECT == "mariadb" else
                "ALTER TABLE logs ADD COLUMN job_id INTEGER DEFAULT NULL"
            )
            await db.commit()


async def _m009_migrate_legacy_emby_to_media_servers():
    """Promote standalone emby_url/emby_api_key settings to media_servers[0].

    Pre-v3 databases stored a single Emby connection as flat settings keys.
    This migration packs them into the media_servers JSON array so the multi-
    server code path can handle them uniformly.

    Idempotent: no-op when media_servers is already populated or emby_url is absent.
    """
    import json
    from .encryption import _decrypt_value, _encrypt_value

    async with get_db() as db:
        ms_row = await db.fetch_one("SELECT value FROM settings WHERE `key`='media_servers'")
    raw = (ms_row or {}).get("value", "[]") or "[]"
    current_ms = _decrypt_value(raw) if raw else "[]"
    if current_ms not in ("[]", "", None, "null"):
        return  # already populated

    async with get_db() as db:
        url_row = await db.fetch_one("SELECT value FROM settings WHERE `key`='emby_url'")
    if not url_row or not url_row.get("value"):
        return  # nothing to migrate

    emby_url = url_row["value"]
    if emby_url.startswith("enc:"):
        return  # url stored encrypted — skip (media_servers already populated earlier)

    async with get_db() as db:
        key_row  = await db.fetch_one("SELECT value FROM settings WHERE `key`='emby_api_key'")
        ext_row  = await db.fetch_one("SELECT value FROM settings WHERE `key`='emby_external_url'")
        type_row = await db.fetch_one("SELECT value FROM settings WHERE `key`='media_server_type'")

    raw_key = (key_row or {}).get("value", "") or ""
    raw_ext = (ext_row or {}).get("value", "") or ""
    server0 = {
        "id": "0", "name": "Serveur Principal",
        "url": emby_url,
        "api_key": _decrypt_value(raw_key),
        "ext_url": _decrypt_value(raw_ext),
        "type": (type_row or {}).get("value", "") or "",
        "enabled": True,
    }
    async with get_db() as db:
        if DIALECT == "mariadb":
            await db.execute(
                "REPLACE INTO settings (`key`, value) VALUES (?, ?)",
                ("media_servers", _encrypt_value(json.dumps([server0])))
            )
        else:
            await db.execute(
                "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
                ("media_servers", _encrypt_value(json.dumps([server0])))
            )
        await db.commit()
    logger.info("m009: migrated legacy emby settings to media_servers[0]")


async def _m010_v2_to_v3_data():
    """Backfill server_id and deletion_unit on libraries from pre-v3 databases.

    Also removes the standalone emby_url/emby_api_key/emby_external_url keys once
    media_servers has been populated.

    Idempotent: the schema_migrations table ensures this runs only once.
    NOTE: does NOT write v3_migration_done — that is m011's responsibility so
    that m011 can still run on a fresh v2 database after m010 completes.
    """
    async with get_db() as db:
        if await db.table_exists("libraries"):
            cols = await db.table_columns("libraries")
            if "server_id" in cols:
                n = await db.execute_write(
                    "UPDATE libraries SET server_id='0' WHERE server_id IS NULL OR server_id=''"
                )
                if n:
                    logger.info("m010: backfilled server_id='0' on %d library row(s)", n)
            if "deletion_unit" in cols:
                n = await db.execute_write(
                    "UPDATE libraries SET deletion_unit='episode' WHERE deletion_unit IS NULL OR deletion_unit=''"
                )
                if n:
                    logger.info("m010: backfilled deletion_unit on %d library row(s)", n)

        ms_row = await db.fetch_one("SELECT value FROM settings WHERE `key`='media_servers'")
        if ms_row and ms_row.get("value") not in (None, "", "[]", "null"):
            for old_key in ("emby_url", "emby_api_key", "emby_external_url", "media_server_type"):
                n = await db.execute_write("DELETE FROM settings WHERE `key`=?", (old_key,))
                if n:
                    logger.info("m010: removed legacy setting '%s'", old_key)

        await db.commit()
    logger.info("m010: v2→v3 data migration complete")


async def _m011_libraries_to_expert_rules():
    """Convert library conditions into expert_rules rows for fresh v2→v3 upgrades.

    Skipped on databases that already went through the v3 migration (guarded by
    v3_migration_done).  This covers all existing v3.x databases where library
    conditions were never converted — users manage their rules directly in v3.

    For a true v2 database being upgraded for the first time, m010 runs first
    (backfills structural fields) WITHOUT setting v3_migration_done, so this
    migration can still run and promote the legacy conditions to expert_rules.
    This migration then sets v3_migration_done to mark the database as fully v3.
    """
    import json as _json

    async with get_db() as db:
        # Existing v3 databases already have v3_migration_done — skip to avoid
        # creating unwanted "(migré)" rules that the user never configured.
        done = await db.fetch_one("SELECT value FROM settings WHERE `key`='v3_migration_done'")
        if done:
            return

        if not await db.table_exists("libraries") or not await db.table_exists("expert_rules"):
            # Still mark as done so we don't retry on every fresh-install startup
            if DIALECT == "mariadb":
                await db.execute(
                    "INSERT IGNORE INTO settings (`key`, value) VALUES (?, ?)", ("v3_migration_done", "1")
                )
            else:
                await db.execute(
                    "INSERT OR IGNORE INTO settings (`key`, value) VALUES (?, ?)", ("v3_migration_done", "1")
                )
            await db.commit()
            return

        rows = await db.fetch_all(
            "SELECT id, name, conditions, logic, seerr_conditions, enabled FROM libraries"
        )

    if not rows:
        return

    from .schema import _build_expert_conditions
    from datetime import datetime, timezone
    _cast = "CHAR" if DIALECT == "mariadb" else "TEXT"
    ts = datetime.now(timezone.utc).isoformat()
    created = 0

    for row in rows:
        lib_id    = row["id"]
        name      = row["name"]
        old_conds = []
        seerr_conds = []
        try:
            old_conds   = _json.loads(row.get("conditions") or "[]")
            seerr_conds = _json.loads(row.get("seerr_conditions") or "[]")
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
                f"SELECT id FROM expert_rules WHERE name=?"
                f" AND CAST(library_id AS {_cast})=CAST(? AS {_cast})",
                (rule_name, lib_id),
            )
            if existing:
                continue
            await db.execute(
                "INSERT INTO expert_rules "
                "(name, library_id, conditions, operator, action, enabled, priority, created_at) "
                "VALUES (?, ?, ?, ?, 'queue', ?, 0, ?)",
                (rule_name, lib_id, _json.dumps(new_conds), row.get("logic") or "AND",
                 int(row.get("enabled", 1)), ts),
            )
            await db.commit()
        created += 1
        logger.info("m011: bibliothèque → règle experte : %s (%d conditions)", rule_name, len(new_conds))

    if created:
        logger.info("m011: %d expert rule(s) created from libraries", created)

    # Mark v3 migration complete — prevents re-running on subsequent startups
    async with get_db() as db:
        if DIALECT == "mariadb":
            await db.execute(
                "INSERT IGNORE INTO settings (`key`, value) VALUES (?, ?)", ("v3_migration_done", "1")
            )
        else:
            await db.execute(
                "INSERT OR IGNORE INTO settings (`key`, value) VALUES (?, ?)", ("v3_migration_done", "1")
            )
        await db.commit()


async def _m012_interval_hours_to_minutes():
    """Convert legacy scan_interval_hours / deletion_check_interval_hours to minutes.

    Pre-v3.4 stored intervals in hours.  The scheduler now uses minutes.
    Idempotent: each key is deleted after conversion so re-running is safe.
    """
    async with get_db() as db:
        for hours_key, minutes_key in [
            ("scan_interval_hours", "scan_interval_minutes"),
            ("deletion_check_interval_hours", "deletion_check_interval_minutes"),
        ]:
            row = await db.fetch_one("SELECT value FROM settings WHERE `key`=?", (hours_key,))
            if not row or not (row.get("value") or "").strip().isdigit():
                continue
            existing = await db.fetch_one("SELECT value FROM settings WHERE `key`=?", (minutes_key,))
            if not existing:
                if DIALECT == "mariadb":
                    await db.execute(
                        "REPLACE INTO settings (`key`, value) VALUES (?, ?)",
                        (minutes_key, str(int(row["value"]) * 60))
                    )
                else:
                    await db.execute(
                        "INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)",
                        (minutes_key, str(int(row["value"]) * 60))
                    )
                logger.info("m012: converted %s → %s (%s→%s min)",
                            hours_key, minutes_key, row["value"], int(row["value"]) * 60)
            await db.execute("DELETE FROM settings WHERE `key`=?", (hours_key,))
        await db.commit()


async def _m013_purge_verbose_scan_logs():
    """Remove per-item 'Ignoré (non demandé sur Seerr)' log entries.

    These were logged at INFO before being moved to DEBUG in v3.4.x,
    causing log table bloat on busy instances.  One-time cleanup.
    """
    async with get_db() as db:
        n = await db.execute_write(
            "DELETE FROM logs WHERE message LIKE 'Ignoré (non demandé sur Seerr)%'"
            " OR message LIKE 'Ignoré (utilisateur Seerr%'"
        )
        if n:
            await db.commit()
            logger.info("m013: purged %d verbose per-item scan log entries", n)


async def _m014_add_library_ids_to_seerr_user_rules():
    """Add library_ids column to seerr_user_rules (was missing from the original MariaDB DDL).

    The SQLite DDL gained this column implicitly via CREATE TABLE. On MariaDB,
    seerr_rules.update_rule writes library_ids — without this column the UPDATE
    would fail with an 'Unknown column' error on fresh MariaDB installs.
    """
    async with get_db() as db:
        cols = await db.table_columns("seerr_user_rules")
        if "library_ids" not in cols:
            await db.execute(
                "ALTER TABLE seerr_user_rules ADD COLUMN library_ids LONGTEXT DEFAULT NULL"
                if DIALECT == "mariadb" else
                "ALTER TABLE seerr_user_rules ADD COLUMN library_ids TEXT DEFAULT NULL"
            )
            await db.commit()


_MIGRATIONS = [
    ("m001", "Establish migration tracking baseline",                    _m001_no_op),
    ("m002", "Ensure logs.seen_status column",                           _m002_ensure_seen_status_on_logs),
    ("m003", "Ensure expert_rules.grace_days column",                    _m003_ensure_grace_days_on_expert_rules),
    ("m004", "Ensure refresh_tokens table",                              _m004_ensure_refresh_tokens_table),
    ("m005", "Normalize library server_id: NULL/empty → '0'",            _m005_normalize_library_server_id),
    ("m006", "Fix MariaDB expert_rules: add library_ids, grace_days",    _m006_fix_mariadb_expert_rules_schema),
    ("m007", "Migrate legacy notified_* columns to notifications table", _m007_migrate_notification_columns),
    ("m008", "Add job_id column to logs for job-run correlation",        _m008_add_job_id_to_logs),
    ("m009", "Promote legacy emby_url settings to media_servers[0]",    _m009_migrate_legacy_emby_to_media_servers),
    ("m010", "v2→v3: backfill server_id, deletion_unit, remove legacy settings keys", _m010_v2_to_v3_data),
    ("m011", "Convert library conditions to expert_rules rows",          _m011_libraries_to_expert_rules),
    ("m012", "Convert interval settings from hours to minutes",          _m012_interval_hours_to_minutes),
    ("m013", "Purge verbose per-item scan log entries",                  _m013_purge_verbose_scan_logs),
    ("m014", "Add library_ids to seerr_user_rules (missing from MariaDB DDL)", _m014_add_library_ids_to_seerr_user_rules),
]
