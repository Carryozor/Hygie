"""SQLite DDL aligned on MariaDB (prod = truth): behaviour of fresh AND legacy DBs.

Covers: expert_rules.library_id TEXT, nullable created_at/sent_at,
media_queue.plex_rating_key DEFAULT NULL, idx_plex_overlays_server creation.
Legacy DBs (created with the old DDL) must still boot and behave consistently;
no table rebuild is performed on them.
"""
import sqlite3

import pytest

import backend.db.engine as engine
from backend.db import schema
from backend.rules.models import ConditionGroup, ExpertRule

OLD_EXPERT_RULES = """CREATE TABLE expert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    library_id  INTEGER,
    library_ids TEXT,
    conditions  TEXT    NOT NULL DEFAULT '[]',
    operator    TEXT    NOT NULL DEFAULT 'AND',
    action      TEXT    NOT NULL DEFAULT 'queue',
    grace_days  INTEGER NOT NULL DEFAULT 7,
    enabled     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
)"""
OLD_NOTIFICATIONS = """CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    INTEGER NOT NULL REFERENCES media_queue(id) ON DELETE CASCADE,
    threshold   TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (media_id, threshold)
)"""


@pytest.fixture
def sqlite_path(tmp_path, monkeypatch):
    path = str(tmp_path / "align.db")
    monkeypatch.setattr(engine, "DIALECT", "sqlite")
    monkeypatch.setattr(engine, "SQLITE_PATH", path)
    import backend.db.utils as db_utils
    monkeypatch.setattr(db_utils, "DB_PATH", path)
    monkeypatch.setattr(schema, "DB_PATH", path, raising=False)
    import backend.db.settings_store as ss
    ss._settings_cache.clear()
    ss._settings_cache_ts = 0.0
    return path


def _create_sql(table: str) -> str:
    return next(sql for name, sql, _ in schema._TABLES if name == table)


def _make_legacy_db(path: str) -> None:
    """DB as created before the alignment: old DDL for the 3 changed tables."""
    new_mq = _create_sql("media_queue")
    old_mq = new_mq.replace("plex_rating_key TEXT DEFAULT NULL", "plex_rating_key TEXT DEFAULT ''")
    assert old_mq != new_mq, "fixture must actually differ from the new DDL"
    conn = sqlite3.connect(path)
    conn.execute(old_mq)
    conn.execute(OLD_EXPERT_RULES)
    conn.execute(OLD_NOTIFICATIONS)
    conn.commit()
    conn.close()


def _info(path: str, table: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _index_names(path: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    finally:
        conn.close()


async def _init():
    await engine.init_db_pool()
    await schema.init_db()


# -- Fresh DB ---------------------------------------------------------------
async def test_fresh_db_matches_mariadb_shape(sqlite_path):
    await _init()
    assert _info(sqlite_path, "expert_rules")["library_id"][2] == "TEXT"
    assert _info(sqlite_path, "expert_rules")["created_at"][3] == 0  # notnull flag
    assert _info(sqlite_path, "notifications")["sent_at"][3] == 0
    assert _info(sqlite_path, "media_queue")["plex_rating_key"][4] == "NULL"


async def test_fresh_db_creates_every_declared_index(sqlite_path):
    from tests.schema_introspect import _parse_index
    await _init()
    declared = {_parse_index(sql)[0] for sql in schema._SQLITE_INDEXES}
    assert "idx_plex_overlays_server" in declared
    assert declared <= _index_names(sqlite_path)


async def test_init_db_is_idempotent_for_indexes(sqlite_path):
    await _init()
    first = _index_names(sqlite_path)
    await _init()
    assert _index_names(sqlite_path) == first


# -- Legacy DB (old DDL) ----------------------------------------------------
async def test_legacy_db_boots_and_gets_missing_index(sqlite_path):
    _make_legacy_db(sqlite_path)
    assert "idx_plex_overlays_server" not in _index_names(sqlite_path)
    await _init()
    assert "idx_plex_overlays_server" in _index_names(sqlite_path)
    # no table rebuild: legacy DDL is left as-is
    assert _info(sqlite_path, "expert_rules")["library_id"][2] == "INTEGER"


def _rule(library_id):
    return ExpertRule(
        name="r", library_id=library_id,
        condition_groups=[ConditionGroup(conditions=[
            {"field": "days_not_watched", "op": "gt", "value": 30}])],
    )


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "legacy-old-ddl"])
@pytest.mark.parametrize("lid", ["12", "abc-def"])
async def test_expert_rule_library_id_is_consistent_via_str(sqlite_path, legacy, lid):
    """The code base compares str(rule.library_id) with str(library id): that must
    hold on both DDL flavours (INTEGER affinity coerces '12' -> 12 on legacy)."""
    from backend.db.repositories import get_expert_rules, save_expert_rule
    if legacy:
        _make_legacy_db(sqlite_path)
    await _init()
    await save_expert_rule(_rule(lid))
    rules = await get_expert_rules()
    assert len(rules) == 1
    assert str(rules[0].library_id) == lid


async def test_fresh_db_keeps_library_id_text_verbatim(sqlite_path):
    """TEXT affinity: a numeric-looking id with a leading zero is not coerced."""
    from backend.db.repositories import get_expert_rules, save_expert_rule
    await _init()
    await save_expert_rule(_rule("0012"))
    assert (await get_expert_rules())[0].library_id == "0012"


async def test_expert_rule_created_at_still_defaults_on_insert(sqlite_path):
    """Nullable column keeps its strftime default when the app omits it."""
    from backend.db.repositories import get_expert_rules, save_expert_rule
    await _init()
    await save_expert_rule(_rule("1"))
    assert (await get_expert_rules())[0].created_at


# -- plex_rating_key NULL is handled by the application ---------------------
def _insert_media(path, emby_id, **extra):
    cols = {"emby_id": emby_id, "title": "t", "media_type": "movie", "library_id": "L1",
            "library_name": "L", "file_path": "/x", "detected_at": "d", "delete_at": "d"}
    cols.update(extra)
    conn = sqlite3.connect(path)
    conn.execute(
        f"INSERT INTO media_queue ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(cols.values()))
    conn.commit()
    conn.close()


async def test_plex_rating_key_defaults_to_null_on_fresh_db(sqlite_path):
    await _init()
    _insert_media(sqlite_path, "e1")
    conn = sqlite3.connect(sqlite_path)
    (val,) = conn.execute("SELECT plex_rating_key FROM media_queue").fetchone()
    conn.close()
    assert val is None


async def test_plex_overlay_query_excludes_null_and_empty_keeps_set(sqlite_path):
    """The exact predicate of plex_collection.sync_plex_overlays."""
    from backend.db.engine import get_db
    await _init()
    _insert_media(sqlite_path, "null-key")                       # NULL
    _insert_media(sqlite_path, "empty-key", plex_rating_key="")  # legacy ''
    _insert_media(sqlite_path, "real-key", plex_rating_key="999")
    async with get_db() as db:
        rows = await db.fetch_all(
            "SELECT emby_id FROM media_queue WHERE library_id IN (?) AND plex_rating_key != ''",
            ("L1",))
    assert {r["emby_id"] for r in rows} == {"real-key"}


def test_get_server_item_id_falls_back_for_null_and_empty():
    from backend.media_server_factory import get_server_item_id
    plex = {"type": "plex"}
    for key in (None, ""):
        assert get_server_item_id(plex, {"plex_rating_key": key, "emby_id": "E"}) == "E"
    assert get_server_item_id(plex, {"plex_rating_key": "42", "emby_id": "E"}) == "42"
