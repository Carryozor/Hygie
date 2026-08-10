"""Regression tests: a failed Emby/Radarr/Sonarr deletion must NOT be reported
as a successful deletion.

Root cause found during the 2026-08-10 audit: MediaServerStep and ArrStep
discarded the bool return value of delete_server_item()/_delete_from_arr()
and never raised on failure. DeletionStep's own docstring says "Raise on
unrecoverable error" — DeletionPipeline.execute() only returns False when a
step raises — but these two steps silently swallowed real failures (Emby
down, Radarr/Sonarr timeout) and let the pipeline report success. The
consolidated season/series path already computed the right `ok` value and
logged accordingly, but didn't raise either — same bug, smaller blast radius
since deletion_unit=season|series is not the default.

Only the two most common paths are exercised here (Movie via media_server_
factory.delete_server_item, single Episode via _delete_from_arr) plus the
consolidated Emby lookup — the paths a real "arr/emby down" failure would
actually hit.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.deletion_pipeline import DeletionContext, MediaServerStep, ArrStep
from backend.deletion import _delete_from_arr


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


def _episode_item(**overrides) -> dict:
    base = {
        "id": 2,
        "title": "Lupin",
        "media_type": "Episode",
        "emby_id": "emby-456",
        "file_path": "/series/lupin/s01e10.mkv",
        "radarr_id": None,
        "sonarr_id": 901,
        "sonarr_series_id": 274,
        "season_number": 1,
        "_server_id": "0",
    }
    base.update(overrides)
    return base


# ─── MediaServerStep ────────────────────────────────────────────────────────

async def test_media_server_step_raises_when_emby_delete_fails():
    """delete_server_item() returning False (Emby down/timeout) must abort
    the pipeline, not be treated as a successful deletion."""
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.media_server_factory.delete_server_item", new=AsyncMock(return_value=False)):
        with pytest.raises(Exception):
            await MediaServerStep().execute(ctx)


async def test_media_server_step_succeeds_when_emby_delete_succeeds():
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.media_server_factory.delete_server_item", new=AsyncMock(return_value=True)):
        await MediaServerStep().execute(ctx)  # must not raise


async def test_media_server_step_consolidated_raises_when_emby_delete_fails():
    item = _episode_item(emby_id="sonarr-series:274", sonarr_id=None, season_number=None)
    ctx = DeletionContext(item=item, dry_run=False)
    series = {"id": 274, "path": "/series/lupin"}
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any", new=AsyncMock(return_value=series)),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value={"Id": "emby-99"})),
        patch("backend.emby_client.delete_item", new=AsyncMock(return_value=False)),
    ):
        with pytest.raises(Exception):
            await MediaServerStep().execute(ctx)


# ─── ArrStep ────────────────────────────────────────────────────────────────

async def test_arr_step_raises_when_radarr_delete_fails():
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.deletion.radarr_delete_by_id", new=AsyncMock(return_value=False)):
        with pytest.raises(Exception):
            await ArrStep().execute(ctx)


async def test_arr_step_succeeds_when_radarr_delete_succeeds():
    ctx = DeletionContext(item=_movie_item(), dry_run=False)
    with patch("backend.deletion.radarr_delete_by_id", new=AsyncMock(return_value=True)):
        await ArrStep().execute(ctx)  # must not raise


async def test_arr_step_raises_when_sonarr_episode_delete_fails():
    ctx = DeletionContext(item=_episode_item(), dry_run=False)
    with patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock(return_value=False)):
        with pytest.raises(Exception):
            await ArrStep().execute(ctx)


# ─── _delete_from_arr() return value ───────────────────────────────────────

async def test_delete_from_arr_returns_false_on_radarr_failure():
    row = _movie_item()
    with patch("backend.deletion.radarr_delete_by_id", new=AsyncMock(return_value=False)):
        assert await _delete_from_arr(row) is False


async def test_delete_from_arr_returns_true_on_radarr_success():
    row = _movie_item()
    with patch("backend.deletion.radarr_delete_by_id", new=AsyncMock(return_value=True)):
        assert await _delete_from_arr(row) is True


async def test_delete_from_arr_returns_true_when_movie_has_no_arr_link():
    """Nothing to remove (no radarr_id, no path match) is not a failure —
    the item may have been added to Hygie's queue without ever having a
    Radarr entry (e.g. manually imported media)."""
    row = _movie_item(radarr_id=None)
    with patch("backend.deletion.radarr_find_by_path", new=AsyncMock(return_value=None)):
        assert await _delete_from_arr(row) is True


async def test_delete_from_arr_returns_false_on_sonarr_episode_failure():
    row = _episode_item()
    with patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock(return_value=False)):
        assert await _delete_from_arr(row) is False


async def test_delete_from_arr_returns_true_on_sonarr_episode_success():
    row = _episode_item()
    with patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock(return_value=True)):
        assert await _delete_from_arr(row) is True
