"""R2/A1 — a 404 'already absent' is a success only on a retry (attempts > 0)."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from pytest_httpx import HTTPXMock

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema

PLEX = "http://plex.local:32400"


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.deletion as _d
    path = str(tmp_path / "a1.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_d, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


# ── clients ──────────────────────────────────────────────────────────────────

async def test_emby_404_without_missing_ok_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://emby.test/Items/x1", status_code=404)
    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))):
        from backend.emby_client import delete_item
        assert await delete_item("x1") is False


async def test_emby_404_with_missing_ok_succeeds(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://emby.test/Items/x1", status_code=404)
    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))):
        from backend.emby_client import delete_item
        assert await delete_item("x1", missing_ok=True) is True


async def test_emby_empty_id_false_without_request(httpx_mock: HTTPXMock):
    with patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))):
        from backend.emby_client import delete_item
        assert await delete_item("", missing_ok=True) is False
    assert httpx_mock.get_requests() == []


@respx.mock
async def test_plex_404_without_missing_ok_returns_false():
    # Same contract as before the hardening: a 404 on a first attempt is a
    # visible failure (False), it must not turn into an exception nor a success.
    from backend.plex_client import PlexClient
    respx.delete(f"{PLEX}/library/metadata/9").mock(return_value=httpx.Response(404))
    assert await PlexClient(url=PLEX, token="t").delete_item("9") is False


@respx.mock
async def test_plex_404_with_missing_ok_succeeds():
    from backend.plex_client import PlexClient
    respx.delete(f"{PLEX}/library/metadata/9").mock(return_value=httpx.Response(404))
    assert await PlexClient(url=PLEX, token="t").delete_item("9", missing_ok=True) is True


@respx.mock
async def test_plex_empty_key_false_without_request():
    from backend.plex_client import PlexClient
    route = respx.delete(f"{PLEX}/library/metadata/").mock(return_value=httpx.Response(404))
    assert await PlexClient(url=PLEX, token="t").delete_item("", missing_ok=True) is False
    assert route.call_count == 0


# ── MediaServerStep wiring ───────────────────────────────────────────────────

@pytest.mark.parametrize("attempts,expected", [(0, False), (None, False), (1, True), (3, True)])
async def test_media_server_step_passes_missing_ok_from_attempts(attempts, expected):
    from backend.deletion_pipeline import DeletionContext, MediaServerStep
    item = {"title": "M", "emby_id": "e1", "media_type": "Movie", "attempts": attempts}
    ctx = DeletionContext(item=item, dry_run=False)
    with (
        patch("backend.media_server_factory.delete_server_item", new=AsyncMock(return_value=True)) as d,
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        await MediaServerStep().execute(ctx)
    assert d.await_args.kwargs["missing_ok"] is expected


async def test_media_server_step_404_first_attempt_fails_end_to_end(httpx_mock: HTTPXMock):
    from backend.deletion_pipeline import DeletionContext, DeletionPipeline, MediaServerStep
    httpx_mock.add_response(url="http://emby.test/Items/e1", status_code=404)
    ctx = DeletionContext(item={"title": "M", "emby_id": "e1", "media_type": "Movie", "attempts": 0},
                          dry_run=False)
    with (
        patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))),
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        assert await DeletionPipeline([MediaServerStep()]).execute(ctx) is False


async def test_media_server_step_404_on_retry_succeeds_end_to_end(httpx_mock: HTTPXMock):
    from backend.deletion_pipeline import DeletionContext, DeletionPipeline, MediaServerStep
    httpx_mock.add_response(url="http://emby.test/Items/e1", status_code=404)
    ctx = DeletionContext(item={"title": "M", "emby_id": "e1", "media_type": "Movie", "attempts": 1},
                          dry_run=False)
    with (
        patch("backend.emby_client.get_client", new=AsyncMock(return_value=("http://emby.test", "k"))),
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        assert await DeletionPipeline([MediaServerStep()]).execute(ctx) is True


# ── release_claim ────────────────────────────────────────────────────────────

async def test_release_claim_increments_attempts(dbpath):
    from backend.db.engine import get_db
    from backend.db.repositories import release_claim
    async with get_db() as db:
        nid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, attempts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("e1", "T", "Movie", "l", "L", "/f", "2026-01-01", "2026-01-01", "deleting", 0),
        )
        await db.commit()
    assert await release_claim(nid) is True
    async with get_db() as db:
        row = await db.fetch_one("SELECT status, attempts FROM media_queue WHERE id=?", (nid,))
    assert (row["status"], row["attempts"]) == ("pending", 1)
    # a row not in 'deleting' is untouched
    assert await release_claim(nid) is False
    async with get_db() as db:
        row = await db.fetch_one("SELECT attempts FROM media_queue WHERE id=?", (nid,))
    assert row["attempts"] == 1
