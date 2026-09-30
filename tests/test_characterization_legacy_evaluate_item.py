"""Characterization tests for rules/legacy_conditions._evaluate_item.

Written against the pre-refactor implementation: they pin the exact output
dict, the AND/OR short-circuit ORDER of the gates (conditions -> queued ->
ignored -> seerr lookup -> seerr filter -> grace -> arr ids -> poster -> log),
the ScanContext precedence rules, defaults, and the log lines. A wrong
deletion decision here removes media, so every branch is pinned by value.
"""
import importlib
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

M = "backend.rules.legacy_conditions"
FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
ADDED = FIXED_NOW - timedelta(days=30)
PLAYED = FIXED_NOW - timedelta(days=10)
NEVER_WATCHED_RULE = [{"field": "never_watched", "op": "eq", "value": 1}]


@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.utils as u
    import backend.db.settings_store as ss
    import backend.db.media_servers as ms
    import backend.db.schema as sc
    import backend.db.engine as en
    p = str(tmp_path / "char_legacy.db")
    for mod, attr in ((u, "DB_PATH"), (ss, "DB_PATH"), (ms, "DB_PATH"), (sc, "DB_PATH"), (en, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, p)
    ms._ms_cache = None
    ms._ms_cache_ts = 0.0
    ss._settings_cache.clear()
    ss._settings_cache_ts = 0.0
    await sc.init_db()


def _item(**over):
    it = {"Id": "e1", "Name": "Movie X", "Type": "Movie", "Path": "/m/x.mkv",
          "DateCreated": ADDED.isoformat(), "ProviderIds": {"Tmdb": "999"}}
    it.update(over)
    return it


class Env:
    """Patches every collaborator of _evaluate_item and records call order."""

    def __init__(self, *, seerr_find=None, radarr_find=None, sonarr_find=None,
                 radarr_poster="", sonarr_poster="", client=("", "")):
        self.calls = []
        self.logs = []
        self.stack = ExitStack()
        self.seerr_find = AsyncMock(side_effect=self._rec("seerr_find", seerr_find))
        self.radarr_find = AsyncMock(side_effect=self._rec("radarr_find", radarr_find))
        self.sonarr_find = AsyncMock(side_effect=self._rec("sonarr_find", sonarr_find))
        self.radarr_poster = AsyncMock(side_effect=self._rec("radarr_poster", radarr_poster))
        self.sonarr_poster = AsyncMock(side_effect=self._rec("sonarr_poster", sonarr_poster))
        self.client = AsyncMock(side_effect=self._rec("get_client", client))
        self.add_log = AsyncMock(side_effect=self._log)
        for name, mock in (
            ("seerr_find_request_by_tmdb", self.seerr_find),
            ("radarr_find_by_path", self.radarr_find),
            ("sonarr_find_by_path_full", self.sonarr_find),
            ("radarr_get_poster_url", self.radarr_poster),
            ("sonarr_get_poster_url", self.sonarr_poster),
            ("get_client", self.client),
            ("add_log", self.add_log),
        ):
            self.stack.enter_context(patch(f"{M}.{name}", new=mock))
        self.stack.enter_context(patch(f"{M}.now_utc", return_value=FIXED_NOW))
        for gate in ("_is_already_queued", "_is_already_ignored", "_get_seerr_grace", "_get_poster_url"):
            self._spy(gate)

    def _rec(self, name, ret):
        async def f(*a, **k):
            self.calls.append(name)
            return ret
        return f

    async def _log(self, level, msg, cat):
        self.logs.append((level, msg, cat))

    def _spy(self, name):
        orig = getattr(importlib.import_module(M), name)

        async def spy(*a, **k):
            self.calls.append(name)
            return await orig(*a, **k)
        self.stack.enter_context(patch(f"{M}.{name}", new=spy))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stack.close()


async def _run(env, item=None, conditions=None, logic="AND", grace=7, seerr_conditions=None, **kw):
    from backend.rules.legacy_conditions import _evaluate_item
    kw.setdefault("queued_ids", set())
    kw.setdefault("ignored_ids", set())
    kw.setdefault("user_data_cache", {})
    return await _evaluate_item(
        item or _item(), {"id": "lib1", "name": "Films"},
        NEVER_WATCHED_RULE if conditions is None else conditions, logic, grace, ["u1"],
        seerr_conditions or [], **kw,
    )


# ─── Full output pinned (movie, radarr via HTTP path) ─────────────────────────

async def test_full_entry_movie_unwatched_radarr_http_lookup():
    with Env(radarr_find=(42, "http://radarr", "key"), radarr_poster="http://cdn/p.jpg") as env:
        res = await _run(env, seerr_ext="https://seerr.x/")
    assert res == {
        "emby_id": "e1", "title": "Movie X", "media_type": "Movie", "library_id": "lib1",
        "library_name": "Films", "file_path": "/m/x.mkv", "poster_url": "http://cdn/p.jpg",
        "tmdb_id": "999", "seerr_id": None, "seerr_user_id": None, "seerr_username": "",
        "seerr_request_url": "", "radarr_id": 42, "sonarr_id": None, "sonarr_series_id": None,
        "season_number": None, "arr_server_url": "http://radarr",
        "detected_at": FIXED_NOW.isoformat(), "delete_at": (FIXED_NOW + timedelta(days=7)).isoformat(),
        "added_date": ADDED.isoformat(), "last_played": None, "view_count": 0,
    }
    assert list(res) == [  # key order is part of the contract (insert column order)
        "emby_id", "title", "media_type", "library_id", "library_name", "file_path", "poster_url",
        "tmdb_id", "seerr_id", "seerr_user_id", "seerr_username", "seerr_request_url", "radarr_id",
        "sonarr_id", "sonarr_series_id", "season_number", "arr_server_url", "detected_at",
        "delete_at", "added_date", "last_played", "view_count"]
    assert env.logs == [("INFO", "Éligible : Movie X → 22/06/2026", "scan")]
    assert env.calls == [
        "_is_already_queued", "_is_already_ignored", "seerr_find", "_get_seerr_grace",
        "radarr_find", "_get_poster_url", "radarr_poster",
    ]


async def test_full_entry_episode_with_sonarr_cache_seerr_cache_and_watched_user():
    item = _item(Type="Episode", Path="/tv/s/e.mkv", ProviderIds={}, SeriesId="ser1")
    sonarr_cache = {"/tv/s/e.mkv": {"ef_id": 7, "series_id": 3, "season_number": 2, "srv_url": "http://sonarr"}}
    seerr_cache = {"555": {"seerr_id": 9, "user_id": 5, "username": "bob"}}
    ud = {"u1": {"e1": {"PlayCount": 2, "Played": True, "LastPlayedDate": PLAYED.isoformat()}}}
    with Env(sonarr_poster="http://cdn/s.jpg") as env:
        res = await _run(
            env, item=item, conditions=[{"field": "play_count", "op": "gte", "value": 2}],
            user_data_cache=ud, sonarr_cache=sonarr_cache, seerr_cache=seerr_cache,
            series_tmdb_map={"ser1": "555"}, seerr_ext="https://seerr.x",
        )
    assert res["tmdb_id"] == "555"
    assert (res["seerr_id"], res["seerr_user_id"], res["seerr_username"]) == (9, 5, "bob")
    assert res["seerr_request_url"] == "https://seerr.x/tv/555"
    assert (res["sonarr_id"], res["sonarr_series_id"], res["season_number"], res["arr_server_url"]) == (7, 3, 2, "http://sonarr")
    assert res["radarr_id"] is None
    assert res["poster_url"] == "http://cdn/s.jpg"
    assert res["last_played"] == PLAYED.isoformat() and res["view_count"] == 2
    assert env.seerr_find.await_count == 0 and env.sonarr_find.await_count == 0
    assert env.sonarr_poster.await_args.args == (7,)


async def test_poster_falls_back_to_proxy_when_client_configured_and_no_arr():
    with Env(client=("http://emby", "k")) as env:
        res = await _run(env)
    assert res["poster_url"] == "/api/proxy/poster/0/e1"


async def test_title_defaults_to_question_mark_and_media_type_empty():
    with Env() as env:
        res = await _run(env, item=_item(Name=None, Type=None))
    assert res["title"] == "?" and res["media_type"] == ""
    assert env.logs[0][1] == "Éligible : ? → 22/06/2026"


# ─── Early exits: nothing else may be touched ─────────────────────────────────

@pytest.mark.parametrize("over", [{"Id": None}, {"Id": ""}, {"Path": ""}, {"Path": None},
                                  {"DateCreated": ""}, {"DateCreated": None}, {"DateCreated": "garbage"}])
async def test_missing_id_path_or_date_returns_none_without_side_effects(over):
    with Env() as env:
        assert await _run(env, item=_item(**over)) is None
    assert env.calls == [] and env.logs == []


async def test_conditions_not_matching_stops_before_queue_ignore_seerr():
    ud = {"u1": {"e1": {"PlayCount": 1, "Played": True, "LastPlayedDate": PLAYED.isoformat()}}}
    with Env() as env:
        assert await _run(env, user_data_cache=ud) is None
    assert env.calls == [] and env.logs == []


async def test_empty_conditions_never_queue():
    with Env() as env:
        assert await _run(env, conditions=[]) is None
    assert env.calls == []


async def test_or_logic_one_true_condition_is_enough_and_and_logic_needs_all():
    conds = [{"field": "never_watched", "op": "eq", "value": 1},
             {"field": "days_since_added", "op": "gt", "value": 100}]  # added 30d ago -> False
    with Env() as env:
        assert await _run(env, conditions=conds, logic="OR") is not None
    with Env() as env:
        assert await _run(env, conditions=conds, logic="AND") is None


async def test_days_not_watched_uses_last_played_age_boundary():
    ud = {"u1": {"e1": {"PlayCount": 1, "Played": True, "LastPlayedDate": PLAYED.isoformat()}}}  # 10 days
    gt10 = [{"field": "days_not_watched", "op": "gt", "value": 10}]
    gte10 = [{"field": "days_not_watched", "op": "gte", "value": 10}]
    with Env() as env:
        assert await _run(env, conditions=gt10, user_data_cache=ud) is None
    with Env() as env:
        assert await _run(env, conditions=gte10, user_data_cache=ud) is not None


async def test_already_queued_stops_before_ignored_and_seerr():
    with Env() as env:
        assert await _run(env, queued_ids={"e1"}) is None
    assert env.calls == ["_is_already_queued"] and env.logs == []


async def test_already_queued_db_fallback_when_queued_ids_none():
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, file_path,"
            " detected_at, delete_at, status) VALUES ('e1','t','Movie','lib1','F','/x',?,?,'pending')",
            (FIXED_NOW.isoformat(), (FIXED_NOW + timedelta(days=7)).isoformat()))
        await db.commit()
    with Env() as env:
        assert await _run(env, queued_ids=None) is None
    # pending row -> grace recomputed through _update_queued_item_if_pending
    assert env.calls == ["_is_already_queued", "_get_seerr_grace"]


