"""Coverage tests for backend/scanner/_emby_scanner.py — the Emby/Jellyfin
library scan engine (item eligibility, consolidated watch-state tracking,
and queue reevaluation).

Targets branches not exercised by the existing related test files
(test_e2e_scan_queue_delete.py, test_reevaluate_no_users_guard.py,
test_scan_pagination_truncation.py, test_scan_integration.py,
test_series_seerr_matching.py, test_watch_state_safety.py,
test_enrich_seerr_batching.py): exception-swallowing paths, the
consolidated-watch early-return guards, the expert-rules-fallback sonarr
branch + Seerr request URL construction, per-item skip guards in the
paginated collector, and reevaluate_library_queue's early returns and
leaving-soon poster restoration.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "emby_scanner_cov.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    yield db_path


def _now():
    return datetime.now(timezone.utc)


def _lib(**overrides) -> dict:
    lib = {
        "id": "lib1", "name": "Films", "emby_library_id": "1", "server_id": "0",
        "conditions": "[]", "logic": "AND", "grace_days": 7,
    }
    lib.update(overrides)
    return lib


async def _insert_library(lib_id="lib1", **overrides) -> None:
    from backend.db.engine import get_db
    lib = _lib(id=lib_id, **overrides)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, "
            "logic, grace_days, enabled, deletion_unit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (lib["id"], lib["name"], lib["emby_library_id"], lib["server_id"],
             lib["conditions"], lib["logic"], lib["grace_days"], 1, "episode", "2024-01-01"),
        )
        await db.commit()


async def _insert_pending(
    emby_id: str, lib_id: str = "lib1", *,
    added_date: str = "2024-01-01T00:00:00+00:00",
    last_played: str | None = None,
    poster_url: str = "",
) -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, poster_url, added_date, delete_at, detected_at, status, last_played) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (emby_id, f"Movie {emby_id}", "Movie", lib_id, "Films", f"/m/{emby_id}.mkv",
             poster_url, added_date, "2030-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00",
             "pending", last_played),
        )
        await db.commit()
        return new_id


# ─── _load_scan_caches — activity log fetch failure ─────────────────────────────

async def test_load_scan_caches_tolerates_activity_log_fetch_failure():
    from backend.scanner._emby_scanner import _load_scan_caches
    with patch(
        "backend.scanner._emby_scanner.get_play_activity",
        new=AsyncMock(side_effect=RuntimeError("emby timeout")),
    ):
        user_data_cache, expert_rules_cache, activity_log = await _load_scan_caches(
            [], "1", "0", None,
        )
    assert activity_log == {}


async def test_load_scan_caches_skips_fetch_when_activity_log_prefetched():
    from backend.scanner._emby_scanner import _load_scan_caches
    with patch("backend.scanner._emby_scanner.get_play_activity", new=AsyncMock()) as mock_fetch:
        _, _, activity_log = await _load_scan_caches([], "1", "0", {"e1": "2024-01-01"})
    mock_fetch.assert_not_awaited()
    assert activity_log == {"e1": "2024-01-01"}


# ─── _apply_activity_log_updates ────────────────────────────────────────────────

async def test_apply_activity_log_updates_noop_on_empty_log():
    from backend.scanner._emby_scanner import _apply_activity_log_updates
    with patch("backend.scanner._emby_scanner.update_activity_log_batch", new=AsyncMock()) as mock_upd:
        await _apply_activity_log_updates({})
    mock_upd.assert_not_awaited()


async def test_apply_activity_log_updates_swallows_db_exception():
    from backend.scanner._emby_scanner import _apply_activity_log_updates
    with patch(
        "backend.scanner._emby_scanner.update_activity_log_batch",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await _apply_activity_log_updates({"e1": "2024-01-01"})  # must not raise


# ─── _accumulate_consolidated_watch — early-return guards ──────────────────────

async def test_accumulate_consolidated_watch_returns_early_when_no_sonarr_cache():
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    await _accumulate_consolidated_watch(
        {"Id": "e1", "Path": "/s/e1.mkv"}, watch,
        user_ids=["u1"], user_data_cache={}, activity_log={}, sonarr_cache=None,
    )
    assert watch == {}


async def test_accumulate_consolidated_watch_returns_early_when_path_not_in_cache():
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    await _accumulate_consolidated_watch(
        {"Id": "e1", "Path": "/s/unknown.mkv"}, watch,
        user_ids=["u1"], user_data_cache={}, activity_log={},
        sonarr_cache={"/s/e1.mkv": {"series_id": 1, "season_number": 1}},
    )
    assert watch == {}


async def test_accumulate_consolidated_watch_returns_early_when_series_id_missing():
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    await _accumulate_consolidated_watch(
        {"Id": "e1", "Path": "/s/e1.mkv"}, watch,
        user_ids=["u1"], user_data_cache={}, activity_log={},
        sonarr_cache={"/s/e1.mkv": {"series_id": None, "season_number": 1}},
    )
    assert watch == {}


async def test_accumulate_consolidated_watch_returns_early_when_item_has_no_id():
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    await _accumulate_consolidated_watch(
        {"Path": "/s/e1.mkv"}, watch,  # no "Id"
        user_ids=["u1"], user_data_cache={}, activity_log={},
        sonarr_cache={"/s/e1.mkv": {"series_id": 1, "season_number": 1}},
    )
    assert watch == {}


async def test_accumulate_consolidated_watch_skips_never_watched_episode():
    """An episode nobody watched must not overwrite an existing better watch
    state recorded for the group with (None, 0) — it contributes nothing."""
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    with patch(
        "backend.scanner._emby_scanner._aggregate_user_data",
        new=AsyncMock(return_value=(0, True, None)),
    ):
        await _accumulate_consolidated_watch(
            {"Id": "e1", "Path": "/s/e1.mkv"}, watch,
            user_ids=["u1"], user_data_cache={}, activity_log={},
            sonarr_cache={"/s/e1.mkv": {"series_id": 1, "season_number": 1}},
        )
    assert watch == {}


async def test_accumulate_consolidated_watch_records_when_watched_but_zero_playcount():
    """Played=True with PlayCount=0 (Emby's "mark played" without ever
    actually playing it) must still count as watched for the group — only
    the conjunction of never_watched AND no play_count skips a contribution."""
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    played = _now() - timedelta(days=1)
    with patch(
        "backend.scanner._emby_scanner._aggregate_user_data",
        new=AsyncMock(return_value=(0, False, played)),
    ):
        await _accumulate_consolidated_watch(
            {"Id": "e1", "Path": "/s/e1.mkv"}, watch,
            user_ids=["u1"], user_data_cache={}, activity_log={},
            sonarr_cache={"/s/e1.mkv": {"series_id": 1, "season_number": 1}},
        )
    assert watch != {}
    assert watch["sonarr-series:1"] == (played, 0)


async def test_accumulate_consolidated_watch_records_series_and_season_keys():
    from backend.scanner._emby_scanner import _accumulate_consolidated_watch
    watch: dict = {}
    played = _now() - timedelta(days=1)
    with patch(
        "backend.scanner._emby_scanner._aggregate_user_data",
        new=AsyncMock(return_value=(3, False, played)),
    ):
        await _accumulate_consolidated_watch(
            {"Id": "e1", "Path": "/s/e1.mkv"}, watch,
            user_ids=["u1"], user_data_cache={}, activity_log={},
            sonarr_cache={"/s/e1.mkv": {"series_id": 1, "season_number": 2}},
        )
    assert set(watch.keys()) == {"sonarr-series:1", "sonarr-season:1:2"}
    assert watch["sonarr-series:1"] == (played, 3)


# ─── _apply_consolidated_watch_updates ──────────────────────────────────────────

async def test_apply_consolidated_watch_updates_noop_when_all_played_none():
    from backend.scanner._emby_scanner import _apply_consolidated_watch_updates
    with patch("backend.scanner._emby_scanner.update_consolidated_watch_state", new=AsyncMock()) as mock_upd:
        await _apply_consolidated_watch_updates({"sonarr-series:1": (None, 0)})
    mock_upd.assert_not_awaited()


async def test_apply_consolidated_watch_updates_swallows_exception():
    from backend.scanner._emby_scanner import _apply_consolidated_watch_updates
    played = _now()
    with patch(
        "backend.scanner._emby_scanner.update_consolidated_watch_state",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await _apply_consolidated_watch_updates({"sonarr-series:1": (played, 3)})  # must not raise


# ─── _evaluate_expert_rules_fallback — sonarr branch + Seerr request URL ───────

def _episode_item(emby_id="e1", path="/s/e1.mkv") -> dict:
    return {
        "Id": emby_id, "Name": "Ep 1", "Type": "Episode", "Path": path,
        "DateCreated": "2024-01-01T00:00:00+00:00",
        "ProviderIds": {}, "SeriesId": "series-1",
    }


async def test_expert_rules_fallback_resolves_sonarr_ids_for_non_movie():
    from backend.scanner._emby_scanner import _evaluate_expert_rules_fallback
    sonarr_cache = {"/s/e1.mkv": {"ef_id": 55, "series_id": 9, "season_number": 2, "srv_url": "http://sonarr"}}
    with (
        patch("backend.scanner._emby_scanner._aggregate_user_data", new=AsyncMock(return_value=(0, True, None))),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules", new=AsyncMock(return_value=("queue", 3))),
        patch("backend.scanner._emby_scanner._get_poster_url", new=AsyncMock(return_value="")),
        patch("backend.scanner._emby_scanner.add_log", new=AsyncMock()),
    ):
        result = await _evaluate_expert_rules_fallback(
            _episode_item(), _lib(), "/s/e1.mkv", "e1",
            user_ids=["u1"], user_data_cache={}, activity_log={},
            seerr_cache=None, series_tmdb_map=None,
            radarr_cache=None, sonarr_cache=sonarr_cache,
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert result is not None
    assert result["sonarr_id"] == 55
    assert result["sonarr_series_id"] == 9
    assert result["season_number"] == 2
    assert result["arr_server_url"] == "http://sonarr"


async def test_expert_rules_fallback_builds_seerr_request_url_for_series():
    from backend.scanner._emby_scanner import _evaluate_expert_rules_fallback
    seerr_cache = {"777": {"seerr_id": 42, "user_id": 9, "username": "bob"}}
    with (
        patch("backend.scanner._emby_scanner._aggregate_user_data", new=AsyncMock(return_value=(0, True, None))),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules", new=AsyncMock(return_value=("queue", 3))),
        patch("backend.scanner._emby_scanner._get_poster_url", new=AsyncMock(return_value="")),
        patch("backend.scanner._emby_scanner.resolve_item_tmdb", return_value="777"),
        patch("backend.scanner._emby_scanner.add_log", new=AsyncMock()),
    ):
        result = await _evaluate_expert_rules_fallback(
            _episode_item(), _lib(), "/s/e1.mkv", "e1",
            user_ids=["u1"], user_data_cache={}, activity_log={},
            seerr_cache=seerr_cache, series_tmdb_map={}, radarr_cache=None, sonarr_cache=None,
            seerr_ext_url="https://seerr.example.com/", expert_rules_cache=[],
        )
    assert result["seerr_request_url"] == "https://seerr.example.com/tv/777"


async def test_expert_rules_fallback_returns_none_when_no_rule_queues():
    from backend.scanner._emby_scanner import _evaluate_expert_rules_fallback
    with (
        patch("backend.scanner._emby_scanner._aggregate_user_data", new=AsyncMock(return_value=(0, True, None))),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules", new=AsyncMock(return_value=(None, 7))),
    ):
        result = await _evaluate_expert_rules_fallback(
            _episode_item(), _lib(), "/s/e1.mkv", "e1",
            user_ids=["u1"], user_data_cache={}, activity_log={},
            seerr_cache=None, series_tmdb_map=None, radarr_cache=None, sonarr_cache=None,
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert result is None


# ─── _collect_eligible_items — per-item skip guards ─────────────────────────────

def _movie_item(emby_id="e1", path="/m/e1.mkv") -> dict:
    return {
        "Id": emby_id, "Name": "Movie X", "Type": "Movie", "Path": path,
        "DateCreated": "2024-01-01T00:00:00+00:00", "ProviderIds": {},
    }


def _collect_patches(items_pages):
    """items_pages: list of (items, total) tuples returned in sequence by
    get_items_in_library across pagination calls."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch(
        "backend.scanner._emby_scanner.get_items_in_library",
        new=AsyncMock(side_effect=items_pages),
    ))
    stack.enter_context(patch(
        "backend.scanner._emby_scanner._evaluate_item",
        new=AsyncMock(return_value=None),  # legacy conditions never match -> fall through
    ))
    return stack


