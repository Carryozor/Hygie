"""Regression test: season/series consolidation must only fire when the
eligible-episode count exactly matches Sonarr's known total for that group —
not merely "at least" that many. Found during the 2026-08-10 audit:
`len(eps) >= total` silently over-accepts if `eligible` ever somehow contains
more entries than Sonarr's own file count for the group (duplicate Emby
pagination, stale cache) — the docstring's own stated intent is "ALL episode
files are eligible", which is an equality check, not a floor.
"""
from unittest.mock import AsyncMock, patch

from backend.scanner._consolidation import _consolidate_and_insert


def _episode(sid: int, sn: int, ef_id: int, delete_at: str = "2026-08-10T00:00:00+00:00") -> dict:
    return {
        "sonarr_series_id": sid, "season_number": sn, "sonarr_id": ef_id,
        "title": f"Ep {ef_id}", "delete_at": delete_at, "file_path": f"/s/e{ef_id}.mkv",
        "poster_url": "", "emby_id": f"emby-{ef_id}",
    }


def _sonarr_cache_for_season(sid: int, sn: int, n: int) -> dict:
    return {
        f"/s/season_file_{i}.mkv": {"series_id": sid, "season_number": sn, "series_title": "Show", "poster_url": ""}
        for i in range(n)
    }


async def test_season_consolidation_fires_on_exact_match():
    cache = _sonarr_cache_for_season(1, 1, 2)
    eligible = [_episode(1, 1, 10), _episode(1, 1, 11)]

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "season", None, False)

    assert added == 1
    mock_insert.assert_awaited_once()


async def test_season_consolidation_does_not_fire_when_eligible_exceeds_known_total():
    """More eligible episodes than Sonarr's own file count for the season is
    a data-consistency anomaly — must not be silently accepted as 'complete'."""
    cache = _sonarr_cache_for_season(1, 1, 2)  # Sonarr says 2 files exist
    eligible = [_episode(1, 1, 10), _episode(1, 1, 11), _episode(1, 1, 12)]  # 3 "eligible"

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "season", None, False)

    assert added == 0
    mock_insert.assert_not_awaited()


async def test_season_consolidation_skips_incomplete_season():
    cache = _sonarr_cache_for_season(1, 1, 5)  # 5 files in the season
    eligible = [_episode(1, 1, 10)]  # only 1 eligible

    with patch("backend.scanner._consolidation._insert_queue_entry", new=AsyncMock()) as mock_insert:
        added = await _consolidate_and_insert({"id": "lib1"}, eligible, cache, "season", None, False)

    assert added == 0
    mock_insert.assert_not_awaited()
