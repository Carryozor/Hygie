# backend/tools/migrate_to_sqlite.py
"""Reverse migration: MariaDB → SQLite.

Usage:
    python -m backend.tools.migrate_to_sqlite \
        --database-url "mysql+aiomysql://hygie:secret@localhost:3306/hygie" \
        --sqlite-path /app/data/hygie_migrated.db

Creates a fresh SQLite database at sqlite-path, initializes the schema,
then copies all data from MariaDB table-by-table in batch inserts.
"""
import argparse
import asyncio
import logging
import os

import aiosqlite

logger = logging.getLogger("hygie.migrate_to_sqlite")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

BATCH_SIZE = 1000

ORDERED_TABLES = [
    "settings", "users", "refresh_tokens", "libraries",
    "media_queue", "ignored_media", "seerr_user_rules",
    "logs", "job_history", "stats_history",
    "rate_limit", "expert_rules", "notifications",
]


async def _read_mariadb_table(db_url: str, table: str) -> list[dict]:
    """Read all rows from a MariaDB table as list of dicts."""
    import aiomysql
    from backend.db.engine import _parse_mariadb_url
    kwargs = _parse_mariadb_url(db_url)
    conn = await aiomysql.connect(**kwargs, autocommit=True, charset="utf8mb4")
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(f"SELECT * FROM `{table}`")
            rows = list(await cur.fetchall())
        return rows
    finally:
        conn.close()


async def _init_sqlite_schema(sqlite_path: str) -> None:
    """Create all tables in the target SQLite database using direct aiosqlite connection.

    Deliberately avoids os.environ / importlib.reload so that calling this
    from a running FastAPI process does not affect the live DB connection.
    """
    from backend.db.schema import _SQLITE_TABLES, _SQLITE_INDEXES
    async with aiosqlite.connect(sqlite_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        for _name, ddl, _extra in _SQLITE_TABLES:
            await db.execute(ddl)
        for idx in _SQLITE_INDEXES:
            await db.execute(idx)
        await db.commit()


async def _write_sqlite_table(sqlite_path: str, table: str, rows: list[dict]) -> None:
    """Insert rows into a SQLite table in batches, skipping duplicates."""
    if not rows:
        logger.info("  %s: 0 rows (skipped)", table)
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(f'"{c}"' for c in cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"
    async with aiosqlite.connect(sqlite_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = [tuple(r.get(c) for c in cols) for r in batch]
            await db.executemany(sql, values)
        await db.commit()
    logger.info("  %s: %d rows migrated", table, len(rows))


async def _mariadb_table_exists(db_url: str, table: str) -> bool:
    """Check if a table exists in MariaDB."""
    import aiomysql
    from backend.db.engine import _parse_mariadb_url
    kwargs = _parse_mariadb_url(db_url)
    conn = await aiomysql.connect(**kwargs, autocommit=True, charset="utf8mb4")
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,)
            )
            return (await cur.fetchone()) is not None
    finally:
        conn.close()


async def migrate(db_url: str, sqlite_path: str, dry_run: bool = False) -> dict:
    """Full reverse migration pipeline. Returns summary dict."""
    logger.info("=== Hygie MariaDB → SQLite Migration ===")
    logger.info("Source: %s", db_url.split("@")[-1])
    logger.info("Target: %s", sqlite_path)

    if os.path.exists(sqlite_path) and not dry_run:
        raise FileExistsError(
            f"{sqlite_path} already exists. Remove it or choose another path."
        )

    if not dry_run:
        await _init_sqlite_schema(sqlite_path)
        logger.info("SQLite schema created at %s", sqlite_path)

    summary = {}
    for table in ORDERED_TABLES:
        exists = await _mariadb_table_exists(db_url, table)
        if not exists:
            logger.warning("  %s: not in MariaDB (skipped)", table)
            continue
        rows = await _read_mariadb_table(db_url, table)
        summary[table] = len(rows)
        if dry_run:
            logger.info("  %s: %d rows (DRY RUN)", table, len(rows))
        else:
            await _write_sqlite_table(sqlite_path, table, rows)

    logger.info("=== Migration complete ===")
    if dry_run:
        logger.info("DRY RUN — no data written to SQLite")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Hygie from MariaDB to SQLite")
    parser.add_argument("--database-url", required=True, help="MariaDB source URL")
    parser.add_argument("--sqlite-path", default="/app/data/hygie_migrated.db",
                        help="Target SQLite path (must not exist)")
    parser.add_argument("--dry-run", action="store_true", help="Read MariaDB but don't write")
    args = parser.parse_args()
    asyncio.run(migrate(args.database_url, args.sqlite_path, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
