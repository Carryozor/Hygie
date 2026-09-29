"""Coverage-focused tests for backend/db/repositories.py.

tests/test_repositories.py already covers insert_queue_entry,
get_pending_queue, get_queued_and_ignored_ids, mark_notified_detected,
update_queue_status and get_enabled_libraries. This file covers the rest:
batch insert (+ rollback-on-failure), expert rules CRUD, and the large set of
media_queue read/update/delete helpers — each verified against real state in
a SQLite DB (row content, row count, or an explicit DB read), not exit codes.
"""
import json

import aiosqlite
import pytest
import pytest_asyncio

import backend.db.schema as _db_schema
import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
from backend.db.repositories import (
    insert_queue_entry,
    insert_queue_entries_batch,
    get_last_job_run,
    save_expert_rule,
    get_expert_rules,
    get_expert_rule_by_id,
    delete_expert_rule,
    get_by_id,
    get_by_emby_id,
    get_pending_item_by_emby_id,
    get_pending_by_library,
    get_all_emby_ids,
    get_queued_ids_for_server,
    get_status_counts,
    get_radarr_ids,
    get_sonarr_ids,
    get_pending_with_tmdb,
    get_pending_before,
    get_pending_before_for_server,
    get_all_for_enrichment,
    get_pending_for_poster_regen,
    get_poster_url_by_emby_id,
    reset_deleting_to_pending,
    claim_for_deletion,
    update_last_played_scrobble,
    update_queue_item_dates,
    update_activity_log_batch,
    update_consolidated_watch_state,
    update_poster,
    update_enrichment_fields,
    delete_by_id,
    delete_by_emby_id,
    delete_by_ids,
    purge_by_status,
    delete_stale_deleted,
    delete_pending_by_server,
    delete_stale_pending_no_seerr,
    _parse_condition_groups,
)
from backend.rules.models import ExpertRule, Condition, ConditionGroup, RuleOperator


