"""Linked cross-seed cleanup (4.4.5).

Deleting a torrent WITH its files leaves every other torrent that points at the
same files in `missingFiles` (2026-10: Spider-Man 3 and Mandalorian & Grogu).
The cure must never destroy a file another torrent still needs, whatever the
names look like: ownership is decided on paths, twins on the exact file list.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend import qbit_siblings as qs

MKV = "Movie.2026.2160p-GRP.mkv"
SIG = frozenset({(MKV, 6_000)})


def tor(h, path, size=6_000, name=MKV, tags="", state="stalledUP"):
    return {"hash": h, "name": name, "size": size, "content_path": path, "tags": tags, "state": state}


def hashes(plan):
    return [r.hash for r in plan]


# ── path overlap ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,expected", [
    ("/d/a.mkv", "/d/a.mkv", True),
    ("/d/Pack", "/d/Pack/e1.mkv", True),
    ("/d/Pack/e1.mkv", "/d/Pack", True),
    ("/d/Pack", "/d/Pack2", False),          # sibling prefix is not containment
    ("/d/Movie.mkv", "/d/Movie.mkv.part", False),
    ("/d/a", "/e/a", False),
    ("/d/Pack/", "/d/Pack/e1.mkv", True),    # trailing slash tolerated
    ("", "/d/a", False),
    ("/dl/Movie", "/dl/movie", True),               # case-insensitive filesystems
    ("/dl//Movie", "/dl/Movie/a.mkv", True),
    ("/dl/./Movie", "/dl/Movie", True),
    ("/dl/x/../Movie", "/dl/Movie/a.mkv", True),
    ("D:\\dl\\Movie", "D:\\dl\\Movie\\a.mkv", True),
    ("/dl/Movie", "/dl/Movies", False),
])
def test_paths_overlap(a, b, expected):
    assert qs.paths_overlap(a, b) is expected


# ── planning ────────────────────────────────────────────────────────────────

def test_twin_with_identical_file_list_is_removed_with_the_target():
    ts = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv"), tor("keep", "/dl/other.mkv", name="other.mkv")]
    plan = qs.plan_removal({"t"}, ts, {"t": SIG, "x": SIG}, include_twins=True)
    assert set(hashes(plan)) == {"t", "x"}
    assert hashes(plan)[0] == "x"            # twins first, target last
    assert all(r.delete_files for r in plan)


def test_similar_name_but_different_files_is_never_touched():
    ts = [tor("t", "/dl/Movie.mkv"), tor("n", "/dl/Movie.2026.2160p-GRP.REPACK.mkv", name="Movie.2026.2160p-GRP.REPACK.mkv")]
    other = frozenset({("Movie.2026.2160p-GRP.REPACK.mkv", 6_000)})
    plan = qs.plan_removal({"t"}, ts, {"t": SIG, "n": other}, include_twins=True)
    assert hashes(plan) == ["t"]


def test_same_name_different_size_is_not_a_twin():
    ts = [tor("t", "/dl/Movie.mkv"), tor("n", "/dl2/Movie.mkv", size=6_001)]
    plan = qs.plan_removal({"t"}, ts, {"t": SIG, "n": frozenset({(MKV, 6_001)})}, include_twins=True)
    assert hashes(plan) == ["t"]


def test_partial_overlap_pack_is_not_a_twin():
    pack = frozenset({("S01/e1.mkv", 1), ("S01/e2.mkv", 1)})
    one = frozenset({("S01/e1.mkv", 1)})
    ts = [tor("t", "/dl/S01/e1.mkv"), tor("p", "/dl/S01")]
    plan = qs.plan_removal({"t"}, ts, {"t": one, "p": pack}, include_twins=True)
    assert [(r.hash, r.delete_files) for r in plan] == [("t", False)]   # pack still needs the file


def test_target_files_are_kept_when_a_survivor_uses_the_same_path():
    ts = [tor("t", "/dl/Movie.mkv"), tor("s", "/dl/Movie.mkv", name="renamed")]
    plan = qs.plan_removal({"t"}, ts, {"t": SIG}, include_twins=False)
    assert [(r.hash, r.delete_files) for r in plan] == [("t", False)]
    assert "autre torrent" in plan[0].reason


def test_survivor_inside_target_folder_protects_the_files():
    ts = [tor("t", "/dl/Pack"), tor("s", "/dl/Pack/e1.mkv")]
    plan = qs.plan_removal({"t"}, ts, {}, include_twins=False)
    assert plan[0].delete_files is False


def test_unknown_content_path_never_deletes_files():
    ts = [tor("t", "")]
    plan = qs.plan_removal({"t"}, ts, {}, include_twins=False)
    assert [(r.hash, r.delete_files) for r in plan] == [("t", False)]


def test_two_targets_sharing_a_path_do_not_protect_each_other():
    # The Mandalorian orphans: same path, both being removed together.
    ts = [tor("a", "/dl/Movie.mkv"), tor("b", "/dl/Movie.mkv")]
    plan = qs.plan_removal({"a", "b"}, ts, {}, include_twins=False)
    assert all(r.delete_files for r in plan)


def test_twin_sharing_its_path_with_a_survivor_keeps_its_files():
    ts = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv"), tor("y", "/cs/Movie.mkv", name="y")]
    plan = qs.plan_removal({"t"}, ts, {"t": SIG, "x": SIG}, include_twins=True)
    byh = {r.hash: r for r in plan}
    assert byh["x"].delete_files is False and byh["t"].delete_files is True


def test_target_absent_from_listing_is_skipped():
    plan = qs.plan_removal({"gone"}, [tor("other", "/dl/o.mkv")], {}, include_twins=True)
    assert plan == []


def test_twins_disabled_leaves_copies_alone():
    ts = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    plan = qs.plan_removal({"t"}, ts, {"t": SIG, "x": SIG}, include_twins=False)
    assert hashes(plan) == ["t"]


def test_hash_comparison_is_case_insensitive():
    ts = [tor("ABC", "/dl/Movie.mkv")]
    assert hashes(qs.plan_removal({"abc"}, ts, {}, include_twins=False)) == ["ABC"]


def test_protected_hash_is_never_a_twin_and_keeps_its_files():
    # A torrent kept on purpose (shared season pack) with the same file list as
    # a deletable one must stay a survivor, not become "its twin".
    ts = [tor("h1", "/dl/Movie.mkv"), tor("h2", "/dl/Movie.mkv")]
    plan = qs.plan_removal({"h2"}, ts, {"h1": SIG, "h2": SIG}, include_twins=True, protected=frozenset({"h1"}))
    assert [(r.hash, r.delete_files) for r in plan] == [("h2", False)]


@pytest.mark.parametrize("path", ["/", "/dl", "dl", "//", "/."])
def test_root_or_top_level_path_never_erases_files(path):
    plan = qs.plan_removal({"t"}, [tor("t", path)], {}, include_twins=False)
    assert plan[0].delete_files is False


async def test_still_exclusive_sees_a_torrent_added_after_planning():
    before = [tor("t", "/dl/Movie.mkv")]
    after = before + [tor("new", "/dl/Movie.mkv", name="late")]
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=after)):
        assert await qs.still_exclusive("/dl/Movie.mkv", {"t"}) is False
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=before)):
        assert await qs.still_exclusive("/dl/Movie.mkv", {"t"}) is True
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=None)):
        assert await qs.still_exclusive("/dl/Movie.mkv", {"t"}) is False


# ── build_plan (qBittorrent I/O) ────────────────────────────────────────────

def _files(*pairs):
    return [{"name": n, "size": s} for n, s in pairs]


async def test_build_plan_fetches_files_only_for_size_matching_candidates():
    ts = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv"),
          tor("big", "/dl/Big.mkv", size=99, name="Big.mkv")]
    files = AsyncMock(side_effect=lambda h: _files((MKV, 6_000)) if h in ("t", "x") else None)
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=ts)), \
         patch.object(qs, "qbit_torrent_files", files):
        plan = await qs.build_plan({"t"}, include_twins=True)
    assert set(hashes(plan)) == {"t", "x"}
    asked = {c.args[0] for c in files.await_args_list}
    assert asked == {"t", "x"}               # "big" never queried


async def test_build_plan_returns_none_when_listing_unavailable():
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=None)):
        assert await qs.build_plan({"t"}, include_twins=True) is None


async def test_unreadable_candidate_files_means_no_twin():
    ts = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv")]
    files = AsyncMock(side_effect=lambda h: _files((MKV, 6_000)) if h == "t" else None)
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=ts)), \
         patch.object(qs, "qbit_torrent_files", files):
        plan = await qs.build_plan({"t"}, include_twins=True)
    assert hashes(plan) == ["t"]


async def test_candidate_count_is_capped():
    ts = [tor("t", "/dl/Movie.mkv")] + [tor(f"c{i}", f"/cs/{i}/Movie.mkv") for i in range(200)]
    files = AsyncMock(return_value=_files((MKV, 6_000)))
    with patch.object(qs, "qbit_list_torrents", AsyncMock(return_value=ts)), \
         patch.object(qs, "qbit_torrent_files", files):
        plan = await qs.build_plan({"t"}, include_twins=True)
    assert len(plan) <= qs.MAX_TWINS + 1


# ── qbit_client I/O wrappers ────────────────────────────────────────────────

async def test_qbit_list_torrents_returns_list_or_none():
    from backend import qbit_client
    ok = httpx.Response(200, json=[{"hash": "a"}])
    with patch.object(qbit_client, "_request", AsyncMock(return_value=ok)):
        assert await qbit_client.qbit_list_torrents() == [{"hash": "a"}]
    for bad in (None, httpx.Response(500), httpx.Response(200, text="not json")):
        with patch.object(qbit_client, "_request", AsyncMock(return_value=bad)):
            assert await qbit_client.qbit_list_torrents() is None


async def test_qbit_list_torrents_drops_malformed_entries():
    from backend import qbit_client
    resp = httpx.Response(200, json=[{"hash": "a"}, {"nohash": 1}, "junk", {"hash": 5}])
    with patch.object(qbit_client, "_request", AsyncMock(return_value=resp)):
        assert await qbit_client.qbit_list_torrents() == [{"hash": "a"}]


async def test_qbit_torrent_files_returns_list_or_none():
    from backend import qbit_client
    ok = httpx.Response(200, json=[{"name": "a.mkv", "size": 1}])
    with patch.object(qbit_client, "_request", AsyncMock(return_value=ok)) as req:
        assert await qbit_client.qbit_torrent_files("h") == [{"name": "a.mkv", "size": 1}]
        assert req.await_args.kwargs["params"] == {"hash": "h"}
    with patch.object(qbit_client, "_request", AsyncMock(return_value=httpx.Response(404))):
        assert await qbit_client.qbit_torrent_files("h") is None


def test_delete_verification_waits_long_enough_for_a_slow_qbittorrent():
    # Measured on prod: qBittorrent removes a torrent 1-4 s after answering 200.
    from backend import qbit_client
    assert sum(qbit_client._VERIFY_DELAYS) >= 15
