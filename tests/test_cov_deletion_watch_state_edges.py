"""Coverage for deletion.py watch-state guard edges not exercised by
test_watch_state_safety.py:

  * _consolidated_last_play() — resolving the most recent play across a
    consolidated season/series group's episodes.
  * The consolidated branch inside _refresh_watch_state_before_deletion().
  * Resilience when the pre-deletion DB write (update_activity_log_batch /
    update_consolidated_watch_state) itself fails.
  * Resilience when the rescue guard's delete_by_id() cleanup fails.

These all sit directly in front of file deletion — a false "not watched"
result here means a media someone is actively watching gets removed.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import backend.db.engine as _db_engine
import backend.db.schema as _db_schema
import backend.db.settings_store as _db_ss
import backend.db.utils as _db_utils

from backend.db.schema import init_db


@pytest.fixture
async def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "watch_state_edges.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    monkeypatch.setattr(_db_engine, "DIALECT", "sqlite")
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await init_db()
    yield db_path


def _consolidated_row(**overrides) -> dict:
    base = {
        "id": 1,
        "emby_id": "sonarr-series:304",
        "title": "Grimm",
        "sonarr_series_id": 304,
        "season_number": None,
        "file_path": "/media/grimm/s01e01.mkv",
        "last_played": None,
        "view_count": 0,
        "library_id": "lib1",
        "detected_at": None,
    }
    base.update(overrides)
    return base


# ─── _consolidated_last_play ───────────────────────────────────────────────

async def test_consolidated_last_play_returns_none_without_series_id():
    """A consolidated row with no sonarr_series_id has nothing to resolve."""
    from backend.deletion import _consolidated_last_play

    row = _consolidated_row(sonarr_series_id=None)
    result = await _consolidated_last_play(row, "0", {})
    assert result is None


async def test_consolidated_last_play_returns_none_when_series_not_found_on_media_server():
    """Sonarr knows the series but it can't be located on the media server —
    must not raise or fabricate a play date."""
    from backend.deletion import _consolidated_last_play

    row = _consolidated_row()
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.emby_client.find_item_by_path", new=AsyncMock(return_value=None)),
    ):
        result = await _consolidated_last_play(row, "0", {})
    assert result is None


async def test_consolidated_last_play_aggregates_max_play_across_episodes():
    """The series' last play is the MAX across all its episodes present in the
    activity log — not an arbitrary anchor episode."""
    from backend.deletion import _consolidated_last_play

    row = _consolidated_row(season_number=None)
    episodes = [{"Id": "ep-1"}, {"Id": "ep-2"}, {"Id": "ep-3"}]
    activity = {"ep-1": "2026-09-01T00:00:00+00:00", "ep-2": "2026-09-20T00:00:00+00:00"}
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.emby_client.find_item_by_path",
              new=AsyncMock(return_value={"Id": "series-emby-id"})) as mock_find,
        patch("backend.emby_client.get_items_in_library",
              new=AsyncMock(return_value=(episodes, 3))),
    ):
        result = await _consolidated_last_play(row, "0", activity)

    assert result == "2026-09-20T00:00:00+00:00"
    assert mock_find.await_args.kwargs.get("include_types") == "Series"


async def test_consolidated_last_play_uses_season_subfolder_for_season_rows():
    """A season-level consolidated row must resolve via the Season item, not
    the whole Series — otherwise plays from other seasons would count."""
    from backend.deletion import _consolidated_last_play

    row = _consolidated_row(season_number=2, file_path="/media/grimm/Season 02/e01.mkv")
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.emby_client.find_item_by_path",
              new=AsyncMock(return_value={"Id": "season-emby-id"})) as mock_find,
        patch("backend.emby_client.get_items_in_library",
              new=AsyncMock(return_value=([], 0))),
    ):
        await _consolidated_last_play(row, "0", {})

    assert mock_find.await_args.args[0] == "/media/grimm/Season 02"
    assert mock_find.await_args.kwargs.get("include_types") == "Season"


async def test_consolidated_last_play_returns_none_when_no_episode_has_a_play():
    """No episode of the group appears in the activity log → None, not a crash."""
    from backend.deletion import _consolidated_last_play

    row = _consolidated_row()
    with (
        patch("backend.arr_clients.sonarr_get_series_by_id_any",
              new=AsyncMock(return_value={"path": "/media/grimm"})),
        patch("backend.emby_client.find_item_by_path",
              new=AsyncMock(return_value={"Id": "series-emby-id"})),
        patch("backend.emby_client.get_items_in_library",
              new=AsyncMock(return_value=([{"Id": "ep-1"}], 1))),
    ):
        result = await _consolidated_last_play(row, "0", {})
    assert result is None


# ─── _refresh_watch_state_before_deletion — consolidated branch ───────────

async def test_refresh_watch_state_advances_consolidated_row_last_played(fresh_db):
    """A consolidated row whose group has a fresher play than its stored
    last_played must have that play written back before deletion proceeds."""
    from backend.deletion import _refresh_watch_state_before_deletion

    fresh_play = "2026-09-25T00:00:00+00:00"
    row = _consolidated_row(last_played=None, view_count=0)

    with (
        patch("backend.deletion.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))),
        patch("backend.deletion.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.deletion._consolidated_last_play", new=AsyncMock(return_value=fresh_play)),
        patch("backend.deletion.update_activity_log_batch", new=AsyncMock()) as mock_direct,
        patch("backend.deletion.update_consolidated_watch_state", new=AsyncMock()) as mock_consolidated,
    ):
        refreshed = await _refresh_watch_state_before_deletion([row], {"lib1": "0"})

    assert refreshed[0]["last_played"] == fresh_play
    mock_consolidated.assert_awaited_once()
    written = mock_consolidated.await_args.args[0]
    assert written == [(fresh_play, 1, "sonarr-series:304", fresh_play)]
    mock_direct.assert_awaited_once_with([])


async def test_refresh_watch_state_leaves_consolidated_row_unchanged_without_a_fresh_play(fresh_db):
    """No fresher play found for the group → last_played is left untouched,
    and nothing is written for that row."""
    from backend.deletion import _refresh_watch_state_before_deletion

    row = _consolidated_row(last_played="2026-01-01T00:00:00+00:00")

    with (
        patch("backend.deletion.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))),
        patch("backend.deletion.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.deletion._consolidated_last_play", new=AsyncMock(return_value=None)),
        patch("backend.deletion.update_activity_log_batch", new=AsyncMock()),
        patch("backend.deletion.update_consolidated_watch_state", new=AsyncMock()) as mock_consolidated,
    ):
        refreshed = await _refresh_watch_state_before_deletion([row], {"lib1": "0"})

    assert refreshed[0]["last_played"] == "2026-01-01T00:00:00+00:00"
    mock_consolidated.assert_awaited_once_with([])


# ─── _refresh_watch_state_before_deletion — write failure resilience ──────

async def test_refresh_watch_state_survives_db_write_failure(fresh_db, caplog):
    """If persisting the refreshed watch state fails (DB error), the refreshed
    rows must still be returned — a transient write failure must not block
    the rescue guard from seeing the up-to-date data in-memory."""
    import logging
    from backend.deletion import _refresh_watch_state_before_deletion

    row = {
        "id": 1, "emby_id": "ep-1", "title": "Movie", "library_id": "lib1",
        "last_played": None, "detected_at": None,
    }
    with (
        patch("backend.deletion.get_client", new=AsyncMock(return_value=("http://emby:8096", "k"))),
        patch("backend.deletion.get_play_activity",
              new=AsyncMock(return_value={"ep-1": "2026-09-20T00:00:00+00:00"})),
        patch("backend.deletion.update_activity_log_batch",
              new=AsyncMock(side_effect=RuntimeError("db locked"))),
        caplog.at_level(logging.WARNING, logger="backend.deletion"),
    ):
        refreshed = await _refresh_watch_state_before_deletion([row], {"lib1": "0"})

    assert refreshed[0]["last_played"] == "2026-09-20T00:00:00+00:00"
    assert any("write failed" in r.message for r in caplog.records)


# ─── _rescue_watched_since_queued — delete_by_id failure resilience ───────

async def test_rescue_still_excludes_item_when_queue_cleanup_fails(caplog):
    """delete_by_id() failing to clear the rescued row from the queue must
    not resurrect the item into the deletion batch — the rescue itself
    (excluding it from `to_delete`) must hold regardless."""
    import logging
    from backend.deletion import _rescue_watched_since_queued

    now = datetime.now(timezone.utc)
    row = {
        "id": 42, "title": "Grimm",
        "detected_at": (now - timedelta(days=5)).isoformat(),
        "last_played": (now - timedelta(days=1)).isoformat(),
    }
    with (
        patch("backend.deletion.add_log", new=AsyncMock()),
        patch("backend.deletion.delete_by_id",
              new=AsyncMock(side_effect=RuntimeError("db locked"))),
        caplog.at_level(logging.WARNING, logger="backend.deletion"),
    ):
        kept = await _rescue_watched_since_queued([row])

    assert kept == [], "rescue must exclude the item even if the cleanup delete fails"
    assert any("Rescue of" in r.message for r in caplog.records)