def _entry(**overrides):
    base = {
        "emby_id": "e1", "title": "Test Movie", "media_type": "movie",
        "library_id": "lib1", "library_name": "Films",
        "file_path": "/srv/movies/test.mkv",
        "poster_url": "", "tmdb_id": "123",
        "seerr_id": None, "seerr_user_id": None, "seerr_username": None,
        "seerr_request_url": None, "radarr_id": None, "sonarr_id": None,
        "sonarr_series_id": None, "season_number": None,
        "detected_at": "2026-01-01T00:00:00+00:00",
        "delete_at": "2020-01-01T00:00:00+00:00",
        "added_date": None, "last_played": None,
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def db_path(monkeypatch, tmp_path):
    import backend.db.engine as _db_engine
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db_schema, "DB_PATH", path)
    monkeypatch.setattr(_db_utils, "DB_PATH", path)
    monkeypatch.setattr(_db_ss, "DB_PATH", path)
    monkeypatch.setattr(_db_engine, "SQLITE_PATH", path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


async def _one(db_path, sql, params=()):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchone()


async def _all(db_path, sql, params=()):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchall()


# ─── insert_queue_entries_batch ────────────────────────────────────────────────

async def test_insert_queue_entries_batch_inserts_all_rows(db_path):
    await insert_queue_entries_batch([_entry(emby_id="e1"), _entry(emby_id="e2")])
    rows = await _all(db_path, "SELECT emby_id FROM media_queue ORDER BY emby_id")
    assert [r[0] for r in rows] == ["e1", "e2"]


async def test_insert_queue_entries_batch_noop_on_empty_list(db_path):
    await insert_queue_entries_batch([])
    rows = await _all(db_path, "SELECT * FROM media_queue")
    assert rows == []


async def test_insert_queue_entries_batch_rolls_back_all_on_duplicate(db_path):
    await insert_queue_entry(_entry(emby_id="dup"))
    with pytest.raises(Exception):
        await insert_queue_entries_batch([_entry(emby_id="fresh"), _entry(emby_id="dup")])
    # atomicity: "fresh" must NOT have been left behind by the failed batch
    rows = await _all(db_path, "SELECT emby_id FROM media_queue")
    assert [r[0] for r in rows] == ["dup"]


async def test_insert_queue_entries_batch_logs_when_rollback_itself_fails(db_path, monkeypatch, caplog):
    """The original executemany failure must still propagate even when the
    best-effort ROLLBACK also errors (see the nested except at repositories.py)."""
    import backend.db.engine as engine
    real_execute = engine.DbConn.execute

    async def flaky_execute(self, sql, params=()):
        if sql == "ROLLBACK":
            raise RuntimeError("rollback boom")
        return await real_execute(self, sql, params)

    monkeypatch.setattr(engine.DbConn, "execute", flaky_execute)
    await insert_queue_entry(_entry(emby_id="dup"))
    with pytest.raises(Exception, match="UNIQUE|constraint"):
        await insert_queue_entries_batch([_entry(emby_id="dup")])
    assert any("rollback failed" in r.message for r in caplog.records)


# ─── get_last_job_run ──────────────────────────────────────────────────────────

async def test_get_last_job_run_returns_most_recent_finished_run(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "INSERT INTO job_history (job_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?)",
            [
                ("scan", "t0", "t1", "ok"),
                ("scan", "t2", "t3", "ok"),
                ("scan", "t4", None, None),  # still running — must be ignored
            ],
        )
        await db.commit()
    run = await get_last_job_run("scan")
    assert run["started_at"] == "t2"


async def test_get_last_job_run_returns_none_when_no_runs(db_path):
    assert await get_last_job_run("scan") is None


# ─── expert rules CRUD ──────────────────────────────────────────────────────────

def _rule(**overrides):
    base = dict(
        name="Old films", library_id=None,
        condition_groups=[ConditionGroup(conditions=[
            Condition(field="days_not_watched", op="gt", value=30)
        ])],
    )
    base.update(overrides)
    return ExpertRule(**base)


async def test_save_expert_rule_inserts_new_rule(db_path):
    rule_id = await save_expert_rule(_rule())
    row = await _one(db_path, "SELECT name FROM expert_rules WHERE id=?", (rule_id,))
    assert row[0] == "Old films"


async def test_save_expert_rule_updates_existing_rule(db_path):
    rule_id = await save_expert_rule(_rule())
    await save_expert_rule(_rule(id=rule_id, name="Renamed"))
    row = await _one(db_path, "SELECT name FROM expert_rules WHERE id=?", (rule_id,))
    assert row[0] == "Renamed"


async def test_save_expert_rule_update_raises_when_id_not_found(db_path):
    with pytest.raises(ValueError, match="not found"):
        await save_expert_rule(_rule(id=999999))


async def test_get_expert_rules_enabled_only_filters_disabled(db_path):
    await save_expert_rule(_rule(name="On", enabled=True))
    await save_expert_rule(_rule(name="Off", enabled=False))
    rules = await get_expert_rules(enabled_only=True)
    assert [r.name for r in rules] == ["On"]


async def test_get_expert_rules_skips_rows_with_no_valid_conditions(db_path):
    await save_expert_rule(_rule(name="Good"))
    # Hand-craft a row whose conditions JSON has no usable groups
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO expert_rules (name, conditions, operator, action, enabled, priority) "
            "VALUES ('Broken', '[]', 'AND', 'queue', 1, 0)"
        )
        await db.commit()
    rules = await get_expert_rules()
    assert [r.name for r in rules] == ["Good"]


async def test_get_expert_rule_by_id_returns_none_when_missing(db_path):
    assert await get_expert_rule_by_id(999999) is None


