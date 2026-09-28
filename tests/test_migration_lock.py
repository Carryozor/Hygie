"""Schema init + migrations must be serialized across uvicorn workers.

With WORKERS=2 every worker runs the startup lifespan, so both ran
run_migrations() concurrently. Observed in production on 2026-09-28 (4.3.4 →
4.3.5): both workers saw m016 as pending, both ran ALTER TABLE, the second
failed with `(1060, "Duplicate column name 'arr_server_url'")` and its worker
died at startup (uvicorn respawned it). A non-idempotent data migration run
twice concurrently would duplicate data instead of crashing.
"""
import asyncio
import os

import pytest
import pytest_asyncio

import backend.db.engine as eng
import backend.db.migrations as mig

_MARIADB_URL = os.environ.get("TEST_MARIADB_URL", "").strip()


async def test_migration_lock_is_a_noop_on_sqlite(monkeypatch):
    monkeypatch.setattr(eng, "DIALECT", "sqlite")
    entered = False
    async with mig.migration_lock():
        entered = True
    assert entered


async def test_startup_runs_schema_init_and_migrations_inside_the_lock(monkeypatch):
    import backend.main as main_mod
    events: list = []

    class _Lock:
        async def __aenter__(self):
            events.append("lock")

        async def __aexit__(self, *_):
            events.append("unlock")

    async def _noop_pool():
        events.append("pool")

    async def _backup():
        events.append("backup")

    async def _init_db():
        events.append("init_db")

    async def _run_migrations():
        events.append("migrations")
        return 0

    monkeypatch.setattr(eng, "init_db_pool", _noop_pool)
    monkeypatch.setattr(mig, "migration_lock", lambda: _Lock())
    monkeypatch.setattr(mig, "run_migrations", _run_migrations)
    import backend.backup as backup_mod
    monkeypatch.setattr(backup_mod, "backup_before_migrations", _backup)
    monkeypatch.setattr(main_mod, "init_db", _init_db)

    assert await main_mod._init_database_and_migrate() == ""
    assert events == ["pool", "lock", "backup", "init_db", "migrations", "unlock"]


# ─── Live MariaDB: two concurrent "workers" ────────────────────────────────────

_PROBE_ID = "t999_lock_probe"


@pytest_asyncio.fixture
async def live_mariadb(monkeypatch):
    import aiomysql

    monkeypatch.setattr(eng, "DATABASE_URL", _MARIADB_URL)
    monkeypatch.setattr(eng, "DIALECT", "mariadb")
    monkeypatch.setattr(mig, "DIALECT", "mariadb")
    await eng.init_db_pool()
    admin = await aiomysql.connect(autocommit=True, **eng._parse_mariadb_url(_MARIADB_URL))
    try:
        async with admin.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS _mig_lock_probe")
            await cur.execute("CREATE TABLE _mig_lock_probe (n INT)")
        await mig._ensure_migrations_table()
        async with admin.cursor() as cur:
            await cur.execute("DELETE FROM schema_migrations WHERE id=%s", (_PROBE_ID,))
        yield admin
    finally:
        await eng.close_db_pool()
        async with admin.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS _mig_lock_probe")
            await cur.execute("DELETE FROM schema_migrations WHERE id=%s", (_PROBE_ID,))
        admin.close()


@pytest.mark.skipif(not _MARIADB_URL, reason="TEST_MARIADB_URL not set — no live MariaDB")
async def test_live_concurrent_workers_apply_each_migration_once(live_mariadb, monkeypatch):
    async def _slow_non_idempotent():
        await asyncio.sleep(0.3)  # wide race window, like a real ALTER on a big table
        async with eng.get_db() as db:
            await db.execute("INSERT INTO _mig_lock_probe (n) VALUES (1)")
            await db.commit()

    monkeypatch.setattr(mig, "_MIGRATIONS", [(_PROBE_ID, "lock probe", _slow_non_idempotent)])

    async def worker():
        async with mig.migration_lock():
            await mig.run_migrations()

    await asyncio.gather(worker(), worker())

    async with live_mariadb.cursor() as cur:
        await cur.execute("SELECT COUNT(*) FROM _mig_lock_probe")
        (applied,) = await cur.fetchone()
    assert applied == 1
