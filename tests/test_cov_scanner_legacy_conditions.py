"""Coverage tests for backend/rules/legacy_conditions.py.

This module holds the eligibility decision for an individual Emby item:
_evaluate_item() is the function that decides whether a movie/episode enters
the deletion queue. Tests here focus on paths not already exercised by
tests/test_conditions.py: the "already queued" / "already ignored" DB
fallbacks, grace-day recalculation, poster URL resolution branches, and the
full _evaluate_item() decision tree (queue vs skip), including the Seerr
include/exclude filters that gate entry into the queue.

Convention follows tests/test_reevaluate_no_users_guard.py (isolated SQLite
DB per test) and tests/test_conditions.py (direct unit calls into
backend.rules.legacy_conditions).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "legacy_cond_cov.db")
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _insert_queue_row(
    emby_id: str,
    *,
    status: str = "pending",
    detected_at: datetime | None = None,
    delete_at: datetime | None = None,
    seerr_user_id: int | None = None,
    library_id: str = "lib1",
) -> int:
    from backend.db.engine import get_db
    detected_at = detected_at or _now()
    delete_at = delete_at or (_now() + timedelta(days=7))
    async with get_db() as db:
        new_id = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, seerr_user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (emby_id, "Some Movie", "Movie", library_id, "Films", "/m/x.mkv",
             detected_at.isoformat(), delete_at.isoformat(), status, seerr_user_id),
        )
        await db.commit()
        return new_id


async def _insert_seerr_user_rule(seerr_user_id: int, library_id: str, grace_days: int) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO seerr_user_rules (seerr_user_id, seerr_username, library_id, grace_days, enabled) "
            "VALUES (?,?,?,?,1)",
            (seerr_user_id, f"user{seerr_user_id}", library_id, grace_days),
        )
        await db.commit()


def _lib(lib_id: str = "lib1") -> dict:
    return {"id": lib_id, "name": "Films"}


def _emby_item(
    emby_id: str = "e1",
    *,
    name: str = "Movie X",
    media_type: str = "Movie",
    path: str = "/media/movies/x.mkv",
    added_days_ago: int = 30,
    tmdb: str = "999",
) -> dict:
    added = (_now() - timedelta(days=added_days_ago)).isoformat()
    return {
        "Id": emby_id, "Name": name, "Type": media_type, "Path": path,
        "DateCreated": added, "ProviderIds": {"Tmdb": tmdb},
    }


# ─── _update_delete_at_if_pending ───────────────────────────────────────────────

async def test_update_delete_at_if_pending_noop_when_no_matching_row():
    from backend.rules.legacy_conditions import _update_delete_at_if_pending
    # No row for this emby_id at all — must not raise.
    await _update_delete_at_if_pending("missing-id", _lib(), 7, "Title")


async def test_update_delete_at_if_pending_recalculates_when_grace_changes():
    detected = _now() - timedelta(days=1)
    old_delete_at = detected + timedelta(days=3)  # stale grace value (was 3, now 7)
    row_id = await _insert_queue_row("e1", detected_at=detected, delete_at=old_delete_at)

    from backend.rules.legacy_conditions import _update_delete_at_if_pending
    await _update_delete_at_if_pending("e1", _lib(), 7, "Title")

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT delete_at FROM media_queue WHERE id=?", (row_id,))
    expected = (detected + timedelta(days=7)).isoformat()
    assert row["delete_at"] == expected


async def test_update_delete_at_if_pending_clears_stale_notifications_on_change():
    detected = _now() - timedelta(days=1)
    old_delete_at = detected + timedelta(days=3)
    row_id = await _insert_queue_row("e1", detected_at=detected, delete_at=old_delete_at)
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO notifications (media_id, threshold, sent_at) VALUES (?, '7d', ?)",
            (row_id, _now().isoformat()),
        )
        await db.execute(
            "INSERT INTO notifications (media_id, threshold, sent_at) VALUES (?, 'now', ?)",
            (row_id, _now().isoformat()),
        )
        await db.commit()

    from backend.rules.legacy_conditions import _update_delete_at_if_pending
    await _update_delete_at_if_pending("e1", _lib(), 7, "Title")

    async with get_db() as db:
        rows = await db.fetch_all("SELECT threshold FROM notifications WHERE media_id=?", (row_id,))
    thresholds = {r["threshold"] for r in rows}
    # 'now' is exempt from the reset (per the SQL's NOT IN clause); '7d' must be purged.
    assert thresholds == {"now"}


async def test_update_delete_at_if_pending_swallows_db_exception():
    from backend.rules.legacy_conditions import _update_delete_at_if_pending
    with patch(
        "backend.rules.legacy_conditions.get_pending_item_by_emby_id",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await _update_delete_at_if_pending("e1", _lib(), 7, "Title")  # must not raise


# ─── _evaluate_conditions — all-invalid-under-OR edge case ─────────────────────

def test_evaluate_conditions_returns_false_when_every_condition_invalid_under_or():
    """Under OR logic, an invalid condition is skipped (non-contributing) rather
    than short-circuiting to False. If EVERY condition is invalid, the group
    list ends up empty and the whole thing must evaluate to False (not True,
    which an empty-OR could wrongly do)."""
    from backend.rules.legacy_conditions import _evaluate_conditions
    result = _evaluate_conditions(
        [{"field": "totally_unknown_field", "op": "gt", "value": 1}],
        "OR", None, None, 0, True,
    )
    assert result is False


# ─── _aggregate_user_data — no pre-fetched cache (per-item HTTP fallback) ──────

async def test_aggregate_user_data_fetches_per_item_when_no_cache():
    from backend.rules.legacy_conditions import _aggregate_user_data
    with patch(
        "backend.rules.legacy_conditions.get_user_data",
        new=AsyncMock(return_value={"PlayCount": 2, "Played": True, "LastPlayedDate": None}),
    ) as mock_get:
        play_count, never_watched, last_played = await _aggregate_user_data(
            ["u1"], "e1", None, None,
        )
    mock_get.assert_awaited_once_with("u1", "e1")
    assert play_count == 2
    assert never_watched is False


# ─── _resolve_arr_ids — sonarr HTTP fallback (no cache) ────────────────────────

async def test_resolve_arr_ids_series_no_cache_uses_http():
    from backend.rules.legacy_conditions import _resolve_arr_ids
    with patch(
        "backend.rules.legacy_conditions.sonarr_find_by_path_full",
        new=AsyncMock(return_value=(88, "http://sonarr.test", "key")),
    ) as mock_http:
        rid, sid, ssid, snum, arr_url = await _resolve_arr_ids(
            "/tv/Show/ep.mkv", "Episode", None, None,
        )
    mock_http.assert_awaited_once_with("/tv/Show/ep.mkv")
    assert sid == 88
    assert arr_url == "http://sonarr.test"
    assert ssid is None and snum is None


async def test_resolve_arr_ids_series_no_cache_http_returns_none():
    from backend.rules.legacy_conditions import _resolve_arr_ids
    with patch(
        "backend.rules.legacy_conditions.sonarr_find_by_path_full",
        new=AsyncMock(return_value=None),
    ):
        rid, sid, ssid, snum, arr_url = await _resolve_arr_ids(
            "/tv/Show/ep.mkv", "Episode", None, None,
        )
    assert sid is None and arr_url is None


# ─── _update_queued_item_if_pending — parse failure + exception paths ──────────

async def test_update_queued_item_if_pending_noop_when_detected_at_unparseable():
    from backend.rules.legacy_conditions import _update_queued_item_if_pending
    existing = {"id": 1, "status": "pending", "detected_at": "not-a-date", "seerr_user_id": None}
    with patch(
        "backend.rules.legacy_conditions.update_queue_item_dates", new=AsyncMock()
    ) as mock_update:
        await _update_queued_item_if_pending(existing, _lib(), 7, "Title", None, 0)
    mock_update.assert_not_awaited()


async def test_update_queued_item_if_pending_swallows_exception():
    from backend.rules.legacy_conditions import _update_queued_item_if_pending
    existing = {"id": 1, "status": "pending", "detected_at": _now().isoformat(), "seerr_user_id": None}
    with patch(
        "backend.rules.legacy_conditions.update_queue_item_dates",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await _update_queued_item_if_pending(existing, _lib(), 7, "Title", None, 0)  # must not raise


# ─── _get_poster_url ────────────────────────────────────────────────────────────

async def test_get_poster_url_movie_uses_radarr_when_available():
    from backend.rules.legacy_conditions import _get_poster_url
    with patch(
        "backend.rules.legacy_conditions.radarr_get_poster_url",
        new=AsyncMock(return_value="https://image.tmdb.org/poster.jpg"),
    ):
        url = await _get_poster_url("e1", media_type="Movie", radarr_id=42)
    assert url == "https://image.tmdb.org/poster.jpg"


async def test_get_poster_url_series_uses_sonarr_when_available():
    from backend.rules.legacy_conditions import _get_poster_url
    with patch(
        "backend.rules.legacy_conditions.sonarr_get_poster_url",
        new=AsyncMock(return_value="https://image.tmdb.org/series.jpg"),
    ):
        url = await _get_poster_url("e1", media_type="Series", sonarr_id=7)
    assert url == "https://image.tmdb.org/series.jpg"


async def test_get_poster_url_falls_back_to_emby_proxy_when_arr_lookup_fails():
    from backend.rules.legacy_conditions import _get_poster_url
    with (
        patch("backend.rules.legacy_conditions.radarr_get_poster_url",
              new=AsyncMock(side_effect=RuntimeError("radarr down"))),
        patch("backend.rules.legacy_conditions.get_client",
              new=AsyncMock(return_value=("http://emby:8096", "key"))),
    ):
        url = await _get_poster_url("e1", media_type="Movie", radarr_id=42, server_id="0")
    assert url == "/api/proxy/poster/0/e1"


async def test_get_poster_url_returns_empty_when_everything_fails():
    from backend.rules.legacy_conditions import _get_poster_url
    with patch(
        "backend.rules.legacy_conditions.get_client",
        new=AsyncMock(side_effect=RuntimeError("emby down")),
    ):
        url = await _get_poster_url("e1", media_type="Movie", radarr_id=None)
    assert url == ""


async def test_get_poster_url_no_arr_id_skips_arr_lookup_goes_to_emby():
    from backend.rules.legacy_conditions import _get_poster_url
    with patch(
        "backend.rules.legacy_conditions.get_client",
        new=AsyncMock(return_value=("http://emby:8096", "key")),
    ):
        url = await _get_poster_url("e1", media_type="Movie", radarr_id=None, server_id="2")
    assert url == "/api/proxy/poster/2/e1"


# ─── _is_already_queued / _is_already_ignored (DB fallback, no pre-fetched sets) ──

async def test_is_already_queued_fallback_returns_false_when_absent():
    from backend.rules.legacy_conditions import _is_already_queued
    result = await _is_already_queued("no-such-id", None, _lib(), 7, "Title", None, 0)
    assert result is False


async def test_is_already_queued_fallback_returns_true_and_updates_pending_row():
    detected = _now() - timedelta(days=1)
    old_delete_at = detected + timedelta(days=3)
    row_id = await _insert_queue_row("e1", detected_at=detected, delete_at=old_delete_at)

    from backend.rules.legacy_conditions import _is_already_queued
    result = await _is_already_queued("e1", None, _lib(), 7, "Title", None, 0)
    assert result is True

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT delete_at FROM media_queue WHERE id=?", (row_id,))
    assert row["delete_at"] == (detected + timedelta(days=7)).isoformat()


async def test_is_already_queued_fallback_skips_update_when_not_pending():
    detected = _now() - timedelta(days=1)
    old_delete_at = detected + timedelta(days=3)
    row_id = await _insert_queue_row("e1", status="deleted", detected_at=detected, delete_at=old_delete_at)

    from backend.rules.legacy_conditions import _is_already_queued
    result = await _is_already_queued("e1", None, _lib(), 7, "Title", None, 0)
    assert result is True  # still reported as "already present"

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT delete_at FROM media_queue WHERE id=?", (row_id,))
    # A non-pending row (e.g. already deleted) must not have its delete_at rewritten.
    assert row["delete_at"] == old_delete_at.isoformat()


async def test_is_already_queued_with_prefetched_set_true_updates_delete_at():
    detected = _now() - timedelta(days=1)
    old_delete_at = detected + timedelta(days=3)
    row_id = await _insert_queue_row("e1", detected_at=detected, delete_at=old_delete_at)

    from backend.rules.legacy_conditions import _is_already_queued
    result = await _is_already_queued("e1", {"e1"}, _lib(), 7, "Title", None, 0)
    assert result is True

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT delete_at FROM media_queue WHERE id=?", (row_id,))
    assert row["delete_at"] == (detected + timedelta(days=7)).isoformat()


async def test_is_already_queued_with_prefetched_set_false_skips_db_lookup():
    from backend.rules.legacy_conditions import _is_already_queued
    with patch(
        "backend.rules.legacy_conditions._get_queue_row_by_emby_id", new=AsyncMock()
    ) as mock_get:
        result = await _is_already_queued("e1", set(), _lib(), 7, "Title", None, 0)
    assert result is False
    mock_get.assert_not_awaited()


async def test_is_already_ignored_fallback_true_when_row_exists():
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ignored_media (emby_id, title, ignored_at) VALUES (?, ?, ?)",
            ("e1", "Some Movie", _now().isoformat()),
        )
        await db.commit()

    from backend.rules.legacy_conditions import _is_already_ignored
    assert await _is_already_ignored("e1", None) is True


async def test_is_already_ignored_fallback_false_when_absent():
    from backend.rules.legacy_conditions import _is_already_ignored
    assert await _is_already_ignored("no-such-id", None) is False


async def test_is_already_ignored_with_prefetched_set():
    from backend.rules.legacy_conditions import _is_already_ignored
    assert await _is_already_ignored("e1", {"e1", "e2"}) is True
    assert await _is_already_ignored("e3", {"e1", "e2"}) is False


# ─── _get_seerr_grace ────────────────────────────────────────────────────────────

async def test_get_seerr_grace_returns_default_when_no_seerr_user():
    from backend.rules.legacy_conditions import _get_seerr_grace
    grace = await _get_seerr_grace(None, "lib1", 7)
    assert grace == 7


async def test_get_seerr_grace_returns_override_when_rule_exists():
    await _insert_seerr_user_rule(seerr_user_id=55, library_id="lib1", grace_days=14)
    from backend.rules.legacy_conditions import _get_seerr_grace
    grace = await _get_seerr_grace(55, "lib1", 7)
    assert grace == 14


async def test_get_seerr_grace_falls_back_to_default_when_no_rule_for_user():
    from backend.rules.legacy_conditions import _get_seerr_grace
    grace = await _get_seerr_grace(999, "lib1", 7)
    assert grace == 7


# ─── _evaluate_item — the entry gate for the deletion queue ────────────────────

def _eval_item_patches(*, seerr_find_return=None):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("backend.rules.legacy_conditions.radarr_find_by_path", new=AsyncMock(return_value=None)))
    stack.enter_context(patch("backend.rules.legacy_conditions.sonarr_find_by_path_full", new=AsyncMock(return_value=None)))
    stack.enter_context(patch("backend.rules.legacy_conditions.seerr_find_request_by_tmdb", new=AsyncMock(return_value=seerr_find_return)))
    stack.enter_context(patch("backend.rules.legacy_conditions.get_client", new=AsyncMock(return_value=("", ""))))
    stack.enter_context(patch("backend.rules.legacy_conditions.add_log", new=AsyncMock()))
    return stack


async def test_evaluate_item_returns_none_when_missing_emby_id():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    del item["Id"]
    with _eval_item_patches():
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
        )
    assert result is None


async def test_evaluate_item_returns_none_when_missing_path():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    item["Path"] = ""
    with _eval_item_patches():
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
        )
    assert result is None


async def test_evaluate_item_returns_none_when_missing_added_date():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    item["DateCreated"] = ""
    with _eval_item_patches():
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
        )
    assert result is None


async def test_evaluate_item_returns_none_when_conditions_do_not_match():
    """An unwatched movie must NOT be queued by a play_count==0 rule if it
    was actually watched (never_watched=False, play_count>0)."""
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    with (
        _eval_item_patches(),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(3, False, _now())),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
        )
    assert result is None


async def test_evaluate_item_queues_matching_unwatched_item():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    with (
        _eval_item_patches(),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
        )
    assert result is not None
    assert result["emby_id"] == "e1"
    assert result["title"] == "Movie X"
    assert result["view_count"] == 0


async def test_evaluate_item_returns_none_when_already_queued():
    await _insert_queue_row("e1")
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    with (
        _eval_item_patches(),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
            queued_ids={"e1"},
        )
    assert result is None


async def test_evaluate_item_returns_none_when_already_ignored():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    with (
        _eval_item_patches(),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
            queued_ids=set(), ignored_ids={"e1"},
        )
    assert result is None


async def test_evaluate_item_skips_when_seerr_include_requires_request_and_item_has_none():
    """seerr_conditions has a user_include entry -> only Seerr-requested items
    may be queued. An item with no matching Seerr request must be skipped."""
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    seerr_conditions = [{"type": "user_include", "user_id": 5}]
    with (
        _eval_item_patches(seerr_find_return=None),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"],
            seerr_conditions, queued_ids=set(), ignored_ids=set(),
        )
    assert result is None


async def test_evaluate_item_skips_when_seerr_user_is_excluded():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    seerr_conditions = [{"type": "user_exclude", "user_id": 5}]
    with (
        _eval_item_patches(seerr_find_return={"seerr_id": 1, "user_id": 5, "username": "bob"}),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"],
            seerr_conditions, queued_ids=set(), ignored_ids=set(),
        )
    assert result is None


async def test_evaluate_item_queues_when_seerr_user_included_and_matches():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    seerr_conditions = [{"type": "user_include", "user_id": 5}]
    with (
        _eval_item_patches(seerr_find_return={"seerr_id": 1, "user_id": 5, "username": "bob"}),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"],
            seerr_conditions, queued_ids=set(), ignored_ids=set(),
        )
    assert result is not None
    assert result["seerr_id"] == 1
    assert result["seerr_username"] == "bob"


async def test_evaluate_item_builds_seerr_request_url_for_movie():
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item(media_type="Movie", tmdb="777")
    with (
        _eval_item_patches(seerr_find_return={"seerr_id": 1, "user_id": 5, "username": "bob"}),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
            queued_ids=set(), ignored_ids=set(), seerr_ext="https://seerr.example.com/",
        )
    assert result["seerr_request_url"] == "https://seerr.example.com/movie/777"


async def test_evaluate_item_applies_seerr_grace_override_and_notes_it():
    await _insert_seerr_user_rule(seerr_user_id=5, library_id="lib1", grace_days=1)
    from backend.rules.legacy_conditions import _evaluate_item
    item = _emby_item()
    with (
        _eval_item_patches(seerr_find_return={"seerr_id": 1, "user_id": 5, "username": "bob"}),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ),
        patch("backend.rules.legacy_conditions.add_log", new=AsyncMock()) as mock_log,
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
            queued_ids=set(), ignored_ids=set(),
        )
    assert result is not None
    detected = datetime.fromisoformat(result["detected_at"])
    delete_at = datetime.fromisoformat(result["delete_at"])
    # Overridden grace (1 day), not the library's default (7 days) — compare in
    # seconds with a small tolerance since detected_at/delete_at are captured
    # via two separate now_utc() calls a few microseconds apart.
    assert abs((delete_at - detected).total_seconds() - 86400) < 5
    # The eligibility log line must mention the override.
    _, message = mock_log.await_args.args[:2]
    assert "règle Seerr" in message


async def test_evaluate_item_uses_ctx_object_for_caches():
    """Passing a ScanContext must populate all the cache kwargs it carries."""
    from backend.rules.legacy_conditions import _evaluate_item, ScanContext
    item = _emby_item()
    ctx = ScanContext(
        user_data_cache={}, radarr_cache={}, sonarr_cache={}, seerr_cache={},
        seerr_ext="https://seerr.example.com", queued_ids=set(), ignored_ids=set(),
        series_tmdb_map={},
    )
    with (
        _eval_item_patches(),
        patch(
            "backend.rules.legacy_conditions._aggregate_user_data",
            new=AsyncMock(return_value=(0, True, None)),
        ) as mock_agg,
    ):
        result = await _evaluate_item(
            item, _lib(), [{"field": "play_count", "op": "eq", "value": 0}], "AND", 7, ["u1"], [],
            ctx=ctx,
        )
    assert result is not None
    # user_data_cache from ctx ({}) must have been threaded through, not None
    # (None would trigger the per-item HTTP fallback path in _aggregate_user_data).
    args = mock_agg.await_args.args
    assert args[2] == {}  # user_data_cache positional arg