async def test_collect_eligible_items_skips_item_with_no_file_path():
    item = _movie_item()
    item["Path"] = ""
    with (
        _collect_patches([([item], 1)]),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules_fallback", new=AsyncMock()) as mock_fb,
    ):
        from backend.scanner._emby_scanner import _collect_eligible_items
        eligible = await _collect_eligible_items(
            _lib(), [], "AND", 7, ["u1"], [], "1", "0",
            user_data_cache={}, activity_log={},
            radarr_cache=None, sonarr_cache=None, seerr_cache=None,
            queued_ids=None, ignored_ids=None,
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert eligible == []
    mock_fb.assert_not_awaited()


async def test_collect_eligible_items_skips_item_already_queued():
    item = _movie_item(emby_id="e1")
    with (
        _collect_patches([([item], 1)]),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules_fallback", new=AsyncMock()) as mock_fb,
    ):
        from backend.scanner._emby_scanner import _collect_eligible_items
        eligible = await _collect_eligible_items(
            _lib(), [], "AND", 7, ["u1"], [], "1", "0",
            user_data_cache={}, activity_log={},
            radarr_cache=None, sonarr_cache=None, seerr_cache=None,
            queued_ids={"e1"}, ignored_ids=None,
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert eligible == []
    mock_fb.assert_not_awaited()


async def test_collect_eligible_items_skips_item_already_ignored():
    item = _movie_item(emby_id="e1")
    with (
        _collect_patches([([item], 1)]),
        patch("backend.scanner._emby_scanner._evaluate_expert_rules_fallback", new=AsyncMock()) as mock_fb,
    ):
        from backend.scanner._emby_scanner import _collect_eligible_items
        eligible = await _collect_eligible_items(
            _lib(), [], "AND", 7, ["u1"], [], "1", "0",
            user_data_cache={}, activity_log={},
            radarr_cache=None, sonarr_cache=None, seerr_cache=None,
            queued_ids=set(), ignored_ids={"e1"},
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert eligible == []
    mock_fb.assert_not_awaited()


async def test_collect_eligible_items_falls_through_to_expert_rules_fallback():
    item = _movie_item(emby_id="e1")
    with (
        _collect_patches([([item], 1)]),
        patch(
            "backend.scanner._emby_scanner._evaluate_expert_rules_fallback",
            new=AsyncMock(return_value={"emby_id": "e1", "title": "Movie X"}),
        ) as mock_fb,
    ):
        from backend.scanner._emby_scanner import _collect_eligible_items
        eligible = await _collect_eligible_items(
            _lib(), [], "AND", 7, ["u1"], [], "1", "0",
            user_data_cache={}, activity_log={},
            radarr_cache=None, sonarr_cache=None, seerr_cache=None,
            queued_ids=set(), ignored_ids=set(),
            seerr_ext_url="", expert_rules_cache=[],
        )
    assert eligible == [{"emby_id": "e1", "title": "Movie X"}]
    mock_fb.assert_awaited_once()


# ─── reevaluate_library_queue — early returns ───────────────────────────────────

async def test_reevaluate_library_queue_returns_zero_when_library_missing():
    from backend.scanner._emby_scanner import reevaluate_library_queue
    removed = await reevaluate_library_queue("does-not-exist")
    assert removed == 0


async def test_reevaluate_library_queue_returns_zero_when_no_pending_items():
    await _insert_library("lib1")
    from backend.scanner._emby_scanner import reevaluate_library_queue
    with patch("backend.scanner._emby_scanner.get_users", new=AsyncMock()) as mock_users:
        removed = await reevaluate_library_queue("lib1")
    assert removed == 0
    mock_users.assert_not_awaited()  # short-circuits before fetching users


# ─── reevaluate_library_queue — leaving-soon poster restoration ────────────────

async def test_reevaluate_removal_restores_poster_when_overlay_enabled():
    await _insert_library("lib1", conditions='[{"field": "days_not_watched", "op": "gt", "value": 5}]')
    await _insert_pending("e1", poster_url="http://emby.local/poster.jpg",
                          last_played=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())  # watched -> stops matching
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('emby_leaving_soon_overlay', 'true')"
        )
        await db.commit()

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock(return_value={})),
        patch("backend.scanner._emby_scanner.get_client", new=AsyncMock(return_value=("http://emby:8096", "key"))),
        patch("backend.scanner._emby_scanner.guarded_image_get", new=AsyncMock(return_value=b"fakejpeg")),
        patch("backend.scanner._emby_scanner.sync_emby_collection", new=AsyncMock()),
    ):
        mock_response = MagicMock()
        mock_post = AsyncMock(return_value=mock_response)
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=MagicMock(post=mock_post))
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        with patch("backend.scanner._emby_scanner.httpx.AsyncClient", return_value=mock_client_instance):
            from backend.scanner._emby_scanner import reevaluate_library_queue
            removed = await reevaluate_library_queue("lib1")

    assert removed == 1
    mock_post.assert_awaited_once()
    call_url = mock_post.await_args.args[0]
    assert "/Items/e1/Images/Primary" in call_url


