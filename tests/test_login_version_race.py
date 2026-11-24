"""/login vs concurrent 2FA activation / password change (4.5.1 review, M1).

The user row is read BEFORE the ~170 ms Argon2 check. A token_version bump that
lands during that window (2FA enabled, password changed, logout-all) must not
leave the in-flight password login with a valid session.
"""
import os
import sqlite3

import aiosqlite
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

PWD = "supersecret123"
COOKIE = "hygie_refresh"


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import backend.db.engine as _eng
    import backend.db.utils as _db_utils
    import backend.auth as auth_mod
    import backend.auth_events as ev
    from fastapi import FastAPI
    from backend.routers import auth as auth_router

    db_path = str(tmp_path / "v.db")
    orig_engine, orig_utils = _eng.SQLITE_PATH, _db_utils.DB_PATH
    _eng.SQLITE_PATH = db_path
    _db_utils.DB_PATH = db_path
    monkeypatch.setattr(auth_mod, "DB_PATH", ":memory:")
    auth_mod._rate_buckets.clear()
    from backend.db.schema import init_db
    await init_db()
    await auth_mod.create_user("alice", PWD)

    async def _no_notify(*a, **k):
        return None
    monkeypatch.setattr(ev, "_notify_login", _no_notify)

    app = FastAPI()
    app.include_router(auth_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, db_path, auth_router
    _eng.SQLITE_PATH = orig_engine
    _db_utils.DB_PATH = orig_utils
    auth_mod._rate_buckets.clear()


def _concurrent_bump(monkeypatch, router, db_path, *, enable_totp=False):
    """verify_password returns the real verdict, then a 'concurrent' request bumps the version."""
    real = router.verify_password

    def racing(password, stored_hash):
        ok = real(password, stored_hash)
        con = sqlite3.connect(db_path)
        try:
            extra = ", totp_enabled=1" if enable_totp else ""
            con.execute(f"UPDATE users SET token_version = token_version + 1{extra} WHERE username='alice'")
            con.commit()
        finally:
            con.close()
        return ok
    monkeypatch.setattr(router, "verify_password", racing)


async def _refresh_rows(path):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT COUNT(*) FROM refresh_tokens") as cur:
            return (await cur.fetchone())[0]


async def _post_login(c):
    return await c.post("/api/auth/login", json={"username": "alice", "password": PWD})


async def test_version_bump_during_argon2_rejects_login(env, monkeypatch):
    c, path, router = env
    _concurrent_bump(monkeypatch, router, path)
    r = await _post_login(c)
    assert r.status_code == 401
    assert COOKIE not in r.cookies and "set-cookie" not in r.headers
    assert await _refresh_rows(path) == 0


async def test_concurrent_2fa_activation_during_argon2_rejects_login(env, monkeypatch):
    c, path, router = env
    _concurrent_bump(monkeypatch, router, path, enable_totp=True)
    r = await _post_login(c)
    assert r.status_code == 401
    assert COOKIE not in r.cookies and "set-cookie" not in r.headers
    assert await _refresh_rows(path) == 0


async def test_normal_login_unchanged(env):
    c, path, _ = env
    r = await _post_login(c)
    assert r.status_code == 200
    assert set(r.json()) == {"token", "access_token", "username"}
    assert COOKIE in r.cookies
    assert await _refresh_rows(path) == 1
    me = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200