async def test_ignored_stops_before_seerr_lookup():
    with Env() as env:
        assert await _run(env, ignored_ids={"e1"}) is None
    assert env.calls == ["_is_already_queued", "_is_already_ignored"]


async def test_ignored_db_fallback_when_ignored_ids_none():
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("INSERT INTO ignored_media (emby_id, title, tmdb_id, media_type, library_id, ignored_at)"
                         " VALUES ('e1','t','1','Movie','lib1','2024-01-01')")
        await db.commit()
    with Env() as env:
        assert await _run(env, ignored_ids=None) is None
    assert env.calls == ["_is_already_queued", "_is_already_ignored"]


# ─── Seerr lookup + filter gates ──────────────────────────────────────────────

async def test_seerr_http_lookup_when_no_cache_and_skipped_when_tmdb_empty():
    with Env(seerr_find={"seerr_id": 1, "user_id": 2, "username": "amy"}) as env:
        res = await _run(env)
    assert env.seerr_find.await_args.args == ("999",)
    assert (res["seerr_id"], res["seerr_user_id"], res["seerr_username"]) == (1, 2, "amy")
    with Env(seerr_find={"seerr_id": 1}) as env:
        res = await _run(env, item=_item(ProviderIds={}))
    assert env.seerr_find.await_count == 0 and res["seerr_id"] is None and res["tmdb_id"] == ""


