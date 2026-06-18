"""Regression test for _delete_from_arr()'s routing logic.

Found during code review of the consolidated-deletion fix: a *normal*
per-episode queue row (deletion_unit="episode") carries sonarr_id (its own
episode file id) AND sonarr_series_id/season_number — the scanner sets all
three together for every regular episode (see backend/scanner/_emby_scanner.py).
The old dispatch only checked sonarr_series_id/season_number, so a single
episode's deletion was misrouted into sonarr_delete_season(), wiping every
episode file in that whole season instead of just the one due for deletion —
and, after the unmonitor fix, unmonitoring the whole season too.
"""
from unittest.mock import AsyncMock, patch

from backend.deletion import _delete_from_arr


def _per_episode_row(**overrides) -> dict:
    """A normal episode row as built by the scanner for deletion_unit='episode'."""
    base = {
        "title": "Lupin",
        "media_type": "Episode",
        "file_path": "/data/media/series/Lupin/Lupin - S01E10.mkv",
        "sonarr_id": 901,            # this episode's own episodeFile id
        "sonarr_series_id": 274,     # scanner sets this for every episode too
        "season_number": 1,          # and this
    }
    base.update(overrides)
    return base


def _consolidated_season_row(**overrides) -> dict:
    base = {
        "title": "Lupin — Saison 1",
        "media_type": "Episode",
        "file_path": "/data/media/series/Lupin/Lupin - S01E10.mkv",
        "sonarr_id": None,
        "sonarr_series_id": 274,
        "season_number": 1,
    }
    base.update(overrides)
    return base


def _consolidated_series_row(**overrides) -> dict:
    base = {
        "title": "Lupin",
        "media_type": "Episode",
        "file_path": "/data/media/series/Lupin/Lupin - S01E10.mkv",
        "sonarr_id": None,
        "sonarr_series_id": 274,
        "season_number": None,
    }
    base.update(overrides)
    return base


async def test_normal_episode_delete_does_not_wipe_the_whole_season():
    row = _per_episode_row()
    with (
        patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock(return_value=True)) as mock_episode,
        patch("backend.deletion.sonarr_delete_season", new=AsyncMock()) as mock_season,
        patch("backend.deletion.sonarr_delete_series", new=AsyncMock()) as mock_series,
    ):
        await _delete_from_arr(row)

    mock_episode.assert_awaited_once_with(901)
    mock_season.assert_not_awaited()
    mock_series.assert_not_awaited()


async def test_consolidated_season_delete_still_uses_season_endpoint():
    row = _consolidated_season_row()
    with (
        patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock()) as mock_episode,
        patch("backend.deletion.sonarr_delete_season", new=AsyncMock(return_value=True)) as mock_season,
        patch("backend.deletion.sonarr_delete_series", new=AsyncMock()) as mock_series,
    ):
        await _delete_from_arr(row)

    mock_season.assert_awaited_once_with(274, 1)
    mock_episode.assert_not_awaited()
    mock_series.assert_not_awaited()


async def test_consolidated_series_delete_still_uses_series_endpoint():
    row = _consolidated_series_row()
    with (
        patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock()) as mock_episode,
        patch("backend.deletion.sonarr_delete_season", new=AsyncMock()) as mock_season,
        patch("backend.deletion.sonarr_delete_series", new=AsyncMock(return_value=True)) as mock_series,
    ):
        await _delete_from_arr(row)

    mock_series.assert_awaited_once_with(274)
    mock_episode.assert_not_awaited()
    mock_season.assert_not_awaited()