async def test_get_expert_rule_by_id_returns_none_for_invalid_conditions(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO expert_rules (id, name, conditions, operator, action, enabled, priority) "
            "VALUES (42, 'Broken', '[]', 'AND', 'queue', 1, 0)"
        )
        await db.commit()
    assert await get_expert_rule_by_id(42) is None


async def test_get_expert_rule_by_id_round_trip_with_library_ids(db_path):
    rule_id = await save_expert_rule(_rule(library_ids=["1", "2"]))
    fetched = await get_expert_rule_by_id(rule_id)
    assert fetched.library_ids == ["1", "2"]


async def test_get_expert_rules_tolerates_invalid_library_ids_json(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO expert_rules (name, library_ids, conditions, operator, action, enabled, priority) "
            "VALUES ('Bad libids', 'NOT-JSON{{', "
            "'[{\"conditions\":[{\"field\":\"play_count\",\"op\":\"eq\",\"value\":0}]}]', "
            "'AND', 'queue', 1, 0)"
        )
        await db.commit()
    rules = await get_expert_rules()
    assert len(rules) == 1
    assert rules[0].library_ids is None  # decode failure swallowed, stays None


async def test_get_expert_rule_by_id_tolerates_invalid_library_ids_json(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO expert_rules (id, name, library_ids, conditions, operator, action, enabled, priority) "
            "VALUES (7, 'Bad libids', 'NOT-JSON{{', "
            "'[{\"conditions\":[{\"field\":\"play_count\",\"op\":\"eq\",\"value\":0}]}]', "
            "'AND', 'queue', 1, 0)"
        )
        await db.commit()
    rule = await get_expert_rule_by_id(7)
    assert rule.library_ids is None


async def test_delete_expert_rule_removes_row(db_path):
    rule_id = await save_expert_rule(_rule())
    await delete_expert_rule(rule_id)
    assert await _one(db_path, "SELECT 1 FROM expert_rules WHERE id=?", (rule_id,)) is None


def test_parse_condition_groups_upgrades_legacy_flat_format():
    raw = json.dumps([{"field": "days_not_watched", "op": "gt", "value": 30}])
    groups = _parse_condition_groups(raw, "OR")
    assert len(groups) == 1
    assert groups[0].operator == RuleOperator.OR
    assert groups[0].conditions[0].field == "days_not_watched"


def test_parse_condition_groups_handles_malformed_json():
    assert _parse_condition_groups("not json", "AND") == []


def test_parse_condition_groups_empty_raw_returns_empty():
    assert _parse_condition_groups("", "AND") == []
    assert _parse_condition_groups("[]", "AND") == []


def test_parse_condition_groups_logs_and_skips_unparseable_flat_conditions():
    # 'op' value is not a valid ConditionOp -> Condition(**c) raises -> caught
    raw = json.dumps([{"field": "days_not_watched", "op": "nonsense", "value": 1}])
    assert _parse_condition_groups(raw, "AND") == []


def test_parse_condition_groups_new_groups_format():
    raw = json.dumps([
        {"operator": "OR", "conditions": [
            {"field": "play_count", "op": "eq", "value": 0}
        ]}
    ])
    groups = _parse_condition_groups(raw)
    assert len(groups) == 1
    assert groups[0].operator == RuleOperator.OR


def test_parse_condition_groups_skips_unparseable_group_but_keeps_others():
    raw = json.dumps([
        {"operator": "BOGUS", "conditions": [{"field": "play_count", "op": "eq", "value": 0}]},
        {"operator": "AND", "conditions": [{"field": "play_count", "op": "eq", "value": 1}]},
    ])
    groups = _parse_condition_groups(raw)
    assert len(groups) == 1
    assert groups[0].conditions[0].value == 1


# ─── media_queue reads ──────────────────────────────────────────────────────────

