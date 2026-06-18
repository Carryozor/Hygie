"""Tests for deletion_pipeline.py steps handling consolidated season/series
entries — regression coverage for the "Lupin" bug:

  * Hygie wiped a whole series' library files via Sonarr's bulk delete, then
    resolved a single (often wrong) qBittorrent hash for the entire group and
    reported success even when the real torrent was untouched.
  * Sonarr stayed monitored after the wipe, risking a re-download.
  * Emby was never contacted at all for these entries ("synthetic consolidated
    entries have no media server file" — false for a real series).
"""
from unittest.mock import AsyncMock, patch

from backend.deletion_pipeline import DeletionContext, MediaServerStep, QbitStep, TorrentHashStep


def _consolidated_item(**overrides) -> dict:
    base = {
        "id": 10306,
        "title": "Lupin",
        "media_type": "Episode",
        "emby_id": "sonarr-series:274",
        "sonarr_id": None,
        "sonarr_series_id": 274,
        "season_number": None,
        "file_path": "/data/media/series/Lupin (2021) [tvdb-375921]/Lupin - S01E10 - Chapter 10.mkv",
        "_server_id": "0",
    }
    base.update(overrides)
    return base


# ─── TorrentHashStep ────────────────────────────────────────────────────────

async def test_torrent_hash_step_uses_group_resolver_for_consolidated_entries():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=False)
    with (
        patch("backend.deletion._find_torrent_hashes_consolidated", new=AsyncMock(return_value={"hash-a", "hash-b"})) as mock_group,
        patch("backend.deletion._find_torrent_hash", new=AsyncMock(return_value="should-not-be-used")) as mock_single,
    ):
        await TorrentHashStep().execute(ctx)

    mock_group.assert_awaited_once()
    mock_single.assert_not_awaited()
    assert ctx.torrent_hashes == {"hash-a", "hash-b"}
    assert ctx.torrent_hash is None


async def test_torrent_hash_step_uses_single_resolver_for_normal_entries():
    item = _consolidated_item(sonarr_series_id=None, sonarr_id=55)
    ctx = DeletionContext(item=item, dry_run=False)
    with (
        patch("backend.deletion._find_torrent_hash", new=AsyncMock(return_value="single-hash")) as mock_single,
        patch("backend.deletion._find_torrent_hashes_consolidated", new=AsyncMock()) as mock_group,
    ):
        await TorrentHashStep().execute(ctx)

    mock_single.assert_awaited_once()
    mock_group.assert_not_awaited()
    assert ctx.torrent_hash == "single-hash"
    assert ctx.torrent_hashes == set()


async def test_torrent_hash_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=True)
    with patch("backend.deletion._find_torrent_hashes_consolidated", new=AsyncMock()) as mock_group:
        await TorrentHashStep().execute(ctx)
    mock_group.assert_not_awaited()


# ─── QbitStep ───────────────────────────────────────────────────────────────

async def test_qbit_step_handles_every_hash_in_a_consolidated_group():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=False)
    ctx.torrent_hashes = {"hash-a", "hash-b"}
    with patch("backend.deletion._handle_qbit", new=AsyncMock()) as mock_handle:
        await QbitStep().execute(ctx)

    assert mock_handle.await_count == 2
    called_hashes = {c.args[0] for c in mock_handle.await_args_list}
    assert called_hashes == {"hash-a", "hash-b"}


async def test_qbit_step_falls_back_to_single_hash_when_no_group_hashes():
    ctx = DeletionContext(item=_consolidated_item(sonarr_series_id=None, sonarr_id=55), dry_run=False)
    ctx.torrent_hash = "single-hash"
    with patch("backend.deletion._handle_qbit", new=AsyncMock()) as mock_handle:
        await QbitStep().execute(ctx)

    mock_handle.assert_awaited_once()
    assert mock_handle.await_args.args[0] == "single-hash"


async def test_qbit_step_logs_not_found_with_no_hashes_at_all():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=False)
    with (
        patch("backend.deletion._handle_qbit", new=AsyncMock()) as mock_handle,
        patch("backend.db.logs.add_log", new=AsyncMock()) as mock_log,
    ):
        await QbitStep().execute(ctx)

    mock_handle.assert_not_awaited()
    mock_log.assert_awaited_once()


# ─── MediaServerStep — consolidated entries ─────────────────────────────────

async def test_media_server_step_deletes_resolved_emby_item_for_consolidated_series():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=False)
    series = {"id": 274, "path": "/data/media/series/Lupin (2021) [tvdb-375921]"}
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value=series)),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value={"Id": "emby-99"})) as mock_find,
        patch("backend.emby_client.delete_item", new=AsyncMock(return_value=True)) as mock_delete,
    ):
        await MediaServerStep().execute(ctx)

    mock_find.assert_awaited_once()
    assert mock_find.await_args.args[0] == series["path"]
    assert mock_find.await_args.kwargs.get("include_types") == "Series"
    mock_delete.assert_awaited_once_with("emby-99", server_id=ctx.server_id)


async def test_media_server_step_skips_silently_when_no_emby_item_found():
    ctx = DeletionContext(item=_consolidated_item(), dry_run=False)
    series = {"id": 274, "path": "/data/media/series/Lupin (2021) [tvdb-375921]"}
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value=series)),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value=None)),
        patch("backend.emby_client.delete_item", new=AsyncMock()) as mock_delete,
    ):
        await MediaServerStep().execute(ctx)

    mock_delete.assert_not_awaited()


async def test_media_server_step_uses_season_directory_for_season_level_entries():
    item = _consolidated_item(
        emby_id="sonarr-season:274:1",
        season_number=1,
        file_path="/data/media/series/Lupin (2021) [tvdb-375921]/Season 01/Lupin - S01E10.mkv",
    )
    ctx = DeletionContext(item=item, dry_run=False)
    series = {"id": 274, "path": "/data/media/series/Lupin (2021) [tvdb-375921]"}
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value=series)),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value={"Id": "emby-season-1"})) as mock_find,
        patch("backend.emby_client.delete_item", new=AsyncMock(return_value=True)) as mock_delete,
    ):
        await MediaServerStep().execute(ctx)

    assert mock_find.await_args.args[0] == "/data/media/series/Lupin (2021) [tvdb-375921]/Season 01"
    assert mock_find.await_args.kwargs.get("include_types") == "Season"
    mock_delete.assert_awaited_once_with("emby-season-1", server_id=ctx.server_id)


async def test_media_server_step_unchanged_for_normal_per_episode_entries():
    """Normal (non-consolidated) items must keep using the existing direct path."""
    item = _consolidated_item(emby_id="emby-real-id-42", sonarr_series_id=None, sonarr_id=55)
    ctx = DeletionContext(item=item, dry_run=False)
    with (
        patch("backend.media_server_factory.delete_server_item", new=AsyncMock(return_value=True)) as mock_delete,
        patch("backend.emby_client.find_item_by_path", new=AsyncMock()) as mock_find,
    ):
        await MediaServerStep().execute(ctx)

    mock_delete.assert_awaited_once()
    mock_find.assert_not_awaited()
