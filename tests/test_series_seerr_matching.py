"""Series ↔ Seerr matching — episodes must resolve the SERIES-level TMDB id.

Bug: the scanner looked up seerr_user_id via item.ProviderIds.Tmdb, but Emby
Episode items carry no series-level Tmdb id (only episode Tvdb/Imdb ids).
The Seerr request cache is keyed by the series tmdbId, so seerr_user_id was
always None for episodes → any rule or filter requiring a Seerr user silently
excluded ALL series.

Uses real SQLite (via tmp_path), mocks Emby client and external services.
Mirrors the pattern from test_scan_integration.py.
"""
import json
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.media_servers as _db_ms
import backend.db.schema as _db_schema
from backend.db.schema import init_db
from backend.db.repositories import save_expert_rule
from backend.rules.models import (
    ExpertRule, Condition, ConditionGroup, ConditionField, ConditionOp, RuleAction, RuleOperator,
)

SERIES_EMBY_ID = "series-45457"
SERIES_TMDB_ID = "300054"
SEERR_CACHE = {SERIES_TMDB_ID: {"seerr_id": 545, "user_id": 4, "username": "Blork"}}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    import backend.db.engine as _db_engine
    db_path = str(tmp_path / "series_seerr_test.db")
    monkeypatch.setattr(_db_utils, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ss, "DB_PATH", db_path)
    monkeypatch.setattr(_db_ms, "DB_PATH", db_path)
    monkeypatch.setattr(_db_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", db_path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    _db_ms._ms_cache = None
    _db_ms._ms_cache_ts = 0.0
    await init_db()

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


async def _create_library(db_path: str, *, conditions: list, seerr_conditions: list) -> str:
    lib_id = str(uuid.uuid4())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO libraries
               (id, name, emby_library_id, conditions, logic, grace_days,
                seerr_conditions, enabled, deletion_unit, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lib_id, "Animés Test", "emby-lib-anime",
                json.dumps(conditions), "AND", 7, json.dumps(seerr_conditions),
                1, "episode",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return lib_id


def _make_old_unwatched_episode() -> dict:
    """Fake Emby Episode: added 105 days ago, never watched, NO series Tmdb id.

    Mirrors real Emby data — episodes carry episode-level Tvdb/Imdb ids only;
    the series tmdb id lives on the parent Series item (SeriesId).
    """
    added = (datetime.now(timezone.utc) - timedelta(days=105)).isoformat()
    return {
        "Id": "emby-ep-001",
        "Name": "The Unstoppable Yawn",
        "Type": "Episode",
        "SeriesId": SERIES_EMBY_ID,
        "SeriesName": "BAKI-DOU",
        "DateCreated": added,
        "UserData": {"PlayCount": 0, "LastPlayedDate": None},
        "Path": "/media/animes/BAKI-DOU/S01E01.mkv",
        "ImageTags": {},
        "ProviderIds": {"Tvdb": "11322971", "Imdb": "tt38230794"},
    }


def _scan_patches(emby_items: list):
    """Stub all external calls for run_scan()."""
    stack = ExitStack()
    stack.enter_context(patch(
        "backend.scanner._emby_scanner.get_items_in_library",
        new_callable=AsyncMock,
        return_value=(emby_items, len(emby_items)),
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.get_users",
        new_callable=AsyncMock,
        return_value=[],
    ))
    stack.enter_context(patch(
        "backend.scanner._emby_scanner.get_library_user_data",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_radarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_sonarr_path_cache",
        new_callable=AsyncMock,
        return_value={},
    ))
    stack.enter_context(patch(
        "backend.scanner._orchestrator.build_seerr_request_cache",
        new_callable=AsyncMock,
        return_value=dict(SEERR_CACHE),
    ))
    stack.enter_context(patch("backend.scanner._orchestrator.sync_emby_collection", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner._queue_entry.send_notification", new_callable=AsyncMock))
    stack.enter_context(patch("backend.scanner._orchestrator._send_pending_notifications", new_callable=AsyncMock))
    stack.enter_context(patch(
        "backend.emby_client.get_client",
        new_callable=AsyncMock,
        return_value=("http://emby:8096", "apikey"),
    ))
    stack.enter_context(patch(
        "backend.emby_client.get_client_ext_url",
        new_callable=AsyncMock,
        return_value="",
    ))
    # Series-level tmdb resolution — present only once the fix is in place.
    # On unfixed code the patch target does not exist; skip it so the test
    # fails on the bug itself (empty queue), not on an AttributeError.
    try:
        stack.enter_context(patch(
            "backend.scanner._emby_scanner.get_series_tmdb_map",
            new_callable=AsyncMock,
            return_value={SERIES_EMBY_ID: SERIES_TMDB_ID},
        ))
    except AttributeError:
        pass
    return stack


async def _pending_rows(db_path: str) -> list:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM media_queue WHERE status='pending'") as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Unit: resolve_item_tmdb ──────────────────────────────────────────────────

def test_resolve_item_tmdb_episode_uses_series_map():
    from backend.emby_client import resolve_item_tmdb
    ep = _make_old_unwatched_episode()
    assert resolve_item_tmdb(ep, {SERIES_EMBY_ID: SERIES_TMDB_ID}) == SERIES_TMDB_ID


def test_resolve_item_tmdb_movie_uses_own_provider_id():
    from backend.emby_client import resolve_item_tmdb
    movie = {"Type": "Movie", "ProviderIds": {"Tmdb": "1226863"}}
    assert resolve_item_tmdb(movie, {}) == "1226863"


def test_resolve_item_tmdb_episode_missing_from_map_falls_back_empty():
    from backend.emby_client import resolve_item_tmdb
    ep = _make_old_unwatched_episode()
    assert resolve_item_tmdb(ep, {}) == ""
    assert resolve_item_tmdb(ep, None) == ""


# ─── Integration: expert rule with seerr_user_id filter ───────────────────────

async def test_expert_rule_seerr_filter_matches_episode(isolated_db):
    """(added>90 OR not-watched>45) AND seerr_user_id IN [4] must queue an old
    unwatched episode whose SERIES was requested by Seerr user 4."""
    await _create_library(
        isolated_db,
        conditions=[{"field": "days_since_added", "op": "gt", "value": 9999}],
        seerr_conditions=[],
    )
    rule = ExpertRule(
        name="Séries Seerr",
        condition_groups=[
            ConditionGroup(
                conditions=[
                    Condition(field=ConditionField.ADDED_DAYS_AGO, op=ConditionOp.GT, value=90),
                    Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=45),
                ],
                operator=RuleOperator.OR,
            ),
            ConditionGroup(
                conditions=[
                    Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=[4]),
                ],
                operator=RuleOperator.AND,
            ),
        ],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=True,
        priority=0,
    )
    await save_expert_rule(rule)

    with _scan_patches([_make_old_unwatched_episode()]):
        from backend.scanner import run_scan
        await run_scan()

    rows = await _pending_rows(isolated_db)
    assert len(rows) == 1, f"Episode should be queued via series-level Seerr match, got {len(rows)}"
    assert rows[0]["emby_id"] == "emby-ep-001"
    assert rows[0]["seerr_user_id"] == 4
    assert rows[0]["seerr_username"] == "Blork"
    assert rows[0]["tmdb_id"] == SERIES_TMDB_ID


# ─── Integration: legacy library seerr_conditions user_include ────────────────

async def test_legacy_seerr_include_matches_episode(isolated_db):
    """Library conditions + seerr user_include filter must accept an episode
    whose SERIES was requested by an included Seerr user."""
    await _create_library(
        isolated_db,
        conditions=[
            {"field": "days_since_added", "op": "gt", "value": 90},
            {"field": "days_not_watched", "op": "gt", "value": 45},
        ],
        seerr_conditions=[{"type": "user_include", "user_id": 4, "username": "Blork"}],
    )

    with _scan_patches([_make_old_unwatched_episode()]):
        from backend.scanner import run_scan
        await run_scan()

    rows = await _pending_rows(isolated_db)
    assert len(rows) == 1, f"Episode should pass legacy Seerr include filter, got {len(rows)}"
    assert rows[0]["emby_id"] == "emby-ep-001"
    assert rows[0]["seerr_user_id"] == 4
