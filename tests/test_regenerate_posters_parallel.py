"""Regression test: POST /api/media/regenerate-posters must fetch poster URLs
concurrently (bounded), not one HTTP round-trip at a time. Found during the
2026-08-10 performance audit. _get_poster_url() is read-only (no side effects
on Radarr/Sonarr/Emby beyond the GET itself), so bounded concurrency is safe
here — DB writes (update_poster) stay sequential to avoid SQLite write
contention.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
async def _setup(test_client):
    import backend.routers.media as media_router_mod
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    test_client.app.dependency_overrides[media_router_mod.require_auth] = lambda: "testuser"
    yield
    test_client.app.dependency_overrides.pop(media_router_mod.require_auth, None)


async def _seed_pending(emby_id: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (emby_id, "Movie", "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending"),
        )
        await db.commit()


async def test_regenerate_posters_fetches_concurrently(test_client):
    for i in range(6):
        await _seed_pending(f"emby-{i}")

    concurrent_peak = 0
    concurrent_now = 0

    async def _fake_get_poster_url(*a, **kw):
        nonlocal concurrent_peak, concurrent_now
        concurrent_now += 1
        concurrent_peak = max(concurrent_peak, concurrent_now)
        await asyncio.sleep(0.01)
        concurrent_now -= 1
        return "http://poster.example/x.jpg"

    with patch("backend.routers.media._get_poster_url", new=_fake_get_poster_url):
        r = test_client.post("/api/media/regenerate-posters")
        assert r.status_code == 200

    assert concurrent_peak > 1, "poster fetches ran fully sequentially — expected some concurrency"


async def test_regenerate_posters_still_updates_all_matching_items(test_client):
    for i in range(3):
        await _seed_pending(f"emby-{i}")

    with patch("backend.routers.media._get_poster_url", new=AsyncMock(return_value="http://poster.example/x.jpg")):
        r = test_client.post("/api/media/regenerate-posters")
        assert r.status_code == 200

    from backend.db.engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all("SELECT poster_url FROM media_queue")
    assert all(row["poster_url"] == "http://poster.example/x.jpg" for row in rows)
    assert len(rows) == 3
