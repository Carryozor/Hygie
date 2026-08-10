"""Endpoint tests for backend/routers/database.py — previously zero coverage
(found during the 2026-08-10 audit). The actual migration execution
(_run_migration → tools/migrate_to_mariadb.py / migrate_to_sqlite.py) is
exercised elsewhere (tests/test_migrate_to_mariadb.py) — these tests cover
the router's own guard/validation logic and the info/test endpoints, mocking
_run_migration itself so a "valid request" test never touches a real target.
"""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def _bypass_database_router_auth(test_client):
    """Same auth-override staleness as backup.py (see test_backup_router.py) —
    conftest.py's global override doesn't reach backend.routers.database."""
    import backend.routers.database as database_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        # A prior interrupted run (real startup recovery, or a previous test
        # in this file) can leave db_migration rows behind — start clean.
        await db.execute("DELETE FROM job_history WHERE job_type='db_migration'")
        await db.commit()
    test_client.app.dependency_overrides[database_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(database_router_mod.require_auth, None)
    # The router's own module-level lock is shared process-wide — release it
    # if a test left it held (e.g. an assertion failed mid-lock).
    if database_router_mod._migration_lock.locked():
        database_router_mod._migration_lock.release()


# ── /api/database/info ──────────────────────────────────────────────────────

def test_db_info_returns_dialect_and_table_counts(test_client):
    r = test_client.get("/api/database/info")
    assert r.status_code == 200
    body = r.json()
    assert body["dialect"] == "sqlite"
    assert "settings" in body["tables"]
    assert isinstance(body["tables"]["settings"], int)


# ── /api/database/test ──────────────────────────────────────────────────────

def test_test_connection_rejects_empty_url(test_client):
    r = test_client.post("/api/database/test", json={"url": ""})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "URL vide"}


def test_test_connection_rejects_non_mariadb_scheme(test_client):
    r = test_client.post("/api/database/test", json={"url": "http://evil.example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_test_connection_reports_timeout(test_client):
    import asyncio
    with patch("aiomysql.connect", new=AsyncMock(side_effect=asyncio.TimeoutError())):
        r = test_client.post("/api/database/test", json={"url": "mysql+aiomysql://user:pass@host:3306/hygie"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Délai" in body["message"]


def test_test_connection_strips_credentials_from_error_message(test_client):
    with patch("aiomysql.connect", new=AsyncMock(side_effect=Exception("Can't connect to mysql+aiomysql://root:s3cr3t@badhost:3306/hygie"))):
        r = test_client.post("/api/database/test", json={"url": "mysql+aiomysql://root:s3cr3t@badhost:3306/hygie"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "s3cr3t" not in body["message"]


def test_test_connection_reports_success(test_client):
    fake_conn = AsyncMock()
    fake_cursor = AsyncMock()
    fake_cursor.fetchone = AsyncMock(return_value=("11.8.6-MariaDB",))
    fake_cursor.__aenter__ = AsyncMock(return_value=fake_cursor)
    fake_cursor.__aexit__ = AsyncMock(return_value=False)
    fake_conn.cursor = lambda: fake_cursor
    fake_conn.close = lambda: None

    with patch("aiomysql.connect", new=AsyncMock(return_value=fake_conn)):
        r = test_client.post("/api/database/test", json={"url": "mysql+aiomysql://user:pass@host:3306/hygie"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "11.8.6-MariaDB" in body["message"]


# ── /api/database/migrate ───────────────────────────────────────────────────

def test_start_migration_rejects_sqlite_to_mariadb_without_target_url(test_client):
    r = test_client.post("/api/database/migrate", json={"direction": "sqlite_to_mariadb"})
    assert r.status_code == 422


def test_start_migration_rejects_invalid_direction(test_client):
    r = test_client.post("/api/database/migrate", json={"direction": "sqlite_to_postgres"})
    assert r.status_code == 422


def test_start_migration_rejects_mariadb_to_sqlite_when_already_sqlite(test_client):
    """Default test DIALECT is sqlite — migrating "mariadb_to_sqlite" makes
    no sense when Hygie is already running on SQLite."""
    r = test_client.post("/api/database/migrate", json={"direction": "mariadb_to_sqlite"})
    assert r.status_code == 409


def test_start_migration_rejects_sqlite_to_mariadb_when_already_mariadb(test_client):
    import backend.routers.database as database_router_mod
    with patch.object(database_router_mod, "DIALECT", "mariadb"):
        r = test_client.post(
            "/api/database/migrate",
            json={"direction": "sqlite_to_mariadb", "target_url": "mysql+aiomysql://u:p@h:3306/hygie"},
        )
    assert r.status_code == 409


async def test_start_migration_returns_409_when_already_running(test_client):
    import backend.routers.database as database_router_mod
    await database_router_mod._migration_lock.acquire()
    try:
        r = test_client.post(
            "/api/database/migrate",
            json={"direction": "sqlite_to_mariadb", "target_url": "mysql+aiomysql://u:p@h:3306/hygie"},
        )
        assert r.status_code == 409
        assert r.json()["status"] == "already_running"
    finally:
        database_router_mod._migration_lock.release()


def test_start_migration_valid_request_schedules_background_job(test_client):
    with patch("backend.routers.database._run_migration", new=AsyncMock()):
        r = test_client.post(
            "/api/database/migrate",
            json={"direction": "sqlite_to_mariadb", "target_url": "mysql+aiomysql://u:p@h:3306/hygie"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert isinstance(body["job_id"], int)


# ── /api/database/migrate/status ────────────────────────────────────────────

def test_migration_status_returns_none_when_no_job_ran(test_client):
    r = test_client.get("/api/database/migrate/status")
    assert r.status_code == 200
    assert r.json() is None


def test_migration_status_returns_latest_job(test_client):
    """_run_migration is mocked to a no-op here (it's the thing that calls
    finish_job_run) — this only verifies the started job is the one surfaced
    by /migrate/status, not the terminal status value."""
    with patch("backend.routers.database._run_migration", new=AsyncMock()):
        started = test_client.post(
            "/api/database/migrate",
            json={"direction": "sqlite_to_mariadb", "target_url": "mysql+aiomysql://u:p@h:3306/hygie"},
        ).json()
    r = test_client.get("/api/database/migrate/status")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert body["id"] == started["job_id"]
