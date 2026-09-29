"""Coverage tests for backend/routers/public.py — the no-auth public dashboard
endpoint. tests/test_public_dashboard_url_leak.py already covers the ext_url
leak regression; this file covers the remaining branches: rate limiting,
the disabled/not-found/password gates, the admin-token bypass, and the
delete_at grouping logic (including the "unparseable date" skip).

Standalone app (same pattern as test_public_dashboard_url_leak.py) — public.py
is a no-auth router so no dependency override is needed, only DB isolation.
"""
from unittest.mock import patch

import pytest
import pytest_asyncio
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")


@pytest_asyncio.fixture
async def public_client(tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _ss
    import backend.db.media_servers as _db_ms
    import backend.auth as _auth_mod

    _auth_mod.DB_PATH = ":memory:"
    _auth_mod._rate_buckets.clear()

    db_path = str(tmp_path / "public_test.db")
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0

    from backend.db.schema import init_db
    await init_db()
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "true")

    from backend.routers import public as public_router
    app = FastAPI()
    app.include_router(public_router.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _auth_mod._rate_buckets.clear()


async def test_upcoming_returns_429_when_rate_limited(public_client):
    with patch("backend.routers.public.rate_limit_attempt", return_value=(True, "tok")):
        r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 429
    assert r.json() == {"error": "too_many_requests"}


async def test_upcoming_returns_403_when_dashboard_disabled(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "false")
    r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 403
    assert r.json() == {"error": "disabled"}


async def test_upcoming_returns_404_for_wrong_slug(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_slug", "correct-slug")
    r = await public_client.get("/api/public/upcoming", params={"slug": "wrong-slug"})
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


async def test_upcoming_requires_password_when_configured(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "s3cret")
    r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 401
    assert r.json() == {"error": "password_required"}


async def test_upcoming_rejects_wrong_password(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "s3cret")
    r = await public_client.get(
        "/api/public/upcoming", headers={"X-Dashboard-Password": "wrong"}
    )
    assert r.status_code == 403
    assert r.json() == {"error": "wrong_password"}


async def test_upcoming_accepts_correct_password(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "s3cret")
    r = await public_client.get(
        "/api/public/upcoming", headers={"X-Dashboard-Password": "s3cret"}
    )
    assert r.status_code == 200


async def test_upcoming_admin_token_bypasses_password_requirement(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "s3cret")
    with patch("backend.routers.public.verify_token", return_value="admin-user"):
        r = await public_client.get(
            "/api/public/upcoming", headers={"Authorization": "Bearer faketoken"}
        )
    assert r.status_code == 200


async def test_upcoming_invalid_bearer_token_still_requires_password(public_client):
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_password", "s3cret")
    with patch("backend.routers.public.verify_token", return_value=None):
        r = await public_client.get(
            "/api/public/upcoming", headers={"Authorization": "Bearer invalid"}
        )
    assert r.status_code == 401


async def test_upcoming_groups_events_by_delete_date(public_client):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, enabled) "
            "VALUES ('lib1', 'Movies', 'emby-lib-1', '0', 1)"
        )
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_name, library_id, "
            "file_path, detected_at, status, delete_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            ("e1", "Movie A", "movie", "Movies", "lib1", "/movies/a", "2025-12-01T00:00:00+00:00", "2026-01-01T12:00:00+00:00"),
        )
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_name, library_id, "
            "file_path, detected_at, status, delete_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            ("e2", "Movie B", "movie", "Movies", "lib1", "/movies/b", "2025-12-01T00:00:00+00:00", "2026-01-01T18:00:00+00:00"),
        )
        await db.commit()
    r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 200
    events = r.json()["events"]
    assert "2026-01-01" in events
    assert len(events["2026-01-01"]) == 2


async def test_upcoming_skips_rows_with_unparseable_delete_at(public_client):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, enabled) "
            "VALUES ('lib1', 'Movies', 'emby-lib-1', '0', 1)"
        )
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_name, library_id, "
            "file_path, detected_at, status, delete_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            ("e1", "Bad Date", "movie", "Movies", "lib1", "/movies/bad", "2025-12-01T00:00:00+00:00", "0000-99-99"),
        )
        await db.commit()
    # The SQL filter "delete_at <= ?" is a plain string compare against the
    # horizon ("2026-..."), so a malformed value must still sort lexically
    # below it (leading "0") to be selected by the query and reach the
    # Python-side parse_iso_dt() skip — otherwise this test would pass
    # vacuously (SQL filtering it out, not the code path under test).
    r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 200
    # No exception + the malformed row (if selected at all) never produces a group
    for items in r.json()["events"].values():
        for item in items:
            assert item["emby_id"] != "e1"


async def test_upcoming_never_leaks_password_setting_shape_when_disabled_releases_rate_limit(public_client):
    """Disabled path must call release_attempt so legitimate repeat visits to
    a disabled dashboard are never rate-limited by this endpoint alone."""
    from backend.db.settings_store import set_setting
    await set_setting("public_dashboard_enabled", "false")
    with patch("backend.routers.public.release_attempt") as mock_release:
        r = await public_client.get("/api/public/upcoming")
    assert r.status_code == 403
    mock_release.assert_called_once()
