"""Database engine: SQLite (default) or MariaDB via DATABASE_URL env var.

Set DATABASE_URL=mysql+aiomysql://user:pass@host:3306/hygie to use MariaDB.
Leave unset (or empty) to use SQLite at DB_PATH.
"""
import os
import re
import logging
from contextlib import asynccontextmanager
from typing import Any

# Matches SQL string literals (single or double-quoted) OR a bare ? placeholder.
# Group 1 captures the ? only when it is a real parameter (outside any literal).
_Q_RE = re.compile(
    r"'(?:[^'\\]|\\.)*'"   # single-quoted literal — consume whole string
    r'|"(?:[^"\\]|\\.)*"'  # double-quoted literal — consume whole string
    r"|(\?)"               # group 1: bare ? parameter placeholder
)

# Strip string literals before keyword checks (a literal 'no limit' must not
# be mistaken for a LIMIT clause).
_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'" r'|"(?:[^"\\]|\\.)*"')
_LIMIT_KW_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)

# SQLite upsert prefixes → MariaDB equivalents. Anchored to the statement
# start so a literal containing the keywords is never rewritten. MariaDB
# rejects `INSERT OR REPLACE` / `INSERT OR IGNORE` with a syntax error.
_OR_REPLACE_RE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)
_OR_IGNORE_RE  = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()
SQLITE_PATH: str = os.environ.get("DB_PATH", "/app/data/hygie.db")

DIALECT: str = "mariadb" if DATABASE_URL.startswith(("mysql+", "mariadb+", "mysql://", "mariadb://")) else "sqlite"

_pool: Any = None


def _parse_mariadb_url(url: str) -> dict:
    """Parse mysql+aiomysql://user:pass@host:3306/dbname into aiomysql kwargs.

    Uses urllib.parse.urlparse for correctness — handles IPv6 brackets, special
    characters in passwords (%-encoded), and non-standard ports without fragile
    string splits that break on edge cases.
    """
    from urllib.parse import urlparse, unquote

    # Normalize the scheme so urlparse can parse it as a standard URL
    normalized = url
    for prefix in ("mysql+aiomysql://", "mariadb+aiomysql://"):
        if normalized.startswith(prefix):
            normalized = "https://" + normalized[len(prefix):]
            break
    if normalized.startswith(("mysql://", "mariadb://")):
        normalized = "https://" + normalized.split("://", 1)[1]

    parsed = urlparse(normalized)
    host = parsed.hostname or "localhost"
    port = parsed.port or 3306
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    db = (parsed.path or "/hygie").lstrip("/") or "hygie"
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

        Uses a regex that skips single- and double-quoted string literals so that
        a literal '?' inside a LIKE pattern or JSON value is left untouched.
        Only bare ? tokens that are actual parameter placeholders are replaced.
        """
        if self._dialect != "mariadb":
            return sql
        sql = _OR_REPLACE_RE.sub("REPLACE INTO", sql)
        sql = _OR_IGNORE_RE.sub("INSERT IGNORE INTO", sql)
        # aiomysql/pymysql treats the query as a printf-style format string
        # (query % args), so any literal % (e.g. LIKE wildcards baked into the
        # SQL) must be doubled. Escape first, THEN emit %s placeholders so the
        # placeholders we generate stay single.
        sql = sql.replace("%", "%%")
        return _Q_RE.sub(lambda m: "%s" if m.group(1) else m.group(0), sql)

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
        # Append LIMIT 1 only to SELECT/CTE statements that have no LIMIT clause
        # outside string literals — PRAGMA and friends don't accept LIMIT.
        head = stripped.lstrip().upper()
        if head.startswith(("SELECT", "WITH")) and not _LIMIT_KW_RE.search(
            _LITERAL_RE.sub("''", stripped)
        ):
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
            rows = await self.fetch_all(f"PRAGMA table_info({table})")
            return {r["name"] for r in rows}
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
            # Wait instead of failing with "database is locked" under
            # concurrent library scans (parallel writers on one SQLite file).
            await raw.execute("PRAGMA busy_timeout=5000")
            yield DbConn(raw, "sqlite")
    else:
        if _pool is None:
            raise RuntimeError("MariaDB pool not initialized — call init_db_pool() at startup")
        async with _pool.acquire() as raw:
            await raw.autocommit(False)
            yield DbConn(raw, "mariadb")
