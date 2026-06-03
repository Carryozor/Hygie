# tests/test_plex_scanner.py
"""Tests: scanner routes Plex server to PlexClient.scan_library()."""
import os
import pytest

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_plex_server_calls_plex_client(tmp_path):
    """When a server has type=plex, scanner calls PlexClient.scan_library()."""
    from backend.plex_client import PlexClient

    mock_items = [
        {
            "plex_id": "101",
            "title": "Inception",
            "media_type": "movie",
            "view_count": 2,
            "last_viewed_at": "2024-01-01T00:00:00+00:00",
            "added_at": "2023-01-01T00:00:00+00:00",
            "duration_ms": 8880000,
            "rating": 8.5,
            "poster_url": "http://plex.local/thumb/101",
            "tmdb_id": "27205",
            "grandparent_title": "",
            "season_number": None,
            "raw": {},
        }
    ]

    with patch.object(PlexClient, "scan_library", new_callable=AsyncMock) as mock_scan:
        mock_scan.return_value = mock_items

        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "plex1", "type": "plex", "url": "http://plex.local:32400", "api_key": "tok"}
        library = {
            "id": "lib-plex-1",
            "name": "Movies",
            "emby_library_id": "1",
            "server_id": "plex1",
            "conditions": "[]",
            "logic": "AND",
            "grace_days": 7,
            "enabled": 1,
            "deletion_unit": "movie",
        }

        import backend.db.engine as eng
        eng.SQLITE_PATH = str(tmp_path / "scan.db")
        from backend.db.schema import init_db
        await init_db()

        result = await _scan_plex_library(server=server, library=library)
        mock_scan.assert_called_once_with("1")
        assert result >= 0


@pytest.mark.asyncio
async def test_plex_no_tmdb_no_rule_not_queued(tmp_path):
    """Items with no TMDB ID and no matching expert rule are NOT queued.

    The Plex scanner has no fallback heuristic — items are only queued via:
    (a) TMDB cross-reference with an Emby-queued item, or
    (b) an explicit expert rule match.
    This prevents the library scan date (addedAt) from being used as a proxy
    for content age, which would queue hundreds of items incorrectly.
    """
    from backend.plex_client import PlexClient

    mock_items = [
        {
            "plex_id": "202",
            "title": "Unwatched Movie",
            "media_type": "movie",
            "view_count": 0,
            "last_viewed_at": None,
            "added_at": "2020-01-01T00:00:00+00:00",
            "duration_ms": 7200000,
            "rating": 0.0,
            "poster_url": "",
            "tmdb_id": "",
            "grandparent_title": "",
            "season_number": None,
            "raw": {},
        }
    ]

    with patch.object(PlexClient, "scan_library", new_callable=AsyncMock) as mock_scan:
        mock_scan.return_value = mock_items
        db_path = str(tmp_path / "scan2.db")
        import backend.db.engine as eng
        eng.SQLITE_PATH = db_path
        from backend.db.schema import init_db
        await init_db()

        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p2", "type": "plex", "url": "http://plex.local:32400", "api_key": "tok"}
        library = {
            "id": "lib-p2",
            "name": "Movies",
            "emby_library_id": "1",
            "server_id": "p2",
            "conditions": "[]",
            "logic": "AND",
            "grace_days": 7,
            "enabled": 1,
            "deletion_unit": "movie",
        }
        count = await _scan_plex_library(server=server, library=library)
        # No TMDB match, no expert rule → item must NOT be queued
        assert count == 0


@pytest.mark.asyncio
async def test_plex_tmdb_crossref_queued(tmp_path):
    """Items whose TMDB ID matches an Emby-queued item are mirrored to Plex queue."""
    from backend.plex_client import PlexClient

    mock_items = [
        {
            "plex_id": "303",
            "title": "Top Gun",
            "media_type": "movie",
            "view_count": 0,
            "last_viewed_at": None,
            "added_at": "2023-01-01T00:00:00+00:00",
            "duration_ms": 7000000,
            "rating": 7.5,
            "poster_url": "",
            "tmdb_id": "744",
            "grandparent_title": "",
            "season_number": None,
            "raw": {},
        }
    ]

    with patch.object(PlexClient, "scan_library", new_callable=AsyncMock) as mock_scan:
        mock_scan.return_value = mock_items
        db_path = str(tmp_path / "scan3.db")
        import backend.db.engine as eng
        eng.SQLITE_PATH = db_path
        from backend.db.schema import init_db
        await init_db()

        # Pre-seed: Emby already queued this TMDB ID
        async with eng.get_db() as db:
            await db.execute(
                """INSERT INTO libraries (id, name, emby_library_id, server_id, conditions,
                   logic, grace_days, enabled, deletion_unit, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("lib-emby-1", "Films Emby", "lib1", "emby1", "[]", "AND", 7, 1, "movie", "2024-01-01"),
            )
            await db.execute(
                """INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name,
                   file_path, poster_url, tmdb_id, status, detected_at, delete_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("44797", "Top Gun", "movie", "lib-emby-1", "Films Emby",
                 "/movies/topgun.mkv", "", "744", "pending",
                 "2024-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"),
            )
            await db.commit()

        from backend.scanner._plex_scanner import _scan_plex_library
        server = {"id": "p3", "type": "plex", "url": "http://plex.local:32400", "api_key": "tok"}
        library = {
            "id": "lib-p3",
            "name": "Movies Plex",
            "emby_library_id": "1",
            "server_id": "p3",
            "conditions": "[]",
            "logic": "AND",
            "grace_days": 7,
            "enabled": 1,
            "deletion_unit": "movie",
        }
        count = await _scan_plex_library(server=server, library=library)
        assert count == 1

        async with eng.get_db() as db:
            row = await db.fetch_one(
                "SELECT status, tmdb_id FROM media_queue WHERE emby_id=?", ("303",)
            )
        assert row is not None
        assert row["status"] == "pending"
        assert row["tmdb_id"] == "744"
