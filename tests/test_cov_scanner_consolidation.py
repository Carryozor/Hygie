"""Coverage tests for backend/scanner/_consolidation.py.

tests/test_consolidation_completeness_guard.py already covers the
season-branch completeness guard (exact-match requirement). This file adds:
the "series" deletion_unit branch (entirely separate code path, same
completeness guard), _group_watch_state's date-parsing branches (string vs.
already-a-datetime vs. unparseable), the isinstance guard filtering
non-dict sonarr_cache entries, and the "already consolidated" skip via
queued_ids.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from backend.scanner._consolidation import _consolidate_and_insert, _group_watch_state


def _episode(sid: int, sn: int, ef_id: int, delete_at: str = "2026-08-10T00:00:00+00:00", **overrides) -> dict:
    ep = {
        "sonarr_series_id": sid, "season_number": sn, "sonarr_id": ef_id,
        "title": f"Ep {ef_id}", "delete_at": delete_at, "file_path": f"/s/e{ef_id}.mkv",
        "poster_url": "", "emby_id": f"emby-{ef_id}",
    }
    ep.update(overrides)
    return ep


def _sonarr_cache_for_series(sid: int, n: int, series_title: str = "Show") -> dict:
    return {
        f"/s/series_file_{i}.mkv": {"series_id": sid, "season_number": i % 3, "series_title": series_title, "poster_url": "http://poster"}
        for i in range(n)
    }


# ─── deletion_unit == "series" ──────────────────────────────────────────────────

async def test_series_consolidation_fires_on_exact_match():
    cache = _sonarr_cache_for_series(1, 3)
    eligible = [_episode(1, 1, 10), _episode(1, 2, 11), _episode(1, 3, 12)]

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "series", None, False)

    assert added == 1
    mock_insert.assert_awaited_once()
    consolidated = mock_insert.await_args.args[0]
    assert consolidated["emby_id"] == "sonarr-series:1"
    assert consolidated["title"] == "Show"
    assert consolidated["season_number"] is None
    assert consolidated["sonarr_id"] is None


async def test_series_consolidation_skips_incomplete_series():
    cache = _sonarr_cache_for_series(1, 5)  # 5 files known
    eligible = [_episode(1, 1, 10)]  # only 1 eligible

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "series", None, False)

    assert added == 0
    mock_insert.assert_not_awaited()


async def test_series_consolidation_does_not_fire_when_eligible_exceeds_known_total():
    cache = _sonarr_cache_for_series(1, 2)
    eligible = [_episode(1, 1, 10), _episode(1, 2, 11), _episode(1, 3, 12)]  # 3 > 2 known

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "series", None, False)

    assert added == 0
    mock_insert.assert_not_awaited()


async def test_series_consolidation_skips_group_already_in_queued_ids():
    cache = _sonarr_cache_for_series(1, 2)
    eligible = [_episode(1, 1, 10), _episode(1, 2, 11)]
    queued_ids = {"sonarr-series:1"}

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "series", queued_ids, False)

    assert added == 0
    mock_insert.assert_not_awaited()


async def test_series_consolidation_falls_back_to_anchor_title_when_no_cache_match():
    """If no sonarr_cache entry matches the series id (edge case: cache built
    from a different snapshot), the anchor episode's own title is used
    rather than crashing on a missing series_title."""
    eligible = [_episode(1, 1, 10), _episode(1, 2, 11)]
    cache = {"/other/path.mkv": {"series_id": 999, "season_number": 1}}  # no match for sid=1
    # series_totals stays empty for sid=1 since no cache entry has series_id=1
    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "series", None, False)
    # total==0 for sid=1 -> "total > 0" guard fails -> not consolidated (0 known files)
    assert added == 0
    mock_insert.assert_not_awaited()


# ─── unsupported / movie deletion_unit ──────────────────────────────────────────

async def test_consolidate_returns_zero_for_movie_deletion_unit():
    added = await _consolidate_and_insert({"id": "lib1"}, [], {}, "movie", None, False)
    assert added == 0


# ─── sonarr_cache isinstance guard ──────────────────────────────────────────────

async def test_consolidate_ignores_non_dict_sonarr_cache_entries():
    """sonarr_cache can hold non-dict values for unrelated cache keys (e.g. a
    plain path->id mapping mixed in) — these must be skipped when building
    season/series totals, not crash on .get()."""
    cache = {
        "/weird/entry": "not-a-dict",
        "/s/e1.mkv": {"series_id": 1, "season_number": 1, "series_title": "Show", "poster_url": ""},
    }
    eligible = [_episode(1, 1, 10)]
    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "season", None, False)
    assert added == 1
    mock_insert.assert_awaited_once()


# ─── _group_watch_state ─────────────────────────────────────────────────────────

def test_group_watch_state_parses_string_last_played():
    eps = [
        {"view_count": 1, "last_played": "2024-01-01T00:00:00+00:00"},
        {"view_count": 3, "last_played": "2024-06-01T00:00:00+00:00"},
    ]
    best_iso, view_count = _group_watch_state(eps)
    assert best_iso == "2024-06-01T00:00:00+00:00"
    assert view_count == 3


def test_group_watch_state_accepts_datetime_object_not_just_string():
    dt = datetime(2024, 3, 1, tzinfo=timezone.utc)
    eps = [{"view_count": 2, "last_played": dt}]
    best_iso, view_count = _group_watch_state(eps)
    assert best_iso == dt
    assert view_count == 2


def test_group_watch_state_skips_unparseable_last_played():
    eps = [
        {"view_count": 1, "last_played": "not-a-real-date"},
        {"view_count": 5, "last_played": None},
    ]
    best_iso, view_count = _group_watch_state(eps)
    assert best_iso is None
    assert view_count == 5


def test_group_watch_state_all_never_watched_returns_none_iso_zero_count():
    eps = [{"view_count": 0, "last_played": None}, {"view_count": 0, "last_played": ""}]
    best_iso, view_count = _group_watch_state(eps)
    assert best_iso is None
    assert view_count == 0
