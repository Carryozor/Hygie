"""MariaDB-backed rate limiting — raw aiomysql, deliberately outside the
DbConn(`?`-placeholder) abstraction.

Lives in its own module for the same reason `_lock_backend.py` does: it talks
to the driver directly with native `%s` placeholders, which the
test_no_backend_sql_string_hardcodes_percent_s guard test would otherwise
flag as a `?`→`%s` dialect-translation bug if it lived inside auth.py.
"""
import asyncio

RATE_LIMIT_MAX = 5  # kept in sync with auth.RATE_LIMIT_MAX; see module docstring


def mariadb_rate_limit(key: str, now: float, cutoff: float, rate_limit_max: int) -> bool:
    """Synchronous MariaDB-backed rate limit, sharing state across workers.

    aiomysql is async-only, but this function (like auth.py's SQLite path)
    must stay synchronous so callers can run it via asyncio.to_thread(). It
    opens a short-lived standalone connection (NOT the shared pool, which is
    bound to the main event loop and can't be used from the fresh loop
    asyncio.run() creates in this worker thread) inside its own event loop.
    """

    async def _do() -> bool:
        import aiomysql
        from .db.engine import _parse_mariadb_url, DATABASE_URL

        kwargs = _parse_mariadb_url(DATABASE_URL)
        conn = await aiomysql.connect(**kwargs, autocommit=True, charset="utf8mb4")
        try:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM rate_limit WHERE ts < %s", (cutoff,))
                await cur.execute(
                    "INSERT INTO rate_limit (`key`, ts) VALUES (%s, %s)", (key, now)
                )
                await cur.execute(
                    "SELECT COUNT(*) FROM rate_limit WHERE `key` = %s AND ts > %s",
                    (key, cutoff),
                )
                row = await cur.fetchone()
                return (row[0] if row else 0) > rate_limit_max
        finally:
            conn.close()

    return asyncio.run(_do())
