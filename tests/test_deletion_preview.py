"""Deletion preview (4.5): show what a delete will touch BEFORE the user confirms.

Deleting a consolidated `sonarr-series:N` row removes a whole series (13 torrents
and their files on 2026-10-30). The preview is read-only and reuses the same
removal planner as the real deletion, so it cannot promise something else.
"""
from unittest.mock import AsyncMock

import pytest

from backend import deletion_preview as dp
from backend.qbit_siblings import Removal


def row(**kw):
    base = {"id": 7, "title": "Call of the Night", "media_type": "Series", "emby_id": "sonarr-series:30",
            "sonarr_series_id": 30, "season_number": None, "sonarr_id": None, "arr_server_url": None,
            "file_path": "/tv/Call of the Night"}
    return {**base, **kw}


@pytest.fixture
def stubs(monkeypatch):
    s = {"size": 5 * 1024 ** 3, "count": 12, "hashes": {"h1", "h2"}, "action": "delete_files", "twins": False,
         "plan": [Removal("h1", True, False, path="/dl/a"), Removal("h2", False, False, "autre torrent", "/dl/b")],
         "torrents": [{"hash": "h1", "name": "Show.S01"}, {"hash": "h2", "name": "Show.S02"}]}
    monkeypatch.setattr(dp, "sonarr_get_files_size", AsyncMock(side_effect=lambda *a, **k: s["size"]))
    monkeypatch.setattr(dp, "sonarr_series_file_count", AsyncMock(side_effect=lambda *a, **k: s["count"]))
    monkeypatch.setattr(dp, "_find_torrent_hashes_consolidated", AsyncMock(side_effect=lambda r: s["hashes"]))
    monkeypatch.setattr(dp, "get_setting", AsyncMock(side_effect=lambda k: s["action"]))
    monkeypatch.setattr(dp, "get_bool_setting", AsyncMock(side_effect=lambda k: s["twins"]))
    monkeypatch.setattr(dp, "build_plan", AsyncMock(side_effect=lambda *a, **k: s["plan"]))
    monkeypatch.setattr(dp, "qbit_list_torrents", AsyncMock(side_effect=lambda: s["torrents"]))
    return s


async def test_whole_series_reports_files_size_and_torrent_plan(stubs):
    out = await dp.build_preview(row())
    assert out["scope"] == "series"
    assert out["files"] == {"count": 12, "bytes": 5 * 1024 ** 3}
    assert out["qbit_action"] == "delete_files"
    by_hash = {t["hash"]: t for t in out["torrents"]}
    assert by_hash["h1"]["name"] == "Show.S01" and by_hash["h1"]["erase_files"] is True
    assert by_hash["h2"]["erase_files"] is False and by_hash["h2"]["reason"] == "autre torrent"
    assert out["torrents_total"] == 2


async def test_season_scope_asks_sonarr_for_that_season_only(stubs):
    out = await dp.build_preview(row(emby_id="sonarr-season:30:2", season_number=2))
    assert out["scope"] == "season"
    dp.sonarr_get_files_size.assert_awaited_once()
    assert dp.sonarr_get_files_size.await_args.kwargs.get("season_number") == 2
    assert out["files"]["count"] is None          # per-season count is not computed


async def test_linked_crossseed_twins_are_flagged(stubs):
    stubs["plan"] = [Removal("h1", True, False, path="/dl/a"), Removal("x9", True, True, path="/cs/a")]
    stubs["torrents"].append({"hash": "x9", "name": "Show.S01.crossseed"})
    out = await dp.build_preview(row())
    assert {t["hash"]: t["twin"] for t in out["torrents"]} == {"h1": False, "x9": True}


async def test_episode_row_has_no_torrent_resolution(stubs):
    out = await dp.build_preview(row(emby_id="abc", media_type="Episode", sonarr_id=99, season_number=1))
    assert out["scope"] == "episode"
    assert out["torrents"] is None
    dp._find_torrent_hashes_consolidated.assert_not_awaited()


async def test_movie_scope(stubs):
    out = await dp.build_preview(row(media_type="Movie", emby_id="m", sonarr_series_id=None, radarr_id=5))
    assert out["scope"] == "movie" and out["files"] is None


async def test_other_qbit_actions_do_not_plan_file_erasure(stubs):
    stubs["action"] = "pause"
    out = await dp.build_preview(row())
    assert out["qbit_action"] == "pause"
    assert out["torrents"] is None
    dp.build_plan.assert_not_awaited()


async def test_unknown_when_sonarr_cannot_answer_is_a_warning_not_zero(stubs):
    stubs["size"], stubs["count"], stubs["hashes"] = None, None, set()
    out = await dp.build_preview(row())
    assert out["files"] is None
    assert out["warning"] and "Sonarr" in out["warning"]
    assert out["torrents"] is None                      # unknown, not "nothing to remove"


async def test_known_files_but_no_torrent_found_is_flagged(stubs):
    stubs["hashes"] = set()
    out = await dp.build_preview(row())
    assert out["files"]["bytes"] == 5 * 1024 ** 3
    assert out["torrents"] is None and out["warning"] and "torrent" in out["warning"].lower()


async def test_size_unknown_but_count_known_keeps_the_count(stubs):
    stubs["size"] = None
    out = await dp.build_preview(row())
    assert out["files"] == {"count": 12, "bytes": None}


async def test_plan_failure_is_reported_not_raised(stubs):
    dp.build_plan.side_effect = RuntimeError("qbit down")
    out = await dp.build_preview(row())
    assert out["torrents"] is None and "qbit down" in out["warning"]


async def test_qbit_listing_unavailable_is_reported(stubs):
    stubs["plan"] = None
    out = await dp.build_preview(row())
    assert out["torrents"] is None and out["warning"]


async def test_preview_never_mutates_anything(stubs, monkeypatch):
    called = AsyncMock()
    import backend.qbit_client as qc
    monkeypatch.setattr(qc, "qbit_delete_torrent", called)
    await dp.build_preview(row())
    called.assert_not_awaited()


def test_endpoint_404_for_unknown_or_finished_item(test_client, monkeypatch):
    import backend.routers.media as m
    test_client.app.dependency_overrides[m.require_auth] = lambda: "testuser"
    monkeypatch.setattr(m, "get_by_id", AsyncMock(return_value=None))
    try:
        assert test_client.get("/api/media/999/deletion-preview").status_code == 404
        monkeypatch.setattr(m, "get_by_id", AsyncMock(return_value={"id": 1, "status": "deleted"}))
        assert test_client.get("/api/media/1/deletion-preview").status_code == 404
    finally:
        test_client.app.dependency_overrides.pop(m.require_auth, None)


def test_endpoint_returns_preview_for_pending_item(test_client, monkeypatch):
    import backend.routers.media as m
    test_client.app.dependency_overrides[m.require_auth] = lambda: "testuser"
    monkeypatch.setattr(m, "get_by_id", AsyncMock(return_value={"id": 1, "status": "pending", "title": "T"}))
    monkeypatch.setattr(m, "build_preview", AsyncMock(return_value={"scope": "series"}))
    try:
        r = test_client.get("/api/media/1/deletion-preview")
    finally:
        test_client.app.dependency_overrides.pop(m.require_auth, None)
    assert r.status_code == 200 and r.json() == {"scope": "series"}


def test_endpoint_requires_auth(test_client):
    assert test_client.get("/api/media/1/deletion-preview").status_code in (401, 403)
