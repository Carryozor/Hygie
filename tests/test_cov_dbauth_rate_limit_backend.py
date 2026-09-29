"""Coverage-focused tests for backend/_rate_limit_backend.py — the raw-aiomysql
MariaDB rate-limit backend (deliberately outside the DbConn `?`-placeholder
abstraction, see its module docstring).

No real MariaDB is used: aiomysql.connect is monkeypatched to hand back a fake
connection/cursor that records every (sql, params) pair. This lets us verify:
  - the atomic record-then-count order (DELETE, then INSERT, then SELECT —
    the burst/TOCTOU-safety property the real docstring calls out)
  - the exact %s-placeholder SQL and bound params sent to the driver
  - the standalone connection is always closed, even when a query raises
"""
import pytest

import backend._rate_limit_backend as rlb


class _FakeCursor:
    def __init__(self, conn, fetchone_result=None, raise_on=None):
        self._conn = conn
        self._fetchone_result = fetchone_result
        self._raise_on = raise_on or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self._conn.queries.append((sql, params))
        for needle, exc in self._raise_on.items():
            if needle in sql:
                raise exc

    async def fetchone(self):
        return self._fetchone_result


class _FakeConn:
    def __init__(self, fetchone_result=None, raise_on=None):
        self.queries: list[tuple] = []
        self.closed = False
        self._fetchone_result = fetchone_result
        self._raise_on = raise_on or {}

    def cursor(self):
        return _FakeCursor(self, self._fetchone_result, self._raise_on)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_aiomysql_connect(monkeypatch):
    """Patch aiomysql.connect (used directly, not the pool) and a fake
    DATABASE_URL so _parse_mariadb_url has something valid to chew on."""
    import backend.db.engine as engine
    monkeypatch.setattr(engine, "DATABASE_URL", "mysql+aiomysql://u:p@dbhost:3306/hygie")

    created = {}

    def _install(conn: _FakeConn):
        import aiomysql

        async def fake_connect(**kwargs):
            created["kwargs"] = kwargs
            return conn

        monkeypatch.setattr(aiomysql, "connect", fake_connect)
        return created

    return _install


# ─── mariadb_rate_limit ─────────────────────────────────────────────────────────

def test_mariadb_rate_limit_not_blocked_when_under_max(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=(3,))
    fake_aiomysql_connect(conn)
    blocked = rlb.mariadb_rate_limit("ip:1.2.3.4", now=1000.0, cutoff=700.0, rate_limit_max=5)
    assert blocked is False
    assert conn.closed is True


def test_mariadb_rate_limit_blocked_when_over_max(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=(6,))
    fake_aiomysql_connect(conn)
    blocked = rlb.mariadb_rate_limit("ip:1.2.3.4", now=1000.0, cutoff=700.0, rate_limit_max=5)
    assert blocked is True


def test_mariadb_rate_limit_treats_no_row_as_zero_count(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=None)
    fake_aiomysql_connect(conn)
    assert rlb.mariadb_rate_limit("k", now=1.0, cutoff=0.0, rate_limit_max=5) is False


def test_mariadb_rate_limit_records_before_counting_and_uses_percent_s(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=(1,))
    fake_aiomysql_connect(conn)
    rlb.mariadb_rate_limit("mykey", now=1000.0, cutoff=700.0, rate_limit_max=5)

    kinds = [
        "DELETE" if "DELETE" in sql else "INSERT" if "INSERT" in sql else "SELECT"
        for sql, _ in conn.queries
    ]
    assert kinds == ["DELETE", "INSERT", "SELECT"], (
        "must record the attempt (INSERT) before counting (SELECT) — an "
        "atomic record-then-count is what closes the burst/TOCTOU window"
    )
    insert_sql, insert_params = conn.queries[1]
    assert "%s" in insert_sql and "?" not in insert_sql
    assert insert_params == ("mykey", 1000.0)
    select_sql, select_params = conn.queries[2]
    assert select_params == ("mykey", 700.0)


def test_mariadb_rate_limit_closes_connection_even_when_query_raises(fake_aiomysql_connect):
    conn = _FakeConn(raise_on={"SELECT COUNT": RuntimeError("db exploded")})
    fake_aiomysql_connect(conn)
    with pytest.raises(RuntimeError, match="db exploded"):
        rlb.mariadb_rate_limit("k", now=1.0, cutoff=0.0, rate_limit_max=5)
    assert conn.closed is True


# ─── mariadb_rate_limit_attempt ─────────────────────────────────────────────────

def test_mariadb_rate_limit_attempt_returns_token_equal_to_now(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=(2,))
    fake_aiomysql_connect(conn)
    blocked, token = rlb.mariadb_rate_limit_attempt("k", now=1234.5, cutoff=1000.0, rate_limit_max=5)
    assert blocked is False
    assert token == 1234.5


def test_mariadb_rate_limit_attempt_blocked_over_max(fake_aiomysql_connect):
    conn = _FakeConn(fetchone_result=(9,))
    fake_aiomysql_connect(conn)
    blocked, _token = rlb.mariadb_rate_limit_attempt("k", now=1.0, cutoff=0.0, rate_limit_max=5)
    assert blocked is True


def test_mariadb_rate_limit_attempt_closes_connection_even_when_query_raises(fake_aiomysql_connect):
    conn = _FakeConn(raise_on={"INSERT INTO rate_limit": RuntimeError("insert failed")})
    fake_aiomysql_connect(conn)
    with pytest.raises(RuntimeError, match="insert failed"):
        rlb.mariadb_rate_limit_attempt("k", now=1.0, cutoff=0.0, rate_limit_max=5)
    assert conn.closed is True


# ─── mariadb_release_attempt ─────────────────────────────────────────────────────

def test_mariadb_release_attempt_deletes_by_key_and_token(fake_aiomysql_connect):
    conn = _FakeConn()
    fake_aiomysql_connect(conn)
    rlb.mariadb_release_attempt("mykey", 1234.5)
    assert len(conn.queries) == 1
    sql, params = conn.queries[0]
    assert "DELETE" in sql and "LIMIT 1" in sql
    assert params == ("mykey", 1234.5)
    assert conn.closed is True


def test_mariadb_release_attempt_closes_connection_even_when_delete_raises(fake_aiomysql_connect):
    conn = _FakeConn(raise_on={"DELETE": RuntimeError("delete failed")})
    fake_aiomysql_connect(conn)
    with pytest.raises(RuntimeError, match="delete failed"):
        rlb.mariadb_release_attempt("k", 1.0)
    assert conn.closed is True
