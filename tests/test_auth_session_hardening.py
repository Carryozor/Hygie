"""Session hardening (4.5).

1. A password change must kill the access tokens issued before it (they used to
   stay valid up to one hour on a stolen device).
2. A refresh token presented again after its rotation grace window is a theft
   signal: every session of that user is revoked.
"""
import os
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

PWD = "supersecret123"
COOKIE = "hygie_refresh"


@pytest_asyncio.fixture
async def env(tmp_path):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod

    db_path = str(tmp_path / "s.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    auth_mod._rate_buckets.clear()
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", PWD)
    yield auth_mod, db_path
    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


def _creds(token):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ── access tokens ───────────────────────────────────────────────────────────

async def test_fresh_token_is_accepted(env):
    auth, _ = env
    assert await auth.require_auth(_creds(await auth.issue_access_token("alice"))) == "alice"


async def test_token_issued_before_password_change_is_rejected(env):
    auth, _ = env
    old = await auth.issue_access_token("alice")
    await auth.invalidate_access_tokens("alice")
    with pytest.raises(HTTPException) as e:
        await auth.require_auth(_creds(old))
    assert e.value.status_code == 401


async def test_token_issued_after_the_invalidation_is_accepted(env):
    auth, _ = env
    await auth.invalidate_access_tokens("alice")
    assert await auth.require_auth(_creds(await auth.issue_access_token("alice"))) == "alice"


async def test_invalidation_is_immediate_within_the_same_second(env):
    auth, _ = env
    old = await auth.issue_access_token("alice")
    await auth.invalidate_access_tokens("alice")
    new = await auth.issue_access_token("alice")
    with pytest.raises(HTTPException):
        await auth.require_auth(_creds(old))
    assert await auth.require_auth(_creds(new)) == "alice"


async def test_token_for_deleted_user_is_rejected(env):
    auth, _ = env
    tok = auth.create_access_token("ghost")
    with pytest.raises(HTTPException) as e:
        await auth.require_auth(_creds(tok))
    assert e.value.status_code == 401


async def test_legacy_token_without_version_is_valid_until_first_invalidation(env):
    import jwt
    auth, _ = env
    tok = jwt.encode({"sub": "alice", "type": "access",
                      "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                     auth.SECRET_KEY, algorithm=auth.ALGORITHM)
    assert await auth.require_auth(_creds(tok)) == "alice"
    await auth.invalidate_access_tokens("alice")
    with pytest.raises(HTTPException):
        await auth.require_auth(_creds(tok))


async def test_change_password_end_to_end_kills_old_access_token(env):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from backend.routers import auth as auth_router
    auth, _ = env
    app = FastAPI()
    app.include_router(auth_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        old = (await c.post("/api/auth/login", json={"username": "alice", "password": PWD})).json()["access_token"]
        r = await c.post("/api/auth/change-password",
                         json={"current_password": PWD, "new_password": "another-secret-456"},
                         headers={"Authorization": f"Bearer {old}"})
        assert r.status_code == 200
        new = r.json()["access_token"]
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})).status_code == 401
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {new}"})).status_code == 200


async def test_logout_all_kills_old_access_token(env):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from backend.routers import auth as auth_router
    app = FastAPI()
    app.include_router(auth_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        old = (await c.post("/api/auth/login", json={"username": "alice", "password": PWD})).json()["access_token"]
        assert (await c.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {old}"})).status_code == 200
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})).status_code == 401


# ── refresh reuse ───────────────────────────────────────────────────────────

async def _age_rotation(db_path, raw, seconds):
    """Pretend the token was rotated `seconds` ago."""
    import backend.auth as auth
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE refresh_tokens SET rotated_at=?, expires_at=? WHERE token_hash=?",
                         (past, past, auth._hash_token(raw)))
        await db.commit()


async def test_rotated_token_inside_grace_window_still_works(env):
    auth, _ = env
    raw = await auth.create_refresh_token("alice")
    await auth.retire_refresh_token(raw, grace_seconds=60)
    assert await auth.verify_refresh_token(raw) == "alice"


async def test_reuse_after_grace_revokes_every_session(env):
    auth, path = env
    stolen = await auth.create_refresh_token("alice")
    other = await auth.create_refresh_token("alice")
    await auth.retire_refresh_token(stolen, grace_seconds=60)
    await _age_rotation(path, stolen, 600)
    assert await auth.verify_refresh_token(stolen) is None
    assert await auth.verify_refresh_token(other) is None     # the whole family is dead


async def test_naturally_expired_token_does_not_revoke_other_sessions(env):
    auth, path = env
    old = await auth.create_refresh_token("alice")
    other = await auth.create_refresh_token("alice")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE refresh_tokens SET expires_at=? WHERE token_hash=?",
                         (past, auth._hash_token(old)))
        await db.commit()
    assert await auth.verify_refresh_token(old) is None
    assert await auth.verify_refresh_token(other) == "alice"


async def test_reuse_detection_does_not_touch_other_users(env):
    auth, path = env
    await auth.create_user("bob", PWD)
    bob = await auth.create_refresh_token("bob")
    stolen = await auth.create_refresh_token("alice")
    await auth.retire_refresh_token(stolen, grace_seconds=60)
    await _age_rotation(path, stolen, 600)
    await auth.verify_refresh_token(stolen)
    assert await auth.verify_refresh_token(bob) == "bob"


async def test_reuse_also_invalidates_access_tokens(env):
    auth, path = env
    access = await auth.issue_access_token("alice")
    stolen = await auth.create_refresh_token("alice")
    await auth.retire_refresh_token(stolen, grace_seconds=60)
    await _age_rotation(path, stolen, 600)
    await auth.verify_refresh_token(stolen)
    with pytest.raises(HTTPException):
        await auth.require_auth(_creds(access))


# ── migration ───────────────────────────────────────────────────────────────

async def test_m019_adds_columns_sqlite_and_is_idempotent(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.db.utils as _u
    import backend.db.settings_store as _s
    import backend.db.schema as _sc
    from backend.db.migrations import _m019_session_hardening_columns
    path = str(tmp_path / "m019.db")
    for mod, attr in ((_u, "DB_PATH"), (_s, "DB_PATH"), (_sc, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        await db.execute("CREATE TABLE refresh_tokens (id INTEGER PRIMARY KEY, token_hash TEXT)")
        await db.execute("INSERT INTO users (id, username) VALUES (1, 'a')")
        await db.commit()
    await _m019_session_hardening_columns()
    await _m019_session_hardening_columns()
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT token_version FROM users") as cur:
            assert await cur.fetchone() == (0,)
        async with db.execute("SELECT rotated_at FROM refresh_tokens") as cur:
            assert await cur.fetchall() == []


async def test_m019_mariadb_emits_both_alters(monkeypatch):
    import backend.db.migrations as mig
    from tests.test_cov_dbauth_migrations import _simulate_mariadb
    conn = _simulate_mariadb(monkeypatch, existing_cols={"users": {"id"}, "refresh_tokens": {"id"}})
    await mig._m019_session_hardening_columns()
    sql = " ".join(conn.executed)
    assert "ADD COLUMN token_version" in sql
    assert "ADD COLUMN rotated_at VARCHAR(32)" in sql


def test_ddl_parity_for_session_columns():
    from backend.db.schema import _TABLES
    from backend.db.schema_mariadb import MARIADB_TABLES
    for table, col in (("users", "token_version"), ("refresh_tokens", "rotated_at")):
        s = next(d for n, d, *_ in _TABLES if n == table)
        m = next(d for n, d, *_ in MARIADB_TABLES if n == table)
        assert col in s and col in m
