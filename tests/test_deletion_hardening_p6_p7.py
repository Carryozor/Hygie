"""P6 — expert rules fail closed on unparsable data. P7 — scrobble never resurrects."""
import json

import pytest

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    path = str(tmp_path / "p6.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


GOOD = {"conditions": [{"field": "play_count", "op": "eq", "value": 0}], "operator": "AND"}


async def _insert_rule(name, groups, library_ids=None):
    from backend.db.engine import get_db
    async with get_db() as db:
        rid = await db.execute(
            "INSERT INTO expert_rules (name, library_id, library_ids, conditions, operator, action, "
            "grace_days, enabled, priority) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, None, library_ids, groups if isinstance(groups, str) else json.dumps(groups),
             "AND", "queue", 7, 1, 1),
        )
        await db.commit()
    return rid


def test_one_bad_group_invalidates_whole_rule():
    from backend.db.repositories import _parse_condition_groups
    raw = json.dumps([GOOD, {"conditions": [{"bogus": 1}], "operator": "AND"}])
    assert _parse_condition_groups(raw) == []


def test_all_good_groups_still_parse():
    from backend.db.repositories import _parse_condition_groups
    assert len(_parse_condition_groups(json.dumps([GOOD, GOOD]))) == 2


async def test_rule_with_bad_group_is_skipped_with_error_log(dbpath, caplog):
    from backend.db.repositories import get_expert_rules
    await _insert_rule("broken", [GOOD, {"conditions": [{"bogus": 1}]}])
    ok = await _insert_rule("ok", [GOOD])
    with caplog.at_level("ERROR"):
        rules = await get_expert_rules()
    assert [r.id for r in rules] == [ok]
    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_invalid_library_ids_json_skips_rule(dbpath):
    from backend.db.repositories import get_expert_rules, get_expert_rule_by_id
    bad = await _insert_rule("badlib", [GOOD], library_ids="{not json")
    good = await _insert_rule("goodlib", [GOOD], library_ids='["a","b"]')
    rules = await get_expert_rules()
    assert [r.id for r in rules] == [good] and rules[0].library_ids == ["a", "b"]
    assert await get_expert_rule_by_id(bad) is None


async def test_library_ids_not_a_list_skips_rule(dbpath):
    from backend.db.repositories import get_expert_rules
    await _insert_rule("weird", [GOOD], library_ids='"all"')
    assert await get_expert_rules() == []


async def test_null_library_ids_means_unrestricted(dbpath):
    from backend.db.repositories import get_expert_rules
    rid = await _insert_rule("free", [GOOD], library_ids=None)
    rules = await get_expert_rules()
    assert [r.id for r in rules] == [rid] and rules[0].library_ids is None


# ── P7 ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("pending", "pending"), ("error", "pending"), ("deleted", "deleted"), ("deleting", "deleting"),
])
async def test_scrobble_never_resurrects_deleted_or_deleting(dbpath, status, expected):
    from backend.db.engine import get_db
    from backend.db.repositories import update_last_played_scrobble
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES ('e1','T','Movie','l','L','/f','a','b',?)",
            (status,),
        )
        await db.commit()
    await update_last_played_scrobble("e1", "2026-05-01T00:00:00+00:00")
    async with get_db() as db:
        r = await db.fetch_one("SELECT status, last_played FROM media_queue WHERE emby_id='e1'")
    assert r["status"] == expected
    assert (r["last_played"] == "2026-05-01T00:00:00+00:00") is (status in ("pending", "error"))
