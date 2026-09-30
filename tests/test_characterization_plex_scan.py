"""Characterization tests for scanner/_plex_scanner._scan_plex_library.

Written against the pre-refactor implementation. They pin: the order of
side effects, the per-item gate order (no plex_id -> queued/ignored by id ->
TMDB-ignored -> TMDB-mirror vs expert rules), grace selection, the exact
entry dict handed to insert_queue_entry, Seerr enrichment, defaults and logs.
This function decides which Plex items enter the deletion queue.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

M = "backend.scanner._plex_scanner"
FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
SERVER = {"id": "p1", "type": "plex", "url": "http://plex:32400", "api_key": "tok", "name": "MyPlex"}


@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.engine as en
    import backend.db.schema as sc
    monkeypatch.setattr(en, "SQLITE_PATH", str(tmp_path / "char_plex.db"))
    await sc.init_db()


def _lib(**over):
    lib = {"id": "lib1", "name": "Movies", "emby_library_id": "sec1", "grace_days": 7}
    lib.update(over)
    return lib


def _pitem(plex_id="501", **over):
    it = {"plex_id": plex_id, "title": f"T{plex_id}", "media_type": "movie", "tmdb_id": "",
          "view_count": 0, "last_viewed_at": None, "added_at": "2020-01-01T00:00:00+00:00",
          "poster_url": "http://p", "season_number": None}
    it.update(over)
    return it


class Env:
    """Patch all collaborators; `rules` maps plex_id -> (action, grace)."""

    def __init__(self, items, *, queued=None, pending_tmdb=None, rules=None, default_rule=("none", 0)):
        self.calls, self.logs, self.inserted, self.rule_inputs = [], [], [], []
        rules = rules or {}
        self.stack = ExitStack()
        env = self

        async def scan_library(_self, section):
            env.calls.append(("scan_library", section))
            return items

        async def queued_for_server(server_id):
            env.calls.append(("queued_ids", server_id))
            return set(queued or ())

        async def pending_with_tmdb():
            env.calls.append(("pending_tmdb",))
            return list(pending_tmdb or ())

        async def expert(item_data, lib_id):
            env.calls.append(("expert", lib_id))
            env.rule_inputs.append(item_data)
            pid = env._current
            return rules.get(pid, default_rule)

        async def insert(entry):
            env.calls.append(("insert", entry["emby_id"]))
            env.inserted.append(entry)

        async def add_log(level, msg, cat):
            env.calls.append(("log", msg))
            env.logs.append((level, msg, cat))

        from backend.plex_client import PlexClient
        self._patches = [
            patch.object(PlexClient, "scan_library", new=scan_library),
            patch(f"{M}.get_queued_ids_for_server", new=queued_for_server),
            patch(f"{M}.get_pending_with_tmdb", new=pending_with_tmdb),
            patch(f"{M}._evaluate_expert_rules", new=expert),
            patch(f"{M}.insert_queue_entry", new=insert),
            patch(f"{M}.add_log", new=add_log),
            patch(f"{M}.now_utc", return_value=FIXED_NOW),
        ]
        self._current = None
        # expert() needs to know which item it is scoring: derive from item_data identity
        orig_build = __import__("backend.scanner._expert_rules", fromlist=["x"])._build_plex_item_data

        def build(item):
            env._current = item.get("plex_id")
            return orig_build(item)
        self._patches.append(patch(f"{M}._build_plex_item_data", new=build))

    def __enter__(self):
        for p in self._patches:
            self.stack.enter_context(p)
        return self

    def __exit__(self, *a):
        self.stack.close()


async def _run(server=None, library=None, **kw):
    from backend.scanner._plex_scanner import _scan_plex_library
    return await _scan_plex_library(server=server or SERVER, library=library or _lib(), **kw)


async def _db(sql, params=()):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(sql, params)
        await db.commit()


async def _server_lib(lib_id, server_id):
    await _db("INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, logic, grace_days,"
              " enabled, deletion_unit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (lib_id, lib_id, "1", server_id, "[]", "AND", 7, 1, "movie", "2024-01-01"))


async def _ignore(emby_id, lib_id, tmdb="", mtype="movie"):
    await _db("INSERT INTO ignored_media (emby_id, title, tmdb_id, media_type, library_id, ignored_at)"
              " VALUES (?,?,?,?,?,?)", (emby_id, "t", tmdb, mtype, lib_id, "2024-01-01"))


# ─── Guards and ordering ──────────────────────────────────────────────────────

async def test_no_client_returns_zero_without_any_side_effect():
    with Env([_pitem()]) as env:
        assert await _run(server={**SERVER, "url": ""}) == 0
        assert await _run(server={**SERVER, "type": "emby"}) == 0
    assert env.calls == []


async def test_side_effect_order_and_log_lines_for_empty_scan():
    from backend.logmsg import lm
    with Env([]) as env:
        assert await _run() == 0
    assert env.calls == [
        ("scan_library", "sec1"),
        ("log", lm("scan.lib_scan", prefix="MyPlex : ", name="Movies")),
        ("queued_ids", "p1"),
        ("pending_tmdb",),
        ("log", lm("scan.lib_result", prefix="MyPlex : ", name="Movies", n=0)),
    ]
    assert [(lvl, cat) for lvl, _, cat in env.logs] == [("INFO", "scan"), ("INFO", "scan")]


@pytest.mark.parametrize("name_kw", [{}, {"name": ""}, {"name": None}])
async def test_server_name_defaults_to_plex(name_kw):
    from backend.logmsg import lm
    srv = {k: v for k, v in SERVER.items() if k != "name"}
    srv.update(name_kw)
    with Env([]) as env:
        await _run(server=srv)
    assert env.logs[0][1] == lm("scan.lib_scan", prefix="Plex : ", name="Movies")


async def test_server_id_passed_as_string_to_queued_lookup():
    with Env([]) as env:
        await _run(server={**SERVER, "id": 7})
    assert ("queued_ids", "7") in env.calls


# ─── Item gates ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pid", [None, "", 0])
async def test_item_without_plex_id_is_skipped_before_anything(pid):
    with Env([_pitem(plex_id=pid)], default_rule=("queue", 3)) as env:
        assert await _run() == 0
    assert not any(c[0] in ("expert", "insert") for c in env.calls)


async def test_already_queued_id_skipped_before_rules():
    with Env([_pitem("501")], queued={"501"}, default_rule=("queue", 3)) as env:
        assert await _run() == 0
    assert not any(c[0] == "expert" for c in env.calls)


async def test_ignored_id_skipped_only_for_same_server_libraries():
    await _server_lib("libP", "p1")
    await _server_lib("libOther", "emby1")
    await _ignore("501", "libP")
    await _ignore("502", "libOther")  # other server: integer-id collision must NOT block
    with Env([_pitem("501"), _pitem("502")], default_rule=("queue", 3)) as env:
        assert await _run() == 1
    assert [e["emby_id"] for e in env.inserted] == ["502"]


async def test_tmdb_ignore_is_cross_server_type_scoped_and_blank_tmdb_ignored():
    await _server_lib("libOther", "emby1")
    await _ignore("9001", "libOther", tmdb="555", mtype="Movie")
    await _ignore("9002", "libOther", tmdb="", mtype="Movie")
    items = [_pitem("1", tmdb_id="555", media_type="movie"),   # ignored (movie_555)
             _pitem("2", tmdb_id="555", media_type="series"),  # tv_555 != movie_555 -> not ignored
             _pitem("3", tmdb_id="", media_type="movie")]      # no tmdb -> never tmdb-ignored
    with Env(items, default_rule=("queue", 3)) as env:
        assert await _run() == 2
    assert [e["emby_id"] for e in env.inserted] == ["2", "3"]


async def test_tmdb_ignore_wins_over_tmdb_mirror():
    await _server_lib("libOther", "emby1")
    await _ignore("9001", "libOther", tmdb="555", mtype="movie")
    with Env([_pitem("1", tmdb_id="555")], pending_tmdb=[{"tmdb_id": "555", "media_type": "Movie"}]) as env:
        assert await _run() == 0
    assert env.inserted == []


# ─── Mirror path vs expert rules ──────────────────────────────────────────────

async def test_tmdb_mirror_uses_library_grace_and_skips_expert_rules():
    pend = [{"tmdb_id": 555, "media_type": "Movie"}, {"tmdb_id": None, "media_type": "Movie"}]
    with Env([_pitem("1", tmdb_id="555")], pending_tmdb=pend, default_rule=("queue", 99)) as env:
        assert await _run(library=_lib(grace_days=4)) == 1
    assert not any(c[0] == "expert" for c in env.calls)
    e = env.inserted[0]
    assert e["delete_at"] == (FIXED_NOW + timedelta(days=4)).isoformat()


async def test_tmdb_mirror_is_type_aware():
    pend = [{"tmdb_id": "555", "media_type": "Series"}]  # tv_555
    with Env([_pitem("1", tmdb_id="555", media_type="movie")], pending_tmdb=pend) as env:
        assert await _run() == 0  # movie_555 not mirrored -> expert rules -> "none" -> skip
    assert ("expert", "lib1") in env.calls


@pytest.mark.parametrize("grace_lib,expected_days", [(None, 7), (0, 7), ("", 7), (3, 3), ("5", 5)])
async def test_library_grace_default_seven_and_int_coercion(grace_lib, expected_days):
    pend = [{"tmdb_id": "1", "media_type": "movie"}]
    with Env([_pitem("1", tmdb_id="1")], pending_tmdb=pend) as env:
        await _run(library=_lib(grace_days=grace_lib))
    assert env.inserted[0]["delete_at"] == (FIXED_NOW + timedelta(days=expected_days)).isoformat()


async def test_missing_grace_days_key_defaults_to_seven():
    lib = {"id": "lib1", "name": "Movies", "emby_library_id": "sec1"}
    pend = [{"tmdb_id": "1", "media_type": "movie"}]
    with Env([_pitem("1", tmdb_id="1")], pending_tmdb=pend) as env:
        await _run(library=lib)
    assert env.inserted[0]["delete_at"] == (FIXED_NOW + timedelta(days=7)).isoformat()


@pytest.mark.parametrize("action,queued", [("queue", True), ("notify_only", False), ("none", False),
                                            (None, False), ("QUEUE", False), ("delete", False)])
async def test_only_exact_queue_action_queues_and_uses_rule_grace(action, queued):
    with Env([_pitem("1")], rules={"1": (action, 2)}) as env:
        n = await _run()
    assert n == (1 if queued else 0)
    if queued:
        assert env.inserted[0]["delete_at"] == (FIXED_NOW + timedelta(days=2)).isoformat()


async def test_expert_rules_receive_plex_item_data_and_library_id():
    with Env([_pitem("1", view_count=3, media_type="series")], rules={"1": ("queue", 1)}) as env:
        await _run(library=_lib(id="libX"))
    assert ("expert", "libX") in env.calls
    d = env.rule_inputs[0]
    assert d["play_count"] == 3 and d["media_type"] == "series" and d["never_watched"] == 0


async def test_rule_grace_zero_is_used_as_is_not_defaulted():
    with Env([_pitem("1")], rules={"1": ("queue", 0)}) as env:
        await _run(library=_lib(grace_days=9))
    assert env.inserted[0]["delete_at"] == FIXED_NOW.isoformat()


# ─── Entry content ────────────────────────────────────────────────────────────

async def test_entry_dict_exact_with_seerr_enrichment():
    item = _pitem("77", title="Dune", media_type="series", tmdb_id=438631, season_number=2,
                  view_count=5, last_viewed_at="2026-01-01T00:00:00+00:00",
                  added_at="2025-01-01T00:00:00+00:00", poster_url="http://poster/77")
    cache = {"438631": {"seerr_id": 8, "user_id": 3, "username": "amy", "request_url": "http://s/tv/438631"}}
    with Env([item], rules={"77": ("queue", 6)}) as env:
        assert await _run(seerr_cache=cache) == 1
    assert env.inserted == [{
        "emby_id": "77", "title": "Dune", "media_type": "series", "library_id": "lib1",
        "library_name": "Movies", "file_path": "", "poster_url": "http://poster/77",
        "tmdb_id": "438631", "seerr_id": 8, "seerr_user_id": 3, "seerr_username": "amy",
        "seerr_request_url": "http://s/tv/438631", "radarr_id": None, "sonarr_id": None,
        "sonarr_series_id": None, "arr_server_url": None, "season_number": 2,
        "detected_at": FIXED_NOW.isoformat(),
        "delete_at": (FIXED_NOW + timedelta(days=6)).isoformat(),
        "added_date": "2025-01-01T00:00:00+00:00", "last_played": "2026-01-01T00:00:00+00:00",
        "view_count": 5,
    }]
    assert list(env.inserted[0]) == [
        "emby_id", "title", "media_type", "library_id", "library_name", "file_path", "poster_url",
        "tmdb_id", "seerr_id", "seerr_user_id", "seerr_username", "seerr_request_url", "radarr_id",
        "sonarr_id", "sonarr_series_id", "arr_server_url", "season_number", "detected_at",
        "delete_at", "added_date", "last_played", "view_count"]


async def test_entry_defaults_when_optional_fields_missing():
    item = {"plex_id": "9", "title": "Bare"}
    with Env([item], rules={"9": ("queue", 1)}) as env:
        assert await _run() == 1
    e = env.inserted[0]
    assert (e["media_type"], e["poster_url"], e["tmdb_id"], e["season_number"], e["added_date"],
            e["last_played"], e["view_count"]) == ("movie", "", "", None, None, None, 0)
    assert (e["seerr_id"], e["seerr_user_id"], e["seerr_username"], e["seerr_request_url"]) == (None, None, "", "")


@pytest.mark.parametrize("cache,tmdb,expect", [
    (None, "5", (None, None, "", "")),
    ({}, "5", (None, None, "", "")),
    ({"5": {"seerr_id": 1}}, "5", (1, None, "", "")),
    ({"5": {"seerr_id": 1}}, "", (None, None, "", "")),  # blank tmdb never consults the cache
])
async def test_seerr_enrichment_variants(cache, tmdb, expect):
    with Env([_pitem("1", tmdb_id=tmdb)], rules={"1": ("queue", 1)}) as env:
        await _run(seerr_cache=cache)
    e = env.inserted[0]
    assert (e["seerr_id"], e["seerr_user_id"], e["seerr_username"], e["seerr_request_url"]) == expect


async def test_view_count_none_becomes_zero_and_string_is_coerced():
    with Env([_pitem("1", view_count=None), _pitem("2", view_count="4")],
             rules={"1": ("queue", 1), "2": ("queue", 1)}) as env:
        await _run()
    assert [e["view_count"] for e in env.inserted] == [0, 4]


async def test_multiple_items_count_order_and_final_log_count():
    items = [_pitem("1"), _pitem("2"), _pitem("3"), _pitem(None)]
    with Env(items, queued={"2"}, rules={"1": ("queue", 1), "3": ("queue", 1)}) as env:
        assert await _run() == 2
    assert [e["emby_id"] for e in env.inserted] == ["1", "3"]
    from backend.logmsg import lm
    assert env.logs[-1][1] == lm("scan.lib_result", prefix="MyPlex : ", name="Movies", n=2)
    assert env.calls[-1][0] == "log"
    # each insert happens right after its own rule evaluation (per-item interleave)
    body = [c for c in env.calls if c[0] in ("expert", "insert")]
    assert body == [("expert", "lib1"), ("insert", "1"), ("expert", "lib1"), ("insert", "3")]


async def test_insert_failure_propagates_and_no_result_log():
    with Env([_pitem("1")], rules={"1": ("queue", 1)}) as env:
        with patch(f"{M}.insert_queue_entry", new=AsyncMock(side_effect=RuntimeError("db"))):
            with pytest.raises(RuntimeError):
                await _run()
    assert len(env.logs) == 1  # only the "scan start" log