async def test_seerr_cache_miss_does_not_fall_back_to_http_and_username_defaults_empty():
    with Env() as env:
        res = await _run(env, seerr_cache={})
    assert env.seerr_find.await_count == 0 and res["seerr_id"] is None
    with Env() as env:
        res = await _run(env, seerr_cache={"999": {"seerr_id": 4}})
    assert (res["seerr_id"], res["seerr_user_id"], res["seerr_username"]) == (4, None, "")


async def test_seerr_include_without_request_logs_not_requested_and_stops_before_grace():
    with Env() as env:
        res = await _run(env, seerr_conditions=[{"type": "user_include", "user_id": 5}])
    assert res is None
    assert env.logs == [("DEBUG", "Ignoré (non demandé sur Seerr) : Movie X", "scan")]
    assert env.calls == ["_is_already_queued", "_is_already_ignored", "seerr_find"]


@pytest.mark.parametrize("conds,user,reason", [
    ([{"type": "user_include", "user_id": 5}], 6, "non inclus"),
    ([{"type": "user_exclude", "user_id": 6}], 6, "exclu"),
    # quirk preserved: has_excludes anywhere => reason says "exclu" even if the include failed
    ([{"type": "user_include", "user_id": 5}, {"type": "user_exclude", "user_id": 9}], 6, "exclu"),
])
async def test_seerr_filter_rejection_reason_and_log(conds, user, reason):
    with Env(seerr_find={"seerr_id": 1, "user_id": user, "username": "u"}) as env:
        assert await _run(env, seerr_conditions=conds) is None
    assert env.logs == [("DEBUG", f"Ignoré (utilisateur Seerr {reason}) : Movie X", "scan")]
    assert "_get_seerr_grace" not in env.calls


