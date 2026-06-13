# backend/tools/migrate_to_mariadb.py
"""One-way migration: SQLite → MariaDB.

Usage:
    python -m backend.tools.migrate_to_mariadb \
        --sqlite-path /app/data/hygie.db \
        --database-url "mysql+aiomysql://hygie:secret@localhost:3306/hygie"

The MariaDB database must already exist and be empty (schema will be created).
Migration is done table-by-table with batch inserts (1000 rows/batch).
"""
import argparse
import asyncio
import logging
import os
import sys

import aiosqlite

logger = logging.getLogger("hygie.migrate")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

BATCH_SIZE = 1000

ORDERED_TABLES = [
    "settings", "users", "refresh_tokens", "libraries",
    "media_queue", "ignored_media", "seerr_user_rules",
    "logs", "job_history", "stats_history",
    "rate_limit", "expert_rules", "notifications",
]


async def validate_sqlite_db(sqlite_path: str) -> set[str]:
    """Verify the SQLite file exists and return its table names."""
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    async with aiosqlite.connect(sqlite_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
    return {r[0] for r in rows}


async def read_sqlite_table(sqlite_path: str, table: str) -> list[dict]:
    """Read all rows from a SQLite table as list of dicts."""
    async with aiosqlite.connect(sqlite_path) as db:
        db.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        async with db.execute(f"SELECT * FROM {table}") as cur:
            return await cur.fetchall()


async def _mariadb_columns(conn, table: str) -> set[str]:
    """Return the set of column names that exist in the MariaDB target table."""
    async with conn.cursor() as cur:
        await cur.execute(f"SHOW COLUMNS FROM `{table}`")
        rows = await cur.fetchall()
    return {r[0] for r in rows}


async def _write_mariadb_table(db_url: str, table: str, rows: list[dict]) -> None:
    """Insert rows into a MariaDB table in batches.

    Columns present in the SQLite source but absent from the MariaDB target
    (e.g. deprecated fields removed by schema migrations) are silently dropped.
    This makes the migration resilient to schema divergence between SQLite
    databases created at older Hygie versions and the current MariaDB schema.
    """
    if not rows:
        logger.info("  %s: 0 rows (skipped)", table)
        return
    import aiomysql
    from backend.db.engine import _parse_mariadb_url
    kwargs = _parse_mariadb_url(db_url)
    conn = await aiomysql.connect(**kwargs, autocommit=False, charset="utf8mb4")
    try:
        # Intersect SQLite columns with MariaDB columns — drop deprecated fields
        target_cols = await _mariadb_columns(conn, table)
        source_cols = list(rows[0].keys())
        cols = [c for c in source_cols if c in target_cols]
        dropped = set(source_cols) - set(cols)
        if dropped:
            logger.info("  %s: dropping legacy columns not in MariaDB schema: %s", table, sorted(dropped))

        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(f"`{c}`" for c in cols)
        sql = f"INSERT IGNORE INTO `{table}` ({col_names}) VALUES ({placeholders})"
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = [tuple(r[c] for c in cols) for r in batch]
            async with conn.cursor() as cur:
                await cur.executemany(sql, values)
            await conn.commit()
        logger.info("  %s: %d rows migrated", table, len(rows))
    finally:
        conn.close()


async def migrate(sqlite_path: str, db_url: str, dry_run: bool = False) -> None:
    """Full migration pipeline."""
    logger.info("=== Hygie SQLite → MariaDB Migration ===")
    logger.info("Source: %s", sqlite_path)
    logger.info("Target: %s", db_url.split("@")[-1])  # hide credentials

    present_tables = await validate_sqlite_db(sqlite_path)
    logger.info("Tables in SQLite: %s", sorted(present_tables))

    # Initialize MariaDB schema
    if not dry_run:
        os.environ["DATABASE_URL"] = db_url
        import importlib
        import backend.db.engine as eng
        importlib.reload(eng)
        await eng.init_db_pool()
        from backend.db.schema import _init_db_mariadb
        await _init_db_mariadb()
        logger.info("MariaDB schema created")

    for table in ORDERED_TABLES:
        if table not in present_tables:
            logger.warning("  %s: not in SQLite (skipped)", table)
            continue
        rows = await read_sqlite_table(sqlite_path, table)
        if dry_run:
            logger.info("  %s: %d rows (DRY RUN)", table, len(rows))
        else:
            await _write_mariadb_table(db_url, table, rows)

    logger.info("=== Migration complete ===")
    if dry_run:
        logger.info("DRY RUN — no data written to MariaDB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Hygie from SQLite to MariaDB")
    parser.add_argument("--sqlite-path", required=True, help="Path to hygie.db")
    parser.add_argument("--database-url", required=True, help="MariaDB connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Read SQLite but don't write")
    args = parser.parse_args()
    asyncio.run(migrate(args.sqlite_path, args.database_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
