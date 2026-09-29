"""Coverage tests for backend/scanner/_orchestrator.py.

Focus: which media gets queued vs skipped when the orchestrator dispatches
per-library scans, and the safety guards around locking / partial failures
(a partial or failed scan must never look like a clean success that queues
media by mistake).

Convention follows tests/test_reevaluate_no_users_guard.py (isolated SQLite
DB per test via monkeypatch) and tests/test_scan_integration.py (patches
external I/O at the names imported into backend.scanner._orchestrator).
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.exceptions import ArrClientError, MediaServerUnreachable


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch, tmp_path):
    import backend.db.utils as _db_utils
    import backend.db.settings_store as _db_ss
    import backend.db.media_servers as _db_ms
    import backend.db.schema as _db_schema
    import backend.db.engine as _db_engine

    db_path = str(tmp_path / "orch_cov.db")
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


async def _set_setting(key: str, value: str) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()


async def _insert_library(lib_id: str = "lib1", server_id: str = "0", enabled: int = 1) -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, conditions, "
            "logic, grace_days, enabled, deletion_unit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (lib_id, "Films", "3", server_id, '[{"field": "days_not_watched", "op": "gt", "value": 5}]',
             "AND", 7, enabled, "movie", "2024-01-01"),
        )
        await db.commit()


def _rule(name, *, field, op, value, library_id=None, library_ids=None, enabled=True):
    from backend.rules.models import ExpertRule, Condition, ConditionGroup, RuleOperator, RuleAction
    return ExpertRule(
        name=name,
        library_id=library_id,
        library_ids=library_ids,
        condition_groups=[ConditionGroup(
            conditions=[Condition(field=field, op=op, value=value)],
            operator=RuleOperator.AND,
        )],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=enabled,
        priority=0,
    )


# ─── _purge_stale_seerr_items ──────────────────────────────────────────────────

async def test_purge_deletes_for_library_where_every_rule_requires_seerr():
    """All rules targeting lib1 require SEERR_USER_ID IN [...] -> lib1 qualifies for purge."""
    from backend.db.repositories import save_expert_rule
    from backend.rules.models import ConditionField, ConditionOp

    rule = _rule("seerr-only", field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN,
                 value=["u1"], library_ids=["lib1"])
    await save_expert_rule(rule)

    with patch(
        "backend.scanner._orchestrator.delete_stale_pending_no_seerr",
        new=AsyncMock(return_value=2),
    ) as mock_delete:
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    mock_delete.assert_awaited_once()
    (called_ids,), _ = mock_delete.await_args
    assert called_ids == ["lib1"]


async def test_purge_skips_library_when_one_rule_lacks_seerr_filter():
    """lib1 has one rule requiring seerr and one that doesn't -> must NOT be purged,
    since the seerr-less rule can legitimately queue items with no seerr_user_id."""
    from backend.db.repositories import save_expert_rule
    from backend.rules.models import ConditionField, ConditionOp

    rule_seerr = _rule("seerr-only", field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN,
                       value=["u1"], library_ids=["lib1"])
    rule_plain = _rule("plain", field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ,
                       value=0, library_ids=["lib1"])
    await save_expert_rule(rule_seerr)
    await save_expert_rule(rule_plain)

    with patch(
        "backend.scanner._orchestrator.delete_stale_pending_no_seerr",
        new=AsyncMock(return_value=0),
    ) as mock_delete:
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    mock_delete.assert_not_awaited()


async def test_purge_noop_when_no_expert_rules():
    with patch(
        "backend.scanner._orchestrator.delete_stale_pending_no_seerr",
        new=AsyncMock(),
    ) as mock_delete:
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    mock_delete.assert_not_awaited()


async def test_purge_global_rule_without_seerr_filter_blocks_every_library():
    """A global rule (no library scope) without a seerr filter applies to ALL
    libraries -> even a library whose own rules are seerr-only must be excluded."""
    from backend.db.repositories import save_expert_rule
    from backend.rules.models import ConditionField, ConditionOp

    rule_seerr = _rule("seerr-only", field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN,
                       value=["u1"], library_ids=["lib1"])
    rule_global_plain = _rule("global-plain", field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=0)
    await save_expert_rule(rule_seerr)
    await save_expert_rule(rule_global_plain)

    with patch(
        "backend.scanner._orchestrator.delete_stale_pending_no_seerr",
        new=AsyncMock(),
    ) as mock_delete:
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    mock_delete.assert_not_awaited()


async def test_purge_swallows_exception_and_does_not_propagate():
    """get_expert_rules() failing must not crash the whole scan (best-effort cleanup).

    get_expert_rules is imported locally inside the function (`from
    ..db.repositories import get_expert_rules`), so the patch target is the
    repositories module, not the already-bound name in _orchestrator.
    """
    with patch(
        "backend.db.repositories.get_expert_rules",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()  # must not raise


async def test_purge_rule_scoped_by_single_library_id_not_library_ids():
    """A rule using the singular `library_id` field (not the `library_ids`
    list) must still scope the purge check to that library."""
    from backend.db.repositories import save_expert_rule
    from backend.rules.models import ConditionField, ConditionOp

    rule = _rule("seerr-only-singular", field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN,
                 value=["u1"], library_id="lib1", library_ids=None)
    await save_expert_rule(rule)

    with patch(
        "backend.scanner._orchestrator.delete_stale_pending_no_seerr",
        new=AsyncMock(return_value=1),
    ) as mock_delete:
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    mock_delete.assert_awaited_once()
    (called_ids,), _ = mock_delete.await_args
    assert called_ids == ["lib1"]


async def test_purge_logs_info_when_items_deleted():
    from backend.db.repositories import save_expert_rule
    from backend.rules.models import ConditionField, ConditionOp

    rule = _rule("seerr-only", field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN,
                 value=["u1"], library_ids=["lib1"])
    await save_expert_rule(rule)

    with (
        patch("backend.scanner._orchestrator.delete_stale_pending_no_seerr", new=AsyncMock(return_value=3)),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
    ):
        from backend.scanner._orchestrator import _purge_stale_seerr_items
        await _purge_stale_seerr_items()

    assert mock_log.await_count == 1
    level, _msg = mock_log.await_args.args[:2]
    assert level == "INFO"


# ─── _do_scan_one_library ───────────────────────────────────────────────────────

async def test_do_scan_one_library_returns_warning_when_library_missing():
    from backend.scanner._orchestrator import _do_scan_one_library
    status, msg, added = await _do_scan_one_library("does-not-exist")
    assert status == "warning"
    assert added == 0


async def test_do_scan_one_library_returns_warning_when_library_disabled():
    """A disabled library must not be scanned (the SQL filters on enabled=1)."""
    await _insert_library("lib1", enabled=0)
    from backend.scanner._orchestrator import _do_scan_one_library
    status, msg, added = await _do_scan_one_library("lib1")
    assert status == "warning"
    assert added == 0


async def test_do_scan_one_library_skips_when_server_disabled():
    await _insert_library("lib1", server_id="7")
    with patch(
        "backend.scanner._orchestrator.get_media_servers",
        new=AsyncMock(return_value=[{"id": "7", "name": "Emby-1", "type": "emby", "enabled": False}]),
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library("lib1")
    assert status == "skipped"
    assert added == 0


async def test_do_scan_one_library_plex_path_returns_success_and_added_count():
    await _insert_library("lib1", server_id="9")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "9", "name": "Plex-1", "type": "plex", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator._scan_plex_library", new=AsyncMock(return_value=4)) as mock_plex,
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library("lib1", seerr_cache={})
    assert status == "success"
    assert added == 4
    mock_plex.assert_awaited_once()


async def test_do_scan_one_library_emby_path_aborts_when_no_users():
    """No users returned by the media server -> must NOT scan the library
    (would otherwise look like everything is never-watched)."""
    await _insert_library("lib1", server_id="0")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "0", "name": "Emby", "type": "emby", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[])),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock()) as mock_scan,
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library("lib1", seerr_cache={})
    assert status == "error"
    assert added == 0
    mock_scan.assert_not_awaited()


async def test_do_scan_one_library_builds_seerr_cache_when_not_supplied_and_warns_on_failure():
    await _insert_library("lib1", server_id="0")
    await _set_setting("discord_alert_seerr_failure", "false")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "0", "name": "Emby", "type": "emby", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator.build_seerr_request_cache",
              new=AsyncMock(side_effect=ArrClientError("seerr down"))) as mock_build,
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.build_radarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.build_sonarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=1)) as mock_scan,
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library("lib1")  # seerr_cache=None

    mock_build.assert_awaited_once()
    assert status == "success"
    assert added == 1
    # The failed seerr cache build must not block the scan — empty dict is used.
    _, kwargs = mock_scan.await_args
    assert kwargs["seerr_cache"] == {}


async def test_do_scan_one_library_seerr_failure_sends_discord_alert_when_enabled():
    await _insert_library("lib1", server_id="0")
    await _set_setting("discord_alert_seerr_failure", "true")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "0", "name": "Emby", "type": "emby", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator.build_seerr_request_cache",
              new=AsyncMock(side_effect=ArrClientError("seerr down"))),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.build_radarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.build_sonarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=0)),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        await _do_scan_one_library("lib1")

    mock_alert.assert_awaited_once()
    title = mock_alert.await_args.args[0]
    assert "Seerr" in title


async def test_do_scan_one_library_emby_path_success_passes_caches_through():
    await _insert_library("lib1", server_id="0")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "0", "name": "Emby", "type": "emby", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={"u1": {}})),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=7)) as mock_scan,
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library(
            "lib1", seerr_cache={"s": 1}, radarr_cache={"r": 1}, sonarr_cache={"n": 1},
        )
    assert (status, added) == ("success", 7)
    _, kwargs = mock_scan.await_args
    assert kwargs["radarr_cache"] == {"r": 1}
    assert kwargs["sonarr_cache"] == {"n": 1}
    assert kwargs["seerr_cache"] == {"s": 1}


async def test_do_scan_one_library_activity_log_failure_does_not_abort_scan():
    """get_play_activity() raising must be tolerated — an empty activity log is
    used and the scan proceeds (mirrors the try/except around it)."""
    await _insert_library("lib1", server_id="0")
    with (
        patch(
            "backend.scanner._orchestrator.get_media_servers",
            new=AsyncMock(return_value=[{"id": "0", "name": "Emby", "type": "emby", "enabled": True}]),
        ),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(side_effect=RuntimeError("timeout"))),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=0)) as mock_scan,
    ):
        from backend.scanner._orchestrator import _do_scan_one_library
        status, msg, added = await _do_scan_one_library("lib1", seerr_cache={})
    assert status == "success"
    _, kwargs = mock_scan.await_args
    assert kwargs["activity_log"] == {}


# ─── _abort_scan_no_users ───────────────────────────────────────────────────────

async def test_abort_scan_no_users_falls_back_to_server_id_when_no_name():
    with (
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _abort_scan_no_users
        await _abort_scan_no_users("srv-42", "")

    level, message = mock_log.await_args.args[:2]
    assert level == "ERROR"
    assert "srv-42" in message
    title, desc = mock_alert.await_args.args[:2]
    assert "srv-42" in desc


# ─── _scan_single_server ────────────────────────────────────────────────────────

async def test_scan_single_server_plex_sums_added_and_survives_per_library_exception():
    server = {"id": "1", "type": "plex", "name": "Plex"}
    libs = [{"id": "l1", "name": "Movies"}, {"id": "l2", "name": "Shows"}]
    with (
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch(
            "backend.scanner._orchestrator._scan_plex_library",
            new=AsyncMock(side_effect=[3, RuntimeError("plex boom")]),
        ),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 3  # second library's exception must not lose the first library's count
    assert any(c.args[0] == "ERROR" for c in mock_log.await_args_list)


async def test_scan_single_server_unsupported_type_returns_zero():
    server = {"id": "2", "type": "kodi", "name": "Kodi"}
    with patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log:
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 0
    mock_log.assert_awaited_once()


async def test_scan_single_server_no_enabled_libraries_returns_zero_without_users_call():
    server = {"id": "3", "type": "emby", "name": "Emby"}
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=[])),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock()) as mock_users,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 0
    mock_users.assert_not_awaited()


async def test_scan_single_server_no_users_aborts_returns_zero():
    server = {"id": "3", "type": "emby", "name": "Emby"}
    libs = [{"id": "l1", "name": "Movies"}]
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[])),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock()) as mock_scan,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 0
    mock_scan.assert_not_awaited()


async def test_scan_single_server_invalid_max_parallel_setting_defaults_to_three():
    """max_parallel_library_scans='not-a-number' must not crash — falls back to 3."""
    await _set_setting("max_parallel_library_scans", "not-a-number")
    server = {"id": "3", "type": "emby", "name": "Emby"}
    libs = [{"id": "l1", "name": "Movies"}]
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=5)),
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 5


async def test_scan_single_server_activity_log_failure_does_not_abort_and_uses_empty_log():
    """get_play_activity() raising inside _scan_single_server (per-server fetch)
    must be tolerated the same way as the single-library path — an empty
    activity log is used rather than crashing the whole server scan."""
    server = {"id": "3", "type": "emby", "name": "Emby"}
    libs = [{"id": "l1", "name": "Movies"}]
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(side_effect=RuntimeError("timeout"))),
        patch("backend.scanner._orchestrator._scan_library", new=AsyncMock(return_value=1)) as mock_scan,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 1
    _, kwargs = mock_scan.await_args
    assert kwargs["activity_log"] == {}


async def test_scan_single_server_media_server_unreachable_sends_discord_alert_when_enabled():
    await _set_setting("discord_alert_scan_failure", "true")
    server = {"id": "3", "type": "emby", "name": "Emby-Main"}
    libs = [{"id": "l1", "name": "Movies"}]
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={})),
        patch(
            "backend.scanner._orchestrator._scan_library",
            new=AsyncMock(side_effect=MediaServerUnreachable("timeout")),
        ),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        added = await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    assert added == 0
    mock_alert.assert_awaited_once()
    title = mock_alert.await_args.args[0]
    assert "non scannée" in title


async def test_scan_single_server_media_server_unreachable_no_alert_when_disabled():
    await _set_setting("discord_alert_scan_failure", "false")
    server = {"id": "3", "type": "emby", "name": "Emby-Main"}
    libs = [{"id": "l1", "name": "Movies"}]
    with (
        patch("backend.scanner._orchestrator.ensure_server_uid", new=AsyncMock()),
        patch("backend.scanner._orchestrator.get_enabled_libraries", new=AsyncMock(return_value=libs)),
        patch("backend.scanner._orchestrator.get_users", new=AsyncMock(return_value=[{"Id": "u1"}])),
        patch("backend.scanner._orchestrator.get_play_activity", new=AsyncMock(return_value={})),
        patch(
            "backend.scanner._orchestrator._scan_library",
            new=AsyncMock(side_effect=MediaServerUnreachable("timeout")),
        ),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _scan_single_server
        await _scan_single_server(server, radarr_cache={}, sonarr_cache={}, seerr_cache={})
    mock_alert.assert_not_awaited()


# ─── _build_shared_caches ────────────────────────────────────────────────────────

async def test_build_shared_caches_returns_all_three_on_success():
    with (
        patch("backend.scanner._orchestrator.build_seerr_request_cache", new=AsyncMock(return_value={"s": 1})),
        patch("backend.scanner._orchestrator.build_radarr_path_cache", new=AsyncMock(return_value={"r": 1})),
        patch("backend.scanner._orchestrator.build_sonarr_path_cache", new=AsyncMock(return_value={"n": 1})),
    ):
        from backend.scanner._orchestrator import _build_shared_caches
        seerr, radarr, sonarr = await _build_shared_caches()
    assert (seerr, radarr, sonarr) == ({"s": 1}, {"r": 1}, {"n": 1})


async def test_build_shared_caches_seerr_failure_returns_empty_dict_and_alerts():
    await _set_setting("discord_alert_seerr_failure", "true")
    with (
        patch("backend.scanner._orchestrator.build_seerr_request_cache",
              new=AsyncMock(side_effect=ArrClientError("down"))),
        patch("backend.scanner._orchestrator.build_radarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.build_sonarr_path_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import _build_shared_caches
        seerr, radarr, sonarr = await _build_shared_caches()
    assert seerr == {}
    mock_alert.assert_awaited_once()


async def test_build_shared_caches_radarr_failure_propagates():
    """Unlike Seerr, Radarr/Sonarr failures are not swallowed — a broken path
    cache must stop the scan rather than silently queuing on wrong paths."""
    with (
        patch("backend.scanner._orchestrator.build_seerr_request_cache", new=AsyncMock(return_value={})),
        patch("backend.scanner._orchestrator.build_radarr_path_cache",
              new=AsyncMock(side_effect=ArrClientError("radarr down"))),
    ):
        from backend.scanner._orchestrator import _build_shared_caches
        with pytest.raises(ArrClientError):
            await _build_shared_caches()


# ─── _run_scan_body ──────────────────────────────────────────────────────────────

async def test_run_scan_body_falls_back_to_default_server_when_none_enabled():
    await _set_setting("media_server_type", "emby")
    with (
        patch("backend.scanner._orchestrator.get_media_servers", new=AsyncMock(return_value=[])),
        patch("backend.scanner._orchestrator._build_shared_caches", new=AsyncMock(return_value=({}, {}, {}))),
        patch("backend.scanner._orchestrator._scan_single_server", new=AsyncMock(return_value=0)) as mock_scan_srv,
        patch("backend.scanner._orchestrator._purge_stale_seerr_items", new=AsyncMock()),
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()),
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import _run_scan_body
        status, msg = await _run_scan_body(1)
    assert status == "success"
    server_arg = mock_scan_srv.await_args.args[0]
    assert server_arg["id"] == "0" and server_arg["type"] == "emby"


async def test_run_scan_body_calls_post_scan_hooks_in_order_on_success():
    calls = []
    with (
        patch("backend.scanner._orchestrator.get_media_servers",
              new=AsyncMock(return_value=[{"id": "1", "type": "emby", "enabled": True}])),
        patch("backend.scanner._orchestrator._build_shared_caches", new=AsyncMock(return_value=({}, {}, {}))),
        patch("backend.scanner._orchestrator._scan_single_server", new=AsyncMock(return_value=2)),
        patch("backend.scanner._orchestrator._purge_stale_seerr_items",
              new=AsyncMock(side_effect=lambda: calls.append("purge"))),
        patch("backend.scanner._orchestrator.sync_emby_collection",
              new=AsyncMock(side_effect=lambda: calls.append("sync"))),
        patch("backend.scanner._orchestrator._send_pending_notifications",
              new=AsyncMock(side_effect=lambda: calls.append("notify"))),
    ):
        from backend.scanner._orchestrator import _run_scan_body
        status, msg = await _run_scan_body(1)
    assert status == "success"
    assert msg == "2 queued"
    assert calls == ["purge", "sync", "notify"]


async def test_run_scan_body_exception_returns_error_and_alerts_when_enabled():
    await _set_setting("discord_alert_scan_failure", "true")
    with (
        patch("backend.scanner._orchestrator.get_media_servers", new=AsyncMock(side_effect=RuntimeError("db exploded"))),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _run_scan_body
        status, msg = await _run_scan_body(1)
    assert status == "error"
    assert "db exploded" in msg
    mock_alert.assert_awaited_once()


async def test_run_scan_body_exception_no_alert_when_disabled():
    await _set_setting("discord_alert_scan_failure", "false")
    with (
        patch("backend.scanner._orchestrator.get_media_servers", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("backend.scanner._orchestrator.send_alert", new=AsyncMock()) as mock_alert,
    ):
        from backend.scanner._orchestrator import _run_scan_body
        status, msg = await _run_scan_body(1)
    assert status == "error"
    mock_alert.assert_not_awaited()


# ─── run_scan ────────────────────────────────────────────────────────────────────

async def test_run_scan_skips_and_logs_when_lock_already_held():
    from backend.scanner._orchestrator import _scan_lock
    await _scan_lock.acquire()
    try:
        with (
            patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
            patch("backend.scanner._orchestrator._run_scan_body", new=AsyncMock()) as mock_body,
        ):
            from backend.scanner._orchestrator import run_scan
            await run_scan()
        mock_body.assert_not_awaited()
        level = mock_log.await_args.args[0]
        assert level == "WARN"
    finally:
        _scan_lock.release()


async def test_run_scan_success_records_finished_job_with_returned_status():
    with patch(
        "backend.scanner._orchestrator._run_scan_body",
        new=AsyncMock(return_value=("success", "5 queued")),
    ):
        from backend.scanner._orchestrator import run_scan
        await run_scan()

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM job_history WHERE job_type='scan' ORDER BY id DESC LIMIT 1")
    assert row["status"] == "success"
    assert row["message"] == "5 queued"
    assert row["finished_at"] is not None


async def test_run_scan_timeout_marks_job_as_timeout_not_success():
    """A scan that exceeds the hard timeout must be recorded as failed/timeout,
    never as a silent success that would hide a stuck run."""
    import asyncio as _asyncio

    async def _quick_return(run_id):
        return "success", "should not get here"

    async def _fake_wait_for(coro, timeout):
        coro.close()  # avoid a "coroutine was never awaited" warning
        raise _asyncio.TimeoutError()

    with (
        patch("backend.scanner._orchestrator._run_scan_body", new=_quick_return),
        patch("backend.scanner._orchestrator.asyncio.wait_for", new=_fake_wait_for),
    ):
        from backend.scanner._orchestrator import run_scan
        await run_scan()

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM job_history WHERE job_type='scan' ORDER BY id DESC LIMIT 1")
    assert row["status"] == "error"
    assert row["message"] == "timeout"


async def test_run_scan_lock_not_available_calls_starvation_check():
    from backend.scanner._orchestrator import LockNotAvailable

    class _FakeLock:
        def locked(self):
            return False

        async def __aenter__(self):
            raise LockNotAvailable()

        async def __aexit__(self, *a):
            return False

    with (
        patch("backend.scanner._orchestrator._scan_lock", new=_FakeLock()),
        patch("backend.scanner._orchestrator.warn_if_job_starved", new=AsyncMock()) as mock_warn,
    ):
        from backend.scanner._orchestrator import run_scan
        await run_scan()

    mock_warn.assert_awaited_once()
    assert mock_warn.await_args.args[0] == "scan"


# ─── run_scan_library ────────────────────────────────────────────────────────────

async def test_run_scan_library_skips_when_lock_already_held():
    from backend.scanner._orchestrator import _scan_lock
    await _scan_lock.acquire()
    try:
        with (
            patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
            patch("backend.scanner._orchestrator._do_scan_one_library", new=AsyncMock()) as mock_do,
        ):
            from backend.scanner._orchestrator import run_scan_library
            await run_scan_library("lib1")
        mock_do.assert_not_awaited()
        assert mock_log.await_args.args[0] == "WARN"
    finally:
        _scan_lock.release()


async def test_run_scan_library_success_runs_post_scan_hooks():
    with (
        patch("backend.scanner._orchestrator._do_scan_one_library",
              new=AsyncMock(return_value=("success", "1 queued", 1))),
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()) as mock_sync,
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()) as mock_notify,
    ):
        from backend.scanner._orchestrator import run_scan_library
        await run_scan_library("lib1")

    mock_sync.assert_awaited_once()
    mock_notify.assert_awaited_once()
    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM job_history WHERE job_type='scan_library' ORDER BY id DESC LIMIT 1")
    assert row["status"] == "success"


async def test_run_scan_library_exception_records_error_status():
    with (
        patch("backend.scanner._orchestrator._do_scan_one_library",
              new=AsyncMock(side_effect=RuntimeError("scan crashed"))),
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()),
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import run_scan_library
        await run_scan_library("lib1")

    from backend.db.engine import get_db
    async with get_db() as db:
        row = await db.fetch_one("SELECT * FROM job_history WHERE job_type='scan_library' ORDER BY id DESC LIMIT 1")
    assert row["status"] == "error"
    assert row["message"] == "scan crashed"


async def test_run_scan_library_lock_not_available_returns_silently_without_post_hooks():
    from backend.scanner._orchestrator import LockNotAvailable

    class _FakeLock:
        def locked(self):
            return False

        async def __aenter__(self):
            raise LockNotAvailable()

        async def __aexit__(self, *a):
            return False

    with (
        patch("backend.scanner._orchestrator._scan_lock", new=_FakeLock()),
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()) as mock_sync,
    ):
        from backend.scanner._orchestrator import run_scan_library
        await run_scan_library("lib1")

    mock_sync.assert_not_awaited()


# ─── run_scan_libraries ──────────────────────────────────────────────────────────

async def test_run_scan_libraries_skips_when_lock_already_held():
    from backend.scanner._orchestrator import _scan_lock
    await _scan_lock.acquire()
    try:
        with (
            patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
            patch("backend.scanner._orchestrator._build_shared_caches", new=AsyncMock()) as mock_caches,
        ):
            from backend.scanner._orchestrator import run_scan_libraries
            await run_scan_libraries(["lib1", "lib2"])
        mock_caches.assert_not_awaited()
        assert mock_log.await_args.args[0] == "WARN"
    finally:
        _scan_lock.release()


async def test_run_scan_libraries_scans_each_library_under_one_lock_and_finishes_each_run():
    with (
        patch("backend.scanner._orchestrator._build_shared_caches", new=AsyncMock(return_value=({}, {}, {}))),
        patch(
            "backend.scanner._orchestrator._do_scan_one_library",
            new=AsyncMock(side_effect=[("success", "1 queued", 1), ("success", "0 queued", 0)]),
        ) as mock_do,
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()),
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import run_scan_libraries
        await run_scan_libraries(["lib1", "lib2"])

    assert mock_do.await_count == 2
    from backend.db.engine import get_db
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT * FROM job_history WHERE job_type='scan_library' ORDER BY id"
        )
    assert len(rows) == 2
    assert all(r["status"] == "success" for r in rows)


async def test_run_scan_libraries_per_library_exception_does_not_stop_the_rest():
    with (
        patch("backend.scanner._orchestrator._build_shared_caches", new=AsyncMock(return_value=({}, {}, {}))),
        patch(
            "backend.scanner._orchestrator._do_scan_one_library",
            new=AsyncMock(side_effect=[RuntimeError("lib1 boom"), ("success", "2 queued", 2)]),
        ) as mock_do,
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()),
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()),
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()),
    ):
        from backend.scanner._orchestrator import run_scan_libraries
        await run_scan_libraries(["lib1", "lib2"])

    assert mock_do.await_count == 2  # lib2 must still be attempted after lib1 raised


async def test_run_scan_libraries_cache_build_failure_still_runs_post_scan_hooks():
    """If building the shared caches fails, no library is scanned, but the
    post-scan hooks (sync/notify) must still run — the finally block."""
    with (
        patch("backend.scanner._orchestrator._build_shared_caches",
              new=AsyncMock(side_effect=RuntimeError("cache build failed"))),
        patch("backend.scanner._orchestrator._do_scan_one_library", new=AsyncMock()) as mock_do,
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()) as mock_sync,
        patch("backend.scanner._orchestrator._send_pending_notifications", new=AsyncMock()) as mock_notify,
        patch("backend.scanner._orchestrator.add_log", new=AsyncMock()) as mock_log,
    ):
        from backend.scanner._orchestrator import run_scan_libraries
        await run_scan_libraries(["lib1"])

    mock_do.assert_not_awaited()
    mock_sync.assert_awaited_once()
    mock_notify.assert_awaited_once()
    assert any(c.args[0] == "ERROR" for c in mock_log.await_args_list)


async def test_run_scan_libraries_lock_not_available_returns_silently():
    from backend.scanner._orchestrator import LockNotAvailable

    class _FakeLock:
        def locked(self):
            return False

        async def __aenter__(self):
            raise LockNotAvailable()

        async def __aexit__(self, *a):
            return False

    with (
        patch("backend.scanner._orchestrator._scan_lock", new=_FakeLock()),
        patch("backend.scanner._orchestrator.sync_emby_collection", new=AsyncMock()) as mock_sync,
    ):
        from backend.scanner._orchestrator import run_scan_libraries
        await run_scan_libraries(["lib1"])

    mock_sync.assert_not_awaited()
