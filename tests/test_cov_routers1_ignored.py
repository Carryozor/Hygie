"""Additional coverage for backend/routers/ignored.py, filling gaps left by
tests/test_ignored_router.py (90% baseline): the `search` filter on
list_ignored, the expire_days branch on add_ignored, the
grace_days-from-library branch on requeue_ignored, and an auth check
(no 401 test existed for this router)."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def _bypass_ignored_router_auth(test_client):
    import backend.routers.ignored as ignored_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM ignored_media")
        await db.execute("DELETE FROM media_queue")
        await db.execute("DELETE FROM libraries")
        await db.commit()
    test_client.app.dependency_overrides[ignored_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(ignored_router_mod.require_auth, None)


def _ignore_body(**overrides) -> dict:
    base = {"emby_id": "cov-ign-1", "title": "Ignored Movie", "media_type": "Movie"}
    base.update(overrides)
    return base


async def test_list_ignored_search_matches_title_or_reason(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="s1", title="The Matrix", reason=""))
        test_client.post("/api/ignored", json=_ignore_body(emby_id="s2", title="Other", reason="too old"))
        test_client.post("/api/ignored", json=_ignore_body(emby_id="s3", title="Unrelated", reason=""))

    r = test_client.get("/api/ignored?search=matrix")
    titles = [row["title"] for row in r.json()]
    assert titles == ["The Matrix"]

    r2 = test_client.get("/api/ignored?search=too old")
    titles2 = [row["title"] for row in r2.json()]
    assert titles2 == ["Other"]


async def test_add_ignored_sets_expire_at_when_expire_days_given(test_client):
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc)
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="exp-1", expire_days=15))

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT expire_at FROM ignored_media WHERE emby_id='exp-1'")
    assert row["expire_at"] is not None
    expire_at = datetime.fromisoformat(row["expire_at"])
    assert (expire_at - before).days >= 14


async def test_add_ignored_no_expire_days_leaves_expire_at_null(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="exp-2"))

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT expire_at FROM ignored_media WHERE emby_id='exp-2'")
    assert row["expire_at"] is None


async def test_requeue_uses_library_grace_days_when_library_matches(test_client):
    """When the ignored item's library_id matches a configured library,
    requeue must use ITS grace_days instead of the 7-day default."""
    from backend.db.engine import get_db
    from datetime import datetime, timezone
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, conditions, logic, grace_days, "
            "seerr_conditions, enabled, deletion_unit, created_at) "
            "VALUES ('lib-cov', 'Movies', 'emby-1', '[]', 'AND', 45, '[]', 1, 'episode', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await db.commit()

    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="rq-lib", library_id="lib-cov"))
    listed = test_client.get("/api/ignored").json()
    ignored_id = next(row["id"] for row in listed if row["emby_id"] == "rq-lib")

    before = datetime.now(timezone.utc)
    r = test_client.post(f"/api/ignored/{ignored_id}/requeue")
    assert r.status_code == 200

    delete_at = datetime.fromisoformat(r.json()["delete_at"])
    assert (delete_at - before).days >= 44  # ~45 days, not the 7-day default


async def test_requeue_defaults_to_seven_days_when_library_unmatched(test_client):
    from datetime import datetime, timezone
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="rq-nolib", library_id="no-such-library"))
    listed = test_client.get("/api/ignored").json()
    ignored_id = next(row["id"] for row in listed if row["emby_id"] == "rq-nolib")

    before = datetime.now(timezone.utc)
    r = test_client.post(f"/api/ignored/{ignored_id}/requeue")
    assert r.status_code == 200
    delete_at = datetime.fromisoformat(r.json()["delete_at"])
    assert 6 <= (delete_at - before).days <= 7


def test_list_ignored_requires_auth(test_client):
    import backend.routers.ignored as ignored_router_mod
    override = test_client.app.dependency_overrides.pop(ignored_router_mod.require_auth, None)
    try:
        r = test_client.get("/api/ignored")
        assert r.status_code == 401
    finally:
        if override is not None:
            test_client.app.dependency_overrides[ignored_router_mod.require_auth] = override
