"""Coverage tests for backend/scanner/_plex_scanner.py — Plex library scan.

Targets branches not exercised by tests/test_plex_scanner.py: the
_tmdb_key() type-prefix branches (movie/tv/other), the no-client guard,
skipping items with no plex_id / already queued / already ignored /
cross-server-TMDB-ignored, and queuing via a direct expert rule match
(as opposed to the TMDB cross-reference mirror path already covered).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.scanner._plex_scanner import _tmdb_key


# ─── _tmdb_key ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("media_type,expected_prefix", [
    ("movie", "movie"),
    ("Movie", "movie"),
    ("series", "tv"),
    ("episode", "tv"),
    ("season", "tv"),
    ("", ""),
    ("weird_type", "weird_type"),
])
def test_tmdb_key_prefixes_by_normalized_media_type(media_type, expected_prefix):
    assert _tmdb_key("123", media_type) == f"{expected_prefix}_123"


def test_tmdb_key_movie_and_tv_do_not_collide():
    assert _tmdb_key("1402", "movie") != _tmdb_key("1402", "series")


# ─── _scan_plex_library — guard clauses ─────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine
    import backend.db.schema as _db_schema
    db_path = str(tmp_path / "plex_scan_cov.db")
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    await _db_schema.init_db()


def _library(**overrides) -> dict:
    lib = {
        "id": "lib1", "name": "Movies", "emby_library_id": "1",
        "server_id": "p1", "conditions": "[]", "logic": "AND",
        "grace_days": 7, "enabled": 1, "deletion_unit": "movie",
    }
    lib.update(overrides)
    return lib


async def test_scan_plex_library_returns_zero_when_client_cannot_be_built():
    """A Plex server with no URL/token must not crash the scan — just skip it."""
    from backend.scanner._plex_scanner import _scan_plex_library
    server = {"id": "p1", "type": "plex", "url": "", "api_key": ""}
    result = await _scan_plex_library(server=server, library=_library())
    assert result == 0


async def test_scan_plex_library_skips_item_with_no_plex_id():
    from backend.plex_client import PlexClient
    items = [{"title": "No ID", "media_type": "movie", "tmdb_id": "1"}]
    with patch.object(PlexClient, "scan_library", new=AsyncMock(return_value=items)):
        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p1", "type": "plex", "url": "http://plex:32400", "api_key": "tok"}
        result = await _scan_plex_library(server=server, library=_library())
    assert result == 0


async def test_scan_plex_library_skips_already_queued_item():
    """An item whose plex_id (stored as emby_id) is already queued for THIS
    server (via the libraries.server_id JOIN) must be skipped — not
    re-queued or duplicated."""
    from backend.plex_client import PlexClient
    from backend.db.engine import get_db

    async with get_db() as db:
        await db.execute(
            """INSERT INTO libraries (id, name, emby_library_id, server_id, conditions,
               logic, grace_days, enabled, deletion_unit, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("lib1", "Movies", "1", "p1", "[]", "AND", 7, 1, "movie", "2024-01-01"),
        )
        await db.execute(
            """INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name,
               file_path, status, detected_at, delete_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            ("501", "Dup", "movie", "lib1", "Movies", "", "pending",
             "2024-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"),
        )
        await db.commit()

    # The item would otherwise be eligible (expert rule matches) so that, if
    # the already-queued guard silently failed to skip it, `added` would
    # become 1 instead of 0 — a signal strong enough to survive mutation
    # (an item skipped further downstream for an unrelated reason would mask
    # a broken guard here).
    items = [{
        "plex_id": "501", "title": "Dup", "media_type": "movie", "tmdb_id": "",
        "view_count": 0, "last_viewed_at": None, "added_at": "2020-01-01T00:00:00+00:00",
    }]
    with (
        patch.object(PlexClient, "scan_library", new=AsyncMock(return_value=items)),
        patch(
            "backend.scanner._plex_scanner._evaluate_expert_rules",
            new=AsyncMock(return_value=("queue", 3)),
        ),
    ):
        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p1", "type": "plex", "url": "http://plex:32400", "api_key": "tok"}
        result = await _scan_plex_library(server=server, library=_library(server_id="p1"))
    assert result == 0

    async with get_db() as db:
        rows = await db.fetch_all("SELECT id FROM media_queue WHERE emby_id='501'")
    assert len(rows) == 1  # still just the one pre-existing row, not duplicated


async def test_scan_plex_library_skips_item_ignored_cross_server_by_tmdb():
    from backend.plex_client import PlexClient
    from backend.db.engine import get_db

    async with get_db() as db:
        await db.execute(
            """INSERT INTO libraries (id, name, emby_library_id, server_id, conditions,
               logic, grace_days, enabled, deletion_unit, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("lib-emby", "Films Emby", "1", "emby1", "[]", "AND", 7, 1, "movie", "2024-01-01"),
        )
        await db.execute(
            """INSERT INTO ignored_media (emby_id, title, tmdb_id, media_type, library_id, ignored_at)
               VALUES (?,?,?,?,?,?)""",
            ("9001", "Ignored Movie", "555", "movie", "lib-emby", "2024-01-01T00:00:00+00:00"),
        )
        await db.commit()

    items = [{"plex_id": "601", "title": "Also 555", "media_type": "movie", "tmdb_id": "555"}]
    with patch.object(PlexClient, "scan_library", new=AsyncMock(return_value=items)):
        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p1", "type": "plex", "url": "http://plex:32400", "api_key": "tok"}
        result = await _scan_plex_library(server=server, library=_library())
    assert result == 0


async def test_scan_plex_library_queues_via_direct_expert_rule_match():
    """An item with no TMDB cross-reference match can still be queued when an
    expert rule directly matches it (the `action == QUEUE` branch, distinct
    from the TMDB-mirror path already covered by test_plex_scanner.py)."""
    from backend.plex_client import PlexClient

    items = [{
        "plex_id": "701", "title": "Rule Matched", "media_type": "movie",
        "view_count": 0, "last_viewed_at": None,
        "added_at": "2020-01-01T00:00:00+00:00", "tmdb_id": "",
        "poster_url": "", "season_number": None,
    }]
    with (
        patch.object(PlexClient, "scan_library", new=AsyncMock(return_value=items)),
        patch(
            "backend.scanner._plex_scanner._evaluate_expert_rules",
            new=AsyncMock(return_value=("queue", 3)),
        ),
    ):
        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p1", "type": "plex", "url": "http://plex:32400", "api_key": "tok"}
        result = await _scan_plex_library(server=server, library=_library())
    assert result == 1

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM media_queue WHERE emby_id='701'")
    assert row is not None
    assert row["status"] == "pending"
