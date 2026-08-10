"""Regression test: a mid-scan Emby pagination failure (circuit breaker open,
timeout) must be surfaced as a visible warning distinguishing "scan stopped
early" from "reached the end of the library" — both currently look identical
(get_items_in_library() returns ([], 0) on any failure, by design, so it
never raises). Found during the 2026-08-10 audit: previously nothing at the
scan level distinguished the two cases.
"""
import logging
from unittest.mock import AsyncMock, patch

from backend.scanner._emby_scanner import _collect_eligible_items


def _item(item_id: str, item_type: str = "Movie") -> dict:
    return {"Id": item_id, "Type": item_type, "Path": f"/movies/{item_id}.mkv", "DateCreated": ""}


async def _run(**overrides):
    kwargs = dict(
        lib={"id": "lib1", "name": "Movies"}, conditions=[], logic="AND", grace_days=7,
        user_ids=[], seerr_conditions=[], emby_library_id="emby-lib-1", server_id="0",
        user_data_cache={}, activity_log={}, radarr_cache=None, sonarr_cache=None,
        seerr_cache=None, queued_ids=set(), ignored_ids=set(),
        seerr_ext_url="", expert_rules_cache=[],
    )
    kwargs.update(overrides)
    return await _collect_eligible_items(**kwargs)


async def test_genuine_end_of_library_logs_no_truncation_warning(caplog):
    """total=2 with exactly 2 items on page 1: start(2) >= total(2) — the
    normal loop-exit path, must never warn."""
    with (
        patch("backend.scanner._emby_scanner.get_items_in_library",
              new=AsyncMock(return_value=([_item("a"), _item("b")], 2))),
        patch("backend.scanner._emby_scanner._evaluate_item", new=AsyncMock(return_value=None)),
    ):
        with caplog.at_level(logging.WARNING, logger="backend.scanner._emby_scanner"):
            await _run()

    assert not any("scan stopped early" in r.message for r in caplog.records)


async def test_empty_library_logs_no_truncation_warning(caplog):
    """A library with zero items: total=0 on the very first call — nothing
    was ever successfully fetched, so this must not look like a failure."""
    with patch("backend.scanner._emby_scanner.get_items_in_library", new=AsyncMock(return_value=([], 0))):
        with caplog.at_level(logging.WARNING, logger="backend.scanner._emby_scanner"):
            await _run()

    assert not any("scan stopped early" in r.message for r in caplog.records)


async def test_mid_scan_failure_logs_truncation_warning(caplog):
    """Page 1 reports total=1000 with 500 items; page 2 fails (circuit
    breaker / timeout) and get_items_in_library returns ([], 0) — the scan
    must warn that it stopped at 500/1000, not silently treat this as done."""
    page1 = ([_item(f"m{i}") for i in range(500)], 1000)
    page2 = ([], 0)  # get_items_in_library's own failure contract

    with (
        patch("backend.scanner._emby_scanner.get_items_in_library",
              new=AsyncMock(side_effect=[page1, page2])),
        patch("backend.scanner._emby_scanner._evaluate_item", new=AsyncMock(return_value=None)),
    ):
        with caplog.at_level(logging.WARNING, logger="backend.scanner._emby_scanner"):
            result = await _run()

    assert any("scan stopped early" in r.message and "500" in r.message and "1000" in r.message for r in caplog.records)
    assert result == []  # items evaluated so far are still returned, not discarded
