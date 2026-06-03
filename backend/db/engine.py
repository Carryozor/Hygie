"""Database engine: SQLite (default) or MariaDB via DATABASE_URL env var.

Set DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie to use MariaDB.
Leave unset (or empty) to use SQLite at DB_PATH.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()
SQLITE_PATH: str = os.environ.get("DB_PATH", "/app/data/hygie.db")

DIALECT: str = "mariadb" if DATABASE_URL.startswith(("mysql+", "mariadb+", "mysql://", "mariadb://")) else "sqlite"

_pool: Any = None


def _parse_mariadb_url(url: str) -> dict:
    """Parse mysql+aiomysql://user:pass@host:3306/dbname into aiomysql kwargs."""
    clean = url
    for prefix in ("mysql+aiomysql://", "mariadb+aiomysql://", "mysql://", "mariadb://"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    user_pass, rest = clean.split("@", 1)
    user, password = (user_pass.split(":", 1) if ":" in user_pass else (user_pass, ""))
    host_port_db = rest
    if "/" in host_port_db:
        host_port, db = host_port_db.rsplit("/", 1)
    else:
        host_port, db = host_port_db, "hygie"
    host, port = (host_port.rsplit(":", 1) if ":" in host_port else (host_port, "3306"))
    return {"host": host, "port": int(port), "user": user, "password": password, "db": db}


async def init_db_pool() -> None:
    """Initialize connection pool (no-op for SQLite)."""
    global _pool
    if DIALECT != "mariadb":
        return
    import aiomysql
    kwargs = _parse_mariadb_url(DATABASE_URL)
    _pool = await aiomysql.create_pool(
        minsize=1, maxsize=10,
        autocommit=False,
        charset="utf8mb4",
        **kwargs,
    )
    logger.info("MariaDB pool initialized: %s@%s:%s/%s", kwargs["user"], kwargs["host"], kwargs["port"], kwargs["db"])


async def close_db_pool() -> None:
    """Close pool (no-op for SQLite)."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


class DbConn:
    """Unified async DB connection for SQLite and MariaDB.

    API surface:
        fetch_all(sql, params) -> list[dict]
        fetch_one(sql, params) -> dict | None
        execute(sql, params)   -> last insert id (int)
        execute_write(sql, params) -> rowcount (int)
        executemany(sql, params_seq)
        commit()
        table_columns(table) -> set[str]
        table_exists(table) -> bool
    """

    def __init__(self, raw, dialect: str) -> None:
        self._raw = raw
        self._dialect = dialect

    def _q(self, sql: str) -> str:
        """Translate ? placeholders to %s for MariaDB.

        Limitation: replaces ALL occurrences of '?' in the SQL string, including
        any that appear inside string literals. Avoid embedding literal '?' characters
        in SQL values — always bind them as parameters instead.
        """
        if self._dialect == "mariadb":
            return sql.replace("?", "%s")
        return sql

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        if self._dialect == "sqlite":
            self._raw.row_factory = _sqlite_row_factory
            async with self._raw.execute(sql, params) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
        else:
            import aiomysql
            async with self._raw.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(self._q(sql), params or ())
                return list(await cur.fetchall())

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        stripped = sql.rstrip().rstrip(";")
        if "LIMIT" not in stripped.upper():
            sql = stripped + " LIMIT 1"
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Run INSERT/UPDATE/DELETE. Returns last insert id."""
        if self._dialect == "sqlite":
            cur = await self._raw.execute(sql, params)
            return cur.lastrowid or 0
        else:
            async with self._raw.cursor() as cur:
                await cur.execute(self._q(sql), params or ())
                return cur.lastrowid or 0

    async def execute_write(self, sql: str, params: tuple = ()) -> int:
        """Run UPDATE/DELETE. Returns rowcount."""
        if self._dialect == "sqlite":
            cur = await self._raw.execute(sql, params)
            return cur.rowcount
        else:
            async with self._raw.cursor() as cur:
                await cur.execute(self._q(sql), params or ())
                return cur.rowcount

    async def executemany(self, sql: str, params_seq) -> None:
        if self._dialect == "sqlite":
            await self._raw.executemany(sql, params_seq)
        else:
            async with self._raw.cursor() as cur:
                await cur.executemany(self._q(sql), list(params_seq))

    async def commit(self) -> None:
        await self._raw.commit()

    async def table_columns(self, table: str) -> set[str]:
        """Return column names for a table (dialect-aware)."""
        if self._dialect == "sqlite":
            async with self._raw.execute(f"PRAGMA table_info({table})") as cur:
                rows = await cur.fetchall()
            return {r[1] for r in rows}
        else:
            rows = await self.fetch_all(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?", (table,)
            )
            return {r["COLUMN_NAME"] for r in rows}

    async def table_exists(self, table: str) -> bool:
        if self._dialect == "sqlite":
            row = await self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            return row is not None
        else:
            row = await self.fetch_one(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?", (table,)
            )
            return row is not None


def _sqlite_row_factory(cursor, row):
    """sqlite3 row_factory -> dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@asynccontextmanager
async def get_db():
    """Async context manager yielding a DbConn for the configured dialect."""
    if DIALECT == "sqlite":
        import aiosqlite
        async with aiosqlite.connect(SQLITE_PATH) as raw:
            await raw.execute("PRAGMA journal_mode=WAL")
            await raw.execute("PRAGMA foreign_keys=ON")
            yield DbConn(raw, "sqlite")
    else:
        if _pool is None:
            raise RuntimeError("MariaDB pool not initialized — call init_db_pool() at startup")
        async with _pool.acquire() as raw:
            await raw.autocommit(False)
            yield DbConn(raw, "mariadb")
