"""Retry-on-transient-conflict for MariaDB writes.

MariaDB raises ER_CHECKREAD (1020, "Record has changed since last read in
table '...'; try restarting transaction") on some concurrent UPDATE/DELETE
conflicts under REPEATABLE-READ. The error message itself names the fix:
the statement is safe to retry. No real MariaDB server is needed — the raw
connection/cursor are faked to deterministically trigger the error.
"""
import pytest
import pymysql.err


class _FakeCursor:
    """Each retry acquires a fresh cursor (mirrors the real `async with
    self._raw.cursor()` per attempt) — failure is decided by the *connection's*
    attempt count, not a per-cursor counter, so retries actually progress."""

    def __init__(self, conn, errno, errmsg):
        self._conn = conn
        self.errno = errno
        self.errmsg = errmsg
        self.lastrowid = 42
        self.rowcount = 1

    async def execute(self, sql, params):
        if self._conn.cursor_calls <= self._conn._fail_times:
            raise pymysql.err.OperationalError(self.errno, self.errmsg)


class _CursorCtx:
    def __init__(self, cur):
        self._cur = cur

    async def __aenter__(self):
        return self._cur

    async def __aexit__(self, *exc):
        return False


class _FakeRawConn:
    def __init__(self, fail_times, errno=1020, errmsg="Record has changed since last read in table 't'; try restarting transaction"):
        self._fail_times = fail_times
        self._errno = errno
        self._errmsg = errmsg
        self.cursor_calls = 0

    def cursor(self, *a, **kw):
        self.cursor_calls += 1
        return _CursorCtx(_FakeCursor(self, self._errno, self._errmsg))


@pytest.mark.asyncio
async def test_execute_retries_once_on_1020_then_succeeds():
    from backend.db.engine import DbConn
    raw = _FakeRawConn(fail_times=1)
    conn = DbConn(raw, "mariadb")
    last_id = await conn.execute("UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", ("h",))
    assert last_id == 42
    assert raw.cursor_calls == 2  # first attempt failed transiently, second succeeded


@pytest.mark.asyncio
async def test_execute_write_retries_on_1020():
    from backend.db.engine import DbConn
    raw = _FakeRawConn(fail_times=1)
    conn = DbConn(raw, "mariadb")
    rowcount = await conn.execute_write("UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", ("h",))
    assert rowcount == 1
    assert raw.cursor_calls == 2


@pytest.mark.asyncio
async def test_execute_gives_up_after_max_retries():
    from backend.db.engine import DbConn
    raw = _FakeRawConn(fail_times=99)  # never succeeds
    conn = DbConn(raw, "mariadb")
    with pytest.raises(pymysql.err.OperationalError):
        await conn.execute("UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", ("h",))
    assert raw.cursor_calls > 1  # it did retry, just not forever


@pytest.mark.asyncio
async def test_execute_does_not_retry_non_transient_errors():
    from backend.db.engine import DbConn
    raw = _FakeRawConn(fail_times=99, errno=1064, errmsg="You have an error in your SQL syntax")
    conn = DbConn(raw, "mariadb")
    with pytest.raises(pymysql.err.OperationalError):
        await conn.execute("UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", ("h",))
    assert raw.cursor_calls == 1  # no retry for an unrelated error
