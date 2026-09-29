"""Additional coverage for backend/routers/database.py beyond
tests/test_database_router.py — the mariadb dialect branch of /info, the
per-table count exception guard, the aiomysql-not-installed branch of
/test, and `_run_migration` itself (both directions, success and failure),
which /migrate only schedules as a background task in the existing tests
(mocked away there, so its own body has never run).
"""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def _bypass_database_router_auth(test_client):
    """Same staleness as tests/test_database_router.py: settings/database
    routers are not reloaded by conftest's test_client fixture, so the
    global dependency_overrides key (post-reload auth_mod.require_auth)
    does not match the require_auth object database.py's routes were built
    with — the router's own reference must be overridden explicitly."""
    import backend.routers.database as database_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM job_history WHERE job_type='db_migration'")
        await db.commit()
    test_client.app.dependency_overrides[database_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(database_router_mod.require_auth, None)
    if database_router_mod._migration_lock.locked():
        database_router_mod._migration_lock.release()


def test_db_info_reports_mariadb_connection_string_without_credentials(test_client):
    import backend.routers.database as database_router_mod
    with patch.object(database_router_mod, "DIALECT", "mariadb"), \
         patch.object(database_router_mod, "DATABASE_URL", "mysql+aiomysql://root:s3cret@dbhost:3306/hygie"):
        r = test_client.get("/api/database/info")
    assert r.status_code == 200
    body = r.json()
    assert body["dialect"] == "mariadb"
    assert body["connection"] == "dbhost:3306/hygie"
    assert "s3cret" not in body["connection"]


def test_db_info_marks_table_as_minus_one_on_query_error(test_client):
    import backend.routers.database as database_router_mod

    class _BoomDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def table_exists(self, table):
            return True

        async def fetch_one(self, sql):
            raise RuntimeError("locked")

    with patch.object(database_router_mod, "get_db", return_value=_BoomDB()):
        r = test_client.get("/api/database/info")
    assert r.status_code == 200
    body = r.json()
    assert all(v == -1 for v in body["tables"].values())


def test_test_connection_reports_aiomysql_not_installed(test_client):
    import sys
    with patch.dict(sys.modules, {"aiomysql": None}):
        r = test_client.post(
            "/api/database/test",
            json={"url": "mysql+aiomysql://user:pass@host:3306/hygie"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "aiomysql" in body["message"]


# ─── _run_migration — never exercised by test_database_router.py, which
#     always mocks it away to test only the /migrate endpoint's own guards.

async def test_run_migration_sqlite_to_mariadb_success_records_job(test_client):
    import backend.routers.database as database_router_mod
    from backend.db.logs import add_job_run
    from backend.db.engine import get_db

    run_id = await add_job_run("db_migration")
    with patch("backend.tools.migrate_to_mariadb.migrate", new=AsyncMock()) as mock_migrate:
        await database_router_mod._run_migration(
            database_router_mod.MigrateRequest(
                direction="sqlite_to_mariadb",
                target_url="mysql+aiomysql://user:pass@dbhost:3306/hygie",
            ),
            run_id,
        )
    mock_migrate.assert_awaited_once()
    async with get_db() as db:
        row = await db.fetch_one("SELECT status, message FROM job_history WHERE id=?", (run_id,))
    assert row["status"] == "success"
    assert "dbhost:3306/hygie" in row["message"]
    assert "user:pass" not in row["message"]


async def test_run_migration_mariadb_to_sqlite_success_records_job(test_client):
    import backend.routers.database as database_router_mod
    from backend.db.logs import add_job_run
    from backend.db.engine import get_db

    run_id = await add_job_run("db_migration")
    with patch("backend.tools.migrate_to_sqlite.migrate", new=AsyncMock()) as mock_migrate:
        await database_router_mod._run_migration(
            database_router_mod.MigrateRequest(
                direction="mariadb_to_sqlite",
                target_path="/tmp/out.db",
            ),
            run_id,
        )
    mock_migrate.assert_awaited_once()
    async with get_db() as db:
        row = await db.fetch_one("SELECT status, message FROM job_history WHERE id=?", (run_id,))
    assert row["status"] == "success"
    assert "/tmp/out.db" in row["message"]


async def test_run_migration_unknown_direction_records_error(test_client):
    import backend.routers.database as database_router_mod
    from backend.db.logs import add_job_run
    from backend.db.engine import get_db

    run_id = await add_job_run("db_migration")
    await database_router_mod._run_migration(
        database_router_mod.MigrateRequest(direction="postgres_to_sqlite"),
        run_id,
    )
    async with get_db() as db:
        row = await db.fetch_one("SELECT status, message FROM job_history WHERE id=?", (run_id,))
    assert row["status"] == "error"
    assert "postgres_to_sqlite" in row["message"]


async def test_run_migration_records_error_on_migrate_exception(test_client):
    import backend.routers.database as database_router_mod
    from backend.db.logs import add_job_run
    from backend.db.engine import get_db

    run_id = await add_job_run("db_migration")
    with patch("backend.tools.migrate_to_mariadb.migrate", new=AsyncMock(side_effect=RuntimeError("connection refused"))):
        await database_router_mod._run_migration(
            database_router_mod.MigrateRequest(
                direction="sqlite_to_mariadb",
                target_url="mysql+aiomysql://u:p@h:3306/hygie",
            ),
            run_id,
        )
    async with get_db() as db:
        row = await db.fetch_one("SELECT status, message FROM job_history WHERE id=?", (run_id,))
    assert row["status"] == "error"
    assert "connection refused" in row["message"]
