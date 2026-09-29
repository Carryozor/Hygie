"""Additional coverage for backend/routers/calendar.py, filling the one gap
left by tests/test_calendar_router.py (95% baseline): a pending item whose
delete_at can't be parsed must be skipped, not crash the endpoint. Also
adds the auth check missing from that file."""
import pytest


@pytest.fixture(autouse=True)
async def _bypass_calendar_router_auth(test_client):
    import backend.routers.calendar as calendar_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    test_client.app.dependency_overrides[calendar_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(calendar_router_mod.require_auth, None)


async def _seed_pending_item(emby_id: str, delete_at: str, title: str = "Test Movie"):
    from backend.db.engine import get_db
    from datetime import datetime, timezone
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, title, "Movie", "lib1", "Library", "/f/x.mkv",
             datetime.now(timezone.utc).isoformat(), delete_at, "pending"),
        )
        await db.commit()


async def test_calendar_skips_item_with_unparseable_delete_at(test_client):
    """An empty/garbage delete_at must not crash grouping — the item is
    silently excluded from the calendar view (better than a 500)."""
    await _seed_pending_item("cal-bad", "", title="Bad Date Movie")
    from datetime import datetime, timedelta, timezone
    good_delete_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await _seed_pending_item("cal-good", good_delete_at, title="Good Date Movie")

    r = test_client.get("/api/calendar")
    assert r.status_code == 200
    all_titles = [item["title"] for items in r.json()["events"].values() for item in items]
    assert "Bad Date Movie" not in all_titles
    assert "Good Date Movie" in all_titles


def test_calendar_requires_auth(test_client):
    import backend.routers.calendar as calendar_router_mod
    override = test_client.app.dependency_overrides.pop(calendar_router_mod.require_auth, None)
    try:
        r = test_client.get("/api/calendar")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[calendar_router_mod.require_auth] = override