async def test_seerr_exclude_only_with_unrequested_item_passes():
    with Env() as env:
        res = await _run(env, seerr_conditions=[{"type": "user_exclude", "user_id": 6}])
    assert res is not None and res["seerr_user_id"] is None


async def test_seerr_include_matching_user_passes():
    with Env(seerr_find={"seerr_id": 1, "user_id": 5, "username": "u"}) as env:
        assert await _run(env, seerr_conditions=[{"type": "user_include", "user_id": 5}]) is not None


# ─── Grace / note / seerr url ─────────────────────────────────────────────────

async def _grace_rule(uid, days, enabled=1):
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("INSERT INTO seerr_user_rules (seerr_user_id, seerr_username, library_id, grace_days, enabled)"
                         " VALUES (?,?,?,?,?)", (uid, f"u{uid}", "lib1", days, enabled))
        await db.commit()


async def test_seerr_grace_override_applied_and_noted_in_log():
    await _grace_rule(5, 2)
    with Env(seerr_find={"seerr_id": 1, "user_id": 5, "username": "bob"}) as env:
        res = await _run(env)
    assert res["delete_at"] == (FIXED_NOW + timedelta(days=2)).isoformat()
    assert env.logs == [("INFO", "Éligible : Movie X → 17/06/2026 (règle Seerr bob: 2j)", "scan")]


async def test_no_note_when_override_equals_default_or_disabled_or_username_empty():
    await _grace_rule(5, 7)
    with Env(seerr_find={"seerr_id": 1, "user_id": 5, "username": "bob"}) as env:
        await _run(env)
    assert env.logs[0][1] == "Éligible : Movie X → 22/06/2026"
    await _grace_rule(6, 1, enabled=0)
    with Env(seerr_find={"seerr_id": 1, "user_id": 6, "username": "amy"}) as env:
        res = await _run(env)
    assert res["delete_at"] == (FIXED_NOW + timedelta(days=7)).isoformat()
    await _grace_rule(8, 1)
    with Env(seerr_find={"seerr_id": 1, "user_id": 8, "username": ""}) as env:
        res = await _run(env)
    assert res["delete_at"] == (FIXED_NOW + timedelta(days=1)).isoformat()
    assert env.logs[0][1] == "Éligible : Movie X → 16/06/2026"


