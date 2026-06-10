"""MariaDB upsert syntax translation.

SQLite uses `INSERT OR REPLACE` / `INSERT OR IGNORE`; MariaDB rejects both
(ERROR 1064). DbConn must translate them to `REPLACE` / `INSERT IGNORE` so
the shared runtime write paths (set_setting, save_media_servers, ignore
media, notifications) work on MariaDB.

The unit tests run everywhere. The integration test runs only when a live
MariaDB is reachable via TEST_MARIADB_URL (set in CI / locally).
"""
import os
import pytest

os.environ.setdefault("DB_PATH", ":memory:")


# ─── Unit: statement translation ──────────────────────────────────────────────

def _mariadb_conn():
    from backend.db.engine import DbConn
    return DbConn(None, "mariadb")


def test_insert_or_replace_becomes_replace():
    sql = _mariadb_conn()._q("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)")
    assert "OR REPLACE" not in sql
    assert sql.startswith("REPLACE INTO settings")
    assert "%s" in sql


def test_insert_or_ignore_becomes_insert_ignore():
    sql = _mariadb_conn()._q("INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?, ?)")
    assert "OR IGNORE" not in sql
    assert sql.startswith("INSERT IGNORE INTO notifications")


def test_translation_is_case_and_whitespace_tolerant():
    sql = _mariadb_conn()._q("  insert  or  ignore  into t (a) values (?)")
    assert "or ignore" not in sql.lower()
    assert "ignore" in sql.lower()


def test_sqlite_dialect_leaves_upsert_untouched():
    from backend.db.engine import DbConn
    sql = DbConn(None, "sqlite")._q("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)")
    assert sql == "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)"


def test_literal_containing_or_replace_is_not_mangled():
    # A string literal that happens to contain the keywords must survive.
    sql = _mariadb_conn()._q("INSERT INTO t (msg) VALUES ('INSERT OR REPLACE demo')")
    assert "'INSERT OR REPLACE demo'" in sql


# ─── Integration: live MariaDB write round-trip ───────────────────────────────

_MARIADB_URL = os.environ.get("TEST_MARIADB_URL", "").strip()


@pytest.mark.skipif(not _MARIADB_URL, reason="TEST_MARIADB_URL not set — no live MariaDB")
@pytest.mark.asyncio
async def test_live_mariadb_upsert_round_trip(monkeypatch):
    """set_setting (INSERT OR REPLACE) and a notification (INSERT OR IGNORE)
    must succeed against a real MariaDB."""
    import importlib
    import backend.db.engine as eng

    monkeypatch.setenv("DATABASE_URL", _MARIADB_URL)
    importlib.reload(eng)
    assert eng.DIALECT == "mariadb"
    await eng.init_db_pool()
    try:
        from backend.db.schema import init_db
        await init_db()

        import backend.db.settings_store as ss
        importlib.reload(ss)
        ss._settings_cache.clear()
        ss._settings_cache_ts = 0.0

        # INSERT OR REPLACE path
        await ss.set_setting("ui_language", "es")
        ss._settings_cache.clear()
        ss._settings_cache_ts = 0.0
        assert await ss.get_setting("ui_language") == "es"

        # INSERT OR IGNORE path (notifications) — should not raise
        async with eng.get_db() as db:
            await db.execute(
                "INSERT INTO media_queue "
                "(emby_id, title, media_type, library_id, library_name, file_path, "
                " detected_at, delete_at, status) "
                "VALUES ('upsert-it','T','Movie','','','', "
                "'2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00','pending')"
            )
            await db.commit()
            row = await db.fetch_one("SELECT id FROM media_queue WHERE emby_id='upsert-it'")
            mid = row["id"]
            await db.execute(
                "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?, ?)",
                (mid, "now"),
            )
            await db.execute(
                "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?, ?)",
                (mid, "now"),  # duplicate — must be ignored, not error
            )
            await db.commit()
            cnt = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM notifications WHERE media_id=?", (mid,)
            )
            assert cnt["n"] == 1
    finally:
        await eng.close_db_pool()


def test_literal_percent_escaped_for_mariadb():
    """Literal % (LIKE wildcards) must become %% so aiomysql's query%%args
    formatting doesn't choke; generated %s placeholders stay single."""
    sql = _mariadb_conn()._q("DELETE FROM logs WHERE message LIKE 'prefix%' OR id=?")
    assert "LIKE 'prefix%%'" in sql
    assert "id=%s" in sql
    assert "%%s" not in sql  # the real placeholder must not be doubled


def test_like_escape_pattern_param_unaffected(_=None):
    # `?` placeholder for a LIKE param: the % lives in the bound value, not the SQL
    sql = _mariadb_conn()._q("SELECT * FROM t WHERE name LIKE ? ESCAPE '!'")
    assert "LIKE %s ESCAPE '!'" in sql
