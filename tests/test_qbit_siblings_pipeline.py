"""QbitStep with delete_files: twins removed together, shared paths protected."""
from unittest.mock import AsyncMock

import pytest

from backend import qbit_siblings as qs
from backend.deletion_pipeline import DeletionContext, QbitStep

MKV = "Movie.2026.mkv"


def tor(h, path, size=6_000, name=MKV, tags=""):
    return {"hash": h, "name": name, "size": size, "content_path": path, "tags": tags, "state": "stalledUP"}


def ctx(hashes=("t",), media_type="Movie", action="delete_files"):
    c = DeletionContext(item={"title": "Movie", "id": 0, "media_type": media_type}, dry_run=False, qbit_action=action)
    c.torrent_hashes = set(hashes)
    return c


@pytest.fixture
def qbit(monkeypatch):
    """Fake qBittorrent: listing, file lists and a delete that records calls."""
    state = {"torrents": [], "files": {}, "linked": True, "calls": []}

    async def delete(h, delete_files=True):
        state["calls"].append((h, delete_files))
        return True

    async def files(h):
        return state["files"].get(h)

    async def listing():
        return state["torrents"]

    async def flag(key, default=False):
        return state["linked"] if key == "qbit_delete_linked_crossseeds" else default

    monkeypatch.setattr(qs, "qbit_list_torrents", listing)
    monkeypatch.setattr(qs, "qbit_torrent_files", files)
    monkeypatch.setattr("backend.deletion_helpers.qbit_delete_torrent", delete)
    monkeypatch.setattr("backend.db.settings_store.get_bool_setting", flag)
    monkeypatch.setattr("backend.db.logs.add_log", AsyncMock())
    monkeypatch.setattr("backend.deletion_helpers.add_log", AsyncMock())
    return state


F = [{"name": MKV, "size": 6_000}]


async def test_linked_twin_is_removed_before_the_target(qbit):
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    qbit["files"] = {"t": F, "x": F}
    await QbitStep().execute(ctx())
    assert qbit["calls"] == [("x", True), ("t", True)]


async def test_feature_off_removes_only_the_target(qbit):
    qbit["linked"] = False
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    qbit["files"] = {"t": F, "x": F}
    await QbitStep().execute(ctx())
    assert qbit["calls"] == [("t", True)]


async def test_similar_name_with_different_files_is_untouched(qbit):
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("n", "/dl/Movie.REPACK.mkv", name="Movie.REPACK.mkv")]
    qbit["files"] = {"t": F, "n": [{"name": "Movie.REPACK.mkv", "size": 6_000}]}
    await QbitStep().execute(ctx())
    assert qbit["calls"] == [("t", True)]


async def test_target_sharing_its_path_with_a_survivor_keeps_the_files(qbit):
    c = ctx()
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("s", "/dl/Movie.mkv", name="other-name")]
    await QbitStep().execute(c)
    assert qbit["calls"] == [("t", False)]
    assert any("autre torrent" in w for w in c.step_warnings)


async def test_listing_unavailable_fails_the_step_without_deleting(qbit, monkeypatch):
    # v4.6.0 (D2a): no blind delete_files; the step raises so the row is retried.
    monkeypatch.setattr(qs, "qbit_list_torrents", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError):
        await QbitStep().execute(ctx())
    assert qbit["calls"] == []


async def test_target_already_gone_from_qbit_is_not_an_error(qbit):
    c = ctx()
    qbit["torrents"] = [tor("other", "/dl/o.mkv", name="o")]
    await QbitStep().execute(c)
    assert qbit["calls"] == [] and c.step_warnings == []


async def test_shared_season_pack_blocker_still_applies_to_episodes(qbit, monkeypatch):
    monkeypatch.setattr("backend.deletion_helpers._shared_pack_blocker",
                        AsyncMock(return_value="pack partagé"))
    c = ctx(media_type="Episode")
    qbit["torrents"] = [tor("t", "/dl/Pack")]
    await QbitStep().execute(c)
    assert qbit["calls"] == []
    assert c.kept_note


async def test_other_actions_do_not_plan(qbit):
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    qbit["files"] = {"t": F, "x": F}
    await QbitStep().execute(ctx(action="delete_torrent"))
    assert qbit["calls"] == [("t", False)]


async def test_one_failed_removal_does_not_stop_the_others(qbit, monkeypatch):
    async def flaky(h, delete_files=True):
        qbit["calls"].append((h, delete_files))
        return h != "x"
    monkeypatch.setattr("backend.deletion_helpers.qbit_delete_torrent", flaky)
    c = ctx()
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    qbit["files"] = {"t": F, "x": F}
    await QbitStep().execute(c)
    assert [h for h, _ in qbit["calls"]] == ["x", "t"]
    assert any("x" in w for w in c.step_warnings)


async def test_blocked_hash_is_not_removed_as_a_twin_of_a_deletable_one(qbit, monkeypatch):
    async def blocker(item, h):
        return "pack partagé" if h == "h1" else None
    monkeypatch.setattr("backend.deletion_helpers._shared_pack_blocker", blocker)
    c = ctx(hashes=("h1", "h2"), media_type="Episode")
    qbit["torrents"] = [tor("h1", "/dl/Pack"), tor("h2", "/dl/Pack")]
    qbit["files"] = {"h1": F, "h2": F}
    await QbitStep().execute(c)
    assert ("h1", True) not in qbit["calls"] and all(h != "h1" for h, _ in qbit["calls"])
    assert c.kept_note


async def test_torrent_added_after_planning_keeps_the_files(qbit, monkeypatch):
    original = qbit["torrents"]
    seq = {"n": 0}

    async def listing():
        seq["n"] += 1
        base = [tor("t", "/dl/Movie.mkv")]
        # the plan sees nobody else; the re-check right before erasing sees a newcomer
        return base if seq["n"] == 1 else base + [tor("late", "/dl/Movie.mkv", name="late")]
    monkeypatch.setattr(qs, "qbit_list_torrents", listing)
    c = ctx()
    await QbitStep().execute(c)
    assert qbit["calls"] == [("t", False)]
    assert any("devenu utilisé" in w for w in c.step_warnings)
    assert original == []


async def test_plan_failure_keeps_every_torrent_untouched(qbit, monkeypatch):
    monkeypatch.setattr(qs, "qbit_list_torrents", AsyncMock(return_value=[{"hash": "t"}]))  # no content_path/size keys
    monkeypatch.setattr(qs, "plan_removal", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    c = ctx()
    await QbitStep().execute(c)
    assert qbit["calls"] == []
    assert c.kept_note and any("plan de suppression impossible" in w for w in c.step_warnings)


async def test_failed_removal_is_counted_as_kept(qbit, monkeypatch):
    async def always_fail(h, delete_files=True):
        return False
    monkeypatch.setattr("backend.deletion_helpers.qbit_delete_torrent", always_fail)
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv")]
    c = ctx()
    await QbitStep().execute(c)
    assert c.kept_note
