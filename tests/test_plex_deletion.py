# tests/test_plex_deletion.py
"""Tests: deletion routes to PlexClient.delete_item() for Plex servers."""
import os
import pytest

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_plex_deletion_calls_delete_item(tmp_path):
    """_delete_single_item calls PlexClient.delete_item for plex-type servers."""
    from backend.plex_client import PlexClient
    with patch.object(PlexClient, "delete_item", new_callable=AsyncMock) as mock_del:
        mock_del.return_value = True
        import backend.db.engine as eng
        eng.SQLITE_PATH = str(tmp_path / "del.db")
        from backend.db.schema import init_db
        await init_db()

        from backend.deletion import _delete_single_item
        item = {
            "id": 1,
            "emby_id": "101",
            "plex_rating_key": "101",
            "title": "Inception",
            "file_path": "/media/inception.mkv",
            "library_id": "lib1",
            "library_name": "Movies",
            "media_type": "movie",
            "radarr_id": None,
            "sonarr_id": None,
        }
        server = {"id": "p1", "type": "plex", "url": "http://plex.local:32400", "api_key": "tok"}
        result = await _delete_single_item(item=item, server=server, dry_run=False)
        mock_del.assert_called_once_with("101")
        assert result is True


@pytest.mark.asyncio
async def test_plex_deletion_dry_run_skips_api(tmp_path):
    """Dry-run mode skips the actual Plex API call."""
    from backend.plex_client import PlexClient
    with patch.object(PlexClient, "delete_item", new_callable=AsyncMock) as mock_del:
        import backend.db.engine as eng
        eng.SQLITE_PATH = str(tmp_path / "dry.db")
        from backend.db.schema import init_db
        await init_db()

        from backend.deletion import _delete_single_item
        item = {"emby_id": "202", "plex_rating_key": "202"}
        server = {"id": "p2", "type": "plex", "url": "http://plex.local:32400", "api_key": "tok"}
        result = await _delete_single_item(item=item, server=server, dry_run=True)
        mock_del.assert_not_called()
        assert result is True
