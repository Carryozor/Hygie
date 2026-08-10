"""Regression test: POST /api/media/enrich-seerr must resolve Seerr requester
info via ONE cached pagination scan (build_seerr_request_cache), not one full
Seerr pagination scan per queue item (seerr_find_request_by_tmdb). Found
during the 2026-08-10 performance audit: seerr_find_request_by_tmdb() pages
through ALL Seerr requests to find a single tmdb_id match — called once per
item needing enrichment, an N-item queue could trigger N full re-scans of
Seerr's request list.
"""
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


async def _seed_item(emby_id: str, tmdb_id: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, tmdb_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (emby_id, "Movie", "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending", tmdb_id),
        )
        await db.commit()


async def test_enrich_seerr_uses_one_cache_scan_for_multiple_items(test_client):
    for i in range(5):
        await _seed_item(f"emby-{i}", f"tmdb-{i}")

    fake_cache = {
        f"tmdb-{i}": {"seerr_id": i, "user_id": i, "username": f"user{i}"}
        for i in range(5)
    }

    with (
        patch("backend.routers.media._get_poster_url", new=AsyncMock(return_value="")),
        patch("backend.routers.media.build_seerr_request_cache", new=AsyncMock(return_value=fake_cache)) as mock_cache,
        patch("backend.arr_clients.seerr.seerr_find_request_by_tmdb", new=AsyncMock()) as mock_per_item,
    ):
        r = test_client.post("/api/media/enrich-seerr")
        assert r.status_code == 200

    mock_cache.assert_awaited_once()
    mock_per_item.assert_not_awaited()


async def test_enrich_seerr_still_applies_cached_seerr_data(test_client):
    await _seed_item("emby-0", "tmdb-0")

    fake_cache = {"tmdb-0": {"seerr_id": 42, "user_id": 7, "username": "alice"}}

    with (
        patch("backend.routers.media._get_poster_url", new=AsyncMock(return_value="")),
        patch("backend.routers.media.build_seerr_request_cache", new=AsyncMock(return_value=fake_cache)),
    ):
        r = test_client.post("/api/media/enrich-seerr")
        assert r.status_code == 200

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT seerr_username FROM media_queue WHERE emby_id=?", ("emby-0",))
    assert row["seerr_username"] == "alice"
