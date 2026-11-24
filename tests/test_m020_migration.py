"""m020: TOTP columns + mfa_backup_codes / mfa_challenges, both dialects."""
import aiosqlite

import backend.db.migrations as mig
from tests.test_cov_dbauth_migrations import _simulate_mariadb


async def test_m020_sqlite_idempotent(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _s
    import backend.db.schema as _sc
    path = str(tmp_path / "m020.db")
    for mod, attr in ((_u, "DB_PATH"), (_s, "DB_PATH"), (_sc, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        await db.execute("INSERT INTO users (id, username) VALUES (1, 'a')")
        await db.commit()
    await mig._m020_mfa_schema()
    await mig._m020_mfa_schema()
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT totp_secret, totp_enabled, totp_last_step, totp_enabled_at FROM users"
        ) as cur:
            assert await cur.fetchone() == (None, 0, None, None)
        for t in ("mfa_backup_codes", "mfa_challenges"):
            async with db.execute(f"SELECT COUNT(*) FROM {t}") as cur:
                assert (await cur.fetchone())[0] == 0
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mfa_%'"
        ) as cur:
            assert {r[0] for r in await cur.fetchall()} == {"idx_mfa_bc_user", "idx_mfa_ch_user"}


async def test_m020_mariadb_emits_alters_and_creates(monkeypatch):
    conn = _simulate_mariadb(monkeypatch, existing_tables=("users",), existing_cols={"users": {"id"}})
    await mig._m020_mfa_schema()
    sql = " ".join(conn.executed)
    for col in ("totp_secret TEXT", "totp_enabled TINYINT", "totp_last_step BIGINT",
                "totp_enabled_at VARCHAR(32)"):
        assert f"ADD COLUMN {col}" in sql
    assert "CREATE TABLE IF NOT EXISTS mfa_backup_codes" in sql
    assert "CREATE TABLE IF NOT EXISTS mfa_challenges" in sql
    assert "CREATE INDEX idx_mfa_bc_user" in sql and "CREATE INDEX idx_mfa_ch_user" in sql
    assert conn.committed


async def test_m020_mariadb_tolerates_duplicate_index(monkeypatch):
    conn = _simulate_mariadb(
        monkeypatch,
        existing_tables=("users", "mfa_backup_codes", "mfa_challenges"),
        existing_cols={"users": {"id", "totp_secret", "totp_enabled", "totp_last_step", "totp_enabled_at"}},
    )

    async def boom(sql, params=()):
        if sql.startswith("CREATE INDEX"):
            raise Exception(1061, "Duplicate key name")
        return 0
    conn.execute = boom
    await mig._m020_mfa_schema()
    assert conn.committed


async def test_m020_mariadb_reraises_other_index_errors(monkeypatch):
    import pytest
    conn = _simulate_mariadb(monkeypatch, existing_tables=("users",),
                             existing_cols={"users": {"totp_secret", "totp_enabled",
                                                       "totp_last_step", "totp_enabled_at"}})

    async def boom(sql, params=()):
        if sql.startswith("CREATE INDEX"):
            raise Exception(1064, "syntax")
        return 0
    conn.execute = boom
    with pytest.raises(Exception, match="syntax"):
        await mig._m020_mfa_schema()


def test_m020_registered_last():
    assert mig._MIGRATIONS[-1][0] == "m020"


def test_ddl_parity_mfa():
    from backend.db.schema import _TABLES
    from backend.db.schema_mariadb import MARIADB_TABLES, MARIADB_INDEXES
    cols = ("totp_secret", "totp_enabled", "totp_last_step", "totp_enabled_at")
    s = next(x for x in _TABLES if x[0] == "users")
    m = next(x for x in MARIADB_TABLES if x[0] == "users")
    for col in cols:
        assert col in s[1] and col in m[1]
    assert {c for c, _ in s[2]} >= set(cols)
    for t in ("mfa_backup_codes", "mfa_challenges"):
        assert any(n == t for n, *_ in _TABLES) and any(n == t for n, *_ in MARIADB_TABLES)
    assert any("idx_mfa_bc_user" in i for i in MARIADB_INDEXES)
    assert any("idx_mfa_ch_user" in i for i in MARIADB_INDEXES)


def test_migration_tools_order_mfa_backup_codes_after_users():
    from backend.tools import migrate_to_mariadb as a, migrate_to_sqlite as b
    for tool in (a, b):
        assert tool.ORDERED_TABLES.index("mfa_backup_codes") == tool.ORDERED_TABLES.index("users") + 1
        assert "mfa_challenges" not in tool.ORDERED_TABLES