async def test_get_by_id_and_get_by_emby_id(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    row = await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'")
    item_id = row[0]
    assert (await get_by_id(item_id))["emby_id"] == "e1"
    assert (await get_by_id(999999)) is None
    assert (await get_by_emby_id("e1"))["id"] == item_id
    assert (await get_by_emby_id("missing")) is None


async def test_get_pending_item_by_emby_id(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item = await get_pending_item_by_emby_id("e1")
    assert item is not None and "delete_at" in item
    assert await get_pending_item_by_emby_id("missing") is None


async def test_get_pending_by_library(db_path):
    await insert_queue_entry(_entry(emby_id="e1", library_id="libA"))
    await insert_queue_entry(_entry(emby_id="e2", library_id="libB"))
    rows = await get_pending_by_library("libA")
    assert [r["emby_id"] for r in rows] == ["e1"]


async def test_get_all_emby_ids(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    await insert_queue_entry(_entry(emby_id="e2"))
    assert await get_all_emby_ids() == {"e1", "e2"}


async def test_get_queued_ids_for_server_filters_by_library_server(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            [("lib1", "L1", "lib1", "0"), ("lib2", "L2", "lib2", "1")],
        )
        await db.commit()
    await insert_queue_entry(_entry(emby_id="e1", library_id="lib1"))
    await insert_queue_entry(_entry(emby_id="e2", library_id="lib2"))
    assert await get_queued_ids_for_server("0") == {"e1"}
    assert await get_queued_ids_for_server("1") == {"e2"}


async def test_get_status_counts(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    await insert_queue_entry(_entry(emby_id="e2"))
    row = await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e2'")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET status='deleted' WHERE id=?", (row[0],))
        await db.commit()
    counts = await get_status_counts()
    assert counts == {"pending": 1, "deleted": 1}


async def test_get_radarr_and_sonarr_ids_filter_by_status_and_non_null(db_path):
    await insert_queue_entry(_entry(emby_id="e1", radarr_id=101, sonarr_id=None))
    await insert_queue_entry(_entry(emby_id="e2", radarr_id=None, sonarr_id=202))
    assert await get_radarr_ids() == [101]
    assert await get_sonarr_ids() == [202]
    assert await get_radarr_ids(status="deleted") == []


async def test_get_pending_with_tmdb_excludes_blank_tmdb(db_path):
    await insert_queue_entry(_entry(emby_id="e1", tmdb_id="123"))
    await insert_queue_entry(_entry(emby_id="e2", tmdb_id=""))
    rows = await get_pending_with_tmdb()
    assert [r["tmdb_id"] for r in rows] == ["123"]


async def test_get_pending_before_orders_ascending(db_path):
    await insert_queue_entry(_entry(emby_id="e1", delete_at="2020-01-02T00:00:00+00:00"))
    await insert_queue_entry(_entry(emby_id="e2", delete_at="2020-01-01T00:00:00+00:00"))
    rows = await get_pending_before("2020-06-01T00:00:00+00:00")
    assert [r["delete_at"] for r in rows] == sorted(r["delete_at"] for r in rows)
    assert [r["title"] for r in rows] == ["Test Movie", "Test Movie"]  # selected columns present


async def test_get_pending_before_for_server(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, enabled) "
            "VALUES ('lib1', 'L1', 'lib1', '0', 1)"
        )
        await db.commit()
    await insert_queue_entry(_entry(emby_id="e1", library_id="lib1"))
    rows = await get_pending_before_for_server("2020-06-01T00:00:00+00:00", "0")
    assert [r["emby_id"] for r in rows] == ["e1"]
    assert await get_pending_before_for_server("2020-06-01T00:00:00+00:00", "9") == []


async def test_get_all_for_enrichment_and_poster_regen(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    all_rows = await get_all_for_enrichment()
    assert [r["emby_id"] for r in all_rows] == ["e1"]
    regen_rows = await get_pending_for_poster_regen()
    assert [r["emby_id"] for r in regen_rows] == ["e1"]


async def test_get_poster_url_by_emby_id(db_path):
    await insert_queue_entry(_entry(emby_id="e1", poster_url="http://poster"))
    assert await get_poster_url_by_emby_id("e1") == "http://poster"
    assert await get_poster_url_by_emby_id("missing") is None


# ─── media_queue updates ────────────────────────────────────────────────────────

async def test_reset_deleting_to_pending_recovers_stuck_items(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET status='deleting'")
        await db.commit()
    n = await reset_deleting_to_pending()
    assert n == 1
    row = await _one(db_path, "SELECT status FROM media_queue WHERE emby_id='e1'")
    assert row[0] == "pending"


async def test_claim_for_deletion_transitions_pending_to_deleting(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    assert await claim_for_deletion(item_id) is True
    assert await claim_for_deletion(item_id) is False  # already claimed


async def test_update_last_played_scrobble_resets_status_to_pending(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET status='deleted' WHERE id=?", (item_id,))
        await db.commit()
    await update_last_played_scrobble("e1", "2026-01-02T00:00:00+00:00")
    row = await _one(db_path, "SELECT status, last_played FROM media_queue WHERE id=?", (item_id,))
    assert row == ("pending", "2026-01-02T00:00:00+00:00")


async def test_update_queue_item_dates(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    await update_queue_item_dates(item_id, "2030-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", 5)
    row = await _one(
        db_path, "SELECT delete_at, last_played, view_count FROM media_queue WHERE id=?", (item_id,)
    )
    assert row == ("2030-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", 5)


async def test_update_activity_log_batch_never_lowers_view_count(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET view_count=5, last_played='2020-01-01'")
        await db.commit()
    # guard param (3rd) must be > current last_played for the row to update at all
    await update_activity_log_batch([("2030-01-01", "e1", "2020-01-02")])
    row = await _one(db_path, "SELECT view_count, last_played FROM media_queue WHERE emby_id='e1'")
    assert row == (5, "2030-01-01")  # view_count raised to 1 -> stays at 5 (already higher)


async def test_update_activity_log_batch_noop_on_empty_params(db_path):
    await update_activity_log_batch([])  # must not raise


async def test_update_consolidated_watch_state_raises_view_count_to_given_value(db_path):
    await insert_queue_entry(_entry(emby_id="sonarr-series:1"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET view_count=1, last_played=''")
        await db.commit()
    await update_consolidated_watch_state([("2030-01-01", 10, "sonarr-series:1", "")])
    row = await _one(
        db_path, "SELECT view_count, last_played FROM media_queue WHERE emby_id='sonarr-series:1'"
    )
    assert row == (10, "2030-01-01")


async def test_update_consolidated_watch_state_noop_on_empty_params(db_path):
    await update_consolidated_watch_state([])  # must not raise


async def test_update_poster(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    await update_poster(item_id, "http://new-poster")
    row = await _one(db_path, "SELECT poster_url FROM media_queue WHERE id=?", (item_id,))
    assert row[0] == "http://new-poster"


async def test_update_enrichment_fields_only_touches_allowed_columns(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    await update_enrichment_fields(item_id, {
        "poster_url": "http://p", "seerr_id": 5, "title": "should be ignored",
    })
    row = await _one(
        db_path, "SELECT poster_url, seerr_id, title FROM media_queue WHERE id=?", (item_id,)
    )
    assert row == ("http://p", 5, "Test Movie")  # title untouched — not in _ENRICH_ALLOWED


async def test_update_enrichment_fields_noop_when_nothing_allowed(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    await update_enrichment_fields(item_id, {"title": "ignored"})  # early return, no crash
    row = await _one(db_path, "SELECT title FROM media_queue WHERE id=?", (item_id,))
    assert row[0] == "Test Movie"


# ─── media_queue deletes ────────────────────────────────────────────────────────

async def test_delete_by_id(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    await delete_by_id(item_id)
    assert await _one(db_path, "SELECT 1 FROM media_queue WHERE id=?", (item_id,)) is None


async def test_delete_by_emby_id(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    await delete_by_emby_id("e1")
    assert await _one(db_path, "SELECT 1 FROM media_queue WHERE emby_id='e1'") is None


async def test_delete_by_ids_multiple(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    await insert_queue_entry(_entry(emby_id="e2"))
    await insert_queue_entry(_entry(emby_id="e3"))
    ids = [r[0] for r in await _all(db_path, "SELECT id FROM media_queue WHERE emby_id IN ('e1','e2')")]
    await delete_by_ids(ids)
    remaining = {r[0] for r in await _all(db_path, "SELECT emby_id FROM media_queue")}
    assert remaining == {"e3"}


async def test_delete_by_ids_noop_on_empty_list(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    await delete_by_ids([])
    assert len(await _all(db_path, "SELECT * FROM media_queue")) == 1


async def test_purge_by_status_returns_count_and_deletes(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    item_id = (await _one(db_path, "SELECT id FROM media_queue WHERE emby_id='e1'"))[0]
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET status='deleted' WHERE id=?", (item_id,))
        await db.commit()
    n = await purge_by_status("deleted")
    assert n == 1
    assert await _one(db_path, "SELECT 1 FROM media_queue WHERE id=?", (item_id,)) is None


async def test_purge_by_status_returns_zero_when_none_match(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    assert await purge_by_status("deleted") == 0
    assert len(await _all(db_path, "SELECT * FROM media_queue")) == 1


async def test_delete_stale_deleted_only_removes_old_enough_rows(db_path):
    await insert_queue_entry(_entry(emby_id="old", detected_at="2020-01-01T00:00:00+00:00"))
    await insert_queue_entry(_entry(emby_id="new", detected_at="2030-01-01T00:00:00+00:00"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE media_queue SET status='deleted'")
        await db.commit()
    n = await delete_stale_deleted("2025-01-01T00:00:00+00:00")
    assert n == 1
    remaining = {r[0] for r in await _all(db_path, "SELECT emby_id FROM media_queue")}
    assert remaining == {"new"}


async def test_delete_stale_deleted_returns_zero_when_nothing_qualifies(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))  # status stays 'pending'
    assert await delete_stale_deleted("2099-01-01T00:00:00+00:00") == 0


async def test_delete_stale_pending_no_seerr_removes_only_matching_rows(db_path):
    await insert_queue_entry(_entry(emby_id="no-seerr", library_id="libA", seerr_user_id=None))
    await insert_queue_entry(_entry(emby_id="has-seerr", library_id="libA", seerr_user_id=7))
    n = await delete_stale_pending_no_seerr(["libA"])
    assert n == 1
    remaining = {r[0] for r in await _all(db_path, "SELECT emby_id FROM media_queue")}
    assert remaining == {"has-seerr"}


async def test_delete_stale_pending_no_seerr_noop_on_empty_library_list(db_path):
    await insert_queue_entry(_entry(emby_id="e1"))
    n = await delete_stale_pending_no_seerr([])
    assert n == 0
    assert len(await _all(db_path, "SELECT * FROM media_queue")) == 1


async def test_delete_pending_by_server_removes_matching_and_returns_count(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "INSERT INTO libraries (id, name, emby_library_id, server_id, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            [("lib1", "L1", "lib1", "0"), ("lib2", "L2", "lib2", "1")],
        )
        await db.commit()
    await insert_queue_entry(_entry(emby_id="e1", library_id="lib1"))
    await insert_queue_entry(_entry(emby_id="e2", library_id="lib2"))
    n = await delete_pending_by_server("0")
    assert n == 1
    remaining = {r[0] for r in await _all(db_path, "SELECT emby_id FROM media_queue")}
    assert remaining == {"e2"}


async def test_delete_pending_by_server_returns_zero_when_no_matching_rows(db_path):
    assert await delete_pending_by_server("nope") == 0