async def test_reevaluate_removal_skips_poster_restore_when_overlay_disabled():
    await _insert_library("lib1", conditions='[{"field": "days_not_watched", "op": "gt", "value": 5}]')
    await _insert_pending("e1", poster_url="http://emby.local/poster.jpg",
                          last_played=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('emby_leaving_soon_overlay', 'false')"
        )
        await db.commit()

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock(return_value={})),
        patch("backend.scanner._emby_scanner.get_client", new=AsyncMock(return_value=("http://emby:8096", "key"))),
        patch("backend.scanner._emby_scanner.guarded_image_get", new=AsyncMock()) as mock_img,
        patch("backend.scanner._emby_scanner.sync_emby_collection", new=AsyncMock()),
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        removed = await reevaluate_library_queue("lib1")

    assert removed == 1
    mock_img.assert_not_awaited()


async def test_reevaluate_removal_swallows_poster_restore_exception():
    """A failure while restoring the leaving-soon overlay poster must not
    block the item's removal from the queue — the removal itself is the
    important side effect, poster restore is best-effort."""
    await _insert_library("lib1", conditions='[{"field": "days_not_watched", "op": "gt", "value": 5}]')
    await _insert_pending("e1", poster_url="http://emby.local/poster.jpg",
                          last_played=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock(return_value={})),
        patch("backend.scanner._emby_scanner.get_client", new=AsyncMock(side_effect=RuntimeError("emby down"))),
        patch("backend.scanner._emby_scanner.sync_emby_collection", new=AsyncMock()),
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        removed = await reevaluate_library_queue("lib1")

    assert removed == 1  # removal still succeeded despite the poster-restore failure

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM media_queue WHERE emby_id='e1'")
    assert row is None


async def test_reevaluate_no_poster_restore_attempt_when_poster_url_not_http():
    """A relative/proxy poster_url (not starting with http) must skip the
    restore branch entirely — nothing to restore from a URL that isn't a
    real external image."""
    await _insert_library("lib1", conditions='[{"field": "days_not_watched", "op": "gt", "value": 5}]')
    await _insert_pending("e1", poster_url="/api/proxy/poster/0/e1",
                          last_played=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())

    with (
        patch("backend.scanner._emby_scanner.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._emby_scanner.get_library_user_data", new=AsyncMock(return_value={})),
        patch("backend.scanner._emby_scanner.get_client", new=AsyncMock()) as mock_client,
        patch("backend.scanner._emby_scanner.sync_emby_collection", new=AsyncMock()),
    ):
        from backend.scanner._emby_scanner import reevaluate_library_queue
        removed = await reevaluate_library_queue("lib1")

    assert removed == 1
    mock_client.assert_not_awaited()
