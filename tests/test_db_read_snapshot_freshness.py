"""Regression test — 2026-09-19: read-only get_db() blocks left a snapshot open.

The MariaDB pool runs with autocommit=False. A bare SELECT therefore opens a
REPEATABLE-READ transaction, and get_db() returned the connection to the pool
without ending it. Every later read on that pooled connection saw the frozen
snapshot until some unrelated write happened to commit.

Effect observed in production: warn_if_job_starved() read a stale
`job_history` on the worker that lost the advisory lock and raised false
"Vérification des suppressions : aucune exécution depuis 180/240/300 min"
ERROR logs + Discord alerts, while the job had actually run every hour.

SQLite reopens a connection per get_db() call, so the suite cannot see this;
the unit tests below pin the contract on the MariaDB branch with a fake pool,
and the live tests replay the real-world reproduction.
"""
import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

os.environ.setdefault("DB_PATH", ":memory:")


class _FakeCursor:
    rowcount = 0
    lastrowid = 0

    async def execute(self, *_):
        return 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeRaw:
    def __init__(self):
        self.calls: list[str] = []
        self.autocommit = AsyncMock()
        self.commit = AsyncMock(side_effect=lambda: self.calls.append("commit"))
        self.rollback = AsyncMock(side_effect=lambda: self.calls.append("rollback"))

    def cursor(self, *_):
        return _FakeCursor()


class _FakePool:
    def __init__(self, raw):
        self._raw = raw
        self.released = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._raw

            async def __aexit__(self, *_):
                pool.released += 1
                return False

        return _Ctx()


@pytest.fixture
def fake_mariadb(monkeypatch):
    import backend.db.engine as eng

    raw = _FakeRaw()
    pool = _FakePool(raw)
    monkeypatch.setattr(eng, "DIALECT", "mariadb")
    monkeypatch.setattr(eng, "_pool", pool)
    return eng, raw, pool


@pytest.mark.asyncio
async def test_clean_exit_ends_the_transaction_before_releasing_the_connection(fake_mariadb):
    eng, raw, pool = fake_mariadb

    async with eng.get_db():
        pass  # read-only block: no explicit commit by the caller

    raw.rollback.assert_awaited_once()
    raw.commit.assert_not_awaited()
    assert pool.released == 1
    # the transaction must be over BEFORE the connection goes back to the pool
    assert raw.calls == ["rollback"]


@pytest.mark.asyncio
async def test_clean_exit_never_commits_work_the_caller_did_not_commit(fake_mariadb):
    """Uncommitted writes were discarded before this fix (the pool closes a
    connection left IN_TRANS); ending the block must not start persisting them."""
    eng, raw, _ = fake_mariadb

    async with eng.get_db() as db:
        await db.execute("UPDATE settings SET value='x' WHERE `key`='k'")

    raw.commit.assert_not_awaited()
    raw.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_exit_rolls_back_and_never_commits(fake_mariadb):
    eng, raw, pool = fake_mariadb

    with pytest.raises(ValueError):
        async with eng.get_db():
            raise ValueError("boom")

    raw.rollback.assert_awaited_once()
    raw.commit.assert_not_awaited()
    assert pool.released == 1


@pytest.mark.asyncio
async def test_explicit_commit_by_caller_is_still_honoured(fake_mariadb):
    eng, raw, _ = fake_mariadb

    async with eng.get_db() as db:
        await db.commit()

    # the caller's commit happens first; the closing rollback is then a no-op
    assert raw.calls == ["commit", "rollback"]


# ─── Integration: the production reproduction against a live MariaDB ──────────

_MARIADB_URL = os.environ.get("TEST_MARIADB_URL", "").strip()


@pytest_asyncio.fixture
async def live_probe(monkeypatch):
    """Real MariaDB pool (minsize=1: sequential get_db() calls share ONE
    connection) + an autocommit writer on a second connection.

    monkeypatch only — never importlib.reload(engine): that would re-evaluate
    SQLITE_PATH from the environment and clobber the path the session-scoped
    test_client fixture installed."""
    import aiomysql
    import backend.db.engine as eng

    monkeypatch.setattr(eng, "DATABASE_URL", _MARIADB_URL)
    monkeypatch.setattr(eng, "DIALECT", "mariadb")
    await eng.init_db_pool()
    writer = await aiomysql.connect(autocommit=True, **eng._parse_mariadb_url(_MARIADB_URL))
    try:
        async with writer.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS _snapshot_probe")
            await cur.execute("CREATE TABLE _snapshot_probe (v INT)")
            await cur.execute("INSERT INTO _snapshot_probe VALUES (1)")
        yield eng, writer
    finally:
        # Close the pool first: an unfinished transaction holds a metadata lock
        # on the table and would make the DROP below hang.
        await eng.close_db_pool()
        async with writer.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS _snapshot_probe")
        writer.close()


async def _writer_value(writer) -> int:
    async with writer.cursor() as cur:
        await cur.execute("SELECT v FROM _snapshot_probe")
        return (await cur.fetchone())[0]


@pytest.mark.skipif(not _MARIADB_URL, reason="TEST_MARIADB_URL not set — no live MariaDB")
@pytest.mark.asyncio
async def test_live_read_sees_write_committed_by_another_connection(live_probe):
    """A read through get_db() must not be pinned to an old snapshot."""
    eng, writer = live_probe

    async def read() -> int:
        async with eng.get_db() as db:
            return (await db.fetch_one("SELECT v FROM _snapshot_probe"))["v"]

    assert await read() == 1
    async with writer.cursor() as cur:
        await cur.execute("UPDATE _snapshot_probe SET v = 2")
    assert await read() == 2  # a stale snapshot would still answer 1


@pytest.mark.skipif(not _MARIADB_URL, reason="TEST_MARIADB_URL not set — no live MariaDB")
@pytest.mark.asyncio
async def test_live_uncommitted_write_is_still_discarded(live_probe):
    """Ending the block must not start persisting writes nobody committed."""
    eng, writer = live_probe

    async with eng.get_db() as db:
        await db.execute("UPDATE _snapshot_probe SET v = 99")  # no commit()

    assert await _writer_value(writer) == 1
