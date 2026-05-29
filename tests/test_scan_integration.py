"""Integration tests for the scan pipeline — expert rules evaluation.

Uses real SQLite (via tmp_path), mocks Emby client and external services.
Mirrors the pattern from test_e2e_scan_queue_delete.py.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
import backend.db.logs as _db_logs
from backend.db.schema import init_db
from backend.db.repositories import save_expert_rule
from backend.rules.models import (
    ExpertRule, Condition, ConditionField, ConditionOp, RuleAction, RuleOperator,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "integration_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_logs, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    await init_db()

    import backend.scanner as scanner_mod
    import backend.conditions as cond_mod
    import backend.notifications as notif_mod
    monkeypatch.setattr(scanner_mod, "DB_PATH", db_path)
    monkeypatch.setattr(cond_mod, "DB_PATH", db_path)
    monkeypatch.setattr(notif_mod, "DB_PATH", db_path)

    settings = {
        "dry_run": "false",
        "discord_alert_scan_failure": "false",
        "discord_alert_seerr_failure": "false",
        "discord_alert_error_threshold": "0",
        "max_parallel_library_scans": "1",
        "emby_leaving_soon_overlay": "false",
        "ui_language": "fr",
        "media_servers": "[]",
        "media_server_type": "emby",
    }
    async with aiosqlite.connect(db_path) as db:
        for k, v in settings.items():
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        await db.commit()

    yield db_path


async def _create_library_no_match(db_path: str) -> str:
    """Insert a library whose conditions will never match (requires 9999 days_since_added)."""
    lib_id = str(uuid.uuid4())
    # Use an impossible condition so library rules never trigger
    conditions = json.dumps([{"field": "days_since_added", "op": "gt", "value": 9999}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id, "Expert Test Library", "emby-lib-999",
                conditions, "AND", 7, "[]", 1, "episode",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return lib_id


def _make_unwatched_item() -> dict:
    """Return a fake Emby item with play_count=0, added 2 days ago."""
    added = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    return {
        "Id": "emby-expert-001",
        "Name": "Never Watched Movie",
        "Type": "Movie",
        "DateCreated": added,
        "UserData": {"PlayCount": 0, "LastPlayedDate": None},
        "Path": "/media/movies/never_watched.mkv",
        "ImageTags": {},
        "ProviderIds": {},
    }


def _expert_scan_patches(emby_items: list):
    """Stub all external calls for run_scan(), mirroring _scan_patches from e2e test."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch(
        "backend.scanner.get_items_in_library",
        new_callable=AsyncMock,
        return_value=(emby_items, len(emby_items)),
    ))
    stack.enter_context(patch(
        "backend.scanner.get_users",
        new_callable=AsyncMock,
        return_value=[],  # no users — play_count stays 0
    ))
    stack.enter_context(patch(
        "backend.scanner.get_library_user_data",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner.build_radarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner.build_sonarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner.build_seerr_request_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch("backend.scanner.sync_emby_collection", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner.send_notification", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner._send_pending_notifications", new_callable=AsyncMock))
    stack.enter_context(patch(
        "backend.conditions.get_client",
        new_callable=AsyncMock,
        return_value=("http://emby:8096", "apikey"),
    ))
    stack.enter_context(patch(
        "backend.conditions.get_client_ext_url",
        new_callable=AsyncMock,
        return_value="",
    ))
    return stack


# ─── Test 1: expert rule queues an unwatched item ─────────────────────────────

async def test_expert_rule_queues_unwatched(isolated_db):
    """Expert rule play_count EQ 0 → action=queue should queue an item
    that the library conditions do NOT match."""
    await _create_library_no_match(isolated_db)

    rule = ExpertRule(
        name="Queue unwatched",
        conditions=[Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=0)],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=True,
        priority=0,
    )
    await save_expert_rule(rule)

    emby_items = [_make_unwatched_item()]

    with _expert_scan_patches(emby_items):
        from backend.scanner import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM media_queue WHERE status='pending'") as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    assert len(rows) == 1, f"Expected 1 queued item via expert rule, got {len(rows)}"
    assert rows[0]["emby_id"] == "emby-expert-001"
    assert rows[0]["title"] == "Never Watched Movie"


# ─── Test 2: disabled expert rule does not queue item ─────────────────────────

async def test_disabled_expert_rule_skipped(isolated_db):
    """A disabled expert rule must not queue items."""
    await _create_library_no_match(isolated_db)

    rule = ExpertRule(
        name="Disabled rule",
        conditions=[Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=0)],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=False,
        priority=0,
    )
    await save_expert_rule(rule)

    emby_items = [_make_unwatched_item()]

    with _expert_scan_patches(emby_items):
        from backend.scanner import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT COUNT(*) FROM media_queue") as cur:
            count = (await cur.fetchone())[0]

    assert count == 0, f"Disabled expert rule must not queue items, got {count}"


# ─── Test 3: expert rule with action=notify_only does not queue item ──────────

async def test_expert_rule_notify_only_does_not_queue(isolated_db):
    """Expert rule with action=notify_only must not insert into media_queue."""
    await _create_library_no_match(isolated_db)

    rule = ExpertRule(
        name="Notify only rule",
        conditions=[Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=0)],
        operator=RuleOperator.AND,
        action=RuleAction.NOTIFY_ONLY,
        enabled=True,
        priority=0,
    )
    await save_expert_rule(rule)

    emby_items = [_make_unwatched_item()]

    with _expert_scan_patches(emby_items):
        from backend.scanner import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT COUNT(*) FROM media_queue") as cur:
            count = (await cur.fetchone())[0]

    assert count == 0, f"notify_only rule must not add to media_queue, got {count}"


# ─── Test 4: expert rule does not double-queue item already queued by library ─

async def test_expert_rule_does_not_double_queue(isolated_db):
    """If library conditions already queue an item, expert rule must not add it again."""
    lib_id = str(uuid.uuid4())
    # This library WILL match (days_since_added > 1, item is 2 days old)
    conditions = json.dumps([{"field": "days_since_added", "op": "gt", "value": 1}])
    async with aiosqlite.connect(isolated_db) as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id, "Matching Library", "emby-lib-match",
                conditions, "AND", 7, "[]", 1, "episode",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    rule = ExpertRule(
        name="Also queue unwatched",
        conditions=[Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=0)],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=True,
        priority=0,
    )
    await save_expert_rule(rule)

    emby_items = [_make_unwatched_item()]

    with _expert_scan_patches(emby_items):
        from backend.scanner import run_scan
        await run_scan()

    async with aiosqlite.connect(isolated_db) as db:
        async with db.execute("SELECT COUNT(*) FROM media_queue WHERE emby_id='emby-expert-001'") as cur:
            count = (await cur.fetchone())[0]

    assert count == 1, f"Item must be queued exactly once, got {count}"