@pytest.mark.parametrize("ext,seerr_id,mtype,expected", [
    ("", 1, "Movie", ""),
    ("https://s", None, "Movie", ""),
    ("https://s/", 1, "Movie", "https://s/movie/999"),
    ("https://s//", 1, "Series", "https://s/tv/999"),
    ("https://s", 1, "Season", "https://s/tv/999"),
])
async def test_seerr_request_url_rules(ext, seerr_id, mtype, expected):
    data = {"seerr_id": seerr_id, "user_id": 5, "username": "b"} if seerr_id is not None else None
    with Env(seerr_find=data) as env:
        res = await _run(env, item=_item(Type=mtype), seerr_ext=ext)
    assert res["seerr_request_url"] == expected


# ─── arr resolution ───────────────────────────────────────────────────────────

async def test_radarr_cache_path_prefix_and_no_http():
    cache = {"/m": (11, "http://r1", "k")}
    with Env(radarr_poster="P") as env:
        res = await _run(env, radarr_cache=cache)
    assert (res["radarr_id"], res["arr_server_url"], res["poster_url"]) == (11, "http://r1", "P")
    assert env.radarr_find.await_count == 0
    assert env.radarr_poster.await_args.args == (11,)


async def test_episode_without_sonarr_cache_uses_http_full_lookup():
    with Env(sonarr_find=(5, "http://s", "k"), sonarr_poster="SP") as env:
        res = await _run(env, item=_item(Type="Episode"))
    assert (res["sonarr_id"], res["sonarr_series_id"], res["season_number"], res["arr_server_url"]) == (5, None, None, "http://s")
    assert res["poster_url"] == "SP"


async def test_movie_with_no_arr_match_has_all_arr_fields_none():
    with Env() as env:
        res = await _run(env)
    assert (res["radarr_id"], res["sonarr_id"], res["arr_server_url"]) == (None, None, None)


# ─── ScanContext precedence ───────────────────────────────────────────────────

async def test_ctx_supplies_caches_and_explicit_kwargs_win():
    from backend.rules.legacy_conditions import ScanContext
    ctx = ScanContext(user_data_cache={}, radarr_cache={"/m": (1, "http://ctx", "k")},
                      seerr_cache={"999": {"seerr_id": 1, "user_id": 5, "username": "ctx"}},
                      seerr_ext="https://ctx", queued_ids=set(), ignored_ids=set())
    with Env() as env:
        res = await _run(env, queued_ids=None, ignored_ids=None, user_data_cache=None, ctx=ctx)
    assert res["arr_server_url"] == "http://ctx" and res["seerr_username"] == "ctx"
    assert res["seerr_request_url"] == "https://ctx/movie/999"
    # explicit kwargs win over ctx; explicit empty-dict/empty-set are NOT treated as missing
    with Env() as env:
        res = await _run(env, ctx=ctx, radarr_cache={}, seerr_cache={}, seerr_ext="https://kw")
    assert res["radarr_id"] is None and res["seerr_id"] is None
    # explicit queued_ids=set() beats ctx.queued_ids={"e1"}
    ctx2 = ScanContext(queued_ids={"e1"}, ignored_ids={"e1"})
    with Env() as env:
        assert await _run(env, ctx=ctx2, queued_ids=set(), ignored_ids=set()) is not None
    with Env() as env:
        assert await _run(env, ctx=ctx2, queued_ids=None, ignored_ids=None) is None
    # seerr_ext: falsy explicit value falls back to ctx (uses `or`)
    ctx3 = ScanContext(seerr_ext="https://ctx3", seerr_cache={"999": {"seerr_id": 1}})
    with Env() as env:
        res = await _run(env, ctx=ctx3, seerr_ext="")
    assert res["seerr_request_url"] == "https://ctx3/movie/999"


# ─── Exceptions are NOT swallowed here ────────────────────────────────────────

async def test_add_log_failure_propagates():
    with Env() as env:
        env.add_log.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await _run(env)
