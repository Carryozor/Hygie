"""Coverage for deletion_pipeline.py steps/branches not exercised by
test_deletion_pipeline_consolidated.py or test_deletion_pipeline_failure_propagation.py:
dry-run short-circuits, exception-isolation in soft-failing steps, the base
DeletionStep contract, and the pipeline's own step_warnings logging.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.deletion_pipeline import (
    ArrStep,
    DeletionContext,
    DeletionPipeline,
    DeletionStep,
    DiscordNotifyStep,
    MediaServerStep,
    QbitStep,
    SeerrStep,
    ServerResolveStep,
    SizeLookupStep,
    StatsStep,
    TorrentHashStep,
)


def _movie_item(**overrides) -> dict:
    base = {
        "id": 1,
        "title": "Inception",
        "media_type": "Movie",
        "emby_id": "emby-123",
        "file_path": "/movies/inception.mkv",
        "radarr_id": 42,
        "sonarr_id": None,
        "sonarr_series_id": None,
        "season_number": None,
        "_server_id": "0",
    }
    base.update(overrides)
    return base


# ─── DeletionStep base contract ────────────────────────────────────────────

async def test_deletion_step_base_class_is_abstract():
    """The base class documents 'raise on unrecoverable error' — its own
    default execute() must raise, forcing every real step to override it."""
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with pytest.raises(NotImplementedError):
        await DeletionStep().execute(ctx)


# ─── SizeLookupStep ─────────────────────────────────────────────────────────

async def test_size_lookup_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.arr_clients.radarr_get_any", new=AsyncMock()) as mock_get:
        await SizeLookupStep().execute(ctx)
    mock_get.assert_not_awaited()
    assert ctx.size_bytes == 0


async def test_size_lookup_step_records_movie_file_size():
    ctx = DeletionContext(item=_movie_item(radarr_id=42), dry_run=False)
    with patch("backend.arr_clients.radarr_get_any",
               new=AsyncMock(return_value={"movieFile": {"size": 12345}})):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 12345


async def test_size_lookup_step_records_series_size_on_disk():
    item = _movie_item(media_type="Episode", radarr_id=None, sonarr_series_id=274)
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any",
               new=AsyncMock(return_value={"statistics": {"sizeOnDisk": 999}})):
        await SizeLookupStep().execute(ctx)
    assert ctx.size_bytes == 999


async def test_size_lookup_step_records_warning_instead_of_raising_on_failure():
    """Stats are a nice-to-have — a lookup failure must not abort deletion,
    only degrade the recorded size to 0 with a warning kept for StatsStep."""
    ctx = DeletionContext(item=_movie_item(radarr_id=42), dry_run=False)
    with patch("backend.arr_clients.radarr_get_any",
               new=AsyncMock(side_effect=RuntimeError("radarr down"))):
        await SizeLookupStep().execute(ctx)  # must not raise
    assert ctx.size_bytes == 0
    assert any("SizeLookup" in w for w in ctx.step_warnings)


# ─── TorrentHashStep — exception isolation ─────────────────────────────────

async def test_torrent_hash_step_records_warning_on_single_item_failure():
    item = _movie_item(sonarr_series_id=None, sonarr_id=None)
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash",
               new=AsyncMock(side_effect=RuntimeError("history unavailable"))):
        await TorrentHashStep().execute(ctx)  # must not raise
    assert ctx.torrent_hash is None
    assert any("TorrentHash" in w for w in ctx.step_warnings)


async def test_torrent_hash_step_records_warning_on_consolidated_failure():
    item = _movie_item(media_type="Episode", radarr_id=None, sonarr_series_id=274, sonarr_id=None)
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hashes_consolidated",
               new=AsyncMock(side_effect=RuntimeError("sonarr down"))):
        await TorrentHashStep().execute(ctx)  # must not raise
    assert ctx.torrent_hashes == set()
    assert any("TorrentHash" in w for w in ctx.step_warnings)


# ─── DiscordNotifyStep ──────────────────────────────────────────────────────

async def test_discord_notify_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.discord_client.send_notification", new=AsyncMock()) as mock_send:
        await DiscordNotifyStep().execute(ctx)
    mock_send.assert_not_awaited()


async def test_discord_notify_step_logs_warning_when_notification_fails():
    """A Discord failure must not abort the pipeline — only be logged."""
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with (
        patch("backend.db.engine.get_db", side_effect=RuntimeError("db down")),
        patch("backend.db.logs.add_log", new=AsyncMock()) as mock_log,
    ):
        await DiscordNotifyStep().execute(ctx)  # must not raise
    mock_log.assert_awaited_once()
    assert mock_log.await_args.args[0] == "WARN"


async def test_discord_notify_step_survives_when_both_notify_and_logging_fail(caplog):
    """If even add_log() itself fails, the step must still not propagate —
    it falls back to a plain logger.warning() so the deletion continues."""
    import logging as _logging
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with (
        patch("backend.db.engine.get_db", side_effect=RuntimeError("db down")),
        patch("backend.db.logs.add_log", new=AsyncMock(side_effect=RuntimeError("db really down"))),
        caplog.at_level(_logging.WARNING, logger="backend.deletion_pipeline"),
    ):
        await DiscordNotifyStep().execute(ctx)  # must not raise
    assert any("both notification and add_log failed" in r.message for r in caplog.records)


# ─── ServerResolveStep ──────────────────────────────────────────────────────

async def test_server_resolve_step_sets_server_none_on_failure():
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.db.media_servers.get_media_servers",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        await ServerResolveStep().execute(ctx)  # must not raise
    assert ctx.server is None


# ─── MediaServerStep ─────────────────────────────────────────────────────────

async def test_media_server_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.media_server_factory.delete_server_item", new=AsyncMock()) as mock_delete:
        await MediaServerStep().execute(ctx)
    mock_delete.assert_not_awaited()


async def test_media_server_step_noop_without_emby_id():
    """A row with no media-server id linked has nothing to delete there —
    must return quietly, not raise."""
    item = _movie_item(emby_id="")
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.media_server_factory.delete_server_item", new=AsyncMock()) as mock_delete:
        await MediaServerStep().execute(ctx)
    mock_delete.assert_not_awaited()


# NOTE: the is_plex(server) == True success-logging branch
# (deletion_pipeline.py:235-237) is intentionally NOT covered here — it
# crashes with a real TypeError in production. See BUGS SUSPECTÉS in the
# final report: lm("plex.deleted", key=item_id) collides with lm()'s own
# first positional parameter, which is itself named `key`. Writing a test
# that mocks this away would certify the crash as passing behavior.


# ─── MediaServerStep — consolidated early exits ────────────────────────────

async def test_media_server_step_consolidated_returns_without_series_id():
    item = _movie_item(media_type="Episode", radarr_id=None, emby_id="sonarr-series:",
                        sonarr_series_id=None)
    ctx = DeletionContext(item=item, dry_run=False)
    with patch("backend.emby_client.delete_item", new=AsyncMock()) as mock_delete:
        await MediaServerStep().execute(ctx)  # must not raise
    mock_delete.assert_not_awaited()


async def test_media_server_step_consolidated_returns_without_series_path():
    item = _movie_item(media_type="Episode", radarr_id=None, emby_id="sonarr-series:274",
                        sonarr_series_id=274)
    ctx = DeletionContext(item=item, dry_run=False)
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value={"path": ""})),
        patch("backend.emby_client.delete_item", new=AsyncMock()) as mock_delete,
    ):
        await MediaServerStep().execute(ctx)  # must not raise
    mock_delete.assert_not_awaited()


# ─── ArrStep / SeerrStep / QbitStep / StatsStep — dry_run short-circuits ───

async def test_arr_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.deletion_helpers._delete_from_arr", new=AsyncMock()) as mock_delete:
        await ArrStep().execute(ctx)
    mock_delete.assert_not_awaited()


async def test_seerr_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.deletion_helpers._delete_from_seerr", new=AsyncMock()) as mock_delete:
        await SeerrStep().execute(ctx)
    mock_delete.assert_not_awaited()


async def test_qbit_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    ctx.torrent_hash = "some-hash"
    with patch("backend.deletion_helpers._handle_qbit", new=AsyncMock()) as mock_handle:
        await QbitStep().execute(ctx)
    mock_handle.assert_not_awaited()


async def test_stats_step_skipped_in_dry_run():
    ctx = DeletionContext(item=_movie_item(), dry_run=True)
    with patch("backend.db.engine.get_db") as mock_get_db:
        await StatsStep().execute(ctx)
    mock_get_db.assert_not_called()


async def test_stats_step_swallows_db_failure_without_raising():
    """Failing to record a stats row must never abort an already-successful
    deletion — the file is gone regardless of whether the stat was recorded."""
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.db.engine.get_db", side_effect=RuntimeError("db down")):
        await StatsStep().execute(ctx)  # must not raise


# ─── DeletionPipeline — step_warnings logging on an otherwise-successful run ──

async def test_pipeline_logs_but_does_not_fail_on_accumulated_soft_warnings():
    """A pipeline that completes every step but accumulated soft warnings
    along the way must still report success — warnings are visibility, not
    failure."""
    class _WarnThenOk(DeletionStep):
        async def execute(self, ctx):
            ctx.step_warnings.append("SizeLookup: unavailable")

    class _Ok(DeletionStep):
        async def execute(self, ctx):
            return None

    pipeline = DeletionPipeline([_WarnThenOk(), _Ok()])
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    result = await pipeline.execute(ctx)

    assert result is True
    assert ctx.step_warnings == ["SizeLookup: unavailable"]
