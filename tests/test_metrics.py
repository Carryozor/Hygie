"""Tests for /api/metrics endpoint — per-library breakdown."""
import importlib
import pytest
import pytest_asyncio
import aiosqlite
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def client_with_data(monkeypatch, tmp_path):
    """FastAPI test client with seeded stats_history data."""
    from argon2 import PasswordHasher

    db_path = str(tmp_path / "metrics_test.db")

    # Patch all DB_PATH references before importing main
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.logs as _db_logs
    import backend.deletion as _deletion
    import backend.routers.metrics as _metrics_router

    for mod in (_db_utils, _db_ss, _db_ms, _db_schema, _db_logs, _deletion, _metrics_router):
        monkeypatch.setattr(mod, "DB_PATH", db_path)

    import backend.auth as auth_mod
    import backend.main as main_mod
    importlib.reload(auth_mod)
    importlib.reload(main_mod)
    auth_mod._ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

    # Re-patch after reload
    for mod in (_db_utils, _db_ss, _db_ms, _db_schema, _db_logs, _deletion, _metrics_router):
        monkeypatch.setattr(mod, "DB_PATH", db_path)

    app = main_mod.app
    async with main_mod.lifespan(app):
        # Seed stats_history with known per-library data
        async with aiosqlite.connect(db_path) as db:
            await db.executemany(
                "INSERT INTO stats_history "
                "(ts, total_deleted, total_scanned, space_freed_bytes, month, library_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("2026-05-01T10:00:00Z", 3, 100, 10_000_000_000, "2026-05", 1),
                    ("2026-05-02T10:00:00Z", 1, 50,  5_000_000_000,  "2026-05", 2),
                ],
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Register and login to get a token
            r = await c.post(
                "/api/auth/setup",
                json={"username": "admin", "password": "strongpass123"},
            )
            if r.status_code == 200:
                token = r.json()["token"]
            else:
                r2 = await c.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "strongpass123"},
                )
                token = r2.json()["token"]

            yield c, token, db_path


@pytest.mark.asyncio
async def test_api_metrics_endpoint_exists(client_with_data):
    """GET /api/metrics returns 200."""
    c, token, _ = client_with_data
    r = await c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_metrics_by_library_key_present(client_with_data):
    """Response contains by_library key."""
    c, token, _ = client_with_data
    r = await c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    assert "by_library" in data


@pytest.mark.asyncio
async def test_metrics_by_library_contains_both_libraries(client_with_data):
    """Both seeded library_ids appear in by_library."""
    c, token, _ = client_with_data
    r = await c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    lib_ids = {item["library_id"] for item in data["by_library"]}
    assert 1 in lib_ids
    assert 2 in lib_ids


@pytest.mark.asyncio
async def test_metrics_by_library_deleted_counts(client_with_data):
    """Deleted counts match seeded data."""
    c, token, _ = client_with_data
    r = await c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    by_lib = {item["library_id"]: item for item in data["by_library"]}
    assert by_lib[1]["deleted"] == 3
    assert by_lib[2]["deleted"] == 1


@pytest.mark.asyncio
async def test_metrics_by_library_space_freed(client_with_data):
    """space_freed_bytes matches seeded data."""
    c, token, _ = client_with_data
    r = await c.get("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    by_lib = {item["library_id"]: item for item in data["by_library"]}
    assert by_lib[1]["space_freed_bytes"] == 10_000_000_000
    assert by_lib[2]["space_freed_bytes"] == 5_000_000_000


@pytest.mark.asyncio
async def test_stats_history_has_library_id_column(tmp_path, monkeypatch):
    """stats_history table has library_id column after init_db."""
    db_path = str(tmp_path / "schema_check.db")
    import backend.db.schema as _schema
    monkeypatch.setattr(_schema, "DB_PATH", db_path)
    monkeypatch.setattr("backend.db.utils.DB_PATH", db_path)
    await _schema.init_db()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(stats_history)") as cur:
            cols = {row[1] async for row in cur}
    assert "library_id" in cols
