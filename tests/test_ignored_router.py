"""Endpoint tests for backend/routers/ignored.py — previously zero coverage
(found during the 2026-08-10 audit): only import-level references from
conftest.py's fixture setup existed, no actual HTTP-level test.
"""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def _bypass_ignored_router_auth(test_client):
    """Same auth-override staleness as backup.py (see test_backup_router.py) —
    conftest.py's global override doesn't reach backend.routers.ignored."""
    import backend.routers.ignored as ignored_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM ignored_media")
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    test_client.app.dependency_overrides[ignored_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(ignored_router_mod.require_auth, None)


def _ignore_body(**overrides) -> dict:
    base = {"emby_id": "ign-1", "title": "Ignored Movie", "media_type": "Movie"}
    base.update(overrides)
    return base


async def _seed_queue_row(emby_id: str, title: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, title, "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending"),
        )
        await db.commit()


def test_list_ignored_empty_initially(test_client):
    r = test_client.get("/api/ignored")
    assert r.status_code == 200
    assert r.json() == []


def test_add_ignored_then_list_returns_it(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        r = test_client.post("/api/ignored", json=_ignore_body())
    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}

    r2 = test_client.get("/api/ignored")
    assert r2.status_code == 200
    titles = [row["title"] for row in r2.json()]
    assert "Ignored Movie" in titles


async def test_add_ignored_removes_matching_queue_entry(test_client):
    """Ignoring an item already in the deletion queue must remove it from
    there — an ignored item and a queued-for-deletion item are mutually
    exclusive states."""
    from backend.db.engine import get_db

    await _seed_queue_row("ign-2", "Dup Movie")

    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        r = test_client.post("/api/ignored", json=_ignore_body(emby_id="ign-2", title="Dup Movie"))
    assert r.status_code == 200

    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM media_queue WHERE emby_id=?", ("ign-2",))
    assert row is None


def test_remove_ignored_deletes_row(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="ign-3"))
    listed = test_client.get("/api/ignored").json()
    ignored_id = next(row["id"] for row in listed if row["emby_id"] == "ign-3")

    r = test_client.delete(f"/api/ignored/{ignored_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "removed"}

    remaining = test_client.get("/api/ignored").json()
    assert all(row["emby_id"] != "ign-3" for row in remaining)


def test_requeue_ignored_returns_404_for_unknown_id(test_client):
    r = test_client.post("/api/ignored/999999/requeue")
    assert r.status_code == 404


def test_requeue_ignored_reinserts_into_media_queue(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="ign-4", title="Requeue Me"))
    listed = test_client.get("/api/ignored").json()
    ignored_id = next(row["id"] for row in listed if row["emby_id"] == "ign-4")

    r = test_client.post(f"/api/ignored/{ignored_id}/requeue")
    assert r.status_code == 200
    assert r.json()["status"] == "requeued"

    # No longer in ignored_media
    remaining = test_client.get("/api/ignored").json()
    assert all(row["emby_id"] != "ign-4" for row in remaining)


async def test_requeue_ignored_already_in_queue_just_removes_ignored_entry(test_client):
    with patch("backend.scheduler.sync_emby_collection", new=AsyncMock()):
        test_client.post("/api/ignored", json=_ignore_body(emby_id="ign-5", title="Both"))
    listed = test_client.get("/api/ignored").json()
    ignored_id = next(row["id"] for row in listed if row["emby_id"] == "ign-5")

    # Simulate the item already having reappeared in the queue (e.g. a rescan)
    await _seed_queue_row("ign-5", "Both")

    r = test_client.post(f"/api/ignored/{ignored_id}/requeue")
    assert r.status_code == 200
    assert r.json() == {"status": "already_in_queue"}

    remaining = test_client.get("/api/ignored").json()
    assert all(row["emby_id"] != "ign-5" for row in remaining)
