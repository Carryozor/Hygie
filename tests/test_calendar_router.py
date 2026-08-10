"""Endpoint tests for backend/routers/calendar.py — previously zero coverage
(found during the 2026-08-10 audit): only import-level references from
conftest.py's fixture setup existed, no actual HTTP-level test.
"""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
async def _bypass_calendar_router_auth(test_client):
    """Same auth-override staleness as backup.py (see test_backup_router.py) —
    conftest.py's global override doesn't reach backend.routers.calendar."""
    import backend.routers.calendar as calendar_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()  # conftest.py's test_client never calls this — schema is otherwise absent
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    test_client.app.dependency_overrides[calendar_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(calendar_router_mod.require_auth, None)


async def _seed_pending_item(emby_id: str, delete_at_offset_days: int, title: str = "Test Movie"):
    from backend.db.engine import get_db
    delete_at = (datetime.now(timezone.utc) + timedelta(days=delete_at_offset_days)).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, title, "Movie", "lib1", "Library", "/f/x.mkv",
             datetime.now(timezone.utc).isoformat(), delete_at, "pending"),
        )
        await db.commit()


async def test_calendar_returns_empty_events_with_no_pending_items(test_client):
    r = test_client.get("/api/calendar")
    assert r.status_code == 200
    assert r.json() == {"events": {}}


async def test_calendar_groups_pending_items_by_delete_date(test_client):
    await _seed_pending_item("cal-emby-1", 5, "Movie A")

    r = test_client.get("/api/calendar")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 1
    day_key = next(iter(events))
    titles = [item["title"] for item in events[day_key]]
    assert "Movie A" in titles


async def test_calendar_excludes_items_beyond_days_ahead(test_client):
    await _seed_pending_item("cal-emby-far", 200, "Far Future Movie")

    r = test_client.get("/api/calendar?days_ahead=30")
    assert r.status_code == 200
    all_titles = [item["title"] for items in r.json()["events"].values() for item in items]
    assert "Far Future Movie" not in all_titles


async def test_calendar_rejects_days_ahead_out_of_range(test_client):
    r = test_client.get("/api/calendar?days_ahead=0")
    assert r.status_code == 422
    r2 = test_client.get("/api/calendar?days_ahead=9999")
    assert r2.status_code == 422
